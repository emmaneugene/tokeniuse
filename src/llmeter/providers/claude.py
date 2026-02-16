"""Claude provider — fetches usage via OAuth API with automatic token refresh.

Run `llmeter --login-claude` to authenticate once.  Tokens are refreshed
automatically from then on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import aiohttp

from ..models import (
    CostInfo,
    PROVIDERS,
    ProviderIdentity,
    ProviderResult,
    RateWindow,
)
from . import claude_oauth
from .helpers import parse_iso8601, http_debug_log

OAUTH_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
BETA_HEADER = "oauth-2025-04-20"


async def fetch_claude(timeout: float = 30.0, settings: dict | None = None) -> ProviderResult:
    """Fetch Claude usage via the OAuth usage API."""
    result = PROVIDERS["claude"].to_result()

    access_token = await claude_oauth.get_valid_access_token(timeout=timeout)
    if not access_token:
        result.error = (
            "No Claude credentials found. "
            "Run `llmeter --login-claude` to authenticate."
        )
        return result

    # --- Fetch usage ---
    try:
        usage = await _fetch_oauth_usage(access_token, timeout=timeout)
    except Exception as e:
        result.error = f"Claude API error: {e}"
        return result

    # --- Parse windows ---
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

    # Extra usage (cost info)
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

    # Identity
    profile = await _fetch_account_info(access_token, timeout=timeout)
    if profile:
        result.identity = ProviderIdentity(
            account_email=profile.get("email"),
            login_method=profile.get("plan"),
        )

    result.source = "oauth"
    result.updated_at = datetime.now(timezone.utc)
    return result


# ── API calls ──────────────────────────────────────────────

async def _fetch_oauth_usage(access_token: str, timeout: float = 30.0) -> dict:
    """Call the Claude OAuth usage endpoint."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "anthropic-beta": BETA_HEADER,
        "User-Agent": "LLMeter/0.1.0",
    }

    http_debug_log(
        "claude",
        "usage_request",
        method="GET",
        url=OAUTH_USAGE_URL,
        headers=headers,
    )

    async with aiohttp.ClientSession() as session:
        async with session.get(
            OAUTH_USAGE_URL,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            http_debug_log(
                "claude",
                "usage_response",
                method="GET",
                url=OAUTH_USAGE_URL,
                status=resp.status,
            )

            if resp.status == 401:
                raise RuntimeError(
                    "Unauthorized — token may be invalid or expired. "
                    "Run `llmeter --login-claude` to re-authenticate."
                )
            if resp.status == 403:
                body = await resp.text()
                if "user:profile" in body:
                    raise RuntimeError(
                        "Token missing 'user:profile' scope. "
                        "Re-authenticate with `llmeter --login-claude`."
                    )
                raise RuntimeError(f"Forbidden (HTTP 403): {body[:200]}")
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"HTTP {resp.status}: {body[:200]}")
            return await resp.json()


async def _fetch_account_info(
    access_token: str,
    timeout: float = 30.0,
) -> Optional[dict]:
    """Fetch account email and plan from the OAuth profile endpoint."""
    try:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "anthropic-beta": BETA_HEADER,
            "User-Agent": "LLMeter/0.1.0",
        }

        http_debug_log(
            "claude",
            "profile_request",
            method="GET",
            url=OAUTH_PROFILE_URL,
            headers=headers,
        )

        async with aiohttp.ClientSession() as session:
            async with session.get(
                OAUTH_PROFILE_URL,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=min(timeout, 10)),
            ) as resp:
                http_debug_log(
                    "claude",
                    "profile_response",
                    method="GET",
                    url=OAUTH_PROFILE_URL,
                    status=resp.status,
                )
                if resp.status != 200:
                    return None
                data = await resp.json()

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
    except Exception:
        return None


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
