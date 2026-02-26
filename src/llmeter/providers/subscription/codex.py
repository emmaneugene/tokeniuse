"""Codex provider — fetches usage via direct OAuth API.

Run `llmeter --login codex` to authenticate once.  Tokens are refreshed
automatically from then on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

import aiohttp

from ... import auth
from ...models import (
    CreditsInfo,
    PROVIDERS,
    ProviderIdentity,
    ProviderResult,
    RateWindow,
)
from ..helpers import decode_jwt_payload, http_get, http_debug_log
from .base import SubscriptionProvider

# ── OAuth constants ────────────────────────────────────────

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REDIRECT_URI = "http://localhost:1455/auth/callback"
SCOPES = "openid profile email offline_access"
JWT_CLAIM_PATH = "https://api.openai.com/auth"
PROVIDER_ID = "openai-codex"

# ── Provider API constants ─────────────────────────────────

# The correct endpoint, per CodexBar and codex-rs source:
# https://chatgpt.com/backend-api/wham/usage
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


# ── Credential management ──────────────────────────────────

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
    """Extract email from an OpenAI id_token JWT."""
    payload = decode_jwt_payload(id_token)
    if not payload:
        return None
    email = payload.get("email")
    if isinstance(email, str) and email:
        return email.strip()
    profile = payload.get("https://api.openai.com/profile")
    if isinstance(profile, dict):
        email = profile.get("email")
        if isinstance(email, str) and email:
            return email.strip()
    return None


def load_credentials() -> Optional[dict]:
    """Load Codex OAuth credentials from the unified auth store."""
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


async def refresh_access_token(creds: dict, timeout: float = 30.0) -> dict:
    """Use the refresh token to obtain a new access token.

    Updates and persists the credentials on success.
    Raises RuntimeError on failure.
    """
    refresh_token = creds.get("refresh")
    if not refresh_token:
        raise RuntimeError("No refresh token available — run `llmeter --login codex`.")

    payload = urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
    })
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    http_debug_log(
        "codex-oauth", "token_refresh_request",
        method="POST", url=TOKEN_URL, headers=headers,
        payload={"grant_type": "refresh_token", "client_id": CLIENT_ID,
                 "refresh_token": refresh_token},
    )

    async with aiohttp.ClientSession() as session:
        async with session.post(
            TOKEN_URL, data=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            http_debug_log(
                "codex-oauth", "token_refresh_response",
                method="POST", url=TOKEN_URL, status=resp.status,
            )
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
    id_token = token_data.get("id_token")
    email = extract_email(id_token) if id_token else creds.get("email")

    new_creds: dict = {
        "type": "oauth",
        "access": access_token,
        "refresh": new_refresh,
        "expires": auth.now_ms() + int(expires_in) * 1000 - auth.EXPIRY_BUFFER_MS,
        "accountId": account_id,
    }
    if email:
        new_creds["email"] = email

    save_credentials(new_creds)
    return new_creds


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


# ── Provider class ─────────────────────────────────────────

class CodexProvider(SubscriptionProvider):
    """Fetches Codex usage via direct OAuth API."""

    @property
    def provider_id(self) -> str:
        return "codex"

    @property
    def no_credentials_error(self) -> str:
        return (
            "No Codex credentials found. "
            "Run `llmeter --login codex` to authenticate."
        )

    async def get_credentials(self, timeout: float) -> Optional[dict]:
        return await get_valid_credentials(timeout=timeout)

    async def _fetch(
        self,
        creds: dict,
        timeout: float,
        settings: dict,
    ) -> ProviderResult:
        result = PROVIDERS["codex"].to_result()

        headers = {
            "Authorization": f"Bearer {creds['access']}",
            "ChatGPT-Account-Id": creds["accountId"],
            "User-Agent": "LLMeter/0.1.0",
            "Accept": "application/json",
        }

        try:
            data = await http_get(
                "codex", USAGE_URL, headers, timeout,
                label="usage",
                errors={
                    401: (
                        "Unauthorized — token may be invalid or expired. "
                        "Run `llmeter --login codex` to re-authenticate."
                    ),
                },
            )
        except Exception as e:
            result.error = f"Codex API error: {e or type(e).__name__}"
            return result

        _parse_usage_response(data, result, email=creds.get("email"))

        result.source = "oauth"
        result.updated_at = datetime.now(timezone.utc)
        return result


# ── Response parsing ───────────────────────────────────────

def _parse_usage_response(data: dict, result: ProviderResult, email: str | None = None) -> None:
    rate_limit = data.get("rate_limit")
    if isinstance(rate_limit, dict):
        primary = rate_limit.get("primary_window")
        if primary:
            result.primary = _parse_window(primary)
        secondary = rate_limit.get("secondary_window")
        if secondary:
            result.secondary = _parse_window(secondary)

    credits_data = data.get("credits")
    if isinstance(credits_data, dict):
        balance = credits_data.get("balance")
        if balance is not None:
            try:
                result.credits = CreditsInfo(remaining=float(balance))
            except (ValueError, TypeError):
                pass

    plan_type = data.get("plan_type")
    if plan_type or email:
        result.identity = ProviderIdentity(
            account_email=email,
            login_method=_format_plan_type(str(plan_type)) if plan_type else None,
        )


def _format_plan_type(plan_type: str) -> str:
    known = {
        "guest": "ChatGPT Guest",
        "free": "ChatGPT Free",
        "go": "ChatGPT Go",
        "plus": "ChatGPT Plus",
        "pro": "ChatGPT Pro",
        "free_workspace": "ChatGPT Free Workspace",
        "team": "ChatGPT Team",
        "business": "ChatGPT Business",
        "education": "ChatGPT Education",
        "enterprise": "ChatGPT Enterprise",
        "edu": "ChatGPT Edu",
    }
    return known.get(plan_type.lower(), f"ChatGPT {plan_type.capitalize()}")


def _parse_window(window: dict) -> RateWindow:
    try:
        used_pct = float(window.get("used_percent") or 0)
    except (TypeError, ValueError):
        used_pct = 0.0
    limit_secs = window.get("limit_window_seconds")
    window_mins = limit_secs // 60 if isinstance(limit_secs, (int, float)) else None
    resets_at = None
    reset_epoch = window.get("reset_at")
    if isinstance(reset_epoch, (int, float)) and reset_epoch > 0:
        resets_at = datetime.fromtimestamp(reset_epoch, tz=timezone.utc)
    return RateWindow(
        used_percent=used_pct,
        window_minutes=window_mins,
        resets_at=resets_at,
    )


# Module-level singleton — used by backend.py and importable as a callable.
fetch_codex = CodexProvider()
