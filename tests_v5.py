#!/usr/bin/env python3
"""Smoke tests for the v5 features (no LLM key required).

Covers: interactive approvals (approve + deny + timeout, broker and
agent-loop level), the skill-proposal lifecycle (tool, store,
distillation, accept/discard via API), briefing overnight results,
memory nudges, settings, and the new dashboard home page.

Run with an isolated data dir so real state is untouched:
    AGENT_DATA_DIR=$(mktemp -d) ./venv/bin/python tests_v5.py

Note: the app is pointed at the isolated dir by setting the env var
BEFORE importing config (config resolves DATA_DIR at import time).
The skills/ directory is repo-local, so accept-tests clean up the
skill file they create.
"""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILURES = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name
          + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ---------------------------------------------------------------- broker
import approvals

approvals.reset()
outcome = {}


def _ask():
    outcome["granted"], outcome["id"] = approvals.request(
        "run1", "browse", args='{"task": "open example.com"}',
        approval_id="test1234", timeout=10)


t = threading.Thread(target=_ask)
t.start()
deadline = time.time() + 5
pending = []
while time.time() < deadline:
    pending = approvals.list_pending()
    if pending:
        break
    time.sleep(0.05)
check("broker: pending approval is listed",
      len(pending) == 1 and pending[0]["id"] == "test1234"
      and pending[0]["tool"] == "browse")
decided = approvals.decide("test1234", True)
check("broker: decide returns the record",
      decided is not None and decided["tool"] == "browse")
t.join(timeout=5)
check("broker: request returns approved", outcome.get("granted") is True)
check("broker: pending cleared after decision",
      approvals.list_pending() == [])
check("broker: unknown id decide -> None",
      approvals.decide("nope", True) is None)

granted, _ = approvals.request("run2", "browse", timeout=0.2)
check("broker: timeout fails closed (denied)", granted is False)

# ---------------------------------------------------------------- settings
import config
import settings as settings_mod

view = settings_mod.public_view()
check("settings: defaults sane",
      view["max_steps"] == 8 and view["theme"] == "dark"
      and view["models"] == config.WEB_MODELS
      and view["webhook_token_set"] is False)
settings_mod.update({"max_steps": 12, "theme": "light",
                     "model": "openai/gpt-oss-20b"})
check("settings: update persists",
      settings_mod.max_steps() == 12
      and settings_mod.get("theme") == "light"
      and settings_mod.get("model") == "openai/gpt-oss-20b")
try:
    settings_mod.update({"max_steps": 99})
    check("settings: max_steps range enforced", False)
except ValueError:
    check("settings: max_steps range enforced", True)
try:
    settings_mod.update({"model": "not-a-model"})
    check("settings: model allowlist enforced", False)
except ValueError:
    check("settings: model allowlist enforced", True)
settings_mod.update({"webhook_token": "s3cret-token"})
view = settings_mod.public_view()
check("settings: token masked in public view",
      view["webhook_token_set"] is True
      and "s3cret" not in json.dumps(view)
      and view["webhook_token_masked"].endswith("en"))
check("settings: effective token readable server-side",
      settings_mod.webhook_token() == "s3cret-token")
settings_mod.update({"webhook_token": "", "max_steps": 8,
                     "theme": "dark", "model": None})

# ------------------------------------------------------- agent approvals
import agent_core
import runs

# --- deny path: browse asks for approval, human says no, agent adapts
_script = [
    json.dumps({"tool": "browse", "task": "open example.com"}),
    json.dumps({"tool": "calculate", "expression": "6*7"}),
    json.dumps({"tool": "finish", "answer": "adapted without browse"}),
]
_state = {"i": 0}
_seen_requests = []


def _fake_generate_deny(system, user, model=None):
    i = _state["i"]
    _state["i"] += 1
    return _script[min(i, len(_script) - 1)]


