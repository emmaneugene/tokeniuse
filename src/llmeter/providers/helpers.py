"""Shared helpers for llmeter providers."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import aiohttp


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
        event["headers"] = dict(headers)
    if payload:
        event["payload"] = dict(payload)
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


# ── HTTP helpers ──────────────────────────────────────────

_BODY_PREVIEW = 200  # chars to include in default error messages


async def _http_request(
    method: str,
    provider: str,
    url: str,
    headers: dict,
    timeout: float,
    *,
    label: str,
    errors: dict[int, str],
    params: dict | None = None,
    payload: dict | None = None,
    session: aiohttp.ClientSession,
) -> dict:
    """Shared core for http_get / http_post: log, request, validate, parse."""
    http_debug_log(
        provider, f"{label}_request",
        method=method, url=url, headers=headers, payload=payload or params,
    )
    request_kwargs: dict = {
        "headers": headers,
        "timeout": aiohttp.ClientTimeout(total=timeout),
    }
    if params is not None:
        request_kwargs["params"] = params
    if payload is not None:
        request_kwargs["json"] = payload

    async with session.request(method, url, **request_kwargs) as resp:
        http_debug_log(
            provider, f"{label}_response",
            method=method, url=url, status=resp.status,
        )
        if resp.status in errors:
            raise RuntimeError(errors[resp.status])
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"HTTP {resp.status}: {body[:_BODY_PREVIEW]}")
        try:
            return await resp.json(content_type=None)
        except (json.JSONDecodeError, ValueError) as exc:
            ct = resp.headers.get("Content-Type", "unknown")
            raise RuntimeError(
                f"Expected JSON but got {ct!r} (HTTP {resp.status})"
            ) from exc


async def http_get(
    provider: str,
    url: str,
    headers: dict,
    timeout: float,
    *,
    label: str = "request",
    errors: dict[int, str] | None = None,
    params: dict | None = None,
    session: aiohttp.ClientSession | None = None,
) -> dict:
    """GET a JSON endpoint with debug logging and standard error handling.

    Raises RuntimeError on non-2xx responses.  If *session* is provided it
    is used as-is and not closed; otherwise a fresh session is created and
    closed after the request.  *errors* maps HTTP status codes to custom
    error messages; unmatched non-200 statuses fall back to
    ``"HTTP {status}: {body[:200]}"``.
    """
    close_session = session is None
    if close_session:
        session = aiohttp.ClientSession()
    try:
        return await _http_request(
            "GET", provider, url, headers, timeout,
            label=label, errors=errors or {}, params=params, session=session,
        )
    finally:
        if close_session:
            await session.close()


async def http_post(
    provider: str,
    url: str,
    headers: dict,
    payload: dict,
    timeout: float,
    *,
    label: str = "request",
    errors: dict[int, str] | None = None,
    session: aiohttp.ClientSession | None = None,
) -> dict:
    """POST a JSON payload and return the JSON response.

    Raises RuntimeError on non-2xx responses.  If *session* is provided it
    is used as-is and not closed; otherwise a fresh session is created and
    closed after the request.
    """
    close_session = session is None
    if close_session:
        session = aiohttp.ClientSession()
    try:
        return await _http_request(
            "POST", provider, url, headers, timeout,
            label=label, errors=errors or {}, payload=payload, session=session,
        )
    finally:
        if close_session:
            await session.close()
