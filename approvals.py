"""Interactive approval broker (v5).

Pattern adapted from Hermes Agent's gateway round-trip approvals:
when an *interactive* agent run wants an `act`-tier tool (today: the
real browser), the run pauses and a human approves or denies. The
agent loop stays transport-agnostic — it receives an `approve`
callable; this module is the web transport for that callable:

  - the agent worker thread calls `request(...)`, which registers a
    pending approval and blocks on an event;
  - the UI polls `list_pending()` / receives the approval id over SSE
    and POSTs the decision to `decide(...)`;
  - the blocked call returns True (approved) or False (denied).
    A timeout is a denial — approvals fail closed, like Hermes.

Unattended runs (scheduled / webhook) never reach this broker: the
policy gate caps them at the `write` tier before approvals apply.
State is in-process only (a pending approval is meaningless after a
restart, and the run waiting on it is gone too).
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any

# How long a run waits for a human decision before failing closed.
TIMEOUT_SECONDS = float(os.environ.get("AGENT_APPROVAL_TIMEOUT", "120"))

_lock = threading.Lock()
_pending: dict[str, dict[str, Any]] = {}


def _public(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry["id"],
        "run_id": entry.get("run_id"),
        "tool": entry.get("tool"),
        "tier": entry.get("tier"),
        "args": entry.get("args") or "",
        "created_at": entry.get("created_at"),
    }


def request(run_id: str | None, tool: str, args: str = "",
            tier: str = "act",
            timeout: float | None = None,
            approval_id: str | None = None) -> tuple[bool, str]:
    """Register a pending approval and block until decided.

    Returns (approved, approval_id). Timeout -> (False, id).
    `approval_id` may be supplied by the caller (the agent loop puts
    it on the approval_request event so the UI knows which pending
    approval its Approve/Deny click belongs to)."""
    approval_id = approval_id or str(uuid.uuid4())[:8]
    event = threading.Event()
    entry = {
        "id": approval_id,
        "run_id": run_id,
        "tool": tool,
        "tier": tier,
        "args": (args or "")[:300],
        "created_at": time.time(),
        "event": event,
        "approved": False,
    }
    with _lock:
        _pending[approval_id] = entry
    try:
        event.wait(TIMEOUT_SECONDS if timeout is None else timeout)
        return bool(entry["approved"]), approval_id
    finally:
        with _lock:
            _pending.pop(approval_id, None)


def decide(approval_id: str, approved: bool) -> dict[str, Any] | None:
    """Resolve a pending approval. Returns its public record or None."""
    with _lock:
        entry = _pending.get(approval_id)
        if entry is None:
            return None
        entry["approved"] = bool(approved)
        public = _public(entry)
        entry["event"].set()
        return public


def list_pending() -> list[dict[str, Any]]:
    with _lock:
        return [_public(e) for e in _pending.values()]


def reset() -> None:
    """Drop all pending approvals (tests / shutdown)."""
    with _lock:
        for entry in _pending.values():
            entry["event"].set()
        _pending.clear()
