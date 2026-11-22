"""Morning briefing assembly (v2; overnight results added in v5).

build_briefing() returns a JSON-ready dict:
  date, city, weather, goals (active), reminders_due (today/overdue),
  memory_facts (latest 5), artifacts (top 3), overnight_results.

"Overnight results" (v5): scheduled tasks that finished since the
previous briefing was built — the delivery channel for run_tasks.py
results. The watermark lives in DATA_DIR/briefing_state.json and
advances on every build_briefing() call, so each result is surfaced
exactly once; the first ever briefing looks back 24 hours.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import artifacts
import config
import goals
import memory
import reminders
import tasks as tasks_mod
import tools_extra

DEFAULT_CITY = "Chennai"
STATE_PATH = config.DATA_DIR / "briefing_state.json"


def _user_city() -> str:
    try:
        prefs = (memory.export() or {}).get("preferences") or {}
        city = (prefs.get("city") or "").strip()
        return city or DEFAULT_CITY
    except Exception:
        return DEFAULT_CITY


def _read_watermark(now_ist: datetime) -> datetime:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        seen = datetime.fromisoformat(data["last_seen"])
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=tools_extra.IST)
        return seen
    except Exception:
        return now_ist - timedelta(hours=24)


def _write_watermark(now_ist: datetime) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps({"last_seen": now_ist.isoformat()}, indent=2) + "\n",
            encoding="utf-8")
    except OSError:
        pass  # read-only host: results just re-appear next time


def build_briefing() -> dict[str, Any]:
    now_ist = datetime.now(timezone.utc).astimezone(tools_extra.IST)
    city = _user_city()

    data = memory.export() or {}
    facts = [str(f) for f in (data.get("facts") or [])][-5:]

    # Overnight results: finished tasks since the previous briefing.
    overnight: list[dict[str, Any]] = []
    try:
        since = _read_watermark(now_ist)
        for t in tasks_mod.completed_since(since.isoformat()):
            overnight.append({
                "id": t.get("id"),
                "instruction": t.get("instruction") or "",
                "status": t.get("status"),
                "executed_at": t.get("executed_at"),
                "result": (t.get("result") or "")[:500],
            })
        _write_watermark(now_ist)
    except Exception:
        overnight = []

    return {
        "date": now_ist.strftime("%A, %d %B %Y"),
        "date_iso": now_ist.date().isoformat(),
        "city": city,
        "weather": tools_extra.weather_data(city),
        "goals": [
            {"id": g["id"], "title": g["title"], "plan": g.get("plan") or ""}
            for g in goals.list_goals("active")
        ],
        "reminders_due": reminders.due_today_or_overdue(now_ist.date()),
        "memory_facts": facts,
        "artifacts": [
            {"id": a["id"], "title": a["title"],
             "created_at": a.get("created_at"), "chars": a.get("chars")}
            for a in artifacts.list_artifacts(3)
        ],
        "overnight_results": overnight,
    }
