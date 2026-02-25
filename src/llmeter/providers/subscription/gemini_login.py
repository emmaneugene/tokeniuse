"""Gemini interactive login — PKCE + local callback server + project discovery."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread, Event
from typing import Optional
from urllib.parse import urlencode, urlparse, parse_qs

import aiohttp

from ... import auth
from ..helpers import http_debug_log
from .base import LoginProvider
from .gemini import (
    CLIENT_ID,
    CLIENT_SECRET,
    TOKEN_URL,
    REDIRECT_URI,
    SCOPES,
    CODE_ASSIST_ENDPOINT,
    _now_ms,
    _get_user_email,
    save_credentials,
)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


# ── PKCE helpers ───────────────────────────────────────────

def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _generate_pkce() -> tuple[str, str]:
    verifier_bytes = os.urandom(32)
    verifier = _base64url_encode(verifier_bytes)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = _base64url_encode(digest)
    return verifier, challenge


# ── Local callback server ──────────────────────────────────

class _OAuthCallbackHandler(BaseHTTPRequestHandler):
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
                f"<p>Error: {error}</p></body></html>".encode()
            )
            _OAuthCallbackHandler.code_event.set()
            return

        if state != self.expected_state or not code:
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body><p>Bad request</p></body></html>")
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
    code_event = Event()
    _OAuthCallbackHandler.received_code = None
    _OAuthCallbackHandler.received_state = None
    _OAuthCallbackHandler.expected_state = state
    _OAuthCallbackHandler.code_event = code_event
    try:
        server = HTTPServer(("127.0.0.1", 8085), _OAuthCallbackHandler)
        Thread(target=server.serve_forever, daemon=True).start()
        return server, code_event
    except OSError:
        return None, code_event


# ── Login class ────────────────────────────────────────────

class GeminiLogin(LoginProvider):
    """PKCE + callback server OAuth login flow for Gemini CLI."""

    @property
    def provider_id(self) -> str:
        return "gemini"

    def interactive_login(self) -> dict:
        """Open browser, capture OAuth callback, discover project, persist tokens."""
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
            code_event.wait(timeout=120)
            server.shutdown()
            code = _OAuthCallbackHandler.received_code

        if not code:
            raw = input("Paste the full redirect URL or authorization code: ").strip()
            if not raw:
                raise RuntimeError("No authorization code provided.")
            code = _parse_auth_input(raw, verifier)

        if not code:
            raise RuntimeError("Failed to extract authorization code.")

        print("Exchanging authorization code for tokens…")
        token_data = _exchange_code_sync(code, verifier)

        access_token = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token", "")
        expires_in = token_data.get("expires_in", 3600)

        if not access_token or not refresh_token:
            raise RuntimeError("Token response missing access_token or refresh_token.")

        print("Discovering Cloud Code Assist project…")
        project_id = asyncio.run(_discover_project(access_token))
        email = asyncio.run(_get_user_email(access_token))

        creds = {
            "type": "oauth",
            "refresh": refresh_token,
            "access": access_token,
            "expires": _now_ms() + int(expires_in) * 1000 - auth.EXPIRY_BUFFER_MS,
            "projectId": project_id,
            "email": email,
        }
        save_credentials(creds)
        print(f"✓ Gemini OAuth credentials saved to {auth._auth_path()}")
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
            raise RuntimeError("OAuth state mismatch")
        if code:
            return code
    except RuntimeError:
        raise
    except Exception:
        pass
    return raw


def _exchange_code_sync(code: str, verifier: str) -> dict:
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
        TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        raise RuntimeError(f"Token exchange failed: {e}") from e


# ── Project discovery ──────────────────────────────────────

async def _discover_project(access_token: str, timeout: float = 30.0) -> str:
    """Discover or provision a Cloud Code Assist project."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "google-api-nodejs-client/9.15.1",
        "X-Goog-Api-Client": "gl-node/22.17.0",
    }
    env_project = (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
    )

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
        "gemini-oauth", "discover_load_code_assist_request",
        method="POST", url=load_url, headers=headers, payload=load_body,
    )

    async with aiohttp.ClientSession() as session:
        async with session.post(
            load_url, json=load_body, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            http_debug_log(
                "gemini-oauth", "discover_load_code_assist_response",
                method="POST", url=load_url, status=resp.status,
            )
            if resp.status == 200:
                data = await resp.json()
            else:
                try:
                    err_data = await resp.json()
                    details = err_data.get("error", {}).get("details", [])
                    if any(d.get("reason") == "SECURITY_POLICY_VIOLATED" for d in details):
                        data = {"currentTier": {"id": "standard-tier"}}
                    else:
                        raise RuntimeError(
                            f"loadCodeAssist failed (HTTP {resp.status}): "
                            f"{json.dumps(err_data)[:300]}"
                        )
                except (json.JSONDecodeError, RuntimeError):
                    raise
                except Exception:
                    raise RuntimeError(f"loadCodeAssist failed (HTTP {resp.status})")

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
        "gemini-oauth", "onboard_user_request",
        method="POST", url=onboard_url, headers=headers, payload=onboard_body,
    )
    async with aiohttp.ClientSession() as session:
        async with session.post(
            onboard_url, json=onboard_body, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            http_debug_log(
                "gemini-oauth", "onboard_user_response",
                method="POST", url=onboard_url, status=resp.status,
            )
            if resp.status != 200:
                error_text = await resp.text()
                raise RuntimeError(f"onboardUser failed (HTTP {resp.status}): {error_text[:300]}")
            lro_data = await resp.json()

    if not lro_data.get("done") and lro_data.get("name"):
        lro_data = await _poll_operation(lro_data["name"], headers, timeout)

    result_project = lro_data.get("response", {}).get("cloudaicompanionProject", {})
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
    url = f"{CODE_ASSIST_ENDPOINT}/v1internal/{operation_name}"
    for attempt in range(max_attempts):
        if attempt > 0:
            await asyncio.sleep(5)
        http_debug_log(
            "gemini-oauth", "poll_operation_request",
            method="GET", url=url, headers=headers,
            message=f"attempt={attempt + 1}/{max_attempts}",
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                http_debug_log(
                    "gemini-oauth", "poll_operation_response",
                    method="GET", url=url, status=resp.status,
                    message=f"attempt={attempt + 1}/{max_attempts}",
                )
                if resp.status != 200:
                    raise RuntimeError(f"Failed to poll operation (HTTP {resp.status})")
                data = await resp.json()
        if data.get("done"):
            return data
    raise RuntimeError("Operation timed out waiting for project provisioning.")


# Module-level singleton — __main__.py imports this directly.
interactive_login = GeminiLogin().interactive_login
