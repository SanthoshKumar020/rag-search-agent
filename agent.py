#!/usr/bin/env python3
"""Search + RAG Reply + Personal Agent — command line entry point.

Usage:
  python agent.py ask "What is Delta Lake?"
  python agent.py ask --local "What does my doc say about X?"
  python agent.py reply "Thanks for your email — can you explain pricing?"
  python agent.py add path/to/file-or-folder
  python agent.py browse "Open BookMyShow and book a movie ticket for today…"
  python agent.py agent "Research best laptops under 50k and save a comparison"
  python agent.py memory list
  python agent.py goals list
  python agent.py artifacts list
  python agent.py skills
  python agent.py remind add "Revise SQL joins" --due 2026-11-25
  python agent.py remind list

Pipeline: web search -> fetch -> chunk -> embed -> retrieve -> LLM.
Optional browse drives real Chromium. The `agent` command runs a
Muse-inspired multi-step tool loop with memory, goals, and artifacts.
"""
import argparse
import sys
from pathlib import Path

import config
from embed import Embedder
from fetch import MIN_TEXT, chunk_text, fetch_text
from index_store import IndexStore
from llm import generate
from search import web_search

SUPPORTED_EXTS = {".txt", ".md", ".pdf"}


def log(step: str) -> None:
    print(f"  [{step}]", file=sys.stderr)


def _noop_event(event: dict) -> None:
    pass


def web_evidence(query: str, on_event=None) -> list[dict]:
    emit = on_event or _noop_event
    log(f"searching the web for: {query}")
    emit({"type": "search", "query": query})
    results = web_search(query, max_results=config.SEARCH_RESULTS)
    log(f"search returned {len(results)} results")
    emit({"type": "results",
          "results": [{"title": r["title"], "url": r["url"]}
                      for r in results]})
    chunks: list[dict] = []
    fetched = 0
    for r in results:
        emit({"type": "fetch", "url": r["url"], "title": r["title"]})
        text = fetch_text(r["url"])
        if len(text) < MIN_TEXT:
            reason = ("no readable text extracted"
                      if not text else
                      f"only {len(text)} chars of readable text "
                      f"(minimum {MIN_TEXT})")
            emit({"type": "fetch_failed", "url": r["url"],
                  "title": r["title"], "reason": reason})
            continue
        fetched += 1
        pieces = chunk_text(text, config.CHUNK_SIZE, config.CHUNK_OVERLAP)
        for piece in pieces:
            chunks.append(
                {"text": piece, "source": r["url"], "title": r["title"]}
            )
        emit({"type": "fetched", "url": r["url"], "title": r["title"],
              "chars": len(text), "chunks": len(pieces)})
    log(f"fetched {fetched} pages, indexed {len(chunks)} chunks")
    emit({"type": "indexed", "chunks": len(chunks), "pages": fetched})
    if not chunks:
        emit({"type": "retrieved", "hits": []})
        return []
    store = IndexStore(embedder=Embedder())
    store.build_ephemeral(chunks)
    hits = store.search(query, top_k=config.TOP_K)
    log(f"retrieved {len(hits)} chunks")
    emit({"type": "retrieved",
          "hits": [{"title": h.get("title") or h["source"],
                    "url": h["source"], "score": h["score"]}
                   for h in hits]})
    return hits


def local_evidence(query: str, on_event=None) -> list[dict]:
    emit = on_event or _noop_event
    store = IndexStore()
    if len(store) == 0:
        print("Local knowledge index is empty. Add documents first:\n"
              "  python agent.py add path/to/file-or-folder")
        emit({"type": "retrieved", "hits": []})
        return []
    log(f"searching local index ({len(store)} chunks)")
    emit({"type": "local", "chunks": len(store)})
    hits = store.search(query, top_k=config.TOP_K)
    log(f"retrieved {len(hits)} chunks")
    emit({"type": "retrieved",
          "hits": [{"title": h.get("title") or h["source"],
                    "url": h["source"], "score": h["score"]}
                   for h in hits]})
    return hits


def format_sources(hits: list[dict]) -> list[str]:
    seen, out = set(), []
    for h in hits:
        key = h["source"]
        if key in seen:
            continue
        seen.add(key)
        title = h.get("title") or h["source"]
        out.append(f"{title} — {h['source']}")
    return out


