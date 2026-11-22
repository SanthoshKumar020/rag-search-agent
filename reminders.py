"""Reminders store (v2).

Reminders live in config.DATA_DIR/reminders.json as
{id, text, due (ISO date or datetime), done, created_at}.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

import config

REMINDERS_PATH = config.DATA_DIR / "reminders.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> list[dict[str, Any]]:
    if not REMINDERS_PATH.exists():
        return []
    try:
        data = json.loads(REMINDERS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(items: list[dict[str, Any]]) -> None:
    REMINDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    REMINDERS_PATH.write_text(
        json.dumps(items, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def add(text: str, due: str) -> dict[str, Any]:
    text = (text or "").strip()
    due = (due or "").strip()
    if not text:
        raise ValueError("Reminder text is required")
    if not due:
        raise ValueError("Reminder due date is required (ISO, e.g. 2026-11-25)")
    reminder = {
        "id": str(uuid.uuid4())[:8],
        "text": text,
        "due": due,
        "done": False,
        "created_at": _now(),
    }
    items = _load()
    items.append(reminder)
    _save(items)
    return reminder


def list_reminders(include_done: bool = False) -> list[dict[str, Any]]:
    items = _load()
    if not include_done:
        items = [r for r in items if not r.get("done")]
    return items


def mark_done(reminder_id: str) -> dict[str, Any]:
    items = _load()
    for r in items:
        if r.get("id") == reminder_id:
            r["done"] = True
            _save(items)
            return r
    raise KeyError(f"Reminder not found: {reminder_id}")


def _due_date(reminder: dict[str, Any]) -> date | None:
    raw = (reminder.get("due") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None


def due_today_or_overdue(today: date | None = None) -> list[dict[str, Any]]:
    """Open reminders whose due date is today or in the past."""
    today = today or datetime.now(timezone.utc).date()
    out = []
    for r in list_reminders():
        d = _due_date(r)
        if d is not None and d <= today:
            out.append(r)
    return out
