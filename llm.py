"""LLM generation on free tiers.

Order of preference:
  1. Groq (OpenAI-compatible) — openai/gpt-oss-120b by default
     (override with GROQ_MODEL), needs GROQ_API_KEY
  2. HuggingFace inference router chat model, needs HF_TOKEN
  3. None — caller falls back to printing retrieved evidence.
"""
import requests

import config

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
HF_ROUTER_URL = "https://router.huggingface.co/v1/chat/completions"


def generate(system: str, user: str, model: str | None = None) -> str | None:
    """Return generated text, or None if no LLM key is configured.

    `model` optionally overrides the Groq model (used by the agent loop,
    which needs a model that follows its JSON tool protocol)."""
    backend = config.llm_backend()
    if backend == "groq":
        return _chat(GROQ_URL, config.GROQ_API_KEY,
                     model or config.GROQ_MODEL, system, user)
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
