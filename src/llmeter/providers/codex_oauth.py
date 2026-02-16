"""OpenAI Codex OAuth flow for llmeter — login once, auto-refresh forever.

Implements the same PKCE-based OAuth flow that Codex CLI / pi-mono uses,
with a local HTTP callback server on port 1455.  Credentials are stored
in the unified ~/.config/llmeter/auth.json under "openai-codex".
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread, Event
from typing import Optional
from urllib.parse import urlencode, urlparse, parse_qs

import aiohttp

from .. import auth
from .helpers import decode_jwt_payload

# OAuth constants (same OpenAI Codex OAuth app as Codex CLI / pi-mono)
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REDIRECT_URI = "http://localhost:1455/auth/callback"
SCOPES = "openid profile email offline_access"

# JWT claim path for account ID
JWT_CLAIM_PATH = "https://api.openai.com/auth"

PROVIDER_ID = "openai-codex"

# 5-minute safety buffer before actual expiry
_EXPIRY_BUFFER_MS = 5 * 60 * 1000


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── PKCE helpers ───────────────────────────────────────────

def _generate_pkce() -> tuple[str, str]:
    """Generate a PKCE verifier and challenge pair."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def extract_account_id(access_token: str) -> Optional[str]:
    """Extract the chatgpt_account_id from an OpenAI access token JWT."""
    payload = decode_jwt_payload(access_token)
    if not payload:
        return None
    auth_claim = payload.get(JWT_CLAIM_PATH)
    if isinstance(auth_claim, dict):
        account_id = auth_claim.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id:
            return account_id
    return None


def extract_email(id_token: str) -> Optional[str]:
    """Extract email from an OpenAI id_token JWT.

    Checks top-level ``email`` claim first, then falls back to
    ``https://api.openai.com/profile.email`` (same as CodexBar).
    """
    payload = decode_jwt_payload(id_token)
    if not payload:
        return None
    # Direct email claim
    email = payload.get("email")
    if isinstance(email, str) and email:
        return email.strip()
    # Nested under profile claim
    profile = payload.get("https://api.openai.com/profile")
    if isinstance(profile, dict):
        email = profile.get("email")
        if isinstance(email, str) and email:
            return email.strip()
    return None


# ── Credential persistence (unified auth.json) ────────────

def load_credentials() -> Optional[dict]:
    """Load Codex OAuth credentials from the unified auth store.

    Returns dict with keys: access, refresh, expires (ms epoch), accountId.
    """
    creds = auth.load_provider(PROVIDER_ID)
    if creds and creds.get("access") and creds.get("refresh") and creds.get("accountId"):
        return creds
    return None


def save_credentials(creds: dict) -> None:
    """Persist credentials to the unified auth store."""
    auth.save_provider(PROVIDER_ID, creds)


def clear_credentials() -> None:
    """Remove stored credentials."""
    auth.clear_provider(PROVIDER_ID)


def is_token_expired(creds: dict) -> bool:
    """Check if the access token has expired (with buffer)."""
    return auth.is_expired(creds)


# ── Local OAuth callback server ────────────────────────────

