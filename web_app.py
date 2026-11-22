"""Flask web UI for the Search + RAG + Personal Agent.

Modes: ask, reply, browse (local Chromium), agent (Muse-inspired tool loop).
"""
import json
import queue
import threading

from flask import (Flask, Response, jsonify, render_template, request,
                   stream_with_context)

import os

import activity
import agent as pipeline
import briefing
import config
import history
import media
import memory
import skills
import tasks as tasks_mod
import tools_extra
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


def _record_activity(task: str, result: dict | None) -> None:
    try:
        result = result or {}
        activity.add_entry(task, result.get("tools_used") or [],
                           result.get("answer") or "")
    except Exception:
        pass  # activity log is best-effort


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


def _memory_payload():
    data = memory.export() or {}
    return {
        "facts": data.get("facts") or [],
        "preferences": data.get("preferences") or {},
        "lines": memory.recall("", limit=100),
    }


@app.get("/api/memory")
def api_memory_get():
    return jsonify(_memory_payload())


@app.post("/api/memory")
def api_memory_add():
    data = request.get_json(silent=True) or {}
    fact = (data.get("fact") or "").strip()
    kind = (data.get("kind") or "fact").strip()
    if kind not in ("fact", "preference"):
        kind = "fact"
    if not fact:
        return jsonify({"error": "Please enter a fact to remember."}), 400
    result = memory.remember(fact, kind=kind)
    return jsonify({"result": result, **_memory_payload()})


@app.post("/api/memory/delete")
def api_memory_delete():
    data = request.get_json(silent=True) or {}
    line = (data.get("line") or "").strip()
    if not line:
        return jsonify({"error": "Missing memory line to delete."}), 400
    result = memory.forget(line)
    return jsonify({"result": result, **_memory_payload()})


@app.post("/api/memory/clear")
def api_memory_clear():
    result = memory.clear()
    return jsonify({"result": result, **_memory_payload()})


@app.post("/api/skills")
def api_skills_create():
    data = request.get_json(silent=True) or {}
    try:
        created = skills.create_skill(
            data.get("name") or "",
            data.get("description") or "",
            data.get("instructions") or "",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError:
        return jsonify({
            "error": ("This hosted server's filesystem is read-only — "
                      "skills can only be created when running the app "
                      "locally on your own computer."),
            "error_code": "skills_readonly_hosted",
        }), 501
    return jsonify({"created": created, "skills": skills.list_skills()})


def _rule_based_ideas() -> list[str]:
    import goals as goals_mod
    import reminders as reminders_mod

    ideas: list[str] = []
    for r in reminders_mod.due_today_or_overdue():
        ideas.append(
            f"Help me prepare for '{r['text']}' (due {r['due']}): "
            "make a short, concrete plan and save it as an artifact.")
    for g in goals_mod.list_goals("active")[:2]:
        ideas.append(
            f"Advance my goal '{g['title']}': research the next concrete "
            "step, do what you can with your tools, and note progress "
            "on the goal.")
    city = briefing._user_city()
    ideas.append(f"Use the daily-briefing skill and brief me for today "
                 f"in {city}.")
    facts = (memory.export() or {}).get("facts") or []
    if facts:
        ideas.append(
            f"I remember this about me: '{facts[-1]}'. Suggest one useful "
            "action for today based on it and create a reminder.")
    ideas.append(
        "Check today's data-engineering headlines and job-market news, "
        "and summarise anything relevant to my job hunt.")
    ideas.append(
        "Use the interview-prep skill: give me 3 tough SQL window-function "
        "questions with model answers.")
    return ideas[:4]


def _ideas_today() -> tuple[list[str], str]:
    """Ideas for today (IST), cached per calendar day in DATA_DIR."""
    from datetime import datetime, timezone

    today = (datetime.now(timezone.utc)
             .astimezone(tools_extra.IST).date().isoformat())
    cache_path = config.DATA_DIR / "ideas.json"
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("date") == today and cached.get("ideas"):
            return cached["ideas"], today
    except Exception:
        pass
    ideas = _rule_based_ideas()
    # Optional one-call LLM polish; silently keep the templates on failure.
    if config.llm_backend() != "none":
        try:
            polished = generate(
                "You polish short task suggestions for a personal AI "
                "agent. Reply with ONLY the polished suggestions, one "
                "per line, no numbering, keeping each under 200 chars.",
                "Polish these task suggestions:\n" + "\n".join(ideas),
            )
            lines = [ln.strip(" -•") for ln in (polished or "").splitlines()
                     if ln.strip()]
            if len(lines) >= 3:
                ideas = lines[:4]
        except Exception:
            pass
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"date": today, "ideas": ideas},
                       indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    except Exception:
        pass
    return ideas, today