_orig_gen = agent_core.generate
agent_core.generate = _fake_generate_deny
try:
    res = agent_core.run_agent(
        "approval deny test", source="web", max_steps=5,
        approve=lambda info: _seen_requests.append(info) or False,
        propose_skills=False)
    check("approvals: deny-path run still finishes",
          res["answer"] == "adapted without browse")
    check("approvals: callback received the request",
          len(_seen_requests) == 1
          and _seen_requests[0]["tool"] == "browse"
          and _seen_requests[0]["tier"] == "act"
          and _seen_requests[0].get("id"))
    browse_trace = [x for x in res["trace"] if x.get("tool") == "browse"]
    check("approvals: denied step traced as blocked + denied",
          len(browse_trace) == 1 and browse_trace[0].get("blocked") is True
          and browse_trace[0].get("approval") == "denied")
    denied_obs = browse_trace[0]["observation"]
    check("approvals: denial observation tells the model not to retry",
          "DENIED" in denied_obs)
finally:
    agent_core.generate = _orig_gen

# --- approve path: a stubbed browse module proves the tool really ran
import types

_fake_browser = types.ModuleType("browser_agent")
_fake_browser.available = lambda: True
_fake_browser.run_browser_task = (
    lambda task, max_steps=25, headless=True: f"browsed: {task}")
_fake_browser._missing_message = lambda: "missing"
sys.modules["browser_agent"] = _fake_browser

_script2 = [
    json.dumps({"tool": "browse", "task": "open example.com"}),
    json.dumps({"tool": "finish", "answer": "browse done"}),
]
_state2 = {"i": 0}


def _fake_generate_approve(system, user, model=None):
    i = _state2["i"]
    _state2["i"] += 1
    return _script2[min(i, len(_script2) - 1)]


agent_core.generate = _fake_generate_approve
try:
    events = []
    res = agent_core.run_agent(
        "approval approve test", source="web", max_steps=4,
        on_event=events.append,
        approve=lambda info: True,
        propose_skills=False)
    check("approvals: approve-path run finishes",
          res["answer"] == "browse done")
    browse_trace = [x for x in res["trace"] if x.get("tool") == "browse"]
    check("approvals: approved step executed the tool",
          len(browse_trace) == 1
          and "browsed: open example.com" in browse_trace[0]["observation"]
          and browse_trace[0].get("approval") == "approved")
    types_seen = [e.get("type") for e in events]
    check("approvals: request + decided events emitted",
          "approval_request" in types_seen
          and "approval_decided" in types_seen)
finally:
    agent_core.generate = _orig_gen
    del sys.modules["browser_agent"]

# --- no callback -> v4 behaviour (act tool runs without prompting)
_state3 = {"i": 0}


def _fake_generate_plain(system, user, model=None):
    i = _state3["i"]
    _state3["i"] += 1
    return _script2[min(i, len(_script2) - 1)]


sys.modules["browser_agent"] = _fake_browser
agent_core.generate = _fake_generate_plain
try:
    res = agent_core.run_agent("no callback test", source="web",
                               max_steps=4, propose_skills=False)
    browse_trace = [x for x in res["trace"] if x.get("tool") == "browse"]
    check("approvals: no callback keeps v4 behaviour",
          res["answer"] == "browse done"
          and browse_trace
          and "approval" not in browse_trace[0])
finally:
    agent_core.generate = _orig_gen
    del sys.modules["browser_agent"]

# ------------------------------------------------------- skill proposals
import proposals
import skills

p = proposals.create("test-train-check", "Check trains between two cities",
                     "1. Search for trains. 2. Compare times. 3. Summarise.",
                     source_run_id="runX", source_task="check trains")
check("proposals: created pending",
      p["status"] == "pending" and p["name"] == "test-train-check")
check("proposals: listed",
      any(x["id"] == p["id"] for x in proposals.list_proposals("pending")))
try:
    proposals.create("test-train-check", "dup", "steps")
    check("proposals: duplicate pending name rejected", False)
except ValueError:
    check("proposals: duplicate pending name rejected", True)
try:
    proposals.create("Bad Name!", "x", "steps")
    check("proposals: bad slug rejected", False)
except ValueError:
    check("proposals: bad slug rejected", True)

discarded = proposals.discard(p["id"])
check("proposals: discard works", discarded["status"] == "discarded")
try:
    proposals.discard(p["id"])
    check("proposals: double-discard rejected", False)
except ValueError:
    check("proposals: double-discard rejected", True)

p2 = proposals.create("test-accept-flow", "For the accept test",
                      "Step one. Step two.")
