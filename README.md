# Search + RAG + Personal Agent

A Python agent that **searches the live web, reads pages, and answers or drafts replies grounded in sources** — plus a **Muse-inspired personal agent** with memory, goals, artifacts, and optional real-browser control.

Everything runs on **free tiers** (Groq / HuggingFace). With no API keys it still searches and shows evidence.

> **Honest note:** This is *inspired by* Meta Muse, not a clone. Muse has a private cloud VM, payment rails, WhatsApp, glasses, and deep OAuth connectors. This project keeps the useful agent ideas that work on a laptop with free APIs: tool loop, memory, goals, artifacts, and browser automation.

## What you get

| Mode | What it does |
|------|----------------|
| **Ask** | Grounded Q&A with citations |
| **Reply** | Draft emails/messages from web evidence |
| **Browse** | Real Chromium via browser-use (local) |
| **Agent** | Multi-step tool loop: search → fetch → remember → goals → save docs |
| **Memory** | Persistent facts & preferences |
| **Goals** | Long-running objectives across sessions |
| **Artifacts** | Saved documents/code the agent produces |

## Quick start

```bash
git clone https://github.com/SanthoshKumar020/rag-search-agent.git
cd rag-search-agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add GROQ_API_KEY (recommended)
```

```bash
python agent.py ask "What is Delta Lake medallion architecture?"
python agent.py reply "Hi, can you explain your refund policy?"
python agent.py add ~/notes/
python agent.py ask --local "What did we decide about the schema?"
```

### Muse-inspired personal agent

```bash
# Multi-step agent (search, memory, goals, artifacts)
python agent.py agent "Research the top 3 open-source vector DBs and save a comparison markdown"

# Memory
python agent.py memory add "I prefer concise answers" --kind preference
python agent.py memory list

# Goals
python agent.py goals add "Ship RAG agent v2" --plan "browser + memory + UI"
python agent.py goals list
python agent.py goals done <id>

# Artifacts the agent saved
python agent.py artifacts list
python agent.py artifacts show <id>
```

### Optional real browser

```bash
pip install 'browser-use==1.0.8'
python agent.py browse "Open example.com and tell me the page title"
# --show to watch the window
```

## Web UI

```bash
python web_app.py   # http://127.0.0.1:5000
```

Modes: **Ask** / **Reply** / **Browse** / **Agent**. Live step streaming via SSE.

On Vercel, Ask/Reply work; Browse and heavy Agent tools that need Chromium stay local.

## Muse vs this project

| Capability | Meta Muse | This repo |
|------------|-----------|-----------|
| Chat + research | Yes | Yes (ask / agent) |
| Multi-step tool use | Yes | Yes (`agent`) |
| Web browser control | Yes (cloud VM) | Yes (local browser-use) |
| Persistent memory | Yes | Yes (`memory.py`) |
| Long-running goals | Yes | Yes (`goals.py`) |
| Artifacts / library | Yes | Yes (`artifacts.py`) |
| Background always-on | Yes | No (run on demand) |
| Payments / Stripe Link | Yes | No (refuses to pay) |
| Email send / OAuth apps | Yes | No (local only) |
| Private cloud VM | Yes | No |
| WhatsApp / glasses | Yes | No |

## Modules

| File | Role |
|------|------|
| `agent.py` | CLI |
| `agent_core.py` | Tool-using personal agent loop |
| `memory.py` | Facts & preferences |
| `goals.py` | Goal tracker |
| `artifacts.py` | Saved outputs |
| `browser_agent.py` | Optional Chromium agent |
| `search.py` / `fetch.py` / `embed.py` / `index_store.py` / `llm.py` | RAG pipeline |
| `web_app.py` | Flask UI + SSE |

## Free keys

| Key | Use |
|-----|-----|
| `GROQ_API_KEY` | Generation + agent + browser (https://console.groq.com) |
| `HF_TOKEN` | Embeddings + fallback chat (https://huggingface.co/settings/tokens) |

## Limits

- Free-tier rate limits apply.
- Agent step count is capped (default 8) to control cost.
- Browse needs local Chromium; not for pure serverless.
- Never auto-pays or sends real email — by design.
