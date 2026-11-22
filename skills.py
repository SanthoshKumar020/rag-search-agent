"""Skills system (Muse-inspired).

A skill is a markdown file in the repo's skills/ directory with a small
front-matter header:

    ---
    name: interview-prep
    description: One-line summary shown in listings
    ---

    # Instructions the agent follows when the skill is active...

`list_skills()` returns [{name, description}]; `load_skill(name)` returns
the full record including `instructions` (the markdown body).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import config

SKILLS_DIR = config.BASE_DIR / "skills"
MAX_INSTRUCTIONS = 6000  # keep the agent's context bounded


def _parse(text: str, fallback_name: str) -> dict[str, Any]:
    name, description, body = fallback_name, "", text.strip()
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            header = text[3:end]
            body = text[end + 4:].strip()
            for line in header.splitlines():
                if ":" not in line:
                    continue
                key, _, val = line.partition(":")
                key, val = key.strip().lower(), val.strip()
                if key == "name" and val:
                    name = val
                elif key == "description":
                    description = val
    return {"name": name, "description": description, "instructions": body}


def list_skills() -> list[dict[str, str]]:
    """All skills as [{name, description}], sorted by name."""
    if not SKILLS_DIR.exists():
        return []
    out = []
    for path in sorted(SKILLS_DIR.glob("*.md")):
        try:
            rec = _parse(path.read_text(encoding="utf-8"), path.stem)
        except OSError:
            continue
        out.append({"name": rec["name"], "description": rec["description"]})
    return out


def load_skill(name: str) -> dict[str, Any] | None:
    """Full skill record {name, description, instructions} or None."""
    wanted = (name or "").strip().lower()
    if not wanted or not SKILLS_DIR.exists():
        return None
    for path in sorted(SKILLS_DIR.glob("*.md")):
        try:
            rec = _parse(path.read_text(encoding="utf-8"), path.stem)
        except OSError:
            continue
        if rec["name"].lower() == wanted or path.stem.lower() == wanted:
            rec["instructions"] = rec["instructions"][:MAX_INSTRUCTIONS]
            return rec
    return None
