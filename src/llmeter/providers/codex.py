"""Codex provider — fetches usage via direct OAuth API.

Run `llmeter --login codex` to authenticate once.  Tokens are refreshed
automatically from then on.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import (
    CreditsInfo,
    PROVIDERS,
    ProviderIdentity,
    ProviderResult,
    RateWindow,
)
from . import codex_oauth
from .helpers import http_get

# The correct endpoint, per CodexBar and codex-rs source:
# https://chatgpt.com/backend-api/wham/usage
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


async def fetch_codex(timeout: float = 20.0, settings: dict | None = None) -> ProviderResult:
    """Fetch Codex usage via direct OAuth API."""
    result = PROVIDERS["codex"].to_result()

    creds = await codex_oauth.get_valid_credentials(timeout=timeout)
    if creds is None:
        result.error = (
            "No Codex credentials found. "
            "Run `llmeter --login codex` to authenticate."
        )
        return result

    access_token = creds["access"]
    account_id = creds["accountId"]

    headers = {
        "Authorization": f"Bearer {access_token}",
        "ChatGPT-Account-Id": account_id,
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
        result.error = f"Codex API error: {e}"
        return result

    _parse_usage_response(data, result, email=creds.get("email"))

    result.source = "oauth"
    result.updated_at = datetime.now(timezone.utc)
    return result


# ── Response parsing ───────────────────────────────────────
#
# The /wham/usage response format (from CodexBar docs):
#
# {
#   "plan_type": "pro",
#   "rate_limit": {
#     "primary_window": {
#       "used_percent": 15,
#       "reset_at": 1735401600,        # epoch seconds
#       "limit_window_seconds": 18000   # 5 hours
#     },
#     "secondary_window": {
#       "used_percent": 5,
#       "reset_at": 1735920000,
#       "limit_window_seconds": 604800  # 7 days
#     }
#   },
#   "credits": {
#     "has_credits": true,
#     "unlimited": false,
#     "balance": 150.0
#   }
# }


def _parse_usage_response(data: dict, result: ProviderResult, email: str | None = None) -> None:
    """Parse the /wham/usage response into the ProviderResult."""
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
    """Format plan type for display (e.g. 'plus' → 'ChatGPT Plus')."""
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
    """Parse a rate limit window snapshot."""
    used_pct = window.get("used_percent", 0)

    # limit_window_seconds → minutes
    limit_secs = window.get("limit_window_seconds")
    window_mins = limit_secs // 60 if isinstance(limit_secs, (int, float)) else None

    # reset_at is epoch seconds
    resets_at = None
    reset_epoch = window.get("reset_at")
    if isinstance(reset_epoch, (int, float)) and reset_epoch > 0:
        resets_at = datetime.fromtimestamp(reset_epoch, tz=timezone.utc)

    return RateWindow(
        used_percent=used_pct,
        window_minutes=window_mins,
        resets_at=resets_at,
    )