def print_evidence(hits: list[dict], sources: list[str]) -> None:
    print("\n--- Retrieved evidence ---")
    for i, h in enumerate(hits, 1):
        snippet = " ".join(h["text"].split())
        print(f"\n[{i}] (score {h['score']:.3f}) {h.get('title') or ''}")
        print(f"    {h['source']}")
        print(f"    {snippet[:500]}")
    print("\nSources:")
    for i, s in enumerate(sources, 1):
        print(f"  [{i}] {s}")


def evidence_block(hits: list[dict], sources: list[str]) -> str:
    lines = []
    for i, h in enumerate(hits, 1):
        lines.append(f"[{i}] Source: {h['source']}\n{h['text']}")
    return "\n\n".join(lines)


def cmd_ask(args) -> int:
    query = args.question
    hits = local_evidence(query) if args.local else web_evidence(query)
    if not hits:
        print("No evidence could be retrieved for that question.")
        return 1
    sources = format_sources(hits)
    system = (
        "You are a research assistant. Answer the user's question using "
        "ONLY the evidence provided. Cite claims inline with bracketed "
        "numbers like [1], [2] that match the evidence blocks. If the "
        "evidence does not cover something, say so plainly. Be accurate "
        "and concise."
    )
    user = (f"Question: {query}\n\nEvidence:\n{evidence_block(hits, sources)}"
            "\n\nWrite the answer with inline citations.")
    answer = generate(system, user)
    if answer is None:
        print("\n(no LLM key configured — showing retrieved evidence)\n")
        print_evidence(hits, sources)
        return 0
    print(f"\n{answer}\n")
    print("Sources:")
    for i, s in enumerate(sources, 1):
        print(f"  [{i}] {s}")
    return 0


def reply_search_query(message: str) -> str:
    import re

    lines = [ln.strip() for ln in message.splitlines() if ln.strip()]
    if lines and len(lines) > 1:
        first = lines[0].lower()
        if first.startswith(("hi", "hello", "hey", "dear", "good ")):
            lines = lines[1:]
    query = " ".join(" ".join(lines).split())
    query = re.sub(
        r"^(hi|hello|hey|dear\b[^,]{0,30}|good (morning|afternoon|evening))"
        r"[\s,!.]*",
        "",
        query,
        flags=re.IGNORECASE,
    )
    return query.strip()[:200]


def cmd_reply(args) -> int:
    message = args.message
    hits = web_evidence(reply_search_query(message))
    if not hits:
        print("No evidence could be retrieved to ground a reply.")
        return 1
    sources = format_sources(hits)
    system = (
        "You draft ready-to-send replies. Write a polite, helpful reply "
        "to the incoming message, grounded ONLY in the evidence provided "
        "— never invent facts, prices, dates or commitments. Keep it "
        "natural and concise, as a human would send it. Do not include "
        "citations or a signature inside the draft."
    )
    user = (f"Incoming message to reply to:\n{message}\n\nEvidence:\n"
            f"{evidence_block(hits, sources)}\n\nDraft the reply.")
    draft = generate(system, user)
    if draft is None:
        print("\n(no LLM key configured — showing retrieved evidence; "
              "add a free key to draft replies automatically)\n")
        print_evidence(hits, sources)
        return 0
    print("\n--- Draft reply ---\n")
    print(draft)
    print("\n--- Grounded in these sources ---")
    for i, s in enumerate(sources, 1):
        print(f"  [{i}] {s}")
    return 0


