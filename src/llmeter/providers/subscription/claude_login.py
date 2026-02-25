"""Claude interactive login — PKCE-based OAuth flow."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import webbrowser
from urllib.parse import urlencode

import aiohttp

from ... import auth
from ..helpers import http_debug_log
from .base import LoginProvider
from .claude import (
    CLIENT_ID,
    TOKEN_URL,
    REDIRECT_URI,
    SCOPES,
    PROVIDER_ID,
    _now_ms,
    save_credentials,
)

AUTHORIZE_URL = "https://claude.ai/oauth/authorize"


# ── PKCE helpers ───────────────────────────────────────────

def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _generate_pkce() -> tuple[str, str]:
    verifier_bytes = os.urandom(32)
    verifier = _base64url_encode(verifier_bytes)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = _base64url_encode(digest)
    return verifier, challenge


# ── Login class ────────────────────────────────────────────

class ClaudeLogin(LoginProvider):
    """PKCE-based OAuth login flow for Claude."""

    @property
    def provider_id(self) -> str:
        return "claude"

    def interactive_login(self) -> dict:
        """Open browser for Anthropic OAuth, exchange code, persist tokens."""
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
            "type": "oauth",
            "refresh": token_data["refresh_token"],
            "access": token_data["access_token"],
            "expires": _now_ms() + token_data["expires_in"] * 1000 - auth.EXPIRY_BUFFER_MS,
        }
        save_credentials(creds)
        print(f"✓ Claude OAuth credentials saved to {auth._auth_path()}")
        return creds


async def _exchange_code(payload: dict, timeout: float = 30.0) -> dict:
    headers = {"Content-Type": "application/json"}
    http_debug_log(
        "claude-oauth", "token_exchange_request",
        method="POST", url=TOKEN_URL, headers=headers, payload=payload,
    )
    async with aiohttp.ClientSession() as session:
        async with session.post(
            TOKEN_URL, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            http_debug_log(
                "claude-oauth", "token_exchange_response",
                method="POST", url=TOKEN_URL, status=resp.status,
            )
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"HTTP {resp.status}: {body[:300]}")
            return await resp.json()


# Module-level singleton — __main__.py imports this directly.
interactive_login = ClaudeLogin().interactive_login