result = proposals.accept(p2["id"])
check("proposals: accept creates a real skill",
      result["proposal"]["status"] == "accepted"
      and skills.load_skill("test-accept-flow") is not None)
# cleanup the skill file the accept created (skills/ is repo-local)
skill_path = skills.SKILLS_DIR / "test-accept-flow.md"
if skill_path.exists():
    skill_path.unlink()
try:
    proposals.accept("doesnotexist")
    check("proposals: accept unknown id -> KeyError", False)
except KeyError:
    check("proposals: accept unknown id -> KeyError", True)

# --- propose_skill tool inside a run
_script3 = [
    json.dumps({"tool": "propose_skill", "name": "test-tool-proposal",
                "description": "From the tool test",
                "instructions": "Do A, then B."}),
    json.dumps({"tool": "finish", "answer": "proposed"}),
]
_state4 = {"i": 0}


def _fake_generate_tool(system, user, model=None):
    i = _state4["i"]
    _state4["i"] += 1
    return _script3[min(i, len(_script3) - 1)]


agent_core.generate = _fake_generate_tool
try:
    res = agent_core.run_agent("tool proposal test", source="web",
                               max_steps=4, propose_skills=False)
    pend = proposals.list_proposals("pending")
    check("proposals: propose_skill tool stores a pending proposal",
          res["answer"] == "proposed"
          and any(x["name"] == "test-tool-proposal" for x in pend)
          and any(x["name"] == "test-tool-proposal"
                  and x["source_task"] == "tool proposal test"
                  for x in pend))
finally:
    agent_core.generate = _orig_gen

# --- post-run distillation: a 3+ step success proposes automatically
_script4 = [
    json.dumps({"tool": "calculate", "expression": "1+1"}),
    json.dumps({"tool": "calculate", "expression": "2+2"}),
    json.dumps({"tool": "calculate", "expression": "3+3"}),
    json.dumps({"tool": "finish", "answer": "maths done"}),
    json.dumps({"name": "test-distilled", "description": "Add numbers in pairs",
                "instructions": "Add step by step and verify each sum."}),
]
_state5 = {"i": 0}


def _fake_generate_distill(system, user, model=None):
    i = _state5["i"]
    _state5["i"] += 1
    return _script4[min(i, len(_script4) - 1)]


agent_core.generate = _fake_generate_distill
try:
    events = []
    res = agent_core.run_agent("distill test", source="web", max_steps=6,
                               on_event=events.append)
    pend = proposals.list_proposals("pending")
    check("proposals: distillation proposes after a 3+ step run",
          res["answer"] == "maths done"
          and any(x["name"] == "test-distilled" for x in pend))
    check("proposals: skill_proposed event emitted",
          any(e.get("type") == "skill_proposed" for e in events))
finally:
    agent_core.generate = _orig_gen

# ------------------------------------------------------------- briefing
import briefing
import tasks as tasks_mod

# remove any watermark so the first briefing looks back 24h
if briefing.STATE_PATH.exists():
    briefing.STATE_PATH.unlink()
task = tasks_mod.create("Overnight test task", "in 1 minute")
tasks_mod.mark_done(task["id"], "the overnight result text")
b1 = briefing.build_briefing()
overnight = b1.get("overnight_results") or []
check("briefing: overnight results include the finished task",
      any(t["id"] == task["id"]
          and "overnight result" in (t["result"] or "")
          for t in overnight))
b2 = briefing.build_briefing()
check("briefing: watermark advances (second briefing is empty)",
      (b2.get("overnight_results") or []) == [])

# --------------------------------------------------------------- nudges
import memory
import nudges
import sessions

memory.clear()
sessions.log("web", "I prefer short answers in all replies", "ok", [])
sessions.log("web", "What is the weather today?", "ok", [])
pending_nudges = nudges.list_pending()
check("nudges: first-person preference detected",
      any("prefer short answers" in n["text"] for n in pending_nudges)
      and all("weather" not in n["text"] for n in pending_nudges))
target = [n for n in pending_nudges
          if "prefer short answers" in n["text"]][0]
check("nudges: kind guessed as preference",
      target["kind"] == "preference")
saved = nudges.accept(target["id"])
check("nudges: accept saves into memory",
      saved["status"] == "saved"
      and any("prefer short answers" in ln
              for ln in memory.recall("", limit=50)))
