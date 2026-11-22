"""Autonomous browser agent powered by browser-use (optional dependency).

Gives the agent a real Chromium so it can click, type, navigate and complete
multi-step web tasks from a natural-language instruction, e.g.:

  python agent.py browse "Open BookMyShow, search for [movie], pick the\n  earliest show in [city] today, select 2 seats, and STOP before payment."

Install (local only — not suitable for pure serverless):

  pip install 'browser-use==1.0.8'
  # first run may download Chromium via Playwright

Safety defaults baked into every task:
  - Never submit payment or final checkout without explicit human OK
  - Prefer stopping with a clear summary of what was selected
"""
from __future__ import annotations

import asyncio
import sys
from typing import Callable, Optional

import config

# Optional dependency — the rest of the agent still works without it.
try:
    from browser_use import Agent, Browser
    from browser_use.llm import ChatOpenAI  # OpenAI-compatible (works with Groq)

    _HAS_BROWSER_USE = True
except ImportError:  # pragma: no cover
    Agent = Browser = ChatOpenAI = None  # type: ignore
    _HAS_BROWSER_USE = False


EventCallback = Callable[[dict], None]


def available() -> bool:
    """True when browser-use is installed and an LLM key is present."""
    return _HAS_BROWSER_USE and config.llm_backend() != "none"


def _missing_message() -> str:
    if not _HAS_BROWSER_USE:
        return (
            "browser-use is not installed.\n"
            "  pip install 'browser-use==1.0.8'\n"
            "Then re-run the browse command. Chromium will be downloaded on first use."
        )
    return (
        "No LLM key configured. Set GROQ_API_KEY (recommended) or HF_TOKEN\n"
        "in .env so the browser agent has a model to drive the browser."
    )


def _make_llm():
    """Map the project's existing free-tier keys onto browser-use's ChatOpenAI."""
    backend = config.llm_backend()
    if backend == "groq":
        return ChatOpenAI(
            model=config.GROQ_MODEL,
            api_key=config.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )
    if backend == "huggingface":
        return ChatOpenAI(
            model=config.HF_CHAT_MODEL,
            api_key=config.HF_TOKEN,
            base_url="https://router.huggingface.co/v1",
        )
    raise RuntimeError(_missing_message())


SAFETY_PREFIX = (
    "IMPORTANT SAFETY RULES (always follow):\n"
    "- Do NOT submit payment, enter card details, or complete a final checkout\n"
    "  that costs money. Stop one step before payment and summarise what you selected.\n"
    "- Do NOT create accounts or change passwords unless the task explicitly asks.\n"
    "- Prefer public / guest flows when available.\n"
    "- When finished, clearly report: what you did, the final page URL, and any\n"
    "  confirmation numbers or selected options.\n\n"
    "TASK:\n"
)


def _run_sync(
    task: str,
    *,
    max_steps: int = 40,
    headless: bool = True,
    on_event: Optional[EventCallback] = None,
) -> str:
    """Run a browser-use Agent and return the final result text."""
    if not available():
        raise RuntimeError(_missing_message())

    emit = on_event or (lambda e: None)
    full_task = SAFETY_PREFIX + task.strip()

    emit({"type": "browse_start", "task": task.strip(), "max_steps": max_steps})

    llm = _make_llm()
    browser = Browser("chromium", headless=headless)

    agent = Agent(
        full_task,
        llm=llm,
        browser=browser,
    )

    # browser-use 1.0 exposes run_sync / async run.
    # We prefer the sync helper for the CLI; the web layer uses the same path
    # inside a worker thread so the SSE generator stays responsive.
    try:
        result = agent.run_sync(max_steps=max_steps)
    except TypeError:
        # Some versions only expose async run()
        result = asyncio.get_event_loop().run_until_complete(
            agent.run(max_steps=max_steps)
        )

    # Collect a human-readable summary
    final = None
    if hasattr(result, "final_result"):
        try:
            final = result.final_result()
        except Exception:
            final = None
    if not final and hasattr(result, "history"):
        # fall back to last action / status string
        final = str(getattr(result, "status", None) or result)

    text = (final or "(browser agent finished with no text result)").strip()
    emit({"type": "browse_done", "result": text})
    return text


def run_browser_task(
    task: str,
    *,
    max_steps: int = 40,
    headless: bool = True,
    on_event: Optional[EventCallback] = None,
) -> str:
    """Public entry point used by the CLI and the web UI."""
    return _run_sync(
        task, max_steps=max_steps, headless=headless, on_event=on_event
    )
