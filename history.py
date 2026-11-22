"""Web chat history (v2).

Each web query run is recorded to config.DATA_DIR/history.json:
{mode, query, answer, sources, timestamp} — newest first, capped at 100.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import config

HISTORY_PATH = config.DATA_DIR / "history.json"
MAX_ENTRIES = 100


def _load() -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(items: list[dict[str, Any]]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(
        json.dumps(items, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def add_entry(mode: str, query: str, answer: str,
              sources: list[dict] | None = None) -> dict[str, Any]:
    entry = {
        "mode": mode,
        "query": query,
        "answer": answer or "",
        "sources": sources or [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    items = _load()
    items.insert(0, entry)
    _save(items[:MAX_ENTRIES])
    return entry


def list_history(limit: int = MAX_ENTRIES) -> list[dict[str, Any]]:
    return _load()[:limit]
