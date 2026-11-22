"""Flask web UI for the Search + RAG + Personal Agent.

Modes: ask, reply, browse (local Chromium), agent (Muse-inspired tool loop).
"""
import json
import queue
import threading

from flask import (Flask, Response, jsonify, render_template, request,
                   stream_with_context)

import agent as pipeline
import briefing
import config
import history
import skills
from llm import generate

app = Flask(__name__)
_pipeline_lock = threading.Lock()

# Friendly, structured explanation used whenever Browse mode is requested
# on a host that cannot run a real Chromium (e.g. Vercel serverless).
BROWSE_HOSTED_MESSAGE = (
    "Browse mode needs a real Chromium browser, which this hosted server "
    "can't run. It works when you run this app on your own computer: "
    "1) pip install 'browser-use==1.0.8'  "
    "2) playwright install chromium  "
    "3) put your free Groq key in .env  "
    "4) run the app locally and choose Browse."
)


def _browse_unavailable_payload():
    return {
        "error_code": "browse_unavailable_hosted",
        "message": BROWSE_HOSTED_MESSAGE,
        "error": BROWSE_HOSTED_MESSAGE,
        "answer": "",
        "sources": [],
        "steps": [],
    }


def _requested_model(data: dict) -> str | None:
    """Web model picker: only allowlisted Groq models, else None."""
    model = (data.get("model") or "").strip()
    return model if model in config.WEB_MODELS else None


def _record(mode: str, query: str, answer: str, sources=None) -> None:
    try:
        history.add_entry(mode, query, answer, sources or [])
    except Exception:
        pass  # history is best-effort; never break a query over it


def _run_with_steps(fn, *args):
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
    seen, out = set(), []
    for h in hits:
        url = h["source"]
        if url in seen:
            continue
        seen.add(url)
        out.append({"title": h.get("title") or url, "url": url})
    return out


def _evidence_text(hits: list[dict]) -> str:
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
        "agent_available": config.llm_backend() != "none",
    })


@app.get("/api/skills")
def api_skills():
    return jsonify({"skills": skills.list_skills()})


@app.get("/api/briefing")
def api_briefing():
    try:
        return jsonify(briefing.build_briefing())
    except Exception as exc:
        return jsonify({"error": f"Briefing failed: {exc}"}), 502


@app.get("/api/history")
def api_history():
    return jsonify({"history": history.list_history()})


