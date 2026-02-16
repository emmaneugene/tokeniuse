"""Google Gemini CLI OAuth flow for llmeter — self-contained, no gemini binary needed.

Implements the same PKCE-based OAuth flow that pi-mono uses for Cloud Code Assist.
Uses a local HTTP callback server on port 8085.
Credentials are stored in the unified ~/.config/llmeter/auth.json under "google-gemini-cli".

OAuth client ID / secret are the same public values embedded in the Gemini CLI npm package
(@google/gemini-cli-core/dist/src/code_assist/oauth2.js) and in pi-mono's implementation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread, Event
from typing import Optional
from urllib.parse import urlencode, urlparse, parse_qs

import aiohttp

from .. import auth
from .helpers import http_debug_log

# ── OAuth constants (same as Gemini CLI / pi-mono) ─────────
# Decoded from base64 for consistency with pi-mono's approach.

_CLIENT_ID_B64 = (
    "NjgxMjU1ODA5Mzk1LW9vOGZ0Mm9wcmRybnA5ZTNhcWY2YXYzaG1kaWIxMzVq"
    "LmFwcHMuZ29vZ2xldXNlcmNvbnRlbnQuY29t"
)
_CLIENT_SECRET_B64 = "R09DU1BYLTR1SGdNUG0tMW83U2stZ2VWNkN1NWNsWEZzeGw="

CLIENT_ID = base64.b64decode(_CLIENT_ID_B64).decode()
CLIENT_SECRET = base64.b64decode(_CLIENT_SECRET_B64).decode()

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REDIRECT_URI = "http://localhost:8085/oauth2callback"
SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform "
    "https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/userinfo.profile"
)

CODE_ASSIST_ENDPOINT = "https://cloudcode-pa.googleapis.com"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"

PROVIDER_ID = "google-gemini-cli"

# 5-minute safety buffer before actual expiry
_EXPIRY_BUFFER_MS = 5 * 60 * 1000


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── PKCE helpers ───────────────────────────────────────────

def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _generate_pkce() -> tuple[str, str]:
    """Generate a PKCE verifier and challenge pair."""
    verifier_bytes = os.urandom(32)
    verifier = _base64url_encode(verifier_bytes)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = _base64url_encode(digest)
    return verifier, challenge


# ── Credential persistence (unified auth.json) ────────────

def load_credentials() -> Optional[dict]:
    """Load Gemini credentials from the unified auth store."""
    return auth.load_provider(PROVIDER_ID)


def save_credentials(creds: dict) -> None:
    """Save Gemini credentials to the unified auth store."""
    auth.save_provider(PROVIDER_ID, creds)


def clear_credentials() -> None:
    """Remove Gemini credentials."""
    auth.clear_provider(PROVIDER_ID)


def is_token_expired(creds: dict) -> bool:
    """Check if the access token has expired (with buffer)."""
    return auth.is_expired(creds)


# ── Local OAuth callback server ────────────────────────────

class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth callback code."""

    received_code: Optional[str] = None
    received_state: Optional[str] = None
    expected_state: str = ""
    code_event: Event

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/oauth2callback":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        params = parse_qs(parsed.query)
        state = (params.get("state") or [None])[0]
        code = (params.get("code") or [None])[0]
        error = (params.get("error") or [None])[0]

        if error:
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                f"<html><body><h1>Authentication Failed</h1>"
                f"<p>Error: {error}</p>"
                f"<p>You can close this window.</p></body></html>".encode()
            )
            _OAuthCallbackHandler.code_event.set()
            return

        if state != self.expected_state:
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body><p>State mismatch</p></body></html>")
            _OAuthCallbackHandler.code_event.set()
            return

        if not code:
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body><p>Missing authorization code</p></body></html>")
            _OAuthCallbackHandler.code_event.set()
            return

        _OAuthCallbackHandler.received_code = code
        _OAuthCallbackHandler.received_state = state
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h1>Authentication Successful</h1>"
            b"<p>You can close this window and return to your terminal.</p>"
            b"</body></html>"
        )
        _OAuthCallbackHandler.code_event.set()

    def log_message(self, format, *args) -> None:  # noqa: A002
        pass


