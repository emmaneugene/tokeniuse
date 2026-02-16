"""Shared helpers for llmeter providers."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional


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


# ── Debug logging ─────────────────────────────────────────


def _debug_enabled() -> bool:
    """Return True if HTTP debug logging is enabled via env var."""
    raw = os.environ.get("LLMETER_DEBUG_HTTP", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _debug_log_path() -> Path:
    """Path to debug log file.

    Override with LLMETER_DEBUG_LOG_PATH, otherwise defaults to
    ~/.config/llmeter/debug.log.
    """
    custom = os.environ.get("LLMETER_DEBUG_LOG_PATH", "").strip()
    if custom:
        return Path(custom).expanduser()
    return config_dir("debug.log")


def _redact_mapping(data: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a plain copy of a mapping for debug logs."""
    if not data:
        return {}
    return dict(data)


def http_debug_log(
    provider: str,
    phase: str,
    *,
    method: str,
    url: str,
    status: int | None = None,
    headers: Mapping[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
    message: str | None = None,
) -> None:
    """Write one JSON log event for HTTP debug traces."""
    if not _debug_enabled():
        return

    event: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "phase": phase,
        "method": method,
        "url": url,
    }
    if status is not None:
        event["status"] = status
    if headers:
        event["headers"] = _redact_mapping(headers)
    if payload:
        event["payload"] = _redact_mapping(payload)
    if message:
        event["message"] = message

    path = _debug_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError:
        # Debug logging must never break normal provider behavior.
        pass
