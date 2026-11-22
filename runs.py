"""Agent run records: structured traces + checkpoint/resume (v4).

Patterns adapted from Hermes Agent (session persistence, run
telemetry, resumable work) and the agent-harness topic's emphasis on
observability: every agent run — web, CLI, scheduled, webhook,
delegate — is persisted as one record:

  {id, task, source, status, started_at, ended_at, answer,
   tools_used, trace: [{step, tool, args, observation, ms, blocked?}],
   digest: "<compressed transcript, for resume>"}

A run that ends `step_limit` or `error` is *resumable*: resume()
hands its digest to a follow-up run so work continues with context
instead of starting over. Storage is the usual DATA_DIR JSON pattern
(runs.json, newest first, capped).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import config

RUNS_PATH = config.DATA_DIR / "runs.json"
MAX_RUNS = 50
MAX_TRACE = 40


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> list[dict[str, Any]]:
    if not RUNS_PATH.exists():
        return []
    try:
        data = json.loads(RUNS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(items: list[dict[str, Any]]) -> None:
    RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUNS_PATH.write_text(
        json.dumps(items, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def start(task: str, source: str) -> str:
    run = {
        "id": str(uuid.uuid4())[:8],
        "task": (task or "")[:500],
        "source": source or "interactive",
        "status": "running",
        "started_at": _now(),
        "ended_at": None,
        "answer": "",
        "tools_used": [],
        "trace": [],
        "digest": "",
        "resumed_from": None,
    }
    items = _load()
    items.insert(0, run)
    _save(items[:MAX_RUNS])
    return run["id"]


def _update(run_id: str, **fields: Any) -> dict[str, Any] | None:
    items = _load()
    for run in items:
        if run.get("id") == run_id:
            run.update(fields)
            _save(items)
            return run
    return None


def add_trace(run_id: str, entry: dict[str, Any]) -> None:
    items = _load()
    for run in items:
        if run.get("id") == run_id:
            trace = run.setdefault("trace", [])
            trace.append(entry)
            run["trace"] = trace[-MAX_TRACE:]
            run["tools_used"] = sorted(
                {t.get("tool") for t in trace if t.get("tool")})
            _save(items)
            return


def finish(run_id: str, status: str, answer: str,
           digest: str = "") -> dict[str, Any] | None:
    return _update(run_id, status=status, ended_at=_now(),
                   answer=(answer or "")[:2000], digest=digest[:6000])


def get(run_id: str) -> dict[str, Any] | None:
    for run in _load():
        if run.get("id") == run_id:
            return run
    return None


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Summaries (no trace/digest bodies) for listing panels."""
    out = []
    for run in _load()[:limit]:
        out.append({
            "id": run.get("id"),
            "task": run.get("task"),
            "source": run.get("source"),
            "status": run.get("status"),
            "started_at": run.get("started_at"),
            "ended_at": run.get("ended_at"),
            "tools_used": run.get("tools_used") or [],
            "steps": len(run.get("trace") or []),
            "resumable": run.get("status") in ("step_limit", "error"),
            "resumed_from": run.get("resumed_from"),
        })
    return out


def mark_resumed_from(run_id: str, parent_id: str) -> None:
    _update(run_id, resumed_from=parent_id)
