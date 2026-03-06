"""Unified credential store for llmeter — single auth.json for all providers.

Stores all OAuth credentials in ~/.config/llmeter/auth.json with the schema:

{
  "anthropic": {
    "type": "oauth",
    "refresh": "xxxx",
    "access": "xxxx",
    "expires": 1771144949262
  },
  "openai-codex": {
    "type": "oauth",
    "access": "xxxx",
    "refresh": "xxxx",
    "expires": 1771810754548,
    "accountId": "xxxx"
  },
  "google-gemini-cli": {
    "type": "oauth",
    "refresh": "xxxx",
    "access": "xxxx",
    "expires": 1771168794661,
    "projectId": "xxxx",
    "email": "xxxx"
  }
}

Each provider stores credentials under its provider key.
Timestamps are in milliseconds since epoch.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

from .providers.helpers import config_dir

# 5-minute safety buffer before actual expiry
EXPIRY_BUFFER_MS = 5 * 60 * 1000


def _auth_path() -> Path:
    """Return the path to the unified auth.json file."""
    return config_dir("auth.json")


def now_ms() -> int:
    """Current time in milliseconds since epoch."""
    return int(time.time() * 1000)


# ── Read / Write ───────────────────────────────────────────


def load_all() -> dict[str, dict]:
    """Load the entire auth.json, returning {} if missing or corrupt."""
    path = _auth_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_all(data: dict[str, dict]) -> None:
    """Write the entire auth.json atomically with restricted permissions."""
    path = _auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp_path, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# ── Per-provider operations ────────────────────────────────


VALID_TYPES = {"oauth", "cookie", "api_key"}


def load_provider(provider_id: str) -> Optional[dict]:
    """Load credentials for a single provider, or None."""
    data = load_all()
    creds = data.get(provider_id)
    if isinstance(creds, dict) and creds.get("type") in VALID_TYPES:
        return creds
    return None


def save_provider(provider_id: str, creds: dict) -> None:
    """Save credentials for a single provider (merges into auth.json)."""
    data = load_all()
    data[provider_id] = creds
    _save_all(data)


def clear_provider(provider_id: str) -> None:
    """Remove credentials for a single provider."""
    data = load_all()
    if provider_id in data:
        del data[provider_id]
        _save_all(data)


def is_expired(creds: dict) -> bool:
    """Check if credentials have expired (with buffer)."""
    expires = creds.get("expires", 0)
    return now_ms() >= expires


# ── API key helpers ────────────────────────────────────────


def load_api_key(provider_id: str) -> Optional[str]:
    """Return the stored API key for *provider_id*, or None."""
    data = load_all()
    creds = data.get(provider_id)
    if isinstance(creds, dict) and creds.get("type") == "api_key":
        key = creds.get("api_key", "")
        return key.strip() or None
    return None


def save_api_key(provider_id: str, api_key: str) -> None:
    """Persist an API key for *provider_id* in auth.json."""
    save_provider(provider_id, {"type": "api_key", "api_key": api_key})


def clear_api_key(provider_id: str) -> None:
    """Remove the stored API key for *provider_id*."""
    clear_provider(provider_id)
