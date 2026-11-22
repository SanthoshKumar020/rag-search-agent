#!/usr/bin/env python3
"""Smoke tests for the v4 harness features (no LLM key required).

Run with an isolated data dir so real memory/runs are untouched:
    AGENT_DATA_DIR=$(mktemp -d) ./venv/bin/python tests_v4.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILURES = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ---------------------------------------------------------------- policy
import policy

check("policy: browse is act tier", policy.tier_of("browse") == "act")
check("policy: web_search is read tier", policy.tier_of("web_search") == "read")
check("policy: remember is write tier", policy.tier_of("remember") == "write")
check("policy: unknown tool fails closed",
      policy.tier_of("rm_rf_everything") == "act")
ok, _ = policy.check("browse", "interactive")
check("policy: interactive may browse", ok)
ok, reason = policy.check("browse", "scheduled")
check("policy: scheduled may NOT browse", not ok and "browse" in reason)
ok, _ = policy.check("remember", "webhook")
check("policy: webhook may write memory", ok)
ok, _ = policy.check("browse", "webhook")
check("policy: webhook may NOT browse", not ok)
ok, _ = policy.check("delegate", "scheduled")
check("policy: scheduled may delegate (inherits ceiling)", ok)

# ---------------------------------------------------------------- context
import context as context_mod

small = [f"entry {i}" for i in range(5)]
check("context: small transcript untouched",
      context_mod.maybe_compress(small, budget=10000) == small)
big = [f"User task: do the thing"] + \
      [f"Observation {i}: " + "x" * 900 for i in range(20)]
out = context_mod.maybe_compress(big, budget=4000)
check("context: big transcript compressed",
      sum(len(e) for e in out) < sum(len(e) for e in big))
check("context: head protected", out[0].startswith("User task"))
check("context: tail protected", out[-1] == big[-1])
check("context: digest inserted",
      any(context_mod.DIGEST_MARKER in e for e in out))

# ---------------------------------------------------------------- memory
import memory

memory.clear()
memory.remember("Always answer in Tanglish", kind="instruction")
memory.remember("Lives in Chennai", kind="fact")
check("memory: instruction stored",
      memory.instructions() == ["Always answer in Tanglish"])
check("memory: instructions block renders",
      "Tanglish" in memory.instructions_block())
check("memory: summary excludes instructions",
      "Tanglish" not in memory.summary_for_prompt())
check("memory: recall includes instruction line",
      any(ln.startswith("instruction: ") for ln in memory.recall("")))
data = memory.export()
check("memory: fact has timestamp metadata",
      "Lives in Chennai" in (data.get("fact_meta") or {}))
check("memory: forget instruction",
      memory.forget("instruction: Always answer in Tanglish")
      .startswith("Forgot"))
memory.clear()

# ---------------------------------------------------------------- runs
import runs

rid = runs.start("Test task", "interactive")
runs.add_trace(rid, {"step": 1, "tool": "web_search",
                     "observation": "ok", "ms": 12})
runs.finish(rid, "step_limit", "partial answer", digest="digest body")
run = runs.get(rid)
check("runs: record persisted", run is not None and run["status"] == "step_limit")
check("runs: trace stored", len(run["trace"]) == 1)
check("runs: tools_used derived", run["tools_used"] == ["web_search"])
summaries = runs.list_runs()
check("runs: summary marks resumable",
      summaries and summaries[0]["resumable"] is True)
check("runs: unknown id -> None", runs.get("nope") is None)

# ---------------------------------------------------------------- sessions
import sessions

sessions.log("web", "Research delta lake", "Delta Lake summary", ["web_search"])
sessions.log("scheduled", "Morning briefing", "Brief done", ["news"])
found = sessions.search("delta")
check("sessions: keyword search hits", len(found) == 1
      and found[0]["task"] == "Research delta lake")
check("sessions: AND search misses cleanly",
      sessions.search("delta briefing") == [])
check("sessions: format renders",
      "Past sessions:" in sessions.format_results(found))

# ---------------------------------------------------------------- llm chain
import llm

check("llm: chain starts with requested model",
      llm._groq_chain("qwen/qwen3.8-27b")[0] == "qwen/qwen3.8-27b")
check("llm: chain covers allowlist, deduped",
      set(llm._groq_chain(None)) == set(
          ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]))

import requests


class _Resp:
    def __init__(self, status):
        self.status_code = status


def _fake_chat_factory(fail_first_n):
    calls = {"n": 0}

    def fake_chat(url, key, model, system, user):
        calls["n"] += 1
        if calls["n"] <= fail_first_n:
            err = requests.HTTPError("rate limited")
            err.response = _Resp(429)
            raise err
        return f"answer from {model}"
    return fake_chat, calls


_orig_chat, _orig_sleep = llm._chat, time.sleep
llm.time.sleep = lambda s: None
try:
    import config
    if config.GROQ_API_KEY:
        fake, calls = _fake_chat_factory(1)
        llm._chat = fake
        out = llm.generate("s", "u", model="openai/gpt-oss-120b")
        check("llm: falls back after 429", out.startswith("answer from")
              and calls["n"] == 2)
        fake2, calls2 = _fake_chat_factory(99)
        llm._chat = fake2
        try:
            llm.generate("s", "u")
            check("llm: chain exhaustion raises", False)
        except requests.HTTPError:
            check("llm: chain exhaustion raises",
                  calls2["n"] == len(llm._groq_chain(None)))
    else:
        check("llm: no key -> generate returns None",
              llm.generate("s", "u") is None)
finally:
    llm._chat = _orig_chat
    llm.time.sleep = _orig_sleep

# ---------------------------------------------------------------- agent loop
import agent_core

schemas = {s["name"]: s for s in agent_core.tool_schemas()}
# v5 added the propose_skill tool -> 30 (was 29 in v4)
check("schemas: 30 tools", len(schemas) == 30, f"got {len(schemas)}")
check("schemas: delegate + search_sessions present",
      "delegate" in schemas and "search_sessions" in schemas)
check("schemas: tiers annotated",
      schemas["browse"]["tier"] == "act"
      and schemas["web_search"]["tier"] == "read")

# no-key mode: run ends gracefully, is recorded, and logs a session
if not os.environ.get("GROQ_API_KEY") and not os.environ.get("HF_TOKEN"):
    before = len(sessions.list_recent(50))
    res = agent_core.run_agent("hello agent", source="scheduled")
    check("agent: no-key run returns guidance",
          "No LLM key" in res["answer"])
    check("agent: run recorded", res["run_id"] is not None
          and runs.get(res["run_id"])["status"] == "error")
    check("agent: session logged",
          len(sessions.list_recent(50)) == before + 1)

# fake-LLM loop: policy block + delegate depth + finish, fully offline
_script = [
    json.dumps({"tool": "browse", "task": "open example.com"}),
    json.dumps({"tool": "delegate", "task": "sub piece"}),
    json.dumps({"tool": "finish", "answer": "done"}),
]
_sub_script = [
    json.dumps({"tool": "delegate", "task": "nested"}),
    json.dumps({"tool": "calculate", "expression": "2*(3+4)"}),
    json.dumps({"tool": "finish", "answer": "sub done"}),
]
_state = {"main": 0, "sub": 0}


def _fake_generate(system, user, model=None):
    if "sub piece" in user and "User task: sub piece" in user:
        i = _state["sub"]
        _state["sub"] += 1
        return _sub_script[min(i, len(_sub_script) - 1)]
    i = _state["main"]
    _state["main"] += 1
    return _script[min(i, len(_script) - 1)]


_orig_gen = agent_core.generate
agent_core.generate = _fake_generate
try:
    res = agent_core.run_agent("main task", source="scheduled", max_steps=6)
    check("agent: fake-LLM run finishes", res["answer"] == "done")
    blocked = [t for t in res["trace"] if t.get("blocked")]
    check("agent: scheduled browse blocked by policy",
          len(blocked) == 1 and blocked[0]["tool"] == "browse")
    tools = res["tools_used"]
    check("agent: delegate executed", "delegate" in tools)
    rec = runs.get(res["run_id"])
    check("agent: trace persisted on run record",
          rec is not None and len(rec["trace"]) >= 3)
finally:
    agent_core.generate = _orig_gen

# ---------------------------------------------------------------- web app
import web_app

client = web_app.app.test_client()
r = client.get("/api/health")
check("web: /api/health ok", r.status_code == 200 and r.get_json()["ok"])
r = client.get("/api/tools")
body = r.get_json()
check("web: /api/tools has tiers",
      body["count"] == 30
      and any(t["name"] == "browse" and t["tier"] == "act"
              for t in body["tools"]))
r = client.get("/api/runs")
check("web: /api/runs lists", r.status_code == 200 and "runs" in r.get_json())
first = (r.get_json()["runs"] or [{}])[0]
if first.get("id"):
    r = client.get(f"/api/runs/{first['id']}")
    check("web: /api/runs/<id> returns trace",
          r.status_code == 200 and "trace" in r.get_json()["run"])
r = client.get("/api/runs/doesnotexist")
check("web: /api/runs/<bad> 404", r.status_code == 404)
r = client.get("/api/sessions?q=delta")
check("web: /api/sessions search", r.status_code == 200
      and len(r.get_json()["sessions"]) >= 1)
r = client.post("/api/runs/resume", json={})
check("web: resume without id 400", r.status_code == 400)
r = client.post("/api/runs/resume", json={"id": "doesnotexist"})
check("web: resume unknown id 404", r.status_code == 404)
r = client.get("/")
check("web: home page has Runs panel",
      r.status_code == 200 and b'data-panel-body="runs"' in r.data)
r = client.post("/api/memory", json={"fact": "Prefer short answers",
                                     "kind": "instruction"})
check("web: memory accepts instruction kind",
      r.status_code == 200
      and "Prefer short answers" in r.get_json().get("instructions", []))
memory.clear()

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("ALL V4 TESTS PASSED")
