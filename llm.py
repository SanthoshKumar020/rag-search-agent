"""LLM generation on free tiers, with a fallback chain (v4).

Order of preference:
  1. Groq (OpenAI-compatible) — the requested model first, then the
     remaining allowlisted models in config.WEB_MODELS order
  2. HuggingFace inference router chat model, needs HF_TOKEN
  3. None — caller falls back to printing retrieved evidence.

Pattern adapted from Hermes Agent's provider fallback chains: when
the primary model is exhausted (free-tier 429s are the usual cause),
walk an ordered chain of backups instead of failing the run, with one
jittered backoff pause between attempts. Only retryable failures
(429, 5xx, network errors) advance the chain; a 4xx like a bad
request is raised immediately.
"""
import random
import time

import requests

import config

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
HF_ROUTER_URL = "https://router.huggingface.co/v1/chat/completions"

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _groq_chain(model: str | None) -> list[str]:
    """Ordered Groq models to try: requested first, then allowlist."""
    chain: list[str] = []
    for m in [model or config.GROQ_MODEL, *config.WEB_MODELS]:
        if m and m not in chain:
            chain.append(m)
    return chain


def generate(system: str, user: str, model: str | None = None) -> str | None:
    """Return generated text, or None if no LLM key is configured.

    `model` optionally overrides the Groq model (used by the agent loop,
    which needs a model that follows its JSON tool protocol). On a
    retryable failure the next model in the fallback chain is tried;
    the last error is re-raised if the whole chain fails."""
    backend = config.llm_backend()
    if backend == "groq":
        last_exc: Exception | None = None
        for i, m in enumerate(_groq_chain(model)):
            if i:
                time.sleep(random.uniform(0.6, 1.6))  # jittered backoff
            try:
                return _chat(GROQ_URL, config.GROQ_API_KEY, m,
                             system, user)
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response else None
                if status not in _RETRYABLE_STATUS:
                    raise
                last_exc = exc
            except requests.RequestException as exc:
                last_exc = exc
        assert last_exc is not None
        raise last_exc
    if backend == "huggingface":
        return _chat(HF_ROUTER_URL, config.HF_TOKEN, config.HF_CHAT_MODEL,
                     system, user)
    return None


def _chat(url: str, key: str, model: str, system: str, user: str) -> str:
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()