@app.post("/api/query")
def query():
    data = request.get_json(silent=True) or {}
    text = (data.get("query") or "").strip()
    mode = (data.get("mode") or "ask").lower()
    use_web = data.get("use_web", True)
    model = _requested_model(data)  # ask/reply only; agent keeps AGENT_MODEL
    if mode not in ("ask", "reply", "browse", "agent"):
        mode = "ask"
    if not text:
        return jsonify({"error": "Please enter a question or message."}), 400

    if mode == "browse":
        return _browse_oneshot(text)
    if mode == "agent":
        return _agent_oneshot(text)

    steps = [f"embeddings: {config.embed_backend()} | "
             f"llm: {config.llm_backend()}"]
    try:
        if use_web:
            search_text = text if mode == "ask" else pipeline.reply_search_query(text)
            hits, found_steps = _run_with_steps(pipeline.web_evidence, search_text)
        else:
            hits, found_steps = _run_with_steps(pipeline.local_evidence, text)
    except Exception as exc:
        return jsonify({
            "answer": "",
            "sources": [],
            "steps": steps + [f"error while gathering evidence: {exc}"],
            "error": f"Could not gather evidence: {exc}",
        }), 502
    steps.extend(found_steps)

    sources = _sources_json(hits)
    if not hits:
        answer = ("No evidence could be retrieved for that input. "
                  "Try rephrasing, or enable web search.")
        _record(mode, text, answer, [])
        return jsonify({
            "answer": answer,
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
        answer = generate(system, user, model=model)
    except Exception as exc:
        answer = None
        steps.append(f"generation failed ({exc}); showing evidence instead")

    if answer is None:
        note = ("No LLM key configured on the server — showing the "
                "retrieved evidence instead of a generated answer. "
                "Set GROQ_API_KEY (or HF_TOKEN) to enable generation.")
        evidence_text = _evidence_text(hits)
        _record(mode, text, evidence_text, sources)
        return jsonify({
            "answer": evidence_text,
            "sources": sources,
            "steps": steps,
            "note": note,
            "evidence_only": True,
        })

    _record(mode, text, answer, sources)
    return jsonify({
        "answer": answer,
        "sources": sources,
        "steps": steps,
        "evidence_only": False,
    })


def _agent_oneshot(task: str):
    try:
        import agent_core
    except ImportError:
        return jsonify({"error": "agent_core module not found.", "answer": "",
                        "sources": [], "steps": []}), 501
    try:
        result = agent_core.run_agent(task, max_steps=8)
    except Exception as exc:
        return jsonify({
            "answer": "",
            "sources": [],
            "steps": [],
            "error": f"Agent failed: {exc}",
        }), 502
    _record("agent", task, result.get("answer") or "", [])
    return jsonify({
        "answer": result.get("answer") or "",
        "sources": [],
        "steps": result.get("steps") or [],
        "tools_used": result.get("tools_used") or [],
        "evidence_only": False,
        "mode": "agent",
    })


def _browse_oneshot(task: str):
    try:
        import browser_agent
    except ImportError:
        return jsonify({
            "answer": "", "sources": [], "steps": [],
            "error": "browser_agent module not found.",
        }), 501

    if not browser_agent.available():
        return jsonify(_browse_unavailable_payload()), 501

    try:
        result = browser_agent.run_browser_task(task, max_steps=40, headless=True)
    except Exception as exc:
        return jsonify({
            "answer": "", "sources": [],
            "steps": [f"browser agent error: {exc}"],
            "error": f"Browser agent failed: {exc}",
        }), 502

    _record("browse", task, result, [])
    return jsonify({
        "answer": result, "sources": [],
        "steps": ["browser agent completed the task"],
        "evidence_only": False, "mode": "browse",
    })


def _prompts(mode: str, text: str, hits: list[dict]):
    evidence = pipeline.evidence_block(hits, pipeline.format_sources(hits))
    if mode == "ask":
        return ASK_SYSTEM, (f"Question: {text}\n\nEvidence:\n{evidence}"
                            "\n\nWrite the answer with inline citations.")
    return REPLY_SYSTEM, (f"Incoming message to reply to:\n{text}\n\n"
                          f"Evidence:\n{evidence}\n\nDraft the reply.")


@app.post("/api/query/stream")
def query_stream():
    data = request.get_json(silent=True) or {}
    text = (data.get("query") or "").strip()
    mode = (data.get("mode") or "ask").lower()
    use_web = data.get("use_web", True)
    model = _requested_model(data)  # ask/reply only; agent keeps AGENT_MODEL
    if mode not in ("ask", "reply", "browse", "agent"):
        mode = "ask"
    if not text:
        return jsonify({"error": "Please enter a question or message."}), 400

    def sse(event: dict) -> str:
        return f"data: {json.dumps(event)}\n\n"

    if mode == "browse":
        return _browse_stream(text, sse)
    if mode == "agent":
        return _agent_stream(text, sse)

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
            except Exception as exc:
                outcome["error"] = exc
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
                       "message": f"Could not gather evidence: {outcome['error']}"})
            yield sse({"type": "done"})
            return

        hits = outcome["hits"]
        sources = _sources_json(hits)
        if not hits:
            no_ev = "No evidence could be retrieved for that input."
            _record(mode, text, no_ev, [])
            yield sse({"type": "answer",
                       "text": no_ev,
                       "sources": [], "evidence_only": False})
            yield sse({"type": "done"})
            return

        yield sse({"type": "generating"})
        system, user = _prompts(mode, text, hits)
        try:
            answer = generate(system, user, model=model)
        except Exception as exc:
            answer = None
            yield sse({"type": "error",
                       "message": f"Generation failed ({exc}); showing evidence."})
        if answer is None:
            note = ("No LLM key configured — showing retrieved evidence. "
                    "Set GROQ_API_KEY (or HF_TOKEN) to enable generation.")
            evidence_text = _evidence_text(hits)
            _record(mode, text, evidence_text, sources)
            yield sse({"type": "answer", "text": evidence_text,
                       "sources": sources, "evidence_only": True, "note": note})
        else:
            _record(mode, text, answer, sources)
            yield sse({"type": "answer", "text": answer,
                       "sources": sources, "evidence_only": False})
        yield sse({"type": "done"})

    return Response(
        stream_with_context(gen()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _agent_stream(task: str, sse):
    def gen():
        try:
            import agent_core
        except ImportError:
            yield sse({"type": "error", "message": "agent_core module not found."})
            yield sse({"type": "done"})
            return

        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def worker():
            try:
                result = agent_core.run_agent(
                    task, max_steps=8, on_event=events.put
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
            yield sse({"type": "error", "message": f"Agent failed: {outcome['error']}"})
            yield sse({"type": "done"})
            return

        result = outcome.get("result") or {}
        _record("agent", task, result.get("answer") or "", [])
        yield sse({
            "type": "answer",
            "text": result.get("answer") or "",
            "sources": [],
            "steps": result.get("steps") or [],
            "tools_used": result.get("tools_used") or [],
            "evidence_only": False,
            "mode": "agent",
        })
        yield sse({"type": "done"})

    return Response(
        stream_with_context(gen()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _browse_stream(task: str, sse):
    def gen():
        try:
            import browser_agent
        except ImportError:
            yield sse({"type": "error", "message": "browser_agent module not found."})
            yield sse({"type": "done"})
            return

        if not browser_agent.available():
            yield sse({
                "type": "browse_unavailable",
                "error_code": "browse_unavailable_hosted",
                "message": BROWSE_HOSTED_MESSAGE,
            })
            yield sse({"type": "done"})
            return

        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def worker():
            try:
                result = browser_agent.run_browser_task(
                    task, max_steps=40, headless=True, on_event=events.put
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

        _record("browse", task, outcome.get("result", ""), [])
        yield sse({
            "type": "answer", "text": outcome.get("result", "(no result)"),
            "sources": [], "evidence_only": False, "mode": "browse",
        })
        yield sse({"type": "done"})

    return Response(
        stream_with_context(gen()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
