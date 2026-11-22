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

MEMORY_PATH = config.DATA_DIR / "memory.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    empty = {"facts": [], "preferences": {}, "instructions": [],
             "fact_meta": {}, "updated_at": None}
    if not MEMORY_PATH.exists():
        return empty
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
        for key, default in empty.items():
            data.setdefault(key, default)
        return data
    except (json.JSONDecodeError, OSError):
        return empty


def _save(data: dict[str, Any]) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    MEMORY_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def remember(fact: str, *, kind: str = "fact") -> str:
    """Store a short fact, preference, or standing instruction.

    Kinds (v4, Hermes-style memory taxonomy): 'fact' and 'preference'
    are agent/user knowledge; 'instruction' is a standing instruction
    that is always injected in full into the agent's system prompt."""
    fact = (fact or "").strip()
    if not fact:
        return "Nothing to remember."
    data = _load()
    if kind == "instruction":
        instructions: list[str] = data.setdefault("instructions", [])
        if fact not in instructions:
            instructions.append(fact)
        data["instructions"] = instructions[-25:]
        _save(data)
        return f"Remembered (instruction): {fact}"
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
            data.setdefault("fact_meta", {})[fact] = _now()
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

    for ins in data.get("instructions") or []:
        lines.append(f"instruction: {ins}")

    q = (query or "").strip().lower()
    if q:
        scored = [ln for ln in lines if any(tok in ln.lower() for tok in q.split())]
        return scored[:limit] if scored else lines[:limit]
    return lines[:limit]


def instructions() -> list[str]:
    """Standing instructions — always injected in full (v4)."""
    return list(_load().get("instructions") or [])


def summary_for_prompt(limit: int = 15) -> str:
    """Compact memory block to inject into system prompts.
    Instructions are NOT included here — they get their own,
    always-complete block via instructions_block()."""
    lines = [ln for ln in recall(limit=limit)
             if not ln.startswith("instruction: ")]
    if not lines:
        return "(no stored memory yet)"
    return "\n".join(f"- {ln}" for ln in lines)


def instructions_block() -> str:
    ins = instructions()
    if not ins:
        return "(none set)"
    return "\n".join(f"- {i}" for i in ins)


def forget(line: str) -> str:
    """Remove ONE entry given its display line from recall(), e.g.
    'fact: X', 'preference: key = value', or 'preference: note text'."""
    line = (line or "").strip()
    if not line:
        return "Nothing to forget."
    data = _load()
    if line.startswith("instruction: "):
        target = line[len("instruction: "):]
        items = data.get("instructions") or []
        if target in items:
            items.remove(target)
            data["instructions"] = items
            _save(data)
            return f"Forgot (instruction): {target}"
        return f"Not in memory: {line}"
    if line.startswith("fact: "):
        target = line[len("fact: "):]
        facts = data.get("facts") or []
        if target in facts:
            facts.remove(target)
            data["facts"] = facts
            (data.get("fact_meta") or {}).pop(target, None)
            _save(data)
            return f"Forgot (fact): {target}"
        return f"Not in memory: {line}"
    if line.startswith("preference: "):
        rest = line[len("preference: "):]
        prefs = data.setdefault("preferences", {})
        if " = " in rest:
            key = rest.split(" = ", 1)[0].strip()
            if key in prefs:
                del prefs[key]
                _save(data)
                return f"Forgot (preference): {key}"
        notes = prefs.get("_notes") or []
        if rest in notes:
            notes.remove(rest)
            prefs["_notes"] = notes
            _save(data)
            return f"Forgot (preference): {rest}"
        return f"Not in memory: {line}"
    return f"Unrecognised memory line: {line}"


def clear() -> str:
    if MEMORY_PATH.exists():
        MEMORY_PATH.unlink()
    return "Memory cleared."


def export() -> dict[str, Any]:
    return _load()
