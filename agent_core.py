"""Tool-using personal agent loop (Muse-inspired).

Combines web search, page fetch, optional browser control, memory,
goals, and artifacts into a multi-step agent that works toward a
user goal instead of answering in a single shot.

This is intentionally simpler than Meta Muse: local storage, free-tier
LLMs, no cloud VM, no payment APIs, no third-party OAuth connectors.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

import artifacts
import config
import goals
import media
import memory
import reminders
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
- Scheduling: when the user asks for something at a FUTURE time ("tomorrow 11:00 am book/check X", "in 2 hours remind me via a task"), call create_task with the instruction and the time in the user's own words (e.g. "tomorrow 11:00 am"). Then finish by confirming the stored time (IST). A scheduled task runs later through this same loop with these same safety rules — it still cannot pay or send real messages.
- Friend mode: when the user expresses a feeling or mood ("I feel sad", "I'm stressed", "feeling lonely"), FIRST respond like a warm friend — acknowledge the feeling briefly and kindly in the finish answer — and use suggest_media to offer songs/shows/movies that fit the mood. Never sound clinical or robotic with feelings.

User memory:
{memory}

Active goals:
{goals}

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
{{"tool": "remember", "fact": "...", "kind": "fact|preference"}}
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
{{"tool": "list_skills"}}
{{"tool": "use_skill", "name": "interview-prep"}}
{{"tool": "finish", "answer": "..."}}
"""


# ----------------------------------------------------------------------
# Structured tool schemas (v3)
#
# A machine-readable registry of every tool — name, description, and
# JSON-Schema-style parameters — so other programs (and GET /api/tools)
# can discover what the agent can do, the same idea as MCP tool
# definitions. The loop itself still uses the JSON protocol in SYSTEM;
# this registry is the contract/documentation layer and must be kept
# in sync with _run_tool.
# ----------------------------------------------------------------------
def _schema(name: str, description: str,
            properties: dict[str, Any],
            required: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
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
    _schema("remember", "Store a fact or preference in memory.",
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


def _run_tool(action: dict[str, Any], on_event: EventCallback) -> str:
    tool = (action.get("tool") or "").strip().lower()

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
        return memory.remember(fact, kind=kind if kind in ("fact", "preference") else "fact")

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
) -> dict[str, Any]:
    """Run the multi-step agent. Returns {answer, steps, tools_used}."""
    emit = on_event or (lambda e: None)
    task = (task or "").strip()
    if not task:
        return {"answer": "Please provide a task.", "steps": [], "tools_used": []}

    emit({"type": "agent_start", "task": task})

    system = SYSTEM.format(
        memory=memory.summary_for_prompt(),
        goals=goals.summary_for_prompt(),
    )
    transcript: list[str] = [f"User task: {task}"]
    steps: list[str] = []
    tools_used: list[str] = []
    skill_context: list[str] = []  # instructions from use_skill, kept all run

    for i in range(1, max_steps + 1):
        user_msg = (
            "Conversation so far:\n"
            + "\n".join(transcript[-12:])
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
            return {"answer": answer, "steps": steps, "tools_used": tools_used}
        if raw is None:
            answer = (
                "No LLM key configured. Set GROQ_API_KEY or HF_TOKEN to run "
                "the tool-using agent. You can still use ask/reply/browse."
            )
            emit({"type": "error", "message": answer})
            return {"answer": answer, "steps": steps, "tools_used": tools_used}

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
            continue

        action = _parse_action(raw)
        if not action:
            # model returned prose — treat as final answer
            steps.append(f"step {i}: model returned final text")
            emit({"type": "agent_done", "answer": raw.strip()})
            return {"answer": raw.strip(), "steps": steps, "tools_used": tools_used}

        tool = (action.get("tool") or "").lower()
        tools_used.append(tool)
        steps.append(f"step {i}: {tool}")

        if tool == "finish":
            answer = (action.get("answer") or "").strip() or "Task complete."
            emit({"type": "agent_done", "answer": answer})
            return {"answer": answer, "steps": steps, "tools_used": tools_used}

        try:
            observation = _run_tool(action, emit)
        except Exception as exc:
            # one failing tool (e.g. storage error) must not kill the run
            observation = f"Tool {tool} failed: {exc}"
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

    # max steps reached
    answer = (
        "Reached the step limit without a finish call. Partial progress is in "
        "the steps above. Re-run with a narrower task or higher max_steps."
    )
    emit({"type": "agent_done", "answer": answer})
    return {"answer": answer, "steps": steps, "tools_used": tools_used}