def read_document(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    return path.read_text(encoding="utf-8", errors="ignore")


def cmd_add(args) -> int:
    target = Path(args.path)
    files: list[Path] = []
    if target.is_dir():
        files = [p for p in sorted(target.rglob("*"))
                 if p.suffix.lower() in SUPPORTED_EXTS]
    elif target.is_file() and target.suffix.lower() in SUPPORTED_EXTS:
        files = [target]
    if not files:
        print(f"No supported documents (.txt/.md/.pdf) found at {target}")
        return 1
    store = IndexStore()
    total_new = 0
    for f in files:
        text = read_document(f)
        pieces = chunk_text(text, config.CHUNK_SIZE, config.CHUNK_OVERLAP)
        chunks = [{"text": p, "source": str(f), "title": f.name}
                  for p in pieces]
        before = len(store)
        store.add_chunks(chunks)
        added = len(store) - before
        total_new += added
        log(f"indexed {f.name}: {added} chunks")
    print(f"Done. Added {total_new} chunks from {len(files)} file(s). "
          f"Local index now holds {len(store)} chunks "
          f"(embeddings: {config.embed_backend()}).")
    return 0


def cmd_browse(args) -> int:
    try:
        import browser_agent
    except ImportError:
        print("browser_agent module missing.", file=sys.stderr)
        return 1

    if not browser_agent.available():
        print(browser_agent._missing_message(), file=sys.stderr)
        return 1

    task = args.task.strip()
    if not task:
        print("Please provide a non-empty task.", file=sys.stderr)
        return 1

    log(f"browser agent starting (max_steps={args.max_steps}, "
        f"headless={not args.show})")

    def _cli_event(ev: dict) -> None:
        t = ev.get("type")
        if t == "browse_start":
            log(f"task: {ev.get('task', '')[:120]}")
        elif t == "browse_done":
            log("browser agent finished")

    try:
        result = browser_agent.run_browser_task(
            task,
            max_steps=args.max_steps,
            headless=not args.show,
            on_event=_cli_event,
        )
    except Exception as exc:
        print(f"Browser agent failed: {exc}", file=sys.stderr)
        return 1

    print("\n--- Browser agent result ---\n")
    print(result)
    print()
    return 0


def cmd_agent(args) -> int:
    """Muse-inspired multi-step tool-using agent."""
    import agent_core

    task = args.task.strip()
    if not task:
        print("Please provide a non-empty task.", file=sys.stderr)
        return 1

    log(f"personal agent starting (max_steps={args.max_steps})")

    def _cli_event(ev: dict) -> None:
        t = ev.get("type")
        if t == "agent_start":
            log(f"task: {ev.get('task', '')[:120]}")
        elif t == "agent_thinking":
            log(f"thinking step {ev.get('step')}/{ev.get('max_steps')}")
        elif t == "tool":
            log(f"tool: {ev.get('tool')} {ev.get('query') or ev.get('url') or ev.get('task') or ''}".strip())
        elif t == "tool_result":
            log(f"result ({ev.get('tool')}): {(ev.get('preview') or '')[:80]}")
        elif t == "agent_done":
            log("agent finished")

    result = agent_core.run_agent(
        task, max_steps=args.max_steps, on_event=_cli_event
    )
    try:
        import activity

        activity.add_entry(task, result.get("tools_used") or [],
                           result.get("answer") or "")
    except Exception:
        pass  # activity log is best-effort
    print("\n--- Agent result ---\n")
    print(result.get("answer") or "")
    if result.get("steps"):
        print("\nSteps:")
        for s in result["steps"]:
            print(f"  - {s}")
    print()
    return 0


def cmd_memory(args) -> int:
    import memory

    if args.memory_cmd == "list":
        lines = memory.recall(args.query or "")
        if not lines:
            print("(memory is empty)")
        else:
            for ln in lines:
                print(f"  - {ln}")
        return 0
    if args.memory_cmd == "add":
        print(memory.remember(args.text, kind=args.kind))
        return 0
    if args.memory_cmd == "clear":
        print(memory.clear())
        return 0
    print("Unknown memory command", file=sys.stderr)
    return 1


def cmd_goals(args) -> int:
    import goals

    if args.goals_cmd == "list":
        items = goals.list_goals(None if args.all else "active")
        if not items:
            print("(no goals)")
            return 0
        for g in items:
            print(f"  [{g['id']}] ({g['status']}) {g['title']}")
            if g.get("plan"):
                print(f"      plan: {g['plan'][:120]}")
        return 0
    if args.goals_cmd == "add":
        g = goals.create(args.title, args.plan or "")
        print(f"Created [{g['id']}]: {g['title']}")
        return 0
    if args.goals_cmd == "done":
        g = goals.update(args.id, status="done", note=args.note or "marked done")
        print(f"Done [{g['id']}]: {g['title']}")
        return 0
    print("Unknown goals command", file=sys.stderr)
    return 1


def cmd_artifacts(args) -> int:
    import artifacts

    if args.artifacts_cmd == "list":
        items = artifacts.list_artifacts()
        if not items:
            print("(no artifacts)")
            return 0
        for a in items:
            print(f"  [{a['id']}] {a['title']} → {a['path']} ({a['chars']} chars)")
        return 0
    if args.artifacts_cmd == "show":
        item = artifacts.get(args.id)
        if not item:
            print(f"Artifact not found: {args.id}", file=sys.stderr)
            return 1
        print(f"# {item['title']}\n")
        print(item.get("content") or "(file missing)")
        return 0
    print("Unknown artifacts command", file=sys.stderr)
    return 1


def cmd_skills(args) -> int:
    import skills

    found = skills.list_skills()
    if not found:
        print("(no skills installed)")
        return 0
    for s in found:
        print(f"  {s['name']}: {s['description']}")
    return 0


def cmd_remind(args) -> int:
    import reminders

    if args.remind_cmd == "add":
        try:
            r = reminders.add(args.text, args.due)
        except ValueError as exc:
            print(f"Could not add reminder: {exc}", file=sys.stderr)
            return 1
        print(f"Created [{r['id']}]: {r['text']} (due {r['due']})")
        return 0
    if args.remind_cmd == "list":
        items = reminders.list_reminders(include_done=args.all)
        if not items:
            print("(no reminders)")
            return 0
        for r in items:
            mark = "✓ " if r.get("done") else ""
            print(f"  {mark}[{r['id']}] {r['text']} (due {r['due']})")
        return 0
    if args.remind_cmd == "done":
        try:
            r = reminders.mark_done(args.id)
        except KeyError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Done [{r['id']}]: {r['text']}")
        return 0
    print("Unknown remind command", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="agent.py",
        description="Search + RAG + personal agent (free-tier LLMs)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ask = sub.add_parser("ask", help="answer a question (web or --local)")
    p_ask.add_argument("question")
    p_ask.add_argument("--local", action="store_true",
                       help="answer from the local knowledge index only")
    p_ask.set_defaults(func=cmd_ask)

    p_reply = sub.add_parser("reply", help="draft a grounded reply")
    p_reply.add_argument("message")
    p_reply.set_defaults(func=cmd_reply)

    p_add = sub.add_parser("add", help="index local documents")
    p_add.add_argument("path")
    p_add.set_defaults(func=cmd_add)

    p_browse = sub.add_parser(
        "browse",
        help="drive a real browser (requires browser-use + LLM key)",
    )
    p_browse.add_argument("task")
    p_browse.add_argument("--max-steps", type=int, default=40)
    p_browse.add_argument("--show", action="store_true")
    p_browse.set_defaults(func=cmd_browse)

    p_agent = sub.add_parser(
        "agent",
        help="Muse-inspired multi-step agent (search, memory, goals, artifacts)",
    )
    p_agent.add_argument("task")
    p_agent.add_argument("--max-steps", type=int, default=8)
    p_agent.set_defaults(func=cmd_agent)

    p_mem = sub.add_parser("memory", help="list / add / clear personal memory")
    mem_sub = p_mem.add_subparsers(dest="memory_cmd", required=True)
    m_list = mem_sub.add_parser("list")
    m_list.add_argument("query", nargs="?", default="")
    m_add = mem_sub.add_parser("add")
    m_add.add_argument("text")
    m_add.add_argument("--kind", choices=("fact", "preference"), default="fact")
    mem_sub.add_parser("clear")
    p_mem.set_defaults(func=cmd_memory)

    p_goals = sub.add_parser("goals", help="list / add / complete goals")
    g_sub = p_goals.add_subparsers(dest="goals_cmd", required=True)
    g_list = g_sub.add_parser("list")
    g_list.add_argument("--all", action="store_true")
    g_add = g_sub.add_parser("add")
    g_add.add_argument("title")
    g_add.add_argument("--plan", default="")
    g_done = g_sub.add_parser("done")
    g_done.add_argument("id")
    g_done.add_argument("--note", default="")
    p_goals.set_defaults(func=cmd_goals)

    p_art = sub.add_parser("artifacts", help="list / show saved artifacts")
    a_sub = p_art.add_subparsers(dest="artifacts_cmd", required=True)
    a_sub.add_parser("list")
    a_show = a_sub.add_parser("show")
    a_show.add_argument("id")
    p_art.set_defaults(func=cmd_artifacts)

    p_skills = sub.add_parser("skills", help="list installed agent skills")
    p_skills.set_defaults(func=cmd_skills)

    p_remind = sub.add_parser("remind", help="add / list / complete reminders")
    r_sub = p_remind.add_subparsers(dest="remind_cmd", required=True)
    r_add = r_sub.add_parser("add")
    r_add.add_argument("text")
    r_add.add_argument("--due", required=True,
                       help="ISO date/datetime, e.g. 2026-11-25")
    r_list = r_sub.add_parser("list")
    r_list.add_argument("--all", action="store_true",
                        help="include completed reminders")
    r_done = r_sub.add_parser("done")
    r_done.add_argument("id")
    p_remind.set_defaults(func=cmd_remind)

    args = parser.parse_args()
    log(f"embeddings: {config.embed_backend()} | llm: {config.llm_backend()}")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
