"""Tool-using personal agent loop (Muse-inspired).

Combines web search, page fetch, optional browser control, memory,
goals, and artifacts into a multi-step agent that works toward a
user goal instead of answering in a single shot.

v4 (patterns adapted from Hermes Agent + the agent-harness topic —
see ~/workspace/research_notes/hermes-agent-harness-20261122):
  - context compression: long transcripts are digested (head + tail
    protected) instead of hard-truncated (context.py)
  - tool risk tiers + fail-closed policy: unattended runs
    (scheduled/webhook) cannot use the browser tool (policy.py)
  - delegation: the `delegate` tool runs an isolated sub-agent
  - run records with structured traces + resume (runs.py)
  - cross-session log + `search_sessions` tool (sessions.py)
  - standing instructions in memory, always injected (memory.py)

This is intentionally simpler than Meta Muse / Hermes: local JSON
storage, free-tier LLMs, no cloud VM, no payment APIs, no OAuth.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Optional

import artifacts
import config
import context as context_mod
import goals
import media
import memory
import policy
import reminders
import runs
import sessions
import skills
import tasks as tasks_mod
import tools_extra
from fetch import MIN_TEXT, fetch_text
from llm import generate
from search import web_search

EventCallback = Callable[[dict], None]

MAX_STEPS_DEFAULT = 8

SYSTEM = """You are a personal AI agent. You complete tasks using tools.
You have persistent memory about the user and can track long-running goals.

Rules:
- Prefer tools over guessing when facts are needed.
- Never invent URLs, prices, or commitments.
- For purchases or sending real emails: STOP and explain what the human must do — you cannot complete payments or send mail yourself.
- When the task is done, call the finish tool with a clear summary.
- Keep tool arguments short and concrete.
- Memory: when you learn a durable fact or preference about the user, save it with the remember tool. If the user states a standing rule ("always…", "from now on…"), save it with kind "instruction".
- Delegation: for a self-contained sub-piece of a big task (e.g. researching one sub-topic), you may call delegate with that subtask; a sub-agent runs it and returns its result. Do not delegate the whole task.
- Past work: use search_sessions to check what you did in earlier runs before repeating research.
- Scheduling: when the user asks for something at a FUTURE time ("tomorrow 11:00 am book/check X", "in 2 hours remind me via a task"), call create_task with the instruction and the time in the user's own words (e.g. "tomorrow 11:00 am"). Then finish by confirming the stored time (IST). A scheduled task runs later through this same loop with these same safety rules — it still cannot pay or send real messages.
- Friend mode: when the user expresses a feeling or mood ("I feel sad", "I'm stressed", "feeling lonely"), FIRST respond like a warm friend — acknowledge the feeling briefly and kindly in the finish answer — and use suggest_media to offer songs/shows/movies that fit the mood. Never sound clinical or robotic with feelings.

User memory:
{memory}

Active goals:
{goals}

Standing instructions from the user (always follow these):
{instructions}

