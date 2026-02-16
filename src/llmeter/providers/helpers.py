"""Shared helpers for llmeter providers."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


def config_dir(*parts: str) -> Path:
    """Return a path under the llmeter XDG config directory.

    >>> config_dir("auth.json")
    PosixPath('/home/user/.config/llmeter/auth.json')
    """
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "llmeter" / Path(*parts) if parts else base / "llmeter"


def parse_iso8601(s: str | None) -> Optional[datetime]:
    """Parse an ISO 8601 datetime string, returning None on failure.

    Handles the common ``"Z"`` suffix that Python < 3.11 can't parse
    natively via ``fromisoformat``.
    """
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def decode_jwt_payload(token: str) -> Optional[dict]:
    """Decode a JWT payload without signature verification.

    Uses only the standard library (base64 + json).
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        # base64url → standard base64, add padding
        payload = parts[1].replace("-", "+").replace("_", "/")
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.b64decode(payload))
    except Exception:
        return None
