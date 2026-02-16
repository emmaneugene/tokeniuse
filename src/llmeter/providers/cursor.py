"""Cursor provider — fetches usage via cursor.com cookie-authenticated APIs.

Run `llmeter --login-cursor` to paste your session cookie.  The cookie is
stored in auth.json and reused until it expires (401/403).

API endpoints (all cookie-authenticated):
- GET https://cursor.com/api/usage-summary   — plan + on-demand usage (dollar-based)
- GET https://cursor.com/api/auth/me         — user email, name, sub ID
- GET https://cursor.com/api/usage?user={id} — legacy request-based usage
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import aiohttp

from ..models import (
    CostInfo,
    CreditsInfo,
    PROVIDERS,
    ProviderIdentity,
    ProviderResult,
    RateWindow,
)
from . import cursor_auth

BASE_URL = "https://cursor.com"
USAGE_SUMMARY_URL = f"{BASE_URL}/api/usage-summary"
AUTH_ME_URL = f"{BASE_URL}/api/auth/me"
USAGE_URL = f"{BASE_URL}/api/usage"


async def fetch_cursor(timeout: float = 20.0, settings: dict | None = None) -> ProviderResult:
    """Fetch Cursor usage via cookie-authenticated APIs."""
    result = PROVIDERS["cursor"].to_result()

    creds = cursor_auth.load_credentials()
    if creds is None:
        result.error = (
            "No Cursor credentials found. "
            "Run `llmeter --login-cursor` to authenticate."
        )
        return result

    cookie = creds["cookie"]
    headers = {
        "Cookie": cookie,
        "Accept": "application/json",
        "User-Agent": "LLMeter/0.1.0",
    }

    try:
        async with aiohttp.ClientSession() as session:
            # Fetch usage summary (dollar-based)
            usage_data = await _fetch_json(
                session, USAGE_SUMMARY_URL, headers, timeout
            )

            # Fetch user info (best-effort — needed for sub ID and email)
            user_data = None
            try:
                user_data = await _fetch_json(
                    session, AUTH_ME_URL, headers, timeout
                )
            except Exception:
                pass

            # Fetch legacy request usage if we have a user ID
            request_data = None
            if user_data and user_data.get("sub"):
                try:
                    url = f"{USAGE_URL}?user={user_data['sub']}"
                    request_data = await _fetch_json(
                        session, url, headers, timeout
                    )
                except Exception:
                    pass

    except RuntimeError as e:
        if "401" in str(e) or "403" in str(e):
            # Cookie expired — clear it so user gets a clean prompt
            cursor_auth.clear_credentials()
            result.error = (
                "Cursor session expired. "
                "Run `llmeter --login-cursor` to re-authenticate."
            )
        else:
            result.error = f"Cursor API error: {e}"
        return result
    except Exception as e:
        result.error = f"Cursor API error: {e}"
        return result

    _parse_usage_response(usage_data, user_data, request_data, result)

    # Persist email if we learned it
    if user_data and user_data.get("email") and not creds.get("email"):
        cursor_auth.save_credentials(cookie, email=user_data["email"])

    result.source = "cookie"
    result.updated_at = datetime.now(timezone.utc)
    return result


async def _fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    headers: dict,
    timeout: float,
) -> dict:
    """Fetch a JSON endpoint, raising RuntimeError on failure."""
    async with session.get(
        url,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        if resp.status in (401, 403):
            raise RuntimeError(f"HTTP {resp.status}: session expired")
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"HTTP {resp.status}: {body[:200]}")
        return await resp.json()


# ── Response parsing ───────────────────────────────────────
#
# /api/usage-summary (dollar-based, values in cents):
# {
#   "billingCycleEnd": "2025-02-01T00:00:00.000Z",
#   "membershipType": "pro",
#   "individualUsage": {
#     "plan": { "used": 1500, "limit": 5000, "totalPercentUsed": 30.0 },
#     "onDemand": { "used": 500, "limit": 10000 }
#   }
# }
#
# /api/usage?user=ID (request-based, for legacy/enterprise plans):
# {
#   "gpt-4": {
#     "numRequests": 138,
#     "numRequestsTotal": 138,
#     "maxRequestUsage": 500
#   }
# }
#
# When maxRequestUsage is present, the plan is request-based and the
# primary bar should show requests used/limit, NOT dollar amounts.
# This matches how Cursor's own dashboard displays usage.


def _parse_usage_response(
    data: dict,
    user_data: dict | None,
    request_data: dict | None,
    result: ProviderResult,
) -> None:
    """Parse usage responses into ProviderResult."""

    # ── Billing cycle reset ─────────────────────────
    billing_end = _parse_iso_date(data.get("billingCycleEnd"))

    # ── Check for legacy request-based plan ─────────
    requests_used, requests_limit = _parse_request_usage(request_data)
    is_request_plan = requests_limit is not None

    # ── Primary bar ─────────────────────────────────
    if is_request_plan:
        # Request-based plan: show requests used / limit
        plan_pct = (requests_used / requests_limit) * 100 if requests_limit > 0 else 0
        result.primary_label = f"Plan {requests_used} / {requests_limit} reqs"
        result.primary = RateWindow(
            used_percent=plan_pct,
            resets_at=billing_end,
        )
    else:
        # Dollar-based plan: show dollar usage percentage
        individual = data.get("individualUsage") or {}
        plan = individual.get("plan") or {}
        plan_pct = _calc_plan_percent(plan)
        result.primary = RateWindow(
            used_percent=plan_pct,
            resets_at=billing_end,
        )

    # ── On-demand usage (secondary bar) ─────────────
    individual = data.get("individualUsage") or {}
    on_demand = individual.get("onDemand") or {}
    od_used_cents = on_demand.get("used", 0) or 0
    od_limit_cents = on_demand.get("limit")

    if od_limit_cents and od_limit_cents > 0:
        od_pct = (od_used_cents / od_limit_cents) * 100
        result.secondary = RateWindow(
            used_percent=od_pct,
            resets_at=billing_end,
        )

    # ── Cost info (on-demand spend in USD) ──────────
    if od_used_cents > 0:
        result.cost = CostInfo(
            used=od_used_cents / 100.0,
            limit=(od_limit_cents or 0) / 100.0,
            currency="USD",
            period="Monthly",
        )

    # ── Identity ────────────────────────────────────
    membership = data.get("membershipType")
    email = (user_data or {}).get("email")
    if membership or email:
        result.identity = ProviderIdentity(
            account_email=email,
            login_method=_format_membership(membership) if membership else None,
        )


def _parse_request_usage(request_data: dict | None) -> tuple[int, int | None]:
    """Extract request counts from /api/usage response.

    Returns (requests_used, requests_limit).
    requests_limit is None if this is not a request-based plan.
    """
    if not request_data:
        return (0, None)

    gpt4 = request_data.get("gpt-4") or {}
    limit = gpt4.get("maxRequestUsage")
    if limit is None:
        return (0, None)

    # Prefer numRequestsTotal (includes all request types),
    # fall back to numRequests
    used = gpt4.get("numRequestsTotal") or gpt4.get("numRequests") or 0
    return (used, limit)


def _calc_plan_percent(plan: dict) -> float:
    """Calculate plan usage percentage from dollar-based fields."""
    plan_used_cents = plan.get("used", 0) or 0
    plan_limit_cents = plan.get("limit", 0) or 0

    if plan_limit_cents > 0:
        return (plan_used_cents / plan_limit_cents) * 100

    if plan.get("totalPercentUsed") is not None:
        raw = plan["totalPercentUsed"]
        # API may return 0-1 or 0-100
        return raw * 100 if raw <= 1 else raw

    return 0.0


def _format_membership(membership: str) -> str:
    """Format membership type for display."""
    known = {
        "pro": "Cursor Pro",
        "hobby": "Cursor Hobby",
        "enterprise": "Cursor Enterprise",
        "team": "Cursor Team",
        "business": "Cursor Business",
    }
    return known.get(membership.lower(), f"Cursor {membership.capitalize()}")


def _parse_iso_date(s: str | None) -> datetime | None:
    """Parse an ISO 8601 date string, or None."""
    if not s:
        return None
    try:
        # Handle both "2025-01-01T00:00:00.000Z" and "2025-01-01T00:00:00Z"
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