check("nudges: accepted nudge leaves the pending list",
      all(n["id"] != target["id"] for n in nudges.list_pending()))
sessions.log("web", "I live in Chennai and commute daily", "ok", [])
pending2 = nudges.list_pending()
fact_nudge = [n for n in pending2 if "live in Chennai" in n["text"]]
check("nudges: fact statement detected", len(fact_nudge) == 1)
if fact_nudge:
    nudges.dismiss(fact_nudge[0]["id"])
    check("nudges: dismiss removes from pending",
          all(n["id"] != fact_nudge[0]["id"]
              for n in nudges.list_pending()))
memory.clear()

# ---------------------------------------------------------------- web app
import web_app

client = web_app.app.test_client()

r = client.get("/")
html = r.data.decode("utf-8")
check("web: home is 200", r.status_code == 200)
for marker in ("sidebar", "mode-btn", 'data-mode="friend"',
               "panel-tab", 'data-panel-body="briefing"',
               'data-panel-body="tasks"', 'data-panel-body="runs"',
               'data-panel-body="memory"', 'data-panel-body="skills"',
               'data-panel-body="mood"', 'data-panel-body="ideas"',
               'data-panel-body="history"', 'data-panel-body="activity"',
               'data-panel-body="settings"', "approval-modal",
               "toasts", "thread", "overnight-wrap", "nudges-wrap",
               "proposals-wrap", "settings-model", "runs-search",
               "memory-search", "sessions-search"):
    check(f"web: dashboard has {marker}", marker in html)

r = client.get("/api/settings")
s = r.get_json()
check("web: /api/settings public view",
      r.status_code == 200 and s["max_steps"] == 8
      and s["webhook_token_set"] is False)
r = client.post("/api/settings", json={"max_steps": 5})
check("web: /api/settings update",
      r.status_code == 200 and r.get_json()["max_steps"] == 5)
r = client.post("/api/settings", json={"max_steps": 500})
check("web: /api/settings rejects out-of-range", r.status_code == 400)
r = client.post("/api/settings", json={"max_steps": 8})
check("web: /api/settings restored", r.get_json()["max_steps"] == 8)

r = client.get("/api/approvals")
check("web: /api/approvals lists (empty)",
      r.status_code == 200 and r.get_json()["approvals"] == [])
r = client.post("/api/approvals/nope/decide", json={"approved": True})
check("web: decide unknown approval 404", r.status_code == 404)
r = client.post("/api/approvals/nope/decide", json={})
check("web: decide without 'approved' 400", r.status_code == 400)

r = client.get("/api/skill-proposals?status=pending")
names = [x["name"] for x in r.get_json()["proposals"]]
check("web: proposals endpoint lists pending",
      "test-tool-proposal" in names and "test-distilled" in names)
prop = [x for x in r.get_json()["proposals"]
        if x["name"] == "test-tool-proposal"][0]
r = client.post(f"/api/skill-proposals/{prop['id']}/discard", json={})
check("web: proposal discard via API",
      r.status_code == 200
      and r.get_json()["proposal"]["status"] == "discarded")
r = client.post("/api/skill-proposals/doesnotexist/accept", json={})
check("web: proposal accept unknown id 404", r.status_code == 404)

r = client.get("/api/nudges")
check("web: /api/nudges responds",
      r.status_code == 200 and "nudges" in r.get_json())
r = client.post("/api/nudges/doesnotexist/dismiss", json={})
check("web: nudge dismiss unknown id 404", r.status_code == 404)

r = client.get("/api/briefing")
check("web: /api/briefing has overnight_results key",
      r.status_code == 200 and "overnight_results" in r.get_json())

r = client.get("/api/tools")
check("web: /api/tools now 30 with propose_skill write tier",
      r.get_json()["count"] == 30
      and any(t["name"] == "propose_skill" and t["tier"] == "write"
              for t in r.get_json()["tools"]))

# webhook still honoured end-to-end (no-key mode -> graceful answer)
r = client.post("/api/webhook/run", json={"task": "say hello"})
check("web: webhook run responds",
      r.status_code == 200 and "answer" in r.get_json())

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("ALL V5 TESTS PASSED")
