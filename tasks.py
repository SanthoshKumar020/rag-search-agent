"""Scheduled tasks store (v3).

The user can say "tomorrow 11:00 am check train availability to Madurai"
and the agent stores a task: {id, instruction, run_at, status,
created_at, executed_at, result}.

  - run_at is an ISO datetime, always timezone-aware (Asia/Kolkata).
  - status: pending | running | done | failed | cancelled.
  - Tasks live in config.DATA_DIR/tasks.json (same pattern as
    reminders.py / activity.py).

Execution: `run_tasks.py` polls this store locally and runs due tasks
through agent_core.run_agent. Hosted servers (Vercel) cannot wake
themselves, so there tasks are stored/listed and can be executed on
demand via POST /api/tasks/run — see README "Scheduled tasks".

Safety: a scheduled task runs through the normal agent loop, whose
system prompt refuses payments and sending real messages. A task that
asks for those ends with an explanation, never an action.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any

import config
import tools_extra

IST = tools_extra.IST
TASKS_PATH = config.DATA_DIR / "tasks.json"
MAX_TASKS = 200

_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thur": 3,
    "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def _now_ist() -> datetime:
    return datetime.now(timezone.utc).astimezone(IST)


def _load() -> list[dict[str, Any]]:
    if not TASKS_PATH.exists():
        return []
    try:
        data = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(items: list[dict[str, Any]]) -> None:
    TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TASKS_PATH.write_text(
        json.dumps(items, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ----------------------------------------------------------------------
# natural-language time parsing (IST)
# ----------------------------------------------------------------------
def _parse_clock(text: str) -> time | None:
    """Find '11:00 am' / '11 am' / '18:30' / '6pm' in text."""
    m = re.search(
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text, flags=re.IGNORECASE)
    if m:
        hour = int(m.group(1)) % 12
        if m.group(3).lower() == "pm":
            hour += 12
        return time(hour, int(m.group(2) or 0))
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    if m:
        return time(int(m.group(1)), int(m.group(2)))
    return None


def parse_due(value: str, now: datetime | None = None) -> datetime:
    """Parse a due-time string into an aware IST datetime.

    Accepts ISO datetimes/dates plus friendly forms:
    'tomorrow 11:00 am', 'today 6 pm', 'in 2 hours', 'in 30 minutes',
    'friday 9 am', '2026-11-25'. A bare date means 09:00 IST that day.
    Raises ValueError when nothing parseable is found.
    """
    raw = (value or "").strip()
    if not raw:
        raise ValueError("A time is required, e.g. 'tomorrow 11:00 am'.")
    now = now or _now_ist()
    low = raw.lower()

    # relative: "in 2 hours" / "in 30 minutes" / "in 3 days"
    m = re.search(r"\bin\s+(\d+)\s*(minute|min|hour|hr|day)s?\b", low)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = (timedelta(minutes=n) if unit.startswith("min")
                 else timedelta(hours=n) if unit.startswith(("hour", "hr"))
                 else timedelta(days=n))
        return now + delta

    # ISO datetime (a bare date falls through to the 09:00 default below)
    if "T" in raw or re.search(r"\d{1,2}:\d{2}", raw):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
            return dt.astimezone(IST)
        except ValueError:
            pass
    try:
        d = datetime.fromisoformat(raw[:10]).date()
        clock = _parse_clock(raw) or time(9, 0)
        return datetime.combine(d, clock, tzinfo=IST)
    except ValueError:
        pass

    clock = _parse_clock(raw) or time(9, 0)

    if "tomorrow" in low:
        return datetime.combine(now.date() + timedelta(days=1), clock,
                                tzinfo=IST)
    if "today" in low or "tonight" in low:
        if "tonight" in low and _parse_clock(raw) is None:
            clock = time(20, 0)
        candidate = datetime.combine(now.date(), clock, tzinfo=IST)
        return candidate

    # weekday name: next occurrence (today counts only if time is ahead)
    for name, weekday in _WEEKDAYS.items():
        if re.search(rf"\b{name}\b", low):
            days_ahead = (weekday - now.weekday()) % 7
            candidate = datetime.combine(
                now.date() + timedelta(days=days_ahead), clock, tzinfo=IST)
            if days_ahead == 0 and candidate <= now:
                candidate += timedelta(days=7)
            return candidate

    raise ValueError(
        f"Could not understand the time '{value}'. Try 'tomorrow 11:00 am', "
        "'in 2 hours', or an ISO datetime like 2026-11-23T11:00.")


# ----------------------------------------------------------------------
# CRUD
# ----------------------------------------------------------------------
def create(instruction: str, run_at: str) -> dict[str, Any]:
    instruction = (instruction or "").strip()
    if not instruction:
        raise ValueError("Task instruction is required.")
    due = parse_due(run_at)
    task = {
        "id": str(uuid.uuid4())[:8],
        "instruction": instruction,
        "run_at": due.isoformat(),
        "status": "pending",
        "created_at": _now_ist().isoformat(),
        "executed_at": None,
        "result": "",
    }
    items = _load()
    items.append(task)
    _save(items[-MAX_TASKS:])
    return task


def list_tasks(include_finished: bool = True) -> list[dict[str, Any]]:
    items = _load()
    if not include_finished:
        items = [t for t in items if t.get("status") == "pending"]
    return sorted(items, key=lambda t: t.get("run_at") or "")


def get(task_id: str) -> dict[str, Any] | None:
    for t in _load():
        if t.get("id") == task_id:
            return t
    return None


def _update(task_id: str, **fields: Any) -> dict[str, Any]:
    items = _load()
    for t in items:
        if t.get("id") == task_id:
            t.update(fields)
            _save(items)
            return t
    raise KeyError(f"Task not found: {task_id}")


def cancel(task_id: str) -> dict[str, Any]:
    task = get(task_id)
    if task is None:
        raise KeyError(f"Task not found: {task_id}")
    if task.get("status") in ("done", "failed"):
        raise ValueError(f"Task {task_id} already finished "
                         f"({task.get('status')}).")
    return _update(task_id, status="cancelled")


def mark_running(task_id: str) -> dict[str, Any]:
    return _update(task_id, status="running")


def mark_done(task_id: str, result: str) -> dict[str, Any]:
    return _update(task_id, status="done",
                   executed_at=_now_ist().isoformat(),
                   result=(result or "")[:1000])


def mark_failed(task_id: str, error: str) -> dict[str, Any]:
    return _update(task_id, status="failed",
                   executed_at=_now_ist().isoformat(),
                   result=(error or "")[:1000])


def due_tasks(now: datetime | None = None) -> list[dict[str, Any]]:
    """Pending tasks whose run_at has arrived."""
    now = now or _now_ist()
    out = []
    for t in list_tasks(include_finished=False):
        try:
            due = datetime.fromisoformat(t["run_at"])
        except (KeyError, ValueError):
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=IST)
        if due <= now:
            out.append(t)
    return out


def completed_since(iso_ts: str) -> list[dict[str, Any]]:
    """Finished tasks (done/failed) executed after `iso_ts`, newest
    first — the briefing's "overnight results" feed (v5)."""
    try:
        since = datetime.fromisoformat(iso_ts)
    except (TypeError, ValueError):
        return []
    if since.tzinfo is None:
        since = since.replace(tzinfo=IST)
    out = []
    for t in _load():
        if t.get("status") not in ("done", "failed"):
            continue
        try:
            executed = datetime.fromisoformat(t.get("executed_at") or "")
        except ValueError:
            continue
        if executed.tzinfo is None:
            executed = executed.replace(tzinfo=IST)
        if executed > since:
            out.append(t)
    return sorted(out, key=lambda t: t.get("executed_at") or "",
                  reverse=True)


def format_task(task: dict[str, Any]) -> str:
    return (f"[{task['id']}] {task['instruction']} — "
            f"runs {task['run_at']} ({task['status']})")
