"""Memory nudges (v5) — Hermes' auto-memory nudge, made dismissible.

Hermes periodically nudges its agent to persist what it learned.
Here the nudge is aimed at the *user*: when recent session tasks
contain a durable first-person statement ("I prefer short answers",
"I live in Chennai", "always check the 8:40 train"), the UI offers a
one-click suggestion card — Save (as fact / preference / instruction)
or Dismiss. Nothing is ever saved without the click.

Detection is deliberately simple and deterministic (regex over the
session log's task texts, no LLM call): it only fires on explicit
first-person phrasing, and a suggestion disappears once the text is
already in memory or the user dismisses/accepts it. State lives in
DATA_DIR/nudges.json: {items: {id: {...}}, } — items carry a status
of pending | saved | dismissed.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

import config
import memory
import sessions

NUDGES_PATH = config.DATA_DIR / "nudges.json"
MAX_ITEMS = 50

# (pattern, suggested kind) — first match wins. "always/never" rules
# are standing instructions; likes/preferences are preferences; the
# rest are facts.
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(always|never|from now on)\b", re.I), "instruction"),
    (re.compile(r"\bi (prefer|like|love|want|need)\b", re.I), "preference"),
    (re.compile(r"\bmy name is\b", re.I), "fact"),
    (re.compile(r"\bi (live|am|work|study)\b", re.I), "fact"),
    (re.compile(r"\bremember (that|to)\b", re.I), "fact"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    if not NUDGES_PATH.exists():
        return {"items": {}}
    try:
        data = json.loads(NUDGES_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"items": {}}


def _save(data: dict[str, Any]) -> None:
    NUDGES_PATH.parent.mkdir(parents=True, exist_ok=True)
    items = data.get("items") or {}
    if len(items) > MAX_ITEMS:  # keep the newest pending + recent decided
        ordered = sorted(items.values(),
                         key=lambda i: i.get("created_at") or "")
        keep = {i["id"]: i for i in ordered[-MAX_ITEMS:]}
        data["items"] = keep
    NUDGES_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _suggestion_id(text: str) -> str:
    return hashlib.sha1(text.lower().encode("utf-8")).hexdigest()[:10]


def _already_in_memory(text: str) -> bool:
    low = text.lower()
    for line in memory.recall("", limit=200):
        body = line.split(": ", 1)[-1].lower()
        if body and (body in low or low in body):
            return True
    return False


def _detect(task_text: str) -> tuple[str, str] | None:
    """(suggested_text, kind) if the task holds a durable statement."""
    text = " ".join((task_text or "").split())
    if len(text) < 8 or len(text) > 400:
        return None
    for pattern, kind in _PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        # suggest the clause from the matched phrase to the sentence end
        snippet = text[m.start():]
        snippet = snippet.split(". ")[0].strip().rstrip(".")
        if len(snippet) < 8:
            snippet = text
        return snippet[:200], kind
    return None


def refresh() -> list[dict[str, Any]]:
    """Scan recent sessions for new suggestions; return pending ones."""
    data = _load()
    items: dict[str, Any] = data.setdefault("items", {})
    for entry in sessions.list_recent(50):
        found = _detect(entry.get("task") or "")
        if not found:
            continue
        text, kind = found
        sid = _suggestion_id(text)
        if sid in items:
            continue
        if _already_in_memory(text):
            continue
        items[sid] = {
            "id": sid,
            "text": text,
            "kind": kind,
            "reason": (f"Spotted in a recent task "
                       f"({(entry.get('source') or 'run')}): "
                       f"“{(entry.get('task') or '')[:80]}”"),
            "status": "pending",
            "created_at": _now(),
        }
    data["items"] = items
    _save(data)
    return [i for i in items.values() if i.get("status") == "pending"]


def list_pending() -> list[dict[str, Any]]:
    return refresh()


def _update(sid: str, status: str) -> dict[str, Any]:
    data = _load()
    item = (data.get("items") or {}).get(sid)
    if item is None:
        raise KeyError(f"Nudge not found: {sid}")
    if item.get("status") != "pending":
        raise ValueError(f"Nudge already {item.get('status')}.")
    item["status"] = status
    item["decided_at"] = _now()
    _save(data)
    return item


def dismiss(sid: str) -> dict[str, Any]:
    return _update(sid, "dismissed")


def accept(sid: str) -> dict[str, Any]:
    """Save the suggested text into memory with its suggested kind."""
    data = _load()
    item = (data.get("items") or {}).get(sid)
    if item is None:
        raise KeyError(f"Nudge not found: {sid}")
    if item.get("status") != "pending":
        raise ValueError(f"Nudge already {item.get('status')}.")
    result = memory.remember(item["text"], kind=item.get("kind") or "fact")
    updated = _update(sid, "saved")
    updated["memory_result"] = result
    return updated