def _start_callback_server(state: str) -> tuple[Optional[HTTPServer], Event]:
    """Start a local HTTP server on port 8085 for the OAuth callback."""
    code_event = Event()
    _OAuthCallbackHandler.received_code = None
    _OAuthCallbackHandler.received_state = None
    _OAuthCallbackHandler.expected_state = state
    _OAuthCallbackHandler.code_event = code_event

    try:
        server = HTTPServer(("127.0.0.1", 8085), _OAuthCallbackHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, code_event
    except OSError:
        return None, code_event


# ── OAuth login flow ───────────────────────────────────────

def interactive_login() -> dict:
    """Run the interactive Google OAuth login flow.

    Opens a browser, listens on localhost:8085 for the callback.
    Discovers the Cloud Code Assist project, fetches user email.
    Returns persisted credentials dict.
    """
    import asyncio
    import webbrowser

    verifier, challenge = _generate_pkce()

    params = urlencode({
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
        "access_type": "offline",
        "prompt": "consent",
    })
    auth_url = f"{AUTH_URL}?{params}"

    # Start local callback server
    server, code_event = _start_callback_server(verifier)

    print()
    print("Opening browser for Google Gemini OAuth login…")
    if not server:
        print("(Port 8085 is busy — you'll need to paste the redirect URL manually.)")
    print(f"If the browser doesn't open, visit:\n  {auth_url}")
    print()

    webbrowser.open(auth_url)

    code: Optional[str] = None

    if server:
        # Wait up to 120 seconds for callback
        code_event.wait(timeout=120)
        server.shutdown()
        code = _OAuthCallbackHandler.received_code

    if not code:
        # Fall back to manual paste
        raw = input("Paste the full redirect URL or authorization code: ").strip()
        if not raw:
            raise RuntimeError("No authorization code provided.")
        code = _parse_auth_input(raw, verifier)

    if not code:
        raise RuntimeError("Failed to extract authorization code.")

    # Exchange code for tokens
    print("Exchanging authorization code for tokens…")
    token_data = _exchange_code_sync(code, verifier)

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)

    if not access_token or not refresh_token:
        raise RuntimeError("Token response missing access_token or refresh_token.")

    # Discover project
    print("Discovering Cloud Code Assist project…")
    project_id = asyncio.run(_discover_project(access_token))

    # Get user email
    email = asyncio.run(_get_user_email(access_token))

    creds = {
        "type": "oauth",
        "refresh": refresh_token,
        "access": access_token,
        "expires": _now_ms() + int(expires_in) * 1000 - _EXPIRY_BUFFER_MS,
        "projectId": project_id,
        "email": email,
    }

    save_credentials(creds)
    print(f"✓ Gemini OAuth credentials saved to {auth._auth_path()}")
    return creds


def _parse_auth_input(raw: str, expected_state: str) -> Optional[str]:
    """Parse user's pasted input — might be a code or URL."""
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
            raise RuntimeError("OAuth state mismatch")
        if code:
            return code
    except RuntimeError:
        raise
    except Exception:
        pass

    # Plain code
    return raw


def _exchange_code_sync(code: str, verifier: str) -> dict:
    """Exchange authorization code for tokens (synchronous for CLI)."""
    import urllib.request

    body = urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    }).encode()

    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        raise RuntimeError(f"Token exchange failed: {e}") from e


# ── Token refresh ──────────────────────────────────────────

