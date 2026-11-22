"""Tool risk tiers + fail-closed gating (v4).

Pattern adapted from Hermes Agent's approval subsystem: every tool is
classified by the blast radius of what it can do, and *unattended*
runs (scheduled tasks, webhook triggers — Hermes' "cron approval
context") run under a stricter ceiling than interactive ones. The
gate fails closed: an unknown tool is treated as the highest tier.

Tiers:
  read  — looks at the world, changes nothing (search, fetch,
          weather, news, calculate, memory recall, …)
  write — changes only the agent's own local stores (memory, goals,
          reminders, scheduled tasks, artifacts, session search is
          read; delegate inherits the caller's ceiling)
  act   — drives something outside the app (the real browser)

Ceilings by run source:
  interactive (web UI, CLI) .... act   (everything allowed)
  scheduled / webhook .......... write (browse is refused, with an
                                        explanation the model can
                                        relay to the user)
  delegate ..................... inherits the parent run's ceiling

There is deliberately NO tier for payments or sending real messages:
no such tools exist in this codebase, and the agent's system prompt
refuses those tasks outright. This gate is about *degree of autonomy*,
not about unlocking dangerous actions.
"""
from __future__ import annotations

# tool name -> tier. Anything missing from this map is treated as
# "act" (fail closed) by tier_of().
TOOL_TIERS: dict[str, str] = {
    # read
    "web_search": "read",
    "fetch_page": "read",
    "get_datetime": "read",
    "weather": "read",
    "news": "read",
    "wikipedia": "read",
    "calculate": "read",
    "currency": "read",
    "translate": "read",
    "holidays": "read",
    "define": "read",
    "recall_memory": "read",
    "list_goals": "read",
    "list_reminders": "read",
    "list_tasks": "read",
    "list_skills": "read",
    "search_sessions": "read",
    "suggest_media": "read",
    "finish": "read",
    # write (the agent's own stores only)
    "remember": "write",
    "create_goal": "write",
    "update_goal": "write",
    "create_reminder": "write",
    "create_task": "write",
    "cancel_task": "write",
    "save_artifact": "write",
    "use_skill": "write",
    # v5: writes only a *proposal* — a human accepts it before it
    # becomes a real skill, so it stays a write-tier store change.
    "propose_skill": "write",
    # delegation runs the same loop under the caller's ceiling
    "delegate": "write",
    # act (drives a real browser outside the app)
    "browse": "act",
}

_TIER_RANK = {"read": 0, "write": 1, "act": 2}

# run source -> highest tier it may execute
SOURCE_CEILINGS: dict[str, str] = {
    "interactive": "act",
    "web": "act",
    "cli": "act",
    "scheduled": "write",
    "webhook": "write",
    "delegate": "write",   # tightened further by inheritance in the loop
}


def tier_of(tool: str) -> str:
    """Risk tier of a tool; unknown tools fail closed at 'act'."""
    return TOOL_TIERS.get((tool or "").strip().lower(), "act")


def ceiling_for(source: str) -> str:
    return SOURCE_CEILINGS.get((source or "interactive").strip().lower(),
                               "write")


def check(tool: str, source: str,
          ceiling: str | None = None) -> tuple[bool, str]:
    """(allowed, reason). `ceiling` overrides the source default —
    used by delegation so a child can never exceed its parent."""
    cap = ceiling or ceiling_for(source)
    tier = tier_of(tool)
    if _TIER_RANK[tier] <= _TIER_RANK[cap]:
        return True, ""
    reason = (
        f"Policy: the '{tool}' tool is tier '{tier}', but this run "
        f"(source: {source}) is capped at tier '{cap}'. "
    )
    if tier == "act":
        reason += (
            "Tools that drive a real browser are only allowed in "
            "interactive runs — explain this to the user and offer to "
            "do the research-only parts of the task instead."
        )
    else:
        reason += "This action is not permitted for this kind of run."
    return False, reason
