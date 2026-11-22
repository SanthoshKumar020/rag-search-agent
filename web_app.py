"""Flask web UI for the Search + RAG Reply Agent.

A thin web layer over the exact same pipeline the CLI uses
(agent.web_evidence / agent.local_evidence -> llm.generate). The web
index is built in memory per request (agent.web_evidence already uses
IndexStore.build_ephemeral), so nothing is written to disk — safe on
serverless hosts like Vercel.

Also supports an optional **Browse** mode that drives a real Chromium
via browser-use (local only — not available on pure serverless).

Secrets come only from environment variables (see config.py, which
loads .env locally and process env in production). No key is ever
stored in code or in the repo.

Run locally:  python web_app.py            (http://127.0.0.1:5000)
On Vercel:    api/index.py imports `app` from here.
"""
import json
import queue
import threading

from flask import (Flask, Response, jsonify, render_template, request,
                   stream_with_context)

import agent as pipeline
import config
from llm import generate

app = Flask(__name__)

# agent.log() writes to stderr in the CLI; here we collect the step
# messages per request so the UI can show what the agent did. The
# pipeline functions are not re-entrant with a swapped logger, so run
# them under a lock (fine for a single-user demo deployment).
_pipeline_lock = threading.Lock()


def _run_with_steps(fn, *args):
    """Run a pipeline evidence function, capturing its log() steps."""
    steps: list[str] = []
    with _pipeline_lock:
        original_log = pipeline.log
        pipeline.log = steps.append
        try:
            hits = fn(*args)
        finally:
            pipeline.log = original_log
    return hits, steps


def _sources_json(hits: list[dict]) -> list[dict]:
    """Unique sources as [{title, url}] in first-seen order."""
    seen, out = set(), []
    for h in hits:
        url = h["source"]
        if url in seen:
            continue
        seen.add(url)
        out.append({"title": h.get("title") or url, "url": url})
    return out


def _evidence_text(hits: list[dict]) -> str:
    """Human-readable evidence dump, mirroring the CLI's no-key output."""
    lines = []
    for i, h in enumerate(hits, 1):
        snippet = " ".join(h["text"].split())
        lines.append(f"[{i}] (score {h['score']:.3f}) {h.get('title') or ''}\n"
                     f"{h['source']}\n{snippet[:500]}")
    return "\n\n".join(lines)


ASK_SYSTEM = (
    "You are a research assistant. Answer the user's question using "
    "ONLY the evidence provided. Cite claims inline with bracketed "
    "numbers like [1], [2] that match the evidence blocks. If the "
    "evidence does not cover something, say so plainly. Be accurate "
    "and concise."
)

REPLY_SYSTEM = (
    "You draft ready-to-send replies. Write a polite, helpful reply "
    "to the incoming message, grounded ONLY in the evidence provided "
    "— never invent facts, prices, dates or commitments. Keep it "
    "natural and concise, as a human would send it. Do not include "
    "citations or a signature inside the draft."
)


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/health")
def health():
    browse_ok = False
    try:
        import browser_agent
        browse_ok = browser_agent.available()
    except Exception:
        pass
    return jsonify({
        "ok": True,
        "llm_backend": config.llm_backend(),
        "embed_backend": config.embed_backend(),
        "browse_available": browse_ok,
    })


