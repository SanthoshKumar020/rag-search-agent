"""Cross-session log + search (v4).

Pattern adapted from Hermes Agent's session search: every agent run,
whatever started it (web, CLI, scheduled task, webhook, delegation),
appends one entry here — {id, time, source, task, summary, tools} —
so a later run can answer "what did we do about X last time?" via the
`search_sessions` tool or GET /api/sessions.

Hermes uses SQLite FTS5; our store is a small capped JSON list, so a
plain keyword match is the right-sized equivalent.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import config

SESSIONS_PATH = config.DATA_DIR / "sessions.json"
MAX_SESSIONS = 200


def _load() -> list[dict[str, Any]]:
    if not SESSIONS_PATH.exists():
        return []
    try:
        data = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(items: list[dict[str, Any]]) -> None:
    SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS_PATH.write_text(
        json.dumps(items, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def log(source: str, task: str, answer: str,
        tools_used: list[str] | None = None) -> dict[str, Any]:
    entry = {
        "id": str(uuid.uuid4())[:8],
        "time": datetime.now(timezone.utc).isoformat(),
        "source": source or "interactive",
        "task": (task or "")[:300],
        "summary": (answer or "")[:600],
        "tools": tools_used or [],
    }
    items = _load()
    items.insert(0, entry)
    _save(items[:MAX_SESSIONS])
    return entry


def list_recent(limit: int = 20) -> list[dict[str, Any]]:
    return _load()[:limit]


def search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Keyword match over task + summary + tools (all tokens must hit
    somewhere in the entry, like a simple AND search)."""
    q = (query or "").strip().lower()
    if not q:
        return list_recent(limit)
    tokens = q.split()
    out = []
    for entry in _load():
        hay = " ".join([
            entry.get("task") or "",
            entry.get("summary") or "",
            " ".join(entry.get("tools") or []),
            entry.get("source") or "",
        ]).lower()
        if all(tok in hay for tok in tokens):
            out.append(entry)
            if len(out) >= limit:
                break
    return out


def format_results(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "No matching past sessions."
    lines = ["Past sessions:"]
    for e in entries:
        when = (e.get("time") or "")[:16].replace("T", " ")
        lines.append(
            f"- [{e.get('id')}] {when} ({e.get('source')}) "
            f"{e.get('task')}\n  → {(e.get('summary') or '')[:220]}")
    return "\n".join(lines)
