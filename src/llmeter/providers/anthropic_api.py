"""Anthropic API billing provider — tracks spend via the Admin API cost report.

Uses GET /v1/organizations/cost_report to get actual dollar costs for the
current billing month. Requires an admin API key (sk-ant-admin01-...).

Config:
  { "id": "anthropic-api", "api_key": "sk-ant-admin01-...", "monthly_budget": 50.0 }

Or set ANTHROPIC_ADMIN_KEY env var. monthly_budget is optional.
"""

from __future__ import annotations

import calendar
import os
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from ..models import (
    CostInfo,
    PROVIDERS,
    ProviderResult,
    RateWindow,
)
from .helpers import http_debug_log

BASE_URL = "https://api.anthropic.com"
COST_REPORT_URL = f"{BASE_URL}/v1/organizations/cost_report"
API_VERSION = "2023-06-01"


async def fetch_anthropic_api(
    timeout: float = 30.0,
    settings: dict | None = None,
) -> ProviderResult:
    """Fetch Anthropic API cost report for the current billing month."""
    settings = settings or {}

    result = PROVIDERS["anthropic-api"].to_result(source="api")

    # Resolve API key — prefer admin key
    api_key = (
        settings.get("api_key")
        or os.environ.get("ANTHROPIC_ADMIN_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or ""
    ).strip()
    if not api_key:
        result.error = (
            "Anthropic API key not configured. "
            "Set ANTHROPIC_ADMIN_KEY env var or add api_key to config."
        )
        return result

    monthly_budget: float = settings.get("monthly_budget", 0.0)

    # Current month boundaries (UTC)
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    _, last_day = calendar.monthrange(now.year, now.month)
    month_end = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=0)

    try:
        total_spend = await _fetch_cost_report(api_key, month_start, month_end, timeout)
    except Exception as e:
        result.error = f"Anthropic API error: {e}"
        return result

    # Show results
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


async def _fetch_cost_report(
    api_key: str,
    start: datetime,
    end: datetime,
    timeout: float,
) -> float:
    """Fetch cost report and return total spend in USD (dollars)."""
    headers = {
        "x-api-key": api_key,
        "anthropic-version": API_VERSION,
        "Content-Type": "application/json",
    }

    start_str = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    total_cents = 0.0
    page_token: Optional[str] = None

    async with aiohttp.ClientSession() as session:
        while True:
            params: dict = {
                "starting_at": start_str,
                "ending_at": end_str,
                "bucket_width": "1d",
                "limit": "31",
            }
            if page_token:
                params["page"] = page_token

            http_debug_log(
                "anthropic-api",
                "cost_report_request",
                method="GET",
                url=COST_REPORT_URL,
                headers=headers,
                payload=params,
            )
            async with session.get(
                COST_REPORT_URL,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                http_debug_log(
                    "anthropic-api",
                    "cost_report_response",
                    method="GET",
                    url=COST_REPORT_URL,
                    status=resp.status,
                )
                if resp.status == 401:
                    raise RuntimeError(
                        "Unauthorized — check your API key. "
                        "Admin keys (sk-ant-admin01-...) required for cost reports."
                    )
                if resp.status == 403:
                    raise RuntimeError(
                        "Forbidden — your key lacks admin permissions. "
                        "Get an admin key from console.anthropic.com."
                    )
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"HTTP {resp.status}: {text[:300]}")

                data = await resp.json()

            # Sum costs from all buckets
            # amount is in lowest currency units (cents) as a decimal string
            for bucket in data.get("data", []):
                for item in bucket.get("results", []):
                    amount_str = item.get("amount", "0")
                    try:
                        total_cents += float(amount_str)
                    except (ValueError, TypeError):
                        pass

            # Pagination
            if data.get("has_more") and data.get("next_page"):
                page_token = data["next_page"]
            else:
                break

    # Convert cents to dollars
    return round(total_cents / 100.0, 2)
