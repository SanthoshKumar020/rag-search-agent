"""Persistent personal memory for the agent (Muse-inspired).

Stores short facts and preferences the agent learns about the user so
later sessions can reuse them. Local JSON only — no cloud sync.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

MEMORY_PATH = config.BASE_DIR / "data" / "memory.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    if not MEMORY_PATH.exists():
        return {"facts": [], "preferences": {}, "updated_at": None}
    try:
        return json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"facts": [], "preferences": {}, "updated_at": None}


def _save(data: dict[str, Any]) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    MEMORY_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def remember(fact: str, *, kind: str = "fact") -> str:
    """Store a short fact or preference. Returns confirmation text."""
    fact = (fact or "").strip()
    if not fact:
        return "Nothing to remember."
    data = _load()
    if kind == "preference":
        # key: value style if "key: value", else free text under _notes
        if ":" in fact:
            key, _, val = fact.partition(":")
            data.setdefault("preferences", {})[key.strip()] = val.strip()
        else:
            notes = data.setdefault("preferences", {}).setdefault("_notes", [])
            if fact not in notes:
                notes.append(fact)
    else:
        facts: list[str] = data.setdefault("facts", [])
        if fact not in facts:
            facts.append(fact)
        # keep memory bounded
        data["facts"] = facts[-100:]
    _save(data)
    return f"Remembered ({kind}): {fact}"


def recall(query: str = "", *, limit: int = 20) -> list[str]:
    """Return relevant memory lines. Empty query returns recent facts + prefs."""
    data = _load()
    lines: list[str] = []
    prefs = data.get("preferences") or {}
    for k, v in prefs.items():
        if k == "_notes":
            for n in v:
                lines.append(f"preference: {n}")
        else:
            lines.append(f"preference: {k} = {v}")
    for f in data.get("facts") or []:
        lines.append(f"fact: {f}")

    q = (query or "").strip().lower()
    if q:
        scored = [ln for ln in lines if any(tok in ln.lower() for tok in q.split())]
        return scored[:limit] if scored else lines[:limit]
    return lines[:limit]


def summary_for_prompt(limit: int = 15) -> str:
    """Compact memory block to inject into system prompts."""
    lines = recall(limit=limit)
    if not lines:
        return "(no stored memory yet)"
    return "\n".join(f"- {ln}" for ln in lines)


def clear() -> str:
    if MEMORY_PATH.exists():
        MEMORY_PATH.unlink()
    return "Memory cleared."


def export() -> dict[str, Any]:
    return _load()
