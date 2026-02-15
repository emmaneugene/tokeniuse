"""Anthropic OAuth flow for llmeter — login once, auto-refresh forever.

Implements the same PKCE-based OAuth flow that pi-mono uses, storing
credentials in ~/.config/llmeter/claude_oauth.json.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional

import aiohttp

# OAuth constants — Anthropic's shared public OAuth client ID,
# used by Claude Code CLI, pi-mono, and llmeter alike.
_CLIENT_ID_B64 = "OWQxYzI1MGEtZTYxYi00NGQ5LTg4ZWQtNTk0NGQxOTYyZjVl"
CLIENT_ID = base64.b64decode(_CLIENT_ID_B64).decode()
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
SCOPES = "org:create_api_key user:profile user:inference"

# 5-minute safety buffer before actual expiry
_EXPIRY_BUFFER_MS = 5 * 60 * 1000


def _creds_path() -> Path:
    """Path to llmeter's own Claude OAuth credentials file."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "llmeter" / "claude_oauth.json"


# ── PKCE helpers ───────────────────────────────────────────

def _base64url_encode(data: bytes) -> str:
    """Encode bytes as base64url string (no padding), matching pi-mono's impl."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _generate_pkce() -> tuple[str, str]:
    """Generate a PKCE verifier and challenge pair.

    Matches pi-mono's implementation: 32 random bytes → base64url verifier,
    then SHA-256 of the verifier string → base64url challenge.
    """
    verifier_bytes = os.urandom(32)
    verifier = _base64url_encode(verifier_bytes)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = _base64url_encode(digest)
    return verifier, challenge


# ── Credential persistence ─────────────────────────────────

def load_credentials() -> Optional[dict]:
    """Load llmeter's own Claude OAuth credentials from disk.

    Returns dict with keys: access_token, refresh_token, expires_at (ms epoch).
    """
    path = _creds_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if data.get("access_token") and data.get("refresh_token"):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def save_credentials(creds: dict) -> None:
    """Persist credentials to disk with restricted permissions."""
    path = _creds_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(creds, indent=2) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def clear_credentials() -> None:
    """Remove stored credentials."""
    path = _creds_path()
    if path.exists():
        path.unlink()


def is_token_expired(creds: dict) -> bool:
    """Check if the access token has expired (with buffer)."""
    expires_at = creds.get("expires_at", 0)
    return _now_ms() >= expires_at


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── OAuth login flow ───────────────────────────────────────

def interactive_login() -> dict:
    """Run the interactive OAuth login flow.

    Opens a browser for authorization, then prompts the user to paste
    the authorization code.  Returns persisted credentials dict.
    """
    import webbrowser
    from urllib.parse import urlencode

    verifier, challenge = _generate_pkce()

    params = urlencode({
        "code": "true",
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
    })
    auth_url = f"{AUTHORIZE_URL}?{params}"

    print()
    print("Opening browser for Anthropic OAuth login…")
    print(f"If it doesn't open, visit:\n  {auth_url}")
    print()

    webbrowser.open(auth_url)

    raw = input("Paste the authorization code here: ").strip()
    if not raw:
        raise RuntimeError("No authorization code provided.")

    parts = raw.split("#")
    code = parts[0]
    state = parts[1] if len(parts) > 1 else None

    # Exchange code for tokens
    import asyncio

    payload: dict = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    }
    if state:
        payload["state"] = state

    try:
        token_data = asyncio.run(_exchange_code(payload))
    except Exception as e:
        raise RuntimeError(f"Token exchange failed: {e}") from e

    creds = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data["refresh_token"],
        "expires_at": _now_ms() + token_data["expires_in"] * 1000 - _EXPIRY_BUFFER_MS,
    }

    save_credentials(creds)
    print(f"✓ Claude OAuth credentials saved to {_creds_path()}")
    return creds


async def _exchange_code(payload: dict, timeout: float = 30.0) -> dict:
    """POST the token exchange request using aiohttp (avoids urllib User-Agent issues)."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            TOKEN_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"HTTP {resp.status}: {body[:300]}")
            return await resp.json()


# ── Token refresh ──────────────────────────────────────────

async def refresh_access_token(creds: dict, timeout: float = 30.0) -> dict:
    """Use the refresh token to obtain a new access token.

    Updates and persists the credentials on success.
    Raises RuntimeError on failure.
    """
    refresh_token = creds.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("No refresh token available — run `llmeter --login-claude`.")

    payload = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            TOKEN_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(
                    f"Token refresh failed (HTTP {resp.status}): {body[:200]}"
                )
            token_data = await resp.json()

    new_creds = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token", refresh_token),
        "expires_at": _now_ms() + token_data["expires_in"] * 1000 - _EXPIRY_BUFFER_MS,
    }

    save_credentials(new_creds)
    return new_creds


# ── High-level: get a valid access token ───────────────────

async def get_valid_access_token(timeout: float = 30.0) -> Optional[str]:
    """Load credentials, refresh if expired, return access token or None.

    This is the main entry point for the Claude provider.
    """
    creds = load_credentials()
    if creds is None:
        return None

    if is_token_expired(creds):
        try:
            creds = await refresh_access_token(creds, timeout=timeout)
        except RuntimeError:
            return None

    return creds.get("access_token")