async def refresh_access_token(creds: dict, timeout: float = 30.0) -> dict:
    """Use the refresh token to obtain a new access token.

    Updates and persists the credentials on success.
    """
    refresh_token = creds.get("refresh")
    if not refresh_token:
        raise RuntimeError("No refresh token — run `llmeter --login-gemini` to re-authenticate.")

    body = urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })

    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    http_debug_log(
        "gemini-oauth",
        "token_refresh_request",
        method="POST",
        url=TOKEN_URL,
        headers=headers,
        payload={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )

    async with aiohttp.ClientSession() as session:
        async with session.post(
            TOKEN_URL,
            data=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            http_debug_log(
                "gemini-oauth",
                "token_refresh_response",
                method="POST",
                url=TOKEN_URL,
                status=resp.status,
            )
            if resp.status != 200:
                resp_body = await resp.text()
                raise RuntimeError(
                    f"Token refresh failed (HTTP {resp.status}): {resp_body[:200]}"
                )
            token_data = await resp.json()

    new_access = token_data.get("access_token", "")
    new_refresh = token_data.get("refresh_token", refresh_token)
    expires_in = token_data.get("expires_in", 3600)

    if not new_access:
        raise RuntimeError("Token refresh response missing access_token.")

    # Optionally refresh email
    email = creds.get("email")
    if not email:
        try:
            email = await _get_user_email(new_access)
        except Exception:
            pass

    new_creds = {
        "type": "oauth",
        "refresh": new_refresh,
        "access": new_access,
        "expires": _now_ms() + int(expires_in) * 1000 - _EXPIRY_BUFFER_MS,
        "projectId": creds.get("projectId", ""),
        "email": email,
    }

    save_credentials(new_creds)
    return new_creds


# ── High-level: get valid credentials ──────────────────────

async def get_valid_credentials(timeout: float = 30.0) -> Optional[dict]:
    """Load credentials, refresh if expired, return full creds dict or None."""
    creds = load_credentials()
    if creds is None:
        return None

    if is_token_expired(creds):
        try:
            creds = await refresh_access_token(creds, timeout=timeout)
        except RuntimeError:
            return None

    return creds


# ── Project discovery ──────────────────────────────────────

async def _discover_project(access_token: str, timeout: float = 30.0) -> str:
    """Discover or provision a Cloud Code Assist project.

    Mirrors pi-mono's discoverProject logic:
    1. Try loadCodeAssist to find an existing project
    2. If no project and no tier, onboard the user (provisions a free project)
    3. Fall back to GOOGLE_CLOUD_PROJECT env var
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "google-api-nodejs-client/9.15.1",
        "X-Goog-Api-Client": "gl-node/22.17.0",
    }

    env_project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")

    # 1. Try loadCodeAssist
    load_url = f"{CODE_ASSIST_ENDPOINT}/v1internal:loadCodeAssist"
    load_body = {
        "cloudaicompanionProject": env_project,
        "metadata": {
            "ideType": "IDE_UNSPECIFIED",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI",
            "duetProject": env_project,
        },
    }

    http_debug_log(
        "gemini-oauth",
        "discover_load_code_assist_request",
        method="POST",
        url=load_url,
        headers=headers,
        payload=load_body,
    )

    async with aiohttp.ClientSession() as session:
        async with session.post(
            load_url,
            json=load_body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            http_debug_log(
                "gemini-oauth",
                "discover_load_code_assist_response",
                method="POST",
                url=load_url,
                status=resp.status,
            )
            if resp.status == 200:
                data = await resp.json()
            else:
                # Check for VPC-SC affected users
                try:
                    err_data = await resp.json()
                    details = err_data.get("error", {}).get("details", [])
                    if any(d.get("reason") == "SECURITY_POLICY_VIOLATED" for d in details):
                        data = {"currentTier": {"id": "standard-tier"}}
                    else:
                        error_text = json.dumps(err_data)
                        raise RuntimeError(f"loadCodeAssist failed (HTTP {resp.status}): {error_text[:300]}")
                except (json.JSONDecodeError, RuntimeError):
                    raise
                except Exception:
                    raise RuntimeError(f"loadCodeAssist failed (HTTP {resp.status})")

    # If user has a tier and project, use it
    current_tier = data.get("currentTier")
    project_id = data.get("cloudaicompanionProject")
    if isinstance(project_id, dict):
        project_id = project_id.get("id") or project_id.get("projectId")
    if isinstance(project_id, str):
        project_id = project_id.strip() or None

    if current_tier and project_id:
        return project_id
    if current_tier and env_project:
        return env_project

    # 2. Need to onboard — get default tier
    allowed_tiers = data.get("allowedTiers", [])
    default_tier = next(
        (t for t in allowed_tiers if t.get("isDefault")),
        {"id": "free-tier"} if allowed_tiers else {"id": "legacy-tier"},
    )
    tier_id = default_tier.get("id", "free-tier")

    if tier_id != "free-tier" and not env_project:
        raise RuntimeError(
            "This account requires GOOGLE_CLOUD_PROJECT or GOOGLE_CLOUD_PROJECT_ID env var. "
            "See https://goo.gle/gemini-cli-auth-docs#workspace-gca"
        )

    # Build onboard request
    onboard_body: dict = {
        "tierId": tier_id,
        "metadata": {
            "ideType": "IDE_UNSPECIFIED",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI",
        },
    }
    if tier_id != "free-tier" and env_project:
        onboard_body["cloudaicompanionProject"] = env_project
        onboard_body["metadata"]["duetProject"] = env_project

    onboard_url = f"{CODE_ASSIST_ENDPOINT}/v1internal:onboardUser"
    http_debug_log(
        "gemini-oauth",
        "onboard_user_request",
        method="POST",
        url=onboard_url,
        headers=headers,
        payload=onboard_body,
    )
    async with aiohttp.ClientSession() as session:
        async with session.post(
            onboard_url,
            json=onboard_body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            http_debug_log(
                "gemini-oauth",
                "onboard_user_response",
                method="POST",
                url=onboard_url,
                status=resp.status,
            )
            if resp.status != 200:
                error_text = await resp.text()
                raise RuntimeError(f"onboardUser failed (HTTP {resp.status}): {error_text[:300]}")
            lro_data = await resp.json()

    # Poll if long-running operation
    if not lro_data.get("done") and lro_data.get("name"):
        lro_data = await _poll_operation(lro_data["name"], headers, timeout)

    result_project = (
        lro_data.get("response", {})
        .get("cloudaicompanionProject", {})
    )
    if isinstance(result_project, dict):
        pid = result_project.get("id")
    elif isinstance(result_project, str):
        pid = result_project
    else:
        pid = None

    if pid:
        return pid
    if env_project:
        return env_project

    raise RuntimeError(
        "Could not discover or provision a Cloud Code project. "
        "Try setting GOOGLE_CLOUD_PROJECT env var."
    )


async def _poll_operation(
    operation_name: str,
    headers: dict[str, str],
    timeout: float = 30.0,
    max_attempts: int = 30,
) -> dict:
    """Poll a long-running operation until completion."""
    import asyncio

    url = f"{CODE_ASSIST_ENDPOINT}/v1internal/{operation_name}"

    for attempt in range(max_attempts):
        if attempt > 0:
            await asyncio.sleep(5)

        http_debug_log(
            "gemini-oauth",
            "poll_operation_request",
            method="GET",
            url=url,
            headers=headers,
            message=f"attempt={attempt + 1}/{max_attempts}",
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                http_debug_log(
                    "gemini-oauth",
                    "poll_operation_response",
                    method="GET",
                    url=url,
                    status=resp.status,
                    message=f"attempt={attempt + 1}/{max_attempts}",
                )
                if resp.status != 200:
                    raise RuntimeError(f"Failed to poll operation (HTTP {resp.status})")
                data = await resp.json()

        if data.get("done"):
            return data

    raise RuntimeError("Operation timed out waiting for project provisioning.")


async def _get_user_email(access_token: str, timeout: float = 10.0) -> Optional[str]:
    """Fetch user email from the Google userinfo endpoint."""
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        http_debug_log(
            "gemini-oauth",
            "userinfo_request",
            method="GET",
            url=USERINFO_ENDPOINT,
            headers=headers,
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(
                USERINFO_ENDPOINT,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                http_debug_log(
                    "gemini-oauth",
                    "userinfo_response",
                    method="GET",
                    url=USERINFO_ENDPOINT,
                    status=resp.status,
                )
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("email")
    except Exception:
        pass
    return None
