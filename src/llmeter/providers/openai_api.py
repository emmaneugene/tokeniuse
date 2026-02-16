"""OpenAI API billing provider — tracks spend via the /v1/organization/costs endpoint.

Config:
  { "id": "openai-api", "api_key": "sk-...", "monthly_budget": 100.0 }

Or set OPENAI_API_KEY / OPENAI_ADMIN_KEY env var. monthly_budget is optional.
"""

from __future__ import annotations

import calendar
import os
from datetime import datetime, timezone

import aiohttp

from ..models import (
    CostInfo,
    PROVIDERS,
    ProviderResult,
    RateWindow,
)
from .helpers import http_debug_log

COSTS_URL = "https://api.openai.com/v1/organization/costs"


async def fetch_openai_api(
    timeout: float = 30.0,
    settings: dict | None = None,
) -> ProviderResult:
    """Fetch OpenAI API spend for the current billing month."""
    settings = settings or {}

    result = PROVIDERS["openai-api"].to_result(source="api")

    # Resolve API key
    api_key = (
        settings.get("api_key")
        or os.environ.get("OPENAI_ADMIN_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    if not api_key:
        result.error = (
            "OpenAI API key not configured. "
            "Set OPENAI_ADMIN_KEY env var or add api_key to config."
        )
        return result

    monthly_budget: float = settings.get("monthly_budget", 0.0)

    # Current month boundaries (UTC)
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    _, last_day = calendar.monthrange(now.year, now.month)
    month_end = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=0)

    start_ts = int(month_start.timestamp())
    end_ts = int(month_end.timestamp())

    try:
        total_spend = await _fetch_costs(api_key, start_ts, end_ts, timeout)
    except Exception as e:
        result.error = f"OpenAI API error: {e}"
        return result

    # Show as cost bar if budget is set, otherwise just cost info
    if monthly_budget > 0:
        spend_pct = min(100.0, (total_spend / monthly_budget) * 100.0)
        result.primary = RateWindow(
            used_percent=spend_pct,
            window_minutes=last_day * 24 * 60,
        )
        result.primary_label = f"${total_spend:,.2f} / ${monthly_budget:,.2f}"
        result.cost = CostInfo(
            used=total_spend,
            limit=monthly_budget,
            currency="USD",
            period="Monthly",
        )
    else:
        # No budget — show just the spend amount as a label with 0% bar
        result.primary = RateWindow(used_percent=0.0)
        result.primary_label = f"Spend: ${total_spend:,.2f} this month"
        result.cost = CostInfo(
            used=total_spend,
            limit=0.0,
            currency="USD",
            period="Monthly",
        )

    result.updated_at = datetime.now(timezone.utc)
    return result


async def _fetch_costs(
    api_key: str,
    start_ts: int,
    end_ts: int,
    timeout: float,
) -> float:
    """Fetch cost data from OpenAI and return total spend in USD."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    params = {
        "start_time": str(start_ts),
        "end_time": str(end_ts),
        "bucket_width": "1d",
        "limit": "31",
    }

    total = 0.0

    async with aiohttp.ClientSession() as session:
        while True:
            http_debug_log(
                "openai-api",
                "costs_request",
                method="GET",
                url=COSTS_URL,
                headers=headers,
                payload=params,
            )
            async with session.get(
                COSTS_URL,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                http_debug_log(
                    "openai-api",
                    "costs_response",
                    method="GET",
                    url=COSTS_URL,
                    status=resp.status,
                )
                if resp.status == 401:
                    raise RuntimeError(
                        "Unauthorized — check your API key. "
                        "Admin keys (sk-admin-...) are required for the costs endpoint."
                    )
                if resp.status == 403:
                    raise RuntimeError(
                        "Forbidden — your API key may lack billing/costs permissions."
                    )
                if resp.status == 429:
                    raise RuntimeError("Rate limited — try again in a moment.")
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"HTTP {resp.status}: {text[:300]}")

                data = await resp.json()

            # Sum up costs from all buckets
            for bucket in data.get("data", []):
                for result_item in bucket.get("results", []):
                    amount = result_item.get("amount", {})
                    value = amount.get("value", 0.0)
                    try:
                        total += float(value)
                    except (ValueError, TypeError):
                        pass

            # Pagination: next_page is a token, not a URL
            next_page = data.get("next_page")
            if next_page:
                params["page"] = next_page
            else:
                break

    return round(total, 2)