@app.get("/api/ideas")
def api_ideas():
    ideas, today = _ideas_today()
    return jsonify({"ideas": ideas, "date": today})


@app.get("/api/cards")
def api_cards():
    city = briefing._user_city()
    return jsonify({
        "city": city,
        "weather": tools_extra.weather_data(city),
        "fx": tools_extra.currency_data("USD", "INR"),
        "headlines": tools_extra.news_items("", 3),
    })


@app.get("/api/activity")
def api_activity():
    return jsonify({"activity": activity.list_activity()})


# ----------------------------------------------------------------------
# v3: scheduled tasks, mood companion, tool schemas, webhook trigger
# ----------------------------------------------------------------------
@app.get("/api/tasks")
def api_tasks_list():
    return jsonify({"tasks": tasks_mod.list_tasks()})


@app.post("/api/tasks")
def api_tasks_create():
    data = request.get_json(silent=True) or {}
    try:
        task = tasks_mod.create(data.get("instruction") or "",
                                data.get("run_at") or "")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"created": task, "tasks": tasks_mod.list_tasks()})


@app.post("/api/tasks/cancel")
def api_tasks_cancel():
    data = request.get_json(silent=True) or {}
    tid = (data.get("id") or "").strip()
    if not tid:
        return jsonify({"error": "Missing task id."}), 400
    try:
        task = tasks_mod.cancel(tid)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"cancelled": task, "tasks": tasks_mod.list_tasks()})


@app.post("/api/tasks/run")
def api_tasks_run():
    """Execute one stored task right now (on-demand path for hosted
    servers, which cannot wake themselves at the scheduled time)."""
    data = request.get_json(silent=True) or {}
    tid = (data.get("id") or "").strip()
    task = tasks_mod.get(tid)
    if task is None:
        return jsonify({"error": f"Task not found: {tid}"}), 404
    if task.get("status") in ("done", "cancelled"):
        return jsonify({"error": f"Task already {task['status']}."}), 400
    try:
        import agent_core
    except ImportError:
        return jsonify({"error": "agent_core module not found."}), 501
    tasks_mod.mark_running(tid)
    try:
        result = agent_core.run_agent(task["instruction"], max_steps=8)
        answer = result.get("answer") or ""
        updated = tasks_mod.mark_done(tid, answer)
    except Exception as exc:
        updated = tasks_mod.mark_failed(tid, str(exc))
        return jsonify({"task": updated, "error": f"Task failed: {exc}"}), 502
    _record_activity(f"[scheduled] {task['instruction']}", result)
    return jsonify({"task": updated, "answer": answer,
                    "tools_used": result.get("tools_used") or []})


@app.get("/api/mood")
def api_mood():
    mood = (request.args.get("mood") or "").strip()
    language = (request.args.get("language") or "both").strip()
    if not mood:
        return jsonify({"error": "Pass ?mood=sad (or happy, stressed, "
                                 "romantic, energetic, bored, lonely)."}), 400
    try:
        return jsonify(media.suggest_media_data(mood, language))
    except Exception as exc:
        return jsonify({"error": f"Mood suggestions failed: {exc}"}), 502


@app.get("/api/tools")
def api_tools():
    try:
        import agent_core
        schemas = agent_core.tool_schemas()
    except Exception:
        schemas = []
    return jsonify({"tools": schemas, "count": len(schemas)})


@app.post("/api/webhook/run")
def api_webhook_run():
    """Trigger endpoint (n8n-style): POST {"task": "..."} runs the
    agent once and returns its answer. If the AGENT_WEBHOOK_TOKEN env
    var is set, the request must send it as the X-Agent-Token header.
    The agent's safety rules apply — a webhook can never make it pay
    or send real messages."""
    token = os.environ.get("AGENT_WEBHOOK_TOKEN", "").strip()
    if token and request.headers.get("X-Agent-Token", "") != token:
        return jsonify({"error": "Invalid or missing X-Agent-Token."}), 401
    data = request.get_json(silent=True) or {}
    task = (data.get("task") or "").strip()
    if not task:
        return jsonify({"error": "JSON body needs a 'task' string."}), 400
    try:
        import agent_core
        result = agent_core.run_agent(task, max_steps=8)
    except Exception as exc:
        return jsonify({"error": f"Agent failed: {exc}"}), 502
    _record_activity(f"[webhook] {task}", result)
    return jsonify({
        "answer": result.get("answer") or "",
        "steps": result.get("steps") or [],
        "tools_used": result.get("tools_used") or [],
    })


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
    _record_activity(task, result)
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
        _record_activity(task, result)
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
