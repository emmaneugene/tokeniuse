"""Codex interactive login — PKCE + local callback server OAuth flow."""

from __future__ import annotations

import hashlib
import json
import secrets
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread, Event
from typing import Optional
from urllib.parse import urlencode, urlparse, parse_qs

from ... import auth
from .base import LoginProvider
from .codex import (
    CLIENT_ID,
    TOKEN_URL,
    REDIRECT_URI,
    SCOPES,
    save_credentials,
    extract_account_id,
    extract_email,
)

AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"


# ── PKCE helpers ───────────────────────────────────────────

def _generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    import base64
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ── Local callback server ──────────────────────────────────

class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    received_code: Optional[str] = None
    expected_state: str = ""
    code_event: Event

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        params = parse_qs(parsed.query)
        state = (params.get("state") or [None])[0]
        code = (params.get("code") or [None])[0]

        if state != self.expected_state:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"State mismatch")
            return

        if not code:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing authorization code")
            return

        _OAuthCallbackHandler.received_code = code
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body><p>Authentication successful. "
            b"Return to your terminal to continue.</p></body></html>"
        )
        _OAuthCallbackHandler.code_event.set()

    def log_message(self, format, *args) -> None:  # noqa: A002
        pass


def _start_callback_server(state: str) -> tuple[Optional[HTTPServer], Event]:
    code_event = Event()
    _OAuthCallbackHandler.received_code = None
    _OAuthCallbackHandler.expected_state = state
    _OAuthCallbackHandler.code_event = code_event
    try:
        server = HTTPServer(("127.0.0.1", 1455), _OAuthCallbackHandler)
        Thread(target=server.serve_forever, daemon=True).start()
        return server, code_event
    except OSError:
        return None, code_event


# ── Login class ────────────────────────────────────────────

class CodexLogin(LoginProvider):
    """PKCE + local callback server OAuth login flow for Codex."""

    @property
    def provider_id(self) -> str:
        return "codex"

    def interactive_login(self) -> dict:
        """Open browser, capture OAuth callback, exchange code, persist tokens."""
        verifier, challenge = _generate_pkce()
        state = secrets.token_hex(16)

        params = urlencode({
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "llmeter",
        })
        auth_url = f"{AUTHORIZE_URL}?{params}"

        server, code_event = _start_callback_server(state)

        print()
        print("Opening browser for OpenAI Codex OAuth login…")
        if not server:
            print("(Port 1455 is busy — you'll need to paste the code manually.)")
        print(f"If the browser doesn't open, visit:\n  {auth_url}")
        print()

        webbrowser.open(auth_url)

        code: Optional[str] = None
        if server:
            code_event.wait(timeout=60)
            server.shutdown()
            code = _OAuthCallbackHandler.received_code

        if not code:
            raw = input("Paste the authorization code or full redirect URL: ").strip()
            if not raw:
                raise RuntimeError("No authorization code provided.")
            code = _parse_auth_input(raw, state)

        if not code:
            raise RuntimeError("Failed to extract authorization code.")

        creds = _exchange_code_sync(code, verifier)
        save_credentials(creds)
        print(f"✓ Codex OAuth credentials saved to {auth._auth_path()}")
        return creds


def _parse_auth_input(raw: str, expected_state: str) -> Optional[str]:
    raw = raw.strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
        params = parse_qs(parsed.query)
        code = (params.get("code") or [None])[0]
        state = (params.get("state") or [None])[0]
        if state and state != expected_state:
            raise RuntimeError("State mismatch")
        if code:
            return code
    except RuntimeError:
        raise
    except Exception:
        pass
    if "#" in raw:
        return raw.split("#", 1)[0]
    return raw


def _exchange_code_sync(code: str, verifier: str) -> dict:
    import urllib.request

    body = urlencode({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    }).encode()

    req = urllib.request.Request(
        TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            token_data = json.loads(resp.read())
    except Exception as e:
        raise RuntimeError(f"Token exchange failed: {e or type(e).__name__}") from e

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")

    if not access_token or not refresh_token or not isinstance(expires_in, (int, float)):
        raise RuntimeError("Token response missing required fields.")

    account_id = extract_account_id(access_token)
    if not account_id:
        raise RuntimeError("Failed to extract accountId from access token.")

    id_token = token_data.get("id_token")
    email = extract_email(id_token) if id_token else None

    creds: dict = {
        "type": "oauth",
        "access": access_token,
        "refresh": refresh_token,
        "expires": auth.now_ms() + int(expires_in) * 1000 - auth.EXPIRY_BUFFER_MS,
        "accountId": account_id,
    }
    if email:
        creds["email"] = email
    return creds


# Module-level singleton — __main__.py imports this directly.
interactive_login = CodexLogin().interactive_login
