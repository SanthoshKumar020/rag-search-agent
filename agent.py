#!/usr/bin/env python3
"""Search + RAG Reply Agent — command line entry point.

Usage:
  python agent.py ask "What is Delta Lake?"
  python agent.py ask --local "What does my doc say about X?"
  python agent.py reply "Thanks for your email — can you explain pricing?"
  python agent.py add path/to/file-or-folder

Pipeline: web search (DuckDuckGo) -> fetch pages -> chunk -> embed ->
vector retrieve -> free LLM (Groq / HuggingFace) writes a grounded
answer or reply with source citations. With no LLM key it prints the
retrieved evidence instead, so it still works with zero keys.
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


# ----------------------------------------------------------------------
# Evidence gathering
# ----------------------------------------------------------------------
def web_evidence(query: str) -> list[dict]:
    """Search the web, fetch top pages, chunk them, retrieve the best."""
    log(f"searching the web for: {query}")
    results = web_search(query, max_results=config.SEARCH_RESULTS)
    log(f"search returned {len(results)} results")
    chunks: list[dict] = []
    fetched = 0
    for r in results:
        text = fetch_text(r["url"])
        if len(text) < MIN_TEXT:
            continue
        fetched += 1
        for piece in chunk_text(text, config.CHUNK_SIZE, config.CHUNK_OVERLAP):
            chunks.append(
                {"text": piece, "source": r["url"], "title": r["title"]}
            )
    log(f"fetched {fetched} pages, indexed {len(chunks)} chunks")
    if not chunks:
        return []
    store = IndexStore(embedder=Embedder())
    store.build_ephemeral(chunks)
    hits = store.search(query, top_k=config.TOP_K)
    log(f"retrieved {len(hits)} chunks")
    return hits


def local_evidence(query: str) -> list[dict]:
    store = IndexStore()
    if len(store) == 0:
        print("Local knowledge index is empty. Add documents first:\n"
              "  python agent.py add path/to/file-or-folder")
        return []
    log(f"searching local index ({len(store)} chunks)")
    hits = store.search(query, top_k=config.TOP_K)
    log(f"retrieved {len(hits)} chunks")
    return hits


# ----------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------
def format_sources(hits: list[dict]) -> list[str]:
    """Unique sources in first-seen order -> ['Title — URL', ...]."""
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


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------
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
    """Turn an incoming message into a search query: drop greetings
    ('Hi,', 'Hello team,' ...) and cap the length."""
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


# ----------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        prog="agent.py",
        description="Search + RAG Reply Agent (free-tier LLMs)",
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

    args = parser.parse_args()
    log(f"embeddings: {config.embed_backend()} | llm: {config.llm_backend()}")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
