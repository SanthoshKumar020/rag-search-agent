"""Shared configuration for the Search + RAG Reply Agent.

All secrets come from environment variables (loaded from .env via
python-dotenv). Nothing is hardcoded.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()

INDEX_DIR = BASE_DIR / "index"
KNOWLEDGE_DIR = BASE_DIR / "knowledge"

# Search / retrieval tuning
SEARCH_RESULTS = 5          # web results to fetch per ask/reply
CHUNK_SIZE = 800            # characters per chunk
CHUNK_OVERLAP = 120         # characters of overlap between chunks
TOP_K = 5                   # chunks retrieved per query

# Models (all free-tier)
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
# The tool-using agent loop needs a model that reliably emits plain JSON
# tool calls; gpt-oss models loop / return empty steps there, while
# qwen3.8-27b follows the JSON protocol (same lesson as BROWSE_MODEL).
AGENT_MODEL = os.environ.get("AGENT_MODEL", "qwen/qwen3.8-27b")
HF_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
HF_CHAT_MODEL = "meta-llama/Llama-3.1-8B-Instruct"


def embed_backend() -> str:
    """Which embedding backend will be used."""
    return "huggingface" if HF_TOKEN else "tfidf"


def llm_backend() -> str:
    """Which LLM backend will be used ('groq', 'huggingface' or 'none')."""
    if GROQ_API_KEY:
        return "groq"
    if HF_TOKEN:
        return "huggingface"
    return "none"
