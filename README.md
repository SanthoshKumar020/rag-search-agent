# Search + RAG Reply Agent

A small Python agent that **searches the live web, reads the top pages,
and answers questions — or drafts replies — grounded in what it actually
found**, with sources cited. It can also index your own documents and
answer questions from them.

Everything runs on **free tiers**. With no API keys at all it still
works: it searches, retrieves evidence and prints it (embeddings fall
back to local TF-IDF). Add a free Groq or HuggingFace key and it writes
full answers and ready-to-send replies.

## Use cases

- **Research answers with sources** — `ask` a question and get a
  grounded answer with numbered citations instead of a model's guess.
- **Drafting grounded replies** — paste a question, email or message
  into `reply` and get a polite, ready-to-send draft based on retrieved
  evidence, with the sources listed separately so you can verify before
  sending. The agent never invents facts to fill gaps.
- **Q&A over your own documents** — `add` your notes, docs or PDFs once,
  then `ask --local` answers from them offline.

## Quick start

```bash
git clone https://github.com/SanthoshKumar020/rag-search-agent.git
cd rag-search-agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add at least one free key (see below)
```

```bash
python agent.py ask "What is Delta Lake medallion architecture?"
python agent.py reply "Hi, can you explain your refund policy for annual plans?"
python agent.py add ~/notes/project-docs/
python agent.py ask --local "What did we decide about the schema?"
```

## Web app (Vercel)

The same pipeline is available as a small web app (`web_app.py`): a
chat-style page where you type a question or paste a message, pick
**Ask** (grounded answer with citations) or **Reply** (draft a reply),
toggle web search on/off, and get the answer plus its sources. The web
index is built in memory per request — nothing is written to disk, so
it runs fine on serverless hosting.

**Deploy on Vercel:**

1. Import this repo in Vercel (New Project → Import Git Repository).
   `vercel.json` and `api/index.py` are already configured — no build
   settings to change.
2. In the project's **Settings → Environment Variables**, add
   `GROQ_API_KEY` (and optionally `HF_TOKEN`). Keys are read only from
   the environment at runtime; never commit them to the repo.
3. Deploy. Every `git push` to `main` redeploys automatically.

Without any key set, the web app still works — it returns the retrieved
evidence instead of a generated answer, exactly like the CLI.

Run it locally the same way as the CLI environment:

```bash
python web_app.py   # http://127.0.0.1:5000
```

The CLI (`agent.py ask / reply / add`) works exactly as before and is
unaffected by the web app.

## Free API keys (optional but recommended)

| Key | Used for | Get it free |
| --- | -------- | ----------- |
| `GROQ_API_KEY` | Answer/reply generation (Groq, default model `openai/gpt-oss-120b`; override with `GROQ_MODEL`) | https://console.groq.com |
| `HF_TOKEN` | Semantic embeddings (all-MiniLM-L6-v2) + fallback chat model | https://huggingface.co/settings/tokens |

Put them in `.env` (see `.env.example`). Behaviour by configuration:

| Keys present | Embeddings | Generation |
| ------------ | ---------- | ---------- |
| Groq + HF | HuggingFace semantic | Groq |
| Groq only | TF-IDF (local) | Groq |
| HF only | HuggingFace semantic | HF chat model |
| none | TF-IDF (local) | prints retrieved evidence |

## How it works

```
 question / message
        |
        v
 [1] SEARCH    DuckDuckGo (ddgs) -> DDG lite -> SearXNG -> Bing
        |      first transport that returns results wins
        v
 [2] FETCH     requests + BeautifulSoup strip boilerplate -> page text
        |
        v
 [3] CHUNK     ~800-char chunks, 120-char overlap
        |
        v
 [4] EMBED     HuggingFace all-MiniLM-L6-v2  (or TF-IDF fallback)
        |
        v
 [5] RETRIEVE  numpy cosine similarity, top 5 chunks
        |
        v
 [6] GENERATE  Groq gpt-oss-120b (or HF chat) writes the answer /
               reply with inline [n] citations + a Sources list
```

For local documents the same chunk/embed/retrieve steps run over a
persistent on-disk index (`index/vectors.npz` + `index/chunks.json`).

### Modules

| File | Role |
| ---- | ---- |
| `agent.py` | CLI (`ask` / `reply` / `add`) and pipeline orchestration |
| `search.py` | Web search transport chain (DuckDuckGo first) |
| `fetch.py` | Page fetching, text extraction, chunking |
| `embed.py` | HuggingFace embeddings with TF-IDF fallback |
| `index_store.py` | Persistent numpy vector index + retrieval |
| `llm.py` | Groq / HuggingFace generation |
| `config.py` | Keys, model names, tuning constants |
| `web_app.py` | Flask web UI (`/` page + `POST /api/query`) over the same pipeline |
| `api/index.py` | Vercel serverless entrypoint (imports the app from `web_app.py`) |

## Honest limits

- **Free-tier rate limits** — Groq and HuggingFace free tiers throttle;
  heavy use will hit 429s.
- **Search quality varies** — results are only as good as the search
  transport that answers. Search engines sometimes bot-wall datacenter
  IPs (DuckDuckGo may return a challenge page), which is why several
  fallback transports exist; on a residential connection DuckDuckGo via
  `ddgs` normally answers first.
- **Answers are only as good as the sources** — the agent grounds its
  output in retrieved pages, but it cannot verify a source's truth.
  Check the citations before acting on an answer.
- **TF-IDF fallback is lexical** — without `HF_TOKEN`, retrieval matches
  keywords rather than meaning, so phrasing matters more.
- The local index's TF-IDF vocabulary is fixed by the first corpus added;
  for a very different document set, delete `index/` and re-add.