@app.post("/api/query")
def query():
    data = request.get_json(silent=True) or {}
    text = (data.get("query") or "").strip()
    mode = (data.get("mode") or "ask").lower()
    use_web = data.get("use_web", True)
    if mode not in ("ask", "reply", "browse"):
        mode = "ask"
    if not text:
        return jsonify({"error": "Please enter a question or message."}), 400

    # ---- Browse mode (local Chromium via browser-use) -----------------
    if mode == "browse":
        return _browse_oneshot(text)

    steps = [f"embeddings: {config.embed_backend()} | "
             f"llm: {config.llm_backend()}"]
    try:
        if use_web:
            search_text = text if mode == "ask" else pipeline.reply_search_query(text)
            hits, found_steps = _run_with_steps(pipeline.web_evidence, search_text)
        else:
            hits, found_steps = _run_with_steps(pipeline.local_evidence, text)
    except Exception as exc:  # search/fetch backends can fail; report, don't crash
        return jsonify({
            "answer": "",
            "sources": [],
            "steps": steps + [f"error while gathering evidence: {exc}"],
            "error": f"Could not gather evidence: {exc}",
        }), 502
    steps.extend(found_steps)

    sources = _sources_json(hits)
    if not hits:
        return jsonify({
            "answer": ("No evidence could be retrieved for that input. "
                       "Try rephrasing, or enable web search."),
            "sources": [],
            "steps": steps,
        })

    evidence = pipeline.evidence_block(hits, pipeline.format_sources(hits))
    if mode == "ask":
        system = ASK_SYSTEM
        user = (f"Question: {text}\n\nEvidence:\n{evidence}"
                "\n\nWrite the answer with inline citations.")
    else:
        system = REPLY_SYSTEM
        user = (f"Incoming message to reply to:\n{text}\n\nEvidence:\n"
                f"{evidence}\n\nDraft the reply.")

    try:
        answer = generate(system, user)
    except Exception as exc:  # LLM call failed (rate limit, network, ...)
        answer = None
        steps.append(f"generation failed ({exc}); showing evidence instead")

    if answer is None:
        note = ("No LLM key configured on the server — showing the "
                "retrieved evidence instead of a generated answer. "
                "Set GROQ_API_KEY (or HF_TOKEN) to enable generation.")
        return jsonify({
            "answer": _evidence_text(hits),
            "sources": sources,
            "steps": steps,
            "note": note,
            "evidence_only": True,
        })

    return jsonify({
        "answer": answer,
        "sources": sources,
        "steps": steps,
        "evidence_only": False,
    })


def _browse_oneshot(task: str):
    """Non-streaming browse fallback."""
    try:
        import browser_agent
    except ImportError:
        return jsonify({
            "answer": "",
            "sources": [],
            "steps": [],
            "error": "browser_agent module not found.",
        }), 501

    if not browser_agent.available():
        return jsonify({
            "answer": "",
            "sources": [],
            "steps": [],
            "error": browser_agent._missing_message(),
            "note": (
                "Browse mode needs browser-use installed and a local "
                "Chromium. It does not run on pure serverless hosts "
                "(Vercel). Use the CLI on your machine instead."
            ),
        }), 501

    try:
        result = browser_agent.run_browser_task(task, max_steps=40, headless=True)
    except Exception as exc:
        return jsonify({
            "answer": "",
            "sources": [],
            "steps": [f"browser agent error: {exc}"],
            "error": f"Browser agent failed: {exc}",
        }), 502

    return jsonify({
        "answer": result,
        "sources": [],
        "steps": ["browser agent completed the task"],
        "evidence_only": False,
        "mode": "browse",
    })


def _prompts(mode: str, text: str, hits: list[dict]):
    """Build (system, user) generation prompts for ask/reply modes."""
    evidence = pipeline.evidence_block(hits, pipeline.format_sources(hits))
    if mode == "ask":
        return ASK_SYSTEM, (f"Question: {text}\n\nEvidence:\n{evidence}"
                            "\n\nWrite the answer with inline citations.")
    return REPLY_SYSTEM, (f"Incoming message to reply to:\n{text}\n\n"
                          f"Evidence:\n{evidence}\n\nDraft the reply.")


