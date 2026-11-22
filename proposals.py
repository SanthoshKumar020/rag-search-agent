"""Skill proposals — the safe half of Hermes' learning loop (v5).

Hermes' agent creates and refines its own skills autonomously. Here,
skill files are code-adjacent, so the loop is split in two:

  1. PROPOSE — the agent (via the `propose_skill` tool, or the
     post-run distillation pass in agent_core) writes a *proposal*:
     {name, description, instructions, source_run_id, source_task}.
     Proposals live in DATA_DIR/skill_proposals.json and change
     nothing by themselves.
  2. ACCEPT / DISCARD — a human reviews proposals in the web UI.
     Accepting writes a real skill through skills.create_skill()
     (same slug validation + size caps as the manual form);
     discarding just marks the proposal closed.

Nothing is ever auto-applied.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import config
import skills

PROPOSALS_PATH = config.DATA_DIR / "skill_proposals.json"
MAX_PROPOSALS = 50
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> list[dict[str, Any]]:
    if not PROPOSALS_PATH.exists():
        return []
    try:
        data = json.loads(PROPOSALS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(items: list[dict[str, Any]]) -> None:
    PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROPOSALS_PATH.write_text(
        json.dumps(items, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def create(name: str, description: str, instructions: str,
           source_run_id: str | None = None,
           source_task: str = "") -> dict[str, Any]:
    """Store a new pending proposal. Raises ValueError on bad input
    (same rules as skills.create_skill, so an accepted proposal can
    always be written)."""
    slug = (name or "").strip().lower()
    if not _SLUG_RE.match(slug):
        raise ValueError(
            "Skill name must be a slug: lowercase letters, digits, "
            "dashes (e.g. 'train-check').")
    instructions = (instructions or "").strip()
    if not instructions:
        raise ValueError("Proposal instructions are required.")
    if len(instructions) > skills.MAX_NEW_INSTRUCTIONS:
        raise ValueError(
            f"Instructions too long ({len(instructions)} chars; "
            f"max {skills.MAX_NEW_INSTRUCTIONS}).")
    description = " ".join((description or "").split())[:200]
    items = _load()
    for p in items:
        if p.get("name") == slug and p.get("status") == "pending":
            raise ValueError(f"A pending proposal named '{slug}' "
                             "already exists.")
    proposal = {
        "id": str(uuid.uuid4())[:8],
        "name": slug,
        "description": description,
        "instructions": instructions,
        "source_run_id": source_run_id,
        "source_task": (source_task or "")[:200],
        "status": "pending",
        "created_at": _now(),
        "decided_at": None,
    }
    items.insert(0, proposal)
    _save(items[:MAX_PROPOSALS])
    return proposal


def list_proposals(status: str | None = None) -> list[dict[str, Any]]:
    items = _load()
    if status:
        items = [p for p in items if p.get("status") == status]
    return items


def get(proposal_id: str) -> dict[str, Any] | None:
    for p in _load():
        if p.get("id") == proposal_id:
            return p
    return None


def _update(proposal_id: str, **fields: Any) -> dict[str, Any]:
    items = _load()
    for p in items:
        if p.get("id") == proposal_id:
            p.update(fields)
            _save(items)
            return p
    raise KeyError(f"Proposal not found: {proposal_id}")


def accept(proposal_id: str) -> dict[str, Any]:
    """Turn a pending proposal into a real skill. Raises KeyError /
    ValueError / OSError (read-only host) — the caller surfaces them."""
    proposal = get(proposal_id)
    if proposal is None:
        raise KeyError(f"Proposal not found: {proposal_id}")
    if proposal.get("status") != "pending":
        raise ValueError(f"Proposal already {proposal.get('status')}.")
    if skills.load_skill(proposal["name"]):
        raise ValueError(
            f"A skill named '{proposal['name']}' already exists.")
    created = skills.create_skill(proposal["name"],
                                  proposal.get("description") or "",
                                  proposal["instructions"])
    updated = _update(proposal_id, status="accepted", decided_at=_now())
    return {"proposal": updated, "skill": created}


def discard(proposal_id: str) -> dict[str, Any]:
    proposal = get(proposal_id)
    if proposal is None:
        raise KeyError(f"Proposal not found: {proposal_id}")
    if proposal.get("status") != "pending":
        raise ValueError(f"Proposal already {proposal.get('status')}.")
    return _update(proposal_id, status="discarded", decided_at=_now())
