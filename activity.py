"""Agent-mode activity log (wave 2).

Every agent-mode run appends {timestamp, task, tools_used,
answer_preview} to config.DATA_DIR/activity.json — newest first,
capped at 100.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import config

ACTIVITY_PATH = config.DATA_DIR / "activity.json"
MAX_ENTRIES = 100


def _load() -> list[dict[str, Any]]:
    if not ACTIVITY_PATH.exists():
        return []
    try:
        data = json.loads(ACTIVITY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(items: list[dict[str, Any]]) -> None:
    ACTIVITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACTIVITY_PATH.write_text(
        json.dumps(items, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def add_entry(task: str, tools_used: list[str] | None,
              answer: str) -> dict[str, Any]:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task": task,
        "tools_used": tools_used or [],
        "answer_preview": (answer or "")[:280],
    }
    items = _load()
    items.insert(0, entry)
    _save(items[:MAX_ENTRIES])
    return entry


def list_activity(limit: int = MAX_ENTRIES) -> list[dict[str, Any]]:
    return _load()[:limit]
