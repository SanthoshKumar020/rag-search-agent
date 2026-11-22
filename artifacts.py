"""Artifacts library — save and list agent outputs (docs, code, notes).

Inspired by Muse's library of generated documents and interactive outputs.
Files are stored under data/artifacts/.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

ARTIFACTS_DIR = config.BASE_DIR / "data" / "artifacts"
INDEX_PATH = ARTIFACTS_DIR / "index.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(title: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (title or "artifact").strip().lower())
    return (s.strip("-") or "artifact")[:60]


def _load_index() -> list[dict[str, Any]]:
    if not INDEX_PATH.exists():
        return []
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_index(items: list[dict[str, Any]]) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps(items, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def save(title: str, content: str, *, kind: str = "markdown") -> dict[str, Any]:
    title = (title or "Untitled").strip()
    content = content or ""
    ext = {
        "markdown": ".md",
        "text": ".txt",
        "code": ".txt",
        "json": ".json",
    }.get(kind, ".md")
    aid = str(uuid.uuid4())[:8]
    filename = f"{aid}-{_slug(title)}{ext}"
    path = ARTIFACTS_DIR / filename
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    meta = {
        "id": aid,
        "title": title,
        "kind": kind,
        "path": str(path.relative_to(config.BASE_DIR)),
        "chars": len(content),
        "created_at": _now(),
    }
    items = _load_index()
    items.insert(0, meta)
    _save_index(items[:200])
    return meta


def list_artifacts(limit: int = 20) -> list[dict[str, Any]]:
    return _load_index()[:limit]


def get(artifact_id: str) -> dict[str, Any] | None:
    for item in _load_index():
        if item.get("id") == artifact_id:
            path = config.BASE_DIR / item["path"]
            if path.exists():
                item = dict(item)
                item["content"] = path.read_text(encoding="utf-8")
            return item
    return None
