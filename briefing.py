"""Morning briefing assembly (v2).

build_briefing() returns a JSON-ready dict:
  date, city, weather, goals (active), reminders_due (today/overdue),
  memory_facts (latest 5), artifacts (top 3).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import artifacts
import goals
import memory
import reminders
import tools_extra

DEFAULT_CITY = "Chennai"


def _user_city() -> str:
    try:
        prefs = (memory.export() or {}).get("preferences") or {}
        city = (prefs.get("city") or "").strip()
        return city or DEFAULT_CITY
    except Exception:
        return DEFAULT_CITY


def build_briefing() -> dict[str, Any]:
    now_ist = datetime.now(timezone.utc).astimezone(tools_extra.IST)
    city = _user_city()

    data = memory.export() or {}
    facts = [str(f) for f in (data.get("facts") or [])][-5:]

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
    }