class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth callback code."""

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
        """Suppress default HTTP server logging."""
        pass


def _start_callback_server(state: str) -> tuple[Optional[HTTPServer], Event]:
    """Start a local HTTP server on port 1455 to receive the OAuth callback."""
    code_event = Event()
    _OAuthCallbackHandler.received_code = None
    _OAuthCallbackHandler.expected_state = state
    _OAuthCallbackHandler.code_event = code_event

    try:
        server = HTTPServer(("127.0.0.1", 1455), _OAuthCallbackHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, code_event
    except OSError:
        return None, code_event


# ── OAuth login flow ───────────────────────────────────────

def interactive_login() -> dict:
    """Run the interactive OAuth login flow.

    Opens a browser, listens on localhost:1455 for the callback,
    falls back to manual paste if the port is busy.
    Returns persisted credentials dict.
    """
    import webbrowser

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

    # Start local callback server
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
        # Wait up to 60 seconds for the callback
        code_event.wait(timeout=60)
        server.shutdown()
        code = _OAuthCallbackHandler.received_code

    if not code:
        # Fall back to manual paste
        raw = input("Paste the authorization code or full redirect URL: ").strip()
        if not raw:
            raise RuntimeError("No authorization code provided.")
        code = _parse_auth_input(raw, state)

    if not code:
        raise RuntimeError("Failed to extract authorization code.")

    # Exchange code for tokens
    creds = _exchange_code_sync(code, verifier)
    save_credentials(creds)
    print(f"✓ Codex OAuth credentials saved to {auth._auth_path()}")
    return creds


def _parse_auth_input(raw: str, expected_state: str) -> Optional[str]:
    """Parse the user's pasted input — might be a code, URL, or code#state."""
    raw = raw.strip()
    if not raw:
        return None

    # Try as URL
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

    # Try code#state format
    if "#" in raw:
        parts = raw.split("#", 1)
        return parts[0]

    # Plain code
    return raw


def _exchange_code_sync(code: str, verifier: str) -> dict:
    """Exchange authorization code for tokens (synchronous for CLI)."""
    import urllib.request

    body = urlencode({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    }).encode()

    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            token_data = json.loads(resp.read())
    except Exception as e:
        raise RuntimeError(f"Token exchange failed: {e}") from e

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")

    if not access_token or not refresh_token or not isinstance(expires_in, (int, float)):
        raise RuntimeError("Token response missing required fields.")

    account_id = extract_account_id(access_token)
    if not account_id:
        raise RuntimeError("Failed to extract accountId from access token.")

    # Extract email from id_token if available
    id_token = token_data.get("id_token")
    email = extract_email(id_token) if id_token else None

    creds = {
        "type": "oauth",
        "access": access_token,
        "refresh": refresh_token,
        "expires": _now_ms() + int(expires_in) * 1000 - _EXPIRY_BUFFER_MS,
        "accountId": account_id,
    }
    if email:
        creds["email"] = email
    return creds


# ── Token refresh ──────────────────────────────────────────

async def refresh_access_token(creds: dict, timeout: float = 30.0) -> dict:
    """Use the refresh token to obtain a new access token.

    Updates and persists the credentials on success.
    Raises RuntimeError on failure.
    """
    refresh_token = creds.get("refresh")
    if not refresh_token:
        raise RuntimeError("No refresh token available — run `llmeter --login-codex`.")

    payload = urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
    })

    async with aiohttp.ClientSession() as session:
        async with session.post(
            TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(
                    f"Token refresh failed (HTTP {resp.status}): {body[:200]}"
                )
            token_data = await resp.json()

    access_token = token_data.get("access_token")
    new_refresh = token_data.get("refresh_token", refresh_token)
    expires_in = token_data.get("expires_in")

    if not access_token or not isinstance(expires_in, (int, float)):
        raise RuntimeError("Token refresh response missing required fields.")

    account_id = extract_account_id(access_token) or creds.get("accountId")

    # Extract email from id_token if available
    id_token = token_data.get("id_token")
    email = extract_email(id_token) if id_token else creds.get("email")

    new_creds = {
        "type": "oauth",
        "access": access_token,
        "refresh": new_refresh,
        "expires": _now_ms() + int(expires_in) * 1000 - _EXPIRY_BUFFER_MS,
        "accountId": account_id,
    }
    if email:
        new_creds["email"] = email

    save_credentials(new_creds)
    return new_creds


# ── High-level: get valid credentials ──────────────────────

async def get_valid_credentials(timeout: float = 30.0) -> Optional[dict]:
    """Load credentials, refresh if expired, return full creds dict or None.

    Returns dict with access and accountId on success.
    """
    creds = load_credentials()
    if creds is None:
        return None

    if is_token_expired(creds):
        try:
            creds = await refresh_access_token(creds, timeout=timeout)
        except RuntimeError:
            return None

    return creds