@app.post("/api/query/stream")
def query_stream():
    """Server-Sent Events version of /api/query.

    Emits one JSON event per line (`data: {...}`) as the pipeline
    actually progresses — search, per-page fetch/fetched, indexed,
    retrieved, answer, done — so the UI can render the agent's
    browsing live. Browse mode emits browse_start / browse_done.
    """
    data = request.get_json(silent=True) or {}
    text = (data.get("query") or "").strip()
    mode = (data.get("mode") or "ask").lower()
    use_web = data.get("use_web", True)
    if mode not in ("ask", "reply", "browse"):
        mode = "ask"
    if not text:
        return jsonify({"error": "Please enter a question or message."}), 400

    def sse(event: dict) -> str:
        return f"data: {json.dumps(event)}\n\n"

    # ---- Browse mode stream ------------------------------------------
    if mode == "browse":
        return _browse_stream(text, sse)

    def gen():
        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def worker():
            steps: list[str] = []
            try:
                with _pipeline_lock:
                    original_log = pipeline.log
                    pipeline.log = steps.append
                    try:
                        if use_web:
                            search_text = (
                                text if mode == "ask"
                                else pipeline.reply_search_query(text))
                            hits = pipeline.web_evidence(
                                search_text, on_event=events.put)
                        else:
                            hits = pipeline.local_evidence(
                                text, on_event=events.put)
                    finally:
                        pipeline.log = original_log
                outcome["hits"] = hits
                outcome["steps"] = steps
            except Exception as exc:  # search/fetch backends can fail
                outcome["error"] = exc
            finally:
                events.put(None)  # sentinel: evidence stage finished

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while True:
            event = events.get()
            if event is None:
                break
            yield sse(event)
        thread.join()

        if "error" in outcome:
            yield sse({"type": "error",
                       "message": f"Could not gather evidence: "
                                  f"{outcome['error']}"})
            yield sse({"type": "done"})
            return

        hits = outcome["hits"]
        sources = _sources_json(hits)
        if not hits:
            yield sse({"type": "answer",
                       "text": "No evidence could be retrieved for that "
                               "input. Try rephrasing, or enable web "
                               "search.",
                       "sources": [], "evidence_only": False})
            yield sse({"type": "done"})
            return

        yield sse({"type": "generating"})
        system, user = _prompts(mode, text, hits)
        try:
            answer = generate(system, user)
        except Exception as exc:  # LLM call failed; fall back to evidence
            answer = None
            yield sse({"type": "error",
                       "message": f"Generation failed ({exc}); showing "
                                  f"the retrieved evidence instead."})
        if answer is None:
            note = ("No LLM key configured on the server — showing the "
                    "retrieved evidence instead of a generated answer. "
                    "Set GROQ_API_KEY (or HF_TOKEN) to enable generation.")
            yield sse({"type": "answer", "text": _evidence_text(hits),
                       "sources": sources, "evidence_only": True,
                       "note": note})
        else:
            yield sse({"type": "answer", "text": answer,
                       "sources": sources, "evidence_only": False})
        yield sse({"type": "done"})

    return Response(
        stream_with_context(gen()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache",
                 "X-Accel-Buffering": "no"},
    )


def _browse_stream(task: str, sse):
    """SSE stream for browse mode."""

    def gen():
        try:
            import browser_agent
        except ImportError:
            yield sse({"type": "error",
                       "message": "browser_agent module not found."})
            yield sse({"type": "done"})
            return

        if not browser_agent.available():
            yield sse({
                "type": "error",
                "message": browser_agent._missing_message(),
            })
            yield sse({
                "type": "answer",
                "text": (
                    "Browse mode is not available here.\n\n"
                    "It needs:\n"
                    "  1. pip install 'browser-use==1.0.8'\n"
                    "  2. A local Chromium (downloaded automatically)\n"
                    "  3. GROQ_API_KEY or HF_TOKEN\n\n"
                    "It does **not** run on pure serverless hosts (Vercel). "
                    "Use the CLI on your machine:\n\n"
                    f'  python agent.py browse "{task[:80]}…"'
                ),
                "sources": [],
                "evidence_only": True,
                "note": "Browse mode requires a local browser.",
            })
            yield sse({"type": "done"})
            return

        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def worker():
            try:
                result = browser_agent.run_browser_task(
                    task,
                    max_steps=40,
                    headless=True,
                    on_event=events.put,
                )
                outcome["result"] = result
            except Exception as exc:
                outcome["error"] = str(exc)
            finally:
                events.put(None)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while True:
            event = events.get()
            if event is None:
                break
            yield sse(event)
        thread.join()

        if "error" in outcome:
            yield sse({"type": "error",
                       "message": f"Browser agent failed: {outcome['error']}"})
            yield sse({"type": "done"})
            return

        yield sse({
            "type": "answer",
            "text": outcome.get("result", "(no result)"),
            "sources": [],
            "evidence_only": False,
            "mode": "browse",
        })
        yield sse({"type": "done"})

    return Response(
        stream_with_context(gen()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache",
                 "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
