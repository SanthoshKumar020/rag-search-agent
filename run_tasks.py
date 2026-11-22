#!/usr/bin/env python3
"""Scheduled-task runner (v3) — the local "always-on" piece.

Polls the tasks store every minute and executes due tasks through the
normal agent loop (agent_core.run_agent), logging each result to the
activity log exactly like an interactive Agent run.

Usage:
  python run_tasks.py            # poll forever (Ctrl-C to stop)
  python run_tasks.py --once     # run whatever is due right now, exit
  python run_tasks.py --interval 30

Honest limits:
  - This only fires while THIS process is running on your computer.
    Hosted servers (Vercel) cannot wake themselves; there, tasks are
    stored/listed and run on demand via POST /api/tasks/run.
  - Needs an LLM key in .env (GROQ_API_KEY) — the agent loop needs it.
  - Safety rules still apply: a due task that asks for a payment or
    sending a real message ends with the agent explaining what the
    human must do. It is never executed silently.
"""
from __future__ import annotations

import argparse
import time

import activity
import agent_core
import tasks


def run_due_tasks(max_steps: int = 8) -> list[dict]:
    """Execute every due pending task once. Returns per-task outcomes."""
    outcomes = []
    for task in tasks.due_tasks():
        tid = task["id"]
        print(f"[tasks] running [{tid}] {task['instruction'][:100]}")
        tasks.mark_running(tid)
        try:
            result = agent_core.run_agent(task["instruction"],
                                          max_steps=max_steps)
            answer = result.get("answer") or ""
            tasks.mark_done(tid, answer)
            activity.add_entry(f"[scheduled] {task['instruction']}",
                               result.get("tools_used") or [], answer)
            outcomes.append({"id": tid, "status": "done",
                             "answer": answer[:280]})
            print(f"[tasks] done [{tid}]")
        except Exception as exc:  # one bad task must not kill the runner
            tasks.mark_failed(tid, str(exc))
            activity.add_entry(f"[scheduled] {task['instruction']}", [],
                               f"FAILED: {exc}")
            outcomes.append({"id": tid, "status": "failed",
                             "error": str(exc)})
            print(f"[tasks] FAILED [{tid}]: {exc}")
    return outcomes


def main() -> int:
    parser = argparse.ArgumentParser(prog="run_tasks.py")
    parser.add_argument("--once", action="store_true",
                        help="run due tasks once and exit")
    parser.add_argument("--interval", type=int, default=60,
                        help="poll interval in seconds (default 60)")
    parser.add_argument("--max-steps", type=int, default=8)
    args = parser.parse_args()

    if args.once:
        outcomes = run_due_tasks(args.max_steps)
        print(f"[tasks] {len(outcomes)} due task(s) executed.")
        return 0

    print(f"[tasks] runner started — polling every {args.interval}s. "
          "Ctrl-C to stop.")
    try:
        while True:
            run_due_tasks(args.max_steps)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[tasks] runner stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