Reply with ONLY a single JSON object, no markdown fences, in one of these forms:
{{"tool": "web_search", "query": "..."}}
{{"tool": "fetch_page", "url": "..."}}
{{"tool": "browse", "task": "..."}}
{{"tool": "get_datetime"}}
{{"tool": "weather", "city": "..."}}
{{"tool": "news", "topic": "..."}}  (topic optional — omit for top headlines)
{{"tool": "wikipedia", "query": "..."}}
{{"tool": "calculate", "expression": "2*(3+4)"}}
{{"tool": "currency", "amount": 100, "from": "USD", "to": "INR"}}
{{"tool": "translate", "text": "...", "to": "ta"}}  (to = language code, source is English)
{{"tool": "holidays", "country": "IN", "year": 2026}}  (both optional; next 5 upcoming)
{{"tool": "define", "word": "..."}}
{{"tool": "remember", "fact": "...", "kind": "fact|preference|instruction"}}
{{"tool": "create_goal", "title": "...", "plan": "..."}}
{{"tool": "update_goal", "id": "...", "status": "active|done|paused|cancelled", "note": "..."}}
{{"tool": "list_goals"}}
{{"tool": "create_reminder", "text": "...", "due": "2026-11-25"}}
{{"tool": "list_reminders"}}
{{"tool": "create_task", "instruction": "...", "run_at": "tomorrow 11:00 am"}}  (run_at: user's words, ISO, or "in 2 hours"; IST)
{{"tool": "list_tasks"}}
{{"tool": "cancel_task", "id": "..."}}
{{"tool": "suggest_media", "mood": "sad", "language": "both"}}  (language: both|ta|en — default both)
{{"tool": "save_artifact", "title": "...", "content": "...", "kind": "markdown|text|code"}}
{{"tool": "recall_memory", "query": "..."}}
{{"tool": "search_sessions", "query": "..."}}
{{"tool": "delegate", "task": "..."}}
{{"tool": "list_skills"}}
{{"tool": "use_skill", "name": "interview-prep"}}
{{"tool": "finish", "answer": "..."}}
"""


# ----------------------------------------------------------------------
# Structured tool schemas (v3, tier-annotated in v4)
#
# A machine-readable registry of every tool — name, description,
# JSON-Schema-style parameters, and risk tier — so other programs
# (and GET /api/tools) can discover what the agent can do, the same
# idea as MCP tool definitions. The loop itself still uses the JSON
# protocol in SYSTEM; this registry is the contract/documentation
# layer and must be kept in sync with _run_tool.
# ----------------------------------------------------------------------
def _schema(name: str, description: str,
            properties: dict[str, Any],
            required: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "tier": policy.tier_of(name),
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required or [],
        },
    }


_STR = {"type": "string"}
TOOL_SCHEMAS: list[dict[str, Any]] = [
    _schema("web_search", "Search the live web.", {"query": _STR}, ["query"]),
    _schema("fetch_page", "Fetch and read a web page.", {"url": _STR}, ["url"]),
    _schema("browse", "Drive a real browser (local only).",
            {"task": _STR}, ["task"]),
    _schema("get_datetime", "Current date/time (UTC + IST).", {}),
    _schema("weather", "Today's weather for a city.", {"city": _STR}, ["city"]),
    _schema("news", "Top headlines, optionally by topic.", {"topic": _STR}),
    _schema("wikipedia", "Wikipedia summary for a topic.",
            {"query": _STR}, ["query"]),
    _schema("calculate", "Safe arithmetic evaluation.",
            {"expression": _STR}, ["expression"]),
    _schema("currency", "Currency conversion (keyless rates).",
            {"amount": {"type": "number"}, "from": _STR, "to": _STR},
            ["amount", "from", "to"]),
    _schema("translate", "Translate English text to a language code.",
            {"text": _STR, "to": _STR}, ["text", "to"]),
    _schema("holidays", "Upcoming public holidays (Nager.Date; no India).",
            {"country": _STR, "year": {"type": "integer"}}),
    _schema("define", "Dictionary definition of a word.",
            {"word": _STR}, ["word"]),
    _schema("remember", "Store a fact, preference or standing "
            "instruction in memory.",
            {"fact": _STR, "kind": _STR}, ["fact"]),
    _schema("recall_memory", "Search memory.", {"query": _STR}),
    _schema("create_goal", "Create a long-running goal.",
            {"title": _STR, "plan": _STR}, ["title"]),
    _schema("update_goal", "Update a goal's status/note.",
            {"id": _STR, "status": _STR, "note": _STR}, ["id"]),
    _schema("list_goals", "List active goals.", {}),
    _schema("create_reminder", "Create a reminder with an ISO due date.",
            {"text": _STR, "due": _STR}, ["text", "due"]),
    _schema("list_reminders", "List open reminders.", {}),
    _schema("create_task",
            "Schedule an instruction to run at a future time (IST).",
            {"instruction": _STR, "run_at": _STR},
            ["instruction", "run_at"]),
    _schema("list_tasks", "List scheduled tasks.", {}),
    _schema("cancel_task", "Cancel a pending scheduled task.",
            {"id": _STR}, ["id"]),
    _schema("suggest_media",
            "Songs/shows/movies for a mood (friend mode).",
            {"mood": _STR, "language": _STR}, ["mood"]),
    _schema("save_artifact", "Save a document/code artifact.",
            {"title": _STR, "content": _STR, "kind": _STR},
            ["title", "content"]),
    _schema("search_sessions",
            "Search past agent runs/sessions for earlier work.",
            {"query": _STR}, ["query"]),
    _schema("delegate",
            "Hand a self-contained subtask to an isolated sub-agent "
            "(4-step budget, same policy ceiling, cannot delegate "
            "further).",
            {"task": _STR}, ["task"]),
    _schema("list_skills", "List installed skills.", {}),
    _schema("use_skill", "Activate a skill's instructions for this run.",
            {"name": _STR}, ["name"]),
    _schema("finish", "End the run with the final answer.",
            {"answer": _STR}, ["answer"]),
]


def tool_schemas() -> list[dict[str, Any]]:
    return TOOL_SCHEMAS


def _parse_action(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    text = text.strip()
    # strip optional ```json fences
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # find first JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
        if isinstance(obj, dict) and "tool" in obj:
            return obj
    except json.JSONDecodeError:
        pass
    return None


def _run_tool(action: dict[str, Any], on_event: EventCallback,
              ctx: dict[str, Any] | None = None) -> str:
    tool = (action.get("tool") or "").strip().lower()
    ctx = ctx or {}

    if tool == "web_search":
        q = (action.get("query") or "").strip()
        on_event({"type": "tool", "tool": "web_search", "query": q})
        results = web_search(q, max_results=5)
        if not results:
            return "No search results."
        lines = [f"- {r['title']}: {r['url']}" for r in results]
        return "Search results:\n" + "\n".join(lines)

    if tool == "fetch_page":
        url = (action.get("url") or "").strip()
        on_event({"type": "tool", "tool": "fetch_page", "url": url})
        text = fetch_text(url)
        if len(text) < MIN_TEXT:
            return f"Could not extract enough text from {url}"
        snippet = " ".join(text.split())[:2500]
        return f"Page content from {url}:\n{snippet}"

    if tool == "browse":
        task = (action.get("task") or "").strip()
        on_event({"type": "tool", "tool": "browse", "task": task})
        try:
            import browser_agent
            if not browser_agent.available():
                return browser_agent._missing_message()
            return browser_agent.run_browser_task(task, max_steps=25, headless=True)
        except Exception as exc:
            return f"Browse failed: {exc}"

    if tool == "remember":
        fact = (action.get("fact") or "").strip()
        kind = (action.get("kind") or "fact").strip()
        on_event({"type": "tool", "tool": "remember", "fact": fact})
        return memory.remember(
            fact, kind=kind if kind in ("fact", "preference", "instruction")
            else "fact")

    if tool == "recall_memory":
        q = (action.get("query") or "").strip()
        on_event({"type": "tool", "tool": "recall_memory", "query": q})
        lines = memory.recall(q)
        return "Memory:\n" + ("\n".join(lines) if lines else "(empty)")

    if tool == "create_goal":
        title = (action.get("title") or "").strip()
        plan = (action.get("plan") or "").strip()
        on_event({"type": "tool", "tool": "create_goal", "title": title})
        g = goals.create(title, plan)
        return f"Created goal [{g['id']}]: {g['title']}"

    if tool == "update_goal":
        gid = (action.get("id") or "").strip()
        status = action.get("status")
        note = action.get("note")
        on_event({"type": "tool", "tool": "update_goal", "id": gid})
        try:
            g = goals.update(gid, status=status, note=note)
            return f"Updated goal [{g['id']}] status={g['status']}"
        except KeyError as exc:
            return str(exc)

    if tool == "list_goals":
        on_event({"type": "tool", "tool": "list_goals"})
        active = goals.list_goals("active")
        if not active:
            return "No active goals."
        return "Active goals:\n" + "\n".join(
            f"- [{g['id']}] {g['title']}" for g in active
        )

    if tool == "get_datetime":
        on_event({"type": "tool", "tool": "get_datetime"})
        return tools_extra.get_datetime()

    if tool == "weather":
        city = (action.get("city") or "").strip()
        on_event({"type": "tool", "tool": "weather", "query": city})
        try:
            return tools_extra.weather(city)
        except Exception as exc:
            return f"Weather lookup failed: {exc}"

    if tool == "news":
        topic = (action.get("topic") or "").strip()
        on_event({"type": "tool", "tool": "news", "query": topic})
        try:
            return tools_extra.news(topic)
        except Exception as exc:
            return f"News lookup failed: {exc}"

    if tool == "wikipedia":
        q = (action.get("query") or "").strip()
        on_event({"type": "tool", "tool": "wikipedia", "query": q})
        try:
            return tools_extra.wikipedia(q)
        except Exception as exc:
            return f"Wikipedia lookup failed: {exc}"

    if tool == "calculate":
        expr = (action.get("expression") or "").strip()
        on_event({"type": "tool", "tool": "calculate", "query": expr})
        return tools_extra.calculate(expr)

    if tool == "currency":
        on_event({"type": "tool", "tool": "currency",
                  "query": f"{action.get('amount')} "
                           f"{action.get('from')}->{action.get('to')}"})
        try:
            return tools_extra.currency(action.get("amount"),
                                        action.get("from") or "",
                                        action.get("to") or "")
        except Exception as exc:
            return f"Currency conversion failed: {exc}"

    if tool == "translate":
        text = (action.get("text") or "").strip()
        to_lang = (action.get("to") or "").strip()
        on_event({"type": "tool", "tool": "translate", "query": text[:60]})
        try:
            return tools_extra.translate_text(text, to_lang)
        except Exception as exc:
            return f"Translate failed: {exc}"

    if tool == "holidays":
        country = (action.get("country") or "IN").strip()
        year = action.get("year")
        on_event({"type": "tool", "tool": "holidays", "query": country})
        try:
            return tools_extra.holidays_list(
                country, int(year) if year else None)
        except Exception as exc:
            return f"Holidays lookup failed: {exc}"

    if tool == "define":
        word = (action.get("word") or "").strip()
        on_event({"type": "tool", "tool": "define", "query": word})
        try:
            return tools_extra.define_word(word)
        except Exception as exc:
            return f"Define failed: {exc}"

    if tool == "create_reminder":
        text = (action.get("text") or "").strip()
        due = (action.get("due") or "").strip()
        on_event({"type": "tool", "tool": "create_reminder", "fact": text})
        try:
            r = reminders.add(text, due)
            return f"Created reminder [{r['id']}]: {r['text']} (due {r['due']})"
        except ValueError as exc:
            return f"Could not create reminder: {exc}"

    if tool == "list_reminders":
        on_event({"type": "tool", "tool": "list_reminders"})
        items = reminders.list_reminders()
        if not items:
            return "No open reminders."
        return "Open reminders:\n" + "\n".join(
            f"- [{r['id']}] {r['text']} (due {r['due']})" for r in items
        )

    if tool == "create_task":
        instruction = (action.get("instruction") or "").strip()
        run_at = (action.get("run_at") or "").strip()
        on_event({"type": "tool", "tool": "create_task", "fact": instruction})
        try:
            t = tasks_mod.create(instruction, run_at)
            return (f"Scheduled task [{t['id']}]: '{t['instruction']}' "
                    f"runs at {t['run_at']} (Asia/Kolkata).")
        except ValueError as exc:
            return f"Could not schedule task: {exc}"

    if tool == "list_tasks":
        on_event({"type": "tool", "tool": "list_tasks"})
        items = tasks_mod.list_tasks()
        if not items:
            return "No scheduled tasks."
        return "Scheduled tasks:\n" + "\n".join(
            tasks_mod.format_task(t) for t in items
        )

    if tool == "cancel_task":
        tid = (action.get("id") or "").strip()
        on_event({"type": "tool", "tool": "cancel_task", "id": tid})
        try:
            t = tasks_mod.cancel(tid)
            return f"Cancelled task [{t['id']}]: {t['instruction']}"
        except (KeyError, ValueError) as exc:
            return str(exc)

    if tool == "suggest_media":
        mood = (action.get("mood") or "").strip()
        language = (action.get("language") or "both").strip()
        on_event({"type": "tool", "tool": "suggest_media", "query": mood})
        try:
            return media.suggest_media(mood, language)
        except Exception as exc:
            return f"Media suggestions failed: {exc}"

    if tool == "search_sessions":
        q = (action.get("query") or "").strip()
        on_event({"type": "tool", "tool": "search_sessions", "query": q})
        return sessions.format_results(sessions.search(q))

    if tool == "delegate":
        subtask = (action.get("task") or "").strip()
        on_event({"type": "tool", "tool": "delegate", "fact": subtask[:120]})
        if not subtask:
            return "Delegate needs a 'task' describing the subtask."
        if int(ctx.get("depth") or 0) >= 1:
            return ("Delegation depth limit reached — a sub-agent cannot "
                    "delegate further. Do this part yourself with your "
                    "other tools.")
        sub = run_agent(
            subtask,
            max_steps=4,
            on_event=on_event,
            source="delegate",
            ceiling=ctx.get("ceiling"),
            _depth=int(ctx.get("depth") or 0) + 1,
        )
        return f"Sub-agent result:\n{sub.get('answer') or '(no answer)'}"

    if tool == "list_skills":
        on_event({"type": "tool", "tool": "list_skills"})
        found = skills.list_skills()
        if not found:
            return "No skills installed."
        return "Available skills:\n" + "\n".join(
            f"- {s['name']}: {s['description']}" for s in found
        )

    if tool == "use_skill":
        name = (action.get("name") or "").strip()
        on_event({"type": "tool", "tool": "use_skill", "fact": name})
        rec = skills.load_skill(name)
        if not rec:
            available = ", ".join(s["name"] for s in skills.list_skills())
            return (f"Skill not found: {name}. Available skills: "
                    f"{available or '(none)'}")
        # run_agent picks the instructions up from the action and adds
        # them to the run context; this observation is the confirmation.
        return (f"Skill '{rec['name']}' is now active — its instructions "
                "have been added to your context for the rest of this run. "
                "Follow them from the next step.")

    if tool == "save_artifact":
        title = (action.get("title") or "Untitled").strip()
        content = action.get("content") or ""
        kind = (action.get("kind") or "markdown").strip()
        on_event({"type": "tool", "tool": "save_artifact", "title": title})
        meta = artifacts.save(title, content, kind=kind)
        return f"Saved artifact [{meta['id']}] → {meta['path']}"

    if tool == "finish":
        return action.get("answer") or "(done)"

    return f"Unknown tool: {tool}. Use one of the listed tools."


def run_agent(
    task: str,
    *,
    max_steps: int = MAX_STEPS_DEFAULT,
    on_event: Optional[EventCallback] = None,
    source: str = "interactive",
    ceiling: str | None = None,
    _depth: int = 0,
    resume_digest: str = "",
) -> dict[str, Any]:
    """Run the multi-step agent.

    Returns {answer, steps, tools_used, run_id, trace}.

    v4: `source` ("interactive" | "scheduled" | "webhook" |
    "delegate" | …) sets the policy ceiling for tool tiers;
    `resume_digest` seeds the run with a previous run's compressed
    transcript so an interrupted run can continue. Every run is
    recorded in runs.py (trace) and sessions.py (searchable log).
    """
    emit = on_event or (lambda e: None)
    task = (task or "").strip()
    if not task:
        return {"answer": "Please provide a task.", "steps": [],
                "tools_used": [], "run_id": None, "trace": []}

    emit({"type": "agent_start", "task": task})
    cap = ceiling or policy.ceiling_for(source)
    run_id = runs.start(task, source)
    trace: list[dict[str, Any]] = []
    steps: list[str] = []
    tools_used: list[str] = []

    def _record(entry: dict[str, Any]) -> None:
        trace.append(entry)
        try:
            runs.add_trace(run_id, entry)
        except Exception:
            pass  # tracing is best-effort; never break a run over it

    def _done(status: str, answer: str, digest: str = "") -> dict[str, Any]:
        try:
            runs.finish(run_id, status, answer, digest=digest)
        except Exception:
            pass
        try:
            sessions.log(source, task, answer, tools_used)
        except Exception:
            pass
        return {"answer": answer, "steps": steps,
                "tools_used": tools_used, "run_id": run_id,
                "trace": trace}

    system = SYSTEM.format(
        memory=memory.summary_for_prompt(),
        goals=goals.summary_for_prompt(),
        instructions=memory.instructions_block(),
    )
    transcript: list[str] = [f"User task: {task}"]
    if resume_digest:
        transcript.append(
            "Context from a previous unfinished run of this task "
            "(continue from it, do not repeat finished steps):\n"
            + resume_digest)
    skill_context: list[str] = []  # instructions from use_skill, kept all run
    ctx = {"source": source, "ceiling": cap, "depth": _depth}

    for i in range(1, max_steps + 1):
        view = context_mod.maybe_compress(transcript)
        user_msg = (
            "Conversation so far:\n"
            + "\n".join(view)
            + (("\n\n" + "\n\n".join(skill_context)) if skill_context else "")
            + f"\n\nStep {i}/{max_steps}. Choose the next tool JSON (or finish)."
        )
        emit({"type": "agent_thinking", "step": i, "max_steps": max_steps})
        try:
            raw = generate(system, user_msg, model=config.AGENT_MODEL)
        except Exception as exc:
            answer = (
                f"The model call failed at step {i} ({exc}). Progress so "
                "far is in the steps above — free-tier rate limits are the "
                "usual cause; wait a minute and re-run."
            )
            emit({"type": "error", "message": answer})
            return _done("error", answer)
        if raw is None:
            answer = (
                "No LLM key configured. Set GROQ_API_KEY or HF_TOKEN to run "
                "the tool-using agent. You can still use ask/reply/browse."
            )
            emit({"type": "error", "message": answer})
            return _done("error", answer)

        if not raw.strip():
            # empty model response (some free models return blank content
            # on a reasoning step) — nudge and continue instead of
            # returning an empty answer
            steps.append(f"step {i}: empty model response, continuing")
            transcript.append(
                "Observation: (your last reply was empty — reply with a "
                "tool JSON or the finish tool)"
            )
            emit({"type": "tool_result", "tool": "retry",
                  "preview": "empty model response"})
            _record({"step": i, "tool": "retry",
                     "observation": "empty model response", "ms": 0})
            continue

        action = _parse_action(raw)
        if not action:
            # model returned prose — treat as final answer
            steps.append(f"step {i}: model returned final text")
            emit({"type": "agent_done", "answer": raw.strip()})
            return _done("done", raw.strip())

        tool = (action.get("tool") or "").lower()
        tools_used.append(tool)
        steps.append(f"step {i}: {tool}")
        args_preview = json.dumps(
            {k: v for k, v in action.items() if k != "tool"},
            ensure_ascii=False)[:200]

        if tool == "finish":
            answer = (action.get("answer") or "").strip() or "Task complete."
            _record({"step": i, "tool": "finish", "args": args_preview,
                     "observation": answer[:300], "ms": 0})
            emit({"type": "agent_done", "answer": answer})
            return _done("done", answer)

        # Policy gate (v4): fail closed on tools above this run's tier
        # ceiling — e.g. unattended runs may not drive the browser.
        allowed, reason = policy.check(tool, source, ceiling=cap)
        started = time.monotonic()
        if not allowed:
            observation = reason
            emit({"type": "policy_block", "tool": tool, "reason": reason})
            _record({"step": i, "tool": tool, "args": args_preview,
                     "observation": observation[:300], "ms": 0,
                     "blocked": True})
        else:
            try:
                observation = _run_tool(action, emit, ctx)
            except Exception as exc:
                # one failing tool (e.g. storage error) must not kill the run
                observation = f"Tool {tool} failed: {exc}"
            _record({"step": i, "tool": tool, "args": args_preview,
                     "observation": observation[:300],
                     "ms": int((time.monotonic() - started) * 1000)})
        if tool == "use_skill":
            rec = skills.load_skill((action.get("name") or "").strip())
            if rec:
                skill_context.append(
                    f"--- Active skill: {rec['name']} ---\n"
                    f"{rec['instructions']}"
                )
                emit({"type": "skill_active", "name": rec["name"]})
        transcript.append(f"Assistant tool call: {json.dumps(action, ensure_ascii=False)}")
        transcript.append(f"Observation: {observation[:3000]}")
        emit({"type": "tool_result", "tool": tool, "preview": observation[:400]})

    # max steps reached — checkpoint a digest so the run can resume
    answer = (
        "Reached the step limit without a finish call. Partial progress is in "
        "the steps above. Re-run with a narrower task or higher max_steps."
    )
    digest = "\n".join(context_mod.maybe_compress(transcript))
    emit({"type": "agent_done", "answer": answer})
    return _done("step_limit", answer, digest=digest)


def resume_run(run_id: str, follow_up: str = "",
               max_steps: int = MAX_STEPS_DEFAULT,
               on_event: Optional[EventCallback] = None) -> dict[str, Any]:
    """Continue an unfinished run (step_limit / error) with its stored
    context digest. Returns the new run's result, with resumed_from
    set on the new run record."""
    run = runs.get(run_id)
    if run is None:
        raise KeyError(f"Run not found: {run_id}")
    task = run.get("task") or ""
    extra = (follow_up or "").strip()
    if extra:
        task = f"{task}\n\nFollow-up instruction: {extra}"
    result = run_agent(task, max_steps=max_steps, on_event=on_event,
                       source="interactive",
                       resume_digest=run.get("digest") or "")
    if result.get("run_id"):
        try:
            runs.mark_resumed_from(result["run_id"], run_id)
        except Exception:
            pass
    result["resumed_from"] = run_id
    return result
