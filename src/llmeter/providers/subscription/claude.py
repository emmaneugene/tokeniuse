"""Claude provider — fetches usage via OAuth API with automatic token refresh.

Run `llmeter --login claude` to authenticate once.  Tokens are refreshed
automatically from then on.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from ... import auth
from ...models import (
    CostInfo,
    PROVIDERS,
    ProviderIdentity,
    ProviderResult,
    RateWindow,
)
from ..helpers import parse_iso8601, http_get, http_debug_log
from .base import SubscriptionProvider

# ── OAuth constants ────────────────────────────────────────

_CLIENT_ID_B64 = "OWQxYzI1MGEtZTYxYi00NGQ5LTg4ZWQtNTk0NGQxOTYyZjVl"
CLIENT_ID = base64.b64decode(_CLIENT_ID_B64).decode()
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
SCOPES = "org:create_api_key user:profile user:inference"
PROVIDER_ID = "anthropic"

# ── Provider API constants ─────────────────────────────────

OAUTH_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
BETA_HEADER = "oauth-2025-04-20"

def _claude_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "anthropic-beta": BETA_HEADER,
        "User-Agent": "LLMeter/0.1.0",
    }


# ── Credential management ──────────────────────────────────

def load_credentials() -> Optional[dict]:
    """Load Claude OAuth credentials from the unified auth store."""
    creds = auth.load_provider(PROVIDER_ID)
    if creds and creds.get("access") and creds.get("refresh"):
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
        raise RuntimeError("No refresh token available — run `llmeter --login claude`.")

    payload = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
    }
    headers = {"Content-Type": "application/json"}
    http_debug_log(
        "claude-oauth", "token_refresh_request",
        method="POST", url=TOKEN_URL, headers=headers, payload=payload,
    )

    async with aiohttp.ClientSession() as session:
        async with session.post(
            TOKEN_URL, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            http_debug_log(
                "claude-oauth", "token_refresh_response",
                method="POST", url=TOKEN_URL, status=resp.status,
            )
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(
                    f"Token refresh failed (HTTP {resp.status}): {body[:200]}"
                )
            token_data = await resp.json()

    new_creds = {
        "type": "oauth",
        "refresh": token_data.get("refresh_token", refresh_token),
        "access": token_data["access_token"],
        "expires": auth.now_ms() + token_data["expires_in"] * 1000 - auth.EXPIRY_BUFFER_MS,
    }
    save_credentials(new_creds)
    return new_creds


async def get_valid_access_token(timeout: float = 30.0) -> Optional[str]:
    """Load credentials, refresh if expired, return access token or None."""
    creds = load_credentials()
    if creds is None:
        return None
    if is_token_expired(creds):
        try:
            creds = await refresh_access_token(creds, timeout=timeout)
        except RuntimeError:
            return None
    return creds.get("access")


# ── Provider class ─────────────────────────────────────────

class ClaudeProvider(SubscriptionProvider):
    """Fetches Claude usage via the OAuth usage API."""

    @property
    def provider_id(self) -> str:
        return "claude"

    @property
    def no_credentials_error(self) -> str:
        return (
            "No Claude credentials found. "
            "Run `llmeter --login claude` to authenticate."
        )

    async def get_credentials(self, timeout: float) -> Optional[str]:
        return await get_valid_access_token(timeout=timeout)

    async def _fetch(
        self,
        creds: str,
        timeout: float,
        settings: dict,
    ) -> ProviderResult:
        access_token = creds
        result = PROVIDERS["claude"].to_result()

        try:
            usage = await http_get(
                "claude", OAUTH_USAGE_URL, _claude_headers(access_token), timeout,
                label="usage",
                errors={
                    401: (
                        "Unauthorized — token may be invalid or expired. "
                        "Run `llmeter --login claude` to re-authenticate."
                    ),
                    403: (
                        "Forbidden — token may be missing required scopes. "
                        "Re-authenticate with `llmeter --login claude`."
                    ),
                },
            )
        except Exception as e:
            result.error = f"Claude API error: {e or type(e).__name__}"
            return result

        five_hour = usage.get("five_hour")
        if five_hour and five_hour.get("utilization") is not None:
            result.primary = RateWindow(
                used_percent=five_hour["utilization"],
                window_minutes=5 * 60,
                resets_at=parse_iso8601(five_hour.get("resets_at")),
            )
        else:
            result.error = "Claude API returned no session usage data."
            return result

        seven_day = usage.get("seven_day")
        if seven_day and seven_day.get("utilization") is not None:
            result.secondary = RateWindow(
                used_percent=seven_day["utilization"],
                window_minutes=7 * 24 * 60,
                resets_at=parse_iso8601(seven_day.get("resets_at")),
            )

        for key in ("seven_day_sonnet", "seven_day_opus"):
            model_window = usage.get(key)
            if model_window and model_window.get("utilization") is not None:
                result.tertiary = RateWindow(
                    used_percent=model_window["utilization"],
                    window_minutes=7 * 24 * 60,
                    resets_at=parse_iso8601(model_window.get("resets_at")),
                )
                result.tertiary_label = "Sonnet" if "sonnet" in key else "Opus"
                break

        extra = usage.get("extra_usage")
        if extra and extra.get("is_enabled"):
            used = extra.get("used_credits")
            limit = extra.get("monthly_limit")
            if used is not None and limit is not None:
                result.cost = CostInfo(
                    used=used / 100.0,
                    limit=limit / 100.0,
                    currency=extra.get("currency", "USD") or "USD",
                )

        profile = await _fetch_account_info(access_token, timeout=timeout)
        if profile:
            result.identity = ProviderIdentity(
                account_email=profile.get("email"),
                login_method=profile.get("plan"),
            )

        result.source = "oauth"
        result.updated_at = datetime.now(timezone.utc)
        return result


# ── Internal API helpers ───────────────────────────────────

async def _fetch_account_info(
    access_token: str,
    timeout: float = 30.0,
) -> Optional[dict]:
    """Fetch account email and plan from the OAuth profile endpoint."""
    try:
        data = await http_get(
            "claude", OAUTH_PROFILE_URL, _claude_headers(access_token), min(timeout, 10),
            label="profile",
        )
    except Exception:
        return None

    result: dict = {}
    account = data.get("account")
    if isinstance(account, dict):
        email = (account.get("email") or "").strip()
        if email:
            result["email"] = email
        if account.get("has_claude_max"):
            result["plan"] = "Claude Max"
        elif account.get("has_claude_pro"):
            result["plan"] = "Claude Pro"

    org = data.get("organization")
    if isinstance(org, dict) and not result.get("plan"):
        plan = _infer_plan_from_org(
            (org.get("organization_type") or "").strip(),
            (org.get("billing_type") or "").strip(),
            (org.get("rate_limit_tier") or "").strip(),
        )
        if plan:
            result["plan"] = plan

    return result if result else None


def _infer_plan_from_org(org_type: str, billing: str = "", tier: str = "") -> Optional[str]:
    """Infer Claude plan from organization metadata."""
    for label, keywords in [
        ("Claude Max", ["max"]),
        ("Claude Pro", ["pro"]),
        ("Claude Team", ["team"]),
        ("Claude Enterprise", ["enterprise"]),
    ]:
        if any(k in org_type.lower() for k in keywords):
            return label
        if any(k in tier.lower() for k in keywords):
            return label
    if "stripe" in billing.lower():
        return "Claude Pro"
    return None


# Module-level singleton — used by backend.py and importable as a callable.
fetch_claude = ClaudeProvider()
