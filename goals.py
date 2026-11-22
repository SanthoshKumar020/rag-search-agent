"""Long-running goals tracker (Muse-inspired).

Goals are multi-step objectives the agent can create, advance, and
report on across sessions. Stored as local JSON.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

GOALS_PATH = config.BASE_DIR / "data" / "goals.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> list[dict[str, Any]]:
    if not GOALS_PATH.exists():
        return []
    try:
        data = json.loads(GOALS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(goals: list[dict[str, Any]]) -> None:
    GOALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOALS_PATH.write_text(
        json.dumps(goals, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def create(title: str, plan: str = "") -> dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise ValueError("Goal title is required")
    goal = {
        "id": str(uuid.uuid4())[:8],
        "title": title,
        "plan": (plan or "").strip(),
        "status": "active",  # active | paused | done | cancelled
        "notes": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    goals = _load()
    goals.append(goal)
    _save(goals)
    return goal


def list_goals(status: str | None = "active") -> list[dict[str, Any]]:
    goals = _load()
    if status:
        return [g for g in goals if g.get("status") == status]
    return goals


def get(goal_id: str) -> dict[str, Any] | None:
    for g in _load():
        if g.get("id") == goal_id:
            return g
    return None


def update(
    goal_id: str,
    *,
    status: str | None = None,
    note: str | None = None,
    plan: str | None = None,
) -> dict[str, Any]:
    goals = _load()
    for g in goals:
        if g.get("id") != goal_id:
            continue
        if status:
            g["status"] = status
        if plan is not None:
            g["plan"] = plan
        if note:
            g.setdefault("notes", []).append({"at": _now(), "text": note})
            g["notes"] = g["notes"][-50:]
        g["updated_at"] = _now()
        _save(goals)
        return g
    raise KeyError(f"Goal not found: {goal_id}")


def summary_for_prompt(limit: int = 8) -> str:
    active = list_goals("active")[:limit]
    if not active:
        return "(no active goals)"
    lines = []
    for g in active:
        lines.append(f"- [{g['id']}] {g['title']} — {g.get('plan') or 'no plan yet'}")
    return "\n".join(lines)
