"""User settings store (v5) — DATA_DIR/settings.json.

Small, explicit set of preferences the web UI can change:
  model          ask/reply model override (must be in
                 config.WEB_MODELS; None = server default)
  max_steps      agent step budget for web runs (1..20, default 8)
  theme          "dark" | "light" (UI default; the browser also
                 remembers its own choice in localStorage)
  webhook_token  optional token for POST /api/webhook/run. When set
                 here it takes precedence over the AGENT_WEBHOOK_TOKEN
                 env var. Stored in the local data dir only — on
                 hosted read-only servers it is ephemeral, like
                 memory, and the env var is the durable option.

Secrets hygiene: the token is never returned in full by the API —
only a masked form and a boolean "is set".
"""
from __future__ import annotations

import json
from typing import Any

import config

SETTINGS_PATH = config.DATA_DIR / "settings.json"

DEFAULTS: dict[str, Any] = {
    "model": None,
    "max_steps": 8,
    "theme": "dark",
    "webhook_token": "",
}

MAX_STEPS_LIMIT = 20


def _load() -> dict[str, Any]:
    data = dict(DEFAULTS)
    if SETTINGS_PATH.exists():
        try:
            stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                data.update({k: stored[k] for k in DEFAULTS if k in stored})
        except (json.JSONDecodeError, OSError):
            pass
    return data


def _save(data: dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def get(key: str) -> Any:
    return _load().get(key, DEFAULTS.get(key))


def all_settings() -> dict[str, Any]:
    return _load()


def max_steps() -> int:
    try:
        value = int(_load().get("max_steps") or DEFAULTS["max_steps"])
    except (TypeError, ValueError):
        value = DEFAULTS["max_steps"]
    return max(1, min(MAX_STEPS_LIMIT, value))


def webhook_token() -> str:
    """Effective webhook token: settings value wins, else env var."""
    import os

    token = str(_load().get("webhook_token") or "").strip()
    return token or os.environ.get("AGENT_WEBHOOK_TOKEN", "").strip()


def update(changes: dict[str, Any]) -> dict[str, Any]:
    """Validate + persist a partial update. Raises ValueError."""
    data = _load()
    if "model" in changes:
        model = changes["model"]
        if model in (None, ""):
            data["model"] = None
        elif model in config.WEB_MODELS:
            data["model"] = model
        else:
            raise ValueError(f"Model not in the allowlist: {model}")
    if "max_steps" in changes:
        try:
            steps = int(changes["max_steps"])
        except (TypeError, ValueError):
            raise ValueError("max_steps must be a number.")
        if not 1 <= steps <= MAX_STEPS_LIMIT:
            raise ValueError(f"max_steps must be between 1 and "
                             f"{MAX_STEPS_LIMIT}.")
        data["max_steps"] = steps
    if "theme" in changes:
        theme = str(changes["theme"]).strip().lower()
        if theme not in ("dark", "light"):
            raise ValueError("theme must be 'dark' or 'light'.")
        data["theme"] = theme
    if "webhook_token" in changes:
        token = str(changes["webhook_token"] or "").strip()
        if len(token) > 200:
            raise ValueError("webhook_token is too long (max 200 chars).")
        data["webhook_token"] = token
    _save(data)
    return data


def _mask(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 4:
        return "••••"
    return token[:2] + "••••" + token[-2:]


def public_view() -> dict[str, Any]:
    """Settings as the API may show them — token masked."""
    data = _load()
    token = str(data.get("webhook_token") or "")
    import os

    env_set = bool(os.environ.get("AGENT_WEBHOOK_TOKEN", "").strip())
    return {
        "model": data.get("model"),
        "models": list(config.WEB_MODELS),
        "max_steps": max_steps(),
        "max_steps_limit": MAX_STEPS_LIMIT,
        "theme": data.get("theme") or "dark",
        "webhook_token_set": bool(token) or env_set,
        "webhook_token_masked": _mask(token),
        "webhook_token_source": ("settings" if token
                                 else "env" if env_set else "none"),
    }
