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

import re
from pathlib import Path
from typing import Any

import config

SKILLS_DIR = config.BASE_DIR / "skills"
MAX_INSTRUCTIONS = 6000  # keep the agent's context bounded
MAX_NEW_INSTRUCTIONS = 4000  # cap for user-created skills (save-a-skill)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


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


def create_skill(name: str, description: str,
                 instructions: str) -> dict[str, str]:
    """Write a new skills/<slug>.md (human-approved save-a-skill).

    Raises ValueError on an invalid slug or oversized instructions,
    OSError when the skills directory is not writable (hosted server).
    """
    slug = (name or "").strip().lower()
    if not _SLUG_RE.match(slug):
        raise ValueError(
            "Skill name must be a slug: lowercase letters, digits, dashes "
            "(e.g. 'cover-letter').")
    instructions = (instructions or "").strip()
    if not instructions:
        raise ValueError("Skill instructions are required.")
    if len(instructions) > MAX_NEW_INSTRUCTIONS:
        raise ValueError(
            f"Instructions too long ({len(instructions)} chars; "
            f"max {MAX_NEW_INSTRUCTIONS}).")
    description = " ".join((description or "").split())[:200]
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    path = SKILLS_DIR / f"{slug}.md"
    path.write_text(
        f"---\nname: {slug}\ndescription: {description}\n---\n\n"
        f"# Skill: {slug}\n\n{instructions}\n",
        encoding="utf-8",
    )
    return {"name": slug, "description": description}


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
