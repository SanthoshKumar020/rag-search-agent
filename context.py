"""Conversation context compression for long agent runs (v4).

Pattern adapted from Hermes Agent's context compressor: when the
running transcript grows past a character budget, protect the head
(the original task and first exchange) and the most recent tail, and
replace the middle with a single digest entry — instead of the old
hard `transcript[-12:]` cut, which silently dropped context.

Summarisation uses the configured LLM when one is available; with no
key (or on any LLM error) it falls back to a deterministic extractive
digest — first line of each middle entry, truncated — so compression
never fails a run.
"""
from __future__ import annotations

# Compress when the joined transcript exceeds this many characters.
BUDGET_CHARS = 12_000
KEEP_HEAD = 2    # entries: the task line + first exchange
KEEP_TAIL = 6    # most recent entries, always verbatim
DIGEST_MARKER = "[context digest of earlier steps]"


def _extractive_digest(middle: list[str]) -> str:
    lines = []
    for entry in middle:
        first = " ".join(entry.split())[:220]
        lines.append(f"- {first}")
    body = "\n".join(lines)
    if len(body) > 2400:
        body = body[:2400] + " …"
    return f"{DIGEST_MARKER}\n{body}"


def _llm_digest(middle: list[str]) -> str | None:
    try:
        from llm import generate
        text = generate(
            "You compress an AI agent's working transcript. Summarise "
            "the steps below into a compact factual digest: what was "
            "tried, what each tool returned (key facts, names, numbers, "
            "URLs), and what remains undecided. No preamble, no "
            "markdown fences, under 1800 characters.",
            "\n".join(middle)[:16000],
        )
        if text and text.strip():
            return f"{DIGEST_MARKER}\n{text.strip()[:2000]}"
    except Exception:
        pass
    return None


def maybe_compress(entries: list[str],
                     budget: int = BUDGET_CHARS) -> list[str]:
    """Return the transcript, compressed if it exceeds `budget`.

    Entries already containing a digest are never re-digested into a
    digest-of-digest chain more than once: an existing digest entry in
    the middle is kept as part of the new digest text verbatim.
    """
    if sum(len(e) for e in entries) <= budget:
        return entries
    if len(entries) <= KEEP_HEAD + KEEP_TAIL + 1:
        return entries
    head = entries[:KEEP_HEAD]
    tail = entries[-KEEP_TAIL:]
    middle = entries[KEEP_HEAD:-KEEP_TAIL]
    digest = _llm_digest(middle) or _extractive_digest(middle)
    return head + [digest] + tail
