"""opencode.ai Zen provider — tracks balance and monthly spend.

Config:
  { "id": "opencode", "api_key": "<auth-cookie-value>" }

Or set OPENCODE_AUTH_COOKIE env var.

The ``api_key`` is the value of the HttpOnly ``auth`` cookie from opencode.ai.
Extract it from DevTools → Application → Cookies → opencode.ai → ``auth``.

Data is scraped from the server-rendered workspace page, which embeds all
billing and usage data as inline JavaScript hydration.  No separate JSON
API endpoint is required.

Cost unit: all raw cost integers on the page are in units of 1e-8 USD.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from ...models import (
    CostInfo,
    CreditsInfo,
    PROVIDERS,
    ProviderIdentity,
    ProviderResult,
    RateWindow,
)
from ..helpers import http_debug_log
from .base import ApiProvider

WORKSPACE_ENTRY_URL = "https://opencode.ai/zen"
COST_UNIT = 1e8  # divide raw int by this to get USD

# ── Regex patterns for the JS hydration payload ────────────────────────────

_RE_BALANCE = re.compile(r"balance:(\d+)")
_RE_MONTHLY_USAGE = re.compile(r"monthlyUsage:(\d+)")
_RE_MONTHLY_LIMIT = re.compile(r"monthlyLimit:(\d+)")
_RE_EMAIL = re.compile(r'"([^"@\s]{1,64}@[^"@\s]{1,128})"')


# ── Provider class ─────────────────────────────────────────────────────────


class OpencodeProvider(ApiProvider):
    """Fetches opencode.ai Zen balance and monthly spend."""

    @property
    def provider_id(self) -> str:
        return "opencode"

    @property
    def no_api_key_error(self) -> str:
        return (
            "opencode.ai auth cookie not configured. "
            "Set OPENCODE_AUTH_COOKIE env var or add api_key to config."
        )

    def resolve_api_key(self, settings: dict) -> Optional[str]:
        """Return the auth cookie value from config or env, or None."""
        key = (
            settings.get("api_key") or os.environ.get("OPENCODE_AUTH_COOKIE") or ""
        ).strip()
        return key or None

    async def _fetch(
        self,
        api_key: str,
        timeout: float,
        settings: dict,
    ) -> ProviderResult:
        result = PROVIDERS["opencode"].to_result(source="api")

        headers = {
            "Cookie": f"auth={api_key}",
            "Accept": "text/html",
            "User-Agent": "llmeter/1.0",
        }

        http_debug_log(
            "opencode",
            "page_request",
            method="GET",
            url=WORKSPACE_ENTRY_URL,
            headers={"Cookie": "auth=<redacted>"},
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    WORKSPACE_ENTRY_URL,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    allow_redirects=True,
                ) as resp:
                    http_debug_log(
                        "opencode",
                        "page_response",
                        method="GET",
                        url=str(resp.url),
                        status=resp.status,
                    )
                    if resp.status in (401, 403):
                        result.error = (
                            "opencode.ai session expired or invalid. "
                            "Update api_key in config or OPENCODE_AUTH_COOKIE."
                        )
                        return result
                    if resp.status != 200:
                        result.error = f"opencode.ai returned HTTP {resp.status}"
                        return result
                    html = await resp.text()
        except aiohttp.ClientError as e:
            result.error = f"opencode.ai request failed: {e}"
            return result

        _parse_html(html, result)
        result.updated_at = datetime.now(timezone.utc)
        return result


# ── HTML / JS hydration parsing ────────────────────────────────────────────


def _parse_html(html: str, result: ProviderResult) -> None:
    """Extract billing data from the SolidStart JS hydration payload."""
    balance_usd = _extract_int(html, _RE_BALANCE) / COST_UNIT
    monthly_usage = _extract_int(html, _RE_MONTHLY_USAGE) / COST_UNIT
    monthly_limit = _extract_int(html, _RE_MONTHLY_LIMIT)  # raw USD dollars (integer)

    # Primary bar: monthly spend vs limit
    if monthly_limit > 0:
        spend_pct = min(100.0, (monthly_usage / monthly_limit) * 100.0)
        result.primary = RateWindow(used_percent=spend_pct)
        result.primary_label = f"${monthly_usage:.2f} / ${monthly_limit:.0f}"
    else:
        result.primary = RateWindow(used_percent=0.0)
        result.primary_label = f"${monthly_usage:.2f} this month"

    # Credits: current wallet balance
    if balance_usd > 0:
        result.credits = CreditsInfo(remaining=balance_usd)

    # Structured cost info for the snapshot renderer
    result.cost = CostInfo(
        used=round(monthly_usage, 4),
        limit=float(monthly_limit),
        currency="USD",
        period="Monthly",
    )

    # Identity: first email-like string in the page
    email_match = _RE_EMAIL.search(html)
    if email_match:
        result.identity = ProviderIdentity(account_email=email_match.group(1))


def _extract_int(html: str, pattern: re.Pattern) -> int:
    m = pattern.search(html)
    return int(m.group(1)) if m else 0


# Module-level singleton — used by backend.py and importable as a callable.
fetch_opencode_api = OpencodeProvider()
