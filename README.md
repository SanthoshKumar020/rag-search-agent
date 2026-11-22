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

## What's inside (v3)

Everything in v2, plus:

- **⏰ Scheduled tasks** — say *"Tomorrow 11:00 am, check trains from Chennai to Madurai and note the options"* in Agent mode (or use the Tasks panel / `python agent.py tasks add "..." --at "tomorrow 11:00 am"`). The instruction + due time (IST-aware; understands "tomorrow 11:00 am", "in 2 hours", weekday names, ISO) are stored in `tasks.json`. **How execution works, honestly:**
  - **Locally:** run `python run_tasks.py` — it polls every minute and executes due tasks through the agent loop, logging results to the Activity panel. This is the always-on piece; it fires only while that process runs on your computer.
  - **On Vercel:** serverless functions can't wake themselves, so tasks are stored and listed, and you execute one on demand with the ▶ button (or `POST /api/tasks/run`). The stored list is also ephemeral there (temp-dir storage, like memory).
  - **Safety:** a scheduled task runs under the same rules as a live request — it can research, browse and prepare, but it **can never complete a payment or send a real message**. A task like "book the 11 am Tatkal ticket" will prepare everything and stop at the IRCTC login/CAPTCHA/payment step, explaining what you must do. That step is yours, by design.
- **💛 Friend mode (mood companion)** — tell the agent *"I feel sad"* (Agent mode) or tap a mood in the **Mood panel**: it responds like a friend first, then suggests songs (iTunes API — page + 30-second preview links), shows (TVMaze) and movie lists (web search) matched to the mood. Tamil + English songs by default; `python agent.py mood sad` works too.
- **🔗 Webhook trigger** — `POST /api/webhook/run {"task": "..."}` runs the agent once and returns its answer, n8n-style, so external automations can trigger it. Set `AGENT_WEBHOOK_TOKEN` to require an `X-Agent-Token` header. Same safety rules apply.
- **🧾 Tool schemas** — every tool now has a structured, MCP-style schema (name, description, JSON-Schema parameters) in `agent_core.TOOL_SCHEMAS`, served at `GET /api/tools` for discovery by other programs.

## What's inside (v2)

- **Modes** — Ask (grounded Q&A), Reply (grounded drafts), Browse (local Chromium), Agent (multi-step tool loop), plus a **Live activity panel** that streams every page the agent opens.
- **Agent tools** — `web_search`, `fetch_page`, `browse`, `get_datetime`, `weather` (Open-Meteo), `news` (Google News / BBC RSS), `wikipedia`, `calculate` (safe AST arithmetic — no eval), `currency` (open.er-api.com), `remember` / `recall_memory`, `create_goal` / `update_goal` / `list_goals`, `create_reminder` / `list_reminders`, `save_artifact`, `list_skills` / `use_skill`, `finish` — plus (v3) `create_task` / `list_tasks` / `cancel_task` and `suggest_media`. All network tools are free and keyless.
- **Skills** — reusable instruction packs in `skills/*.md` (front-matter `name` + `description`, then instructions). Ships with `interview-prep` (Data Engineer coaching), `job-hunt-tracker` (JD → fit analysis + goal + memory), and `daily-briefing`. In Agent mode: *"Use the daily-briefing skill and brief me."* List them with `python agent.py skills`.
- **☀️ Briefing panel** — the web UI opens with today's date, weather for your city (memory preference `city`, default Chennai), reminders due today/overdue, active goals, latest memory facts, and newest artifacts (`GET /api/briefing`).
- **Reminders** — `python agent.py remind add "Revise SQL joins" --due 2026-11-25`, `remind list`, `remind done <id>`.
- **History** — every web run is saved (mode, query, answer, sources) and listed in the History panel; click one to restore it (`GET /api/history`).
- **Model picker** — the web UI can switch ask/reply between GPT-OSS 120B (default), GPT-OSS 20B (fast), and Qwen 3.8 27B. Agent mode keeps its own tuned model (`AGENT_MODEL`).
- **Browse on hosted servers** — instead of a raw library error, the web UI explains up front that Browse needs a local Chromium and how to run it on your own computer.
- **More keyless tools (wave 2)** — `translate` (MyMemory), `holidays` (Nager.Date — note: it does not cover India), `define` (Free Dictionary API).
- **🧠 Editable memory UI** — view, add, and delete memory entries from the web panel (`/api/memory`).
- **🎤 Voice** — speak your query (Web Speech API, en-IN) and listen to answers read aloud (speechSynthesis). Client-side only, no keys.
- **💡 Ideas panel** — 4 proactive task suggestions generated from your goals, reminders, memory, and briefing, cached per day; one click loads one into Agent mode.
- **Live cards** — weather, USD→INR rate, and top headlines in a card row under the briefing (`/api/cards`).
- **Save-a-skill** — create your own skill from the Skills panel form (slug-validated, 4000-char cap). On hosted read-only servers this returns a clear "run locally" message.
- **📜 Activity log** — every agent-mode run (task, tools used, answer preview) is logged and shown in the Activity panel (`/api/activity`).

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
| `reminders.py` | Reminders store |
| `tasks.py` | Scheduled tasks store + IST natural-language time parsing |
| `run_tasks.py` | Local runner: polls and executes due tasks |
| `media.py` | Mood companion suggestions (iTunes / TVMaze / web search) |
| `skills.py` + `skills/*.md` | Skill loader + starter skills |
| `tools_extra.py` | Keyless tools: datetime, weather, news, Wikipedia, calculator, currency |
| `briefing.py` | Morning briefing assembly (`/api/briefing`) |
| `history.py` | Web chat history (`/api/history`) |
| `activity.py` | Agent-run activity log (`/api/activity`) |
| `browser_agent.py` | Optional Chromium agent |
| `search.py` / `fetch.py` / `embed.py` / `index_store.py` / `llm.py` | RAG pipeline |
| `web_app.py` | Flask UI + SSE |

## Free keys

| Key | Use |
|-----|-----|
| `GROQ_API_KEY` | Generation + agent + browser (https://console.groq.com) |
| `HF_TOKEN` | Embeddings + fallback chat (https://huggingface.co/settings/tokens) |

## Limits (honest)

- Free-tier rate limits apply (Groq may 429 on heavy agent runs — wait a minute and retry).
- Agent step count is capped (default 8) to control cost.
- **Browse stays local-only.** It needs a real Chromium via browser-use; hosted serverless (Vercel) cannot run it, so the hosted UI shows an explanation card instead.
- **Web memory is ephemeral on Vercel.** Memory, goals, reminders, artifacts, and history are stored in `config.DATA_DIR` — locally that's `data/` in the repo; on Vercel's read-only filesystem it falls back to the server temp dir and resets when the instance restarts. Run locally for durable memory.
- The public web URL has no login — anyone with the link can run queries against your free-tier quota.
- Never auto-pays or sends real email — by design.
