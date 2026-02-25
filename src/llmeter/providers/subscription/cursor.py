"""Cursor provider — fetches usage via cursor.com cookie-authenticated APIs.

Run `llmeter --login cursor` to paste your session cookie.  The cookie is
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

from ... import auth
from ...models import (
    CostInfo,
    PROVIDERS,
    ProviderIdentity,
    ProviderResult,
    RateWindow,
)
from ..helpers import http_get, parse_iso8601
from .base import SubscriptionProvider

# ── Auth constants ─────────────────────────────────────────

PROVIDER_KEY = "cursor"

# ── Provider API constants ─────────────────────────────────

BASE_URL = "https://cursor.com"
USAGE_SUMMARY_URL = f"{BASE_URL}/api/usage-summary"
AUTH_ME_URL = f"{BASE_URL}/api/auth/me"
USAGE_URL = f"{BASE_URL}/api/usage"


# ── Credential management ──────────────────────────────────

def load_credentials() -> Optional[dict]:
    """Load stored Cursor cookie credentials, or None."""
    creds = auth.load_provider(PROVIDER_KEY)
    if creds is None:
        return None
    if not creds.get("cookie"):
        return None
    return creds


def save_credentials(cookie: str, email: str | None = None) -> None:
    """Save Cursor cookie credentials."""
    creds: dict = {"type": "cookie", "cookie": cookie}
    if email:
        creds["email"] = email
    auth.save_provider(PROVIDER_KEY, creds)


def clear_credentials() -> None:
    """Remove stored Cursor credentials."""
    auth.clear_provider(PROVIDER_KEY)


# ── Provider class ─────────────────────────────────────────

class CursorProvider(SubscriptionProvider):
    """Fetches Cursor usage via cookie-authenticated APIs."""

    @property
    def provider_id(self) -> str:
        return "cursor"

    @property
    def no_credentials_error(self) -> str:
        return (
            "No Cursor credentials found. "
            "Run `llmeter --login cursor` to authenticate."
        )

    async def get_credentials(self, timeout: float) -> Optional[dict]:
        return load_credentials()

    async def _fetch(
        self,
        creds: dict,
        timeout: float,
        settings: dict,
    ) -> ProviderResult:
        result = PROVIDERS["cursor"].to_result()
        cookie = creds["cookie"]
        headers = {
            "Cookie": cookie,
            "Accept": "application/json",
            "User-Agent": "LLMeter/0.1.0",
        }

        try:
            async with aiohttp.ClientSession() as session:
                usage_data = await http_get(
                    "cursor", USAGE_SUMMARY_URL, headers, timeout,
                    label="usage_summary", session=session,
                    errors={
                        401: "Cursor session expired. Run `llmeter --login cursor` to re-authenticate.",
                        403: "Cursor session expired. Run `llmeter --login cursor` to re-authenticate.",
                    },
                )

                user_data = None
                try:
                    user_data = await http_get(
                        "cursor", AUTH_ME_URL, headers, timeout,
                        label="auth_me", session=session,
                    )
                except Exception:
                    pass

                request_data = None
                if user_data and user_data.get("sub"):
                    try:
                        url = f"{USAGE_URL}?user={user_data['sub']}"
                        request_data = await http_get(
                            "cursor", url, headers, timeout,
                            label="usage", session=session,
                        )
                    except Exception:
                        pass

        except RuntimeError as e:
            msg = str(e)
            if "session expired" in msg:
                clear_credentials()
            result.error = msg
            return result
        except Exception as e:
            result.error = f"Cursor API error: {e}"
            return result

        _parse_usage_response(usage_data, user_data, request_data, result)

        if user_data and user_data.get("email") and not creds.get("email"):
            save_credentials(cookie, email=user_data["email"])

        result.source = "cookie"
        result.updated_at = datetime.now(timezone.utc)
        return result


# ── Response parsing ───────────────────────────────────────

def _parse_usage_response(
    data: dict,
    user_data: dict | None,
    request_data: dict | None,
    result: ProviderResult,
) -> None:
    billing_end = parse_iso8601(data.get("billingCycleEnd"))
    requests_used, requests_limit = _parse_request_usage(request_data)
    is_request_plan = requests_limit is not None

    if is_request_plan:
        plan_pct = (requests_used / requests_limit) * 100 if requests_limit > 0 else 0
        result.primary_label = f"Plan {requests_used} / {requests_limit} reqs"
        result.primary = RateWindow(used_percent=plan_pct, resets_at=billing_end)
    else:
        individual = data.get("individualUsage") or {}
        plan = individual.get("plan") or {}
        plan_pct = _calc_plan_percent(plan)
        result.primary = RateWindow(used_percent=plan_pct, resets_at=billing_end)

    individual = data.get("individualUsage") or {}
    on_demand = individual.get("onDemand") or {}
    try:
        od_used_cents = float(on_demand.get("used") or 0)
    except (TypeError, ValueError):
        od_used_cents = 0.0
    try:
        od_limit_cents = float(on_demand.get("limit") or 0)
    except (TypeError, ValueError):
        od_limit_cents = 0.0

    if od_limit_cents > 0:
        od_pct = (od_used_cents / od_limit_cents) * 100
        result.secondary = RateWindow(used_percent=od_pct, resets_at=billing_end)

    if od_used_cents > 0:
        result.cost = CostInfo(
            used=od_used_cents / 100.0,
            limit=od_limit_cents / 100.0,
            currency="USD",
            period="Monthly",
        )

    membership = data.get("membershipType")
    email = (user_data or {}).get("email")
    if membership or email:
        result.identity = ProviderIdentity(
            account_email=email,
            login_method=_format_membership(membership) if membership else None,
        )


def _parse_request_usage(request_data: dict | None) -> tuple[int, int | None]:
    if not request_data:
        return (0, None)
    gpt4 = request_data.get("gpt-4") or {}
    raw_limit = gpt4.get("maxRequestUsage")
    if raw_limit is None:
        return (0, None)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return (0, None)
    try:
        used = int(gpt4.get("numRequestsTotal") or gpt4.get("numRequests") or 0)
    except (TypeError, ValueError):
        used = 0
    return (used, limit)


def _calc_plan_percent(plan: dict) -> float:
    try:
        plan_used_cents = float(plan.get("used") or 0)
    except (TypeError, ValueError):
        plan_used_cents = 0.0
    try:
        plan_limit_cents = float(plan.get("limit") or 0)
    except (TypeError, ValueError):
        plan_limit_cents = 0.0
    if plan_limit_cents > 0:
        return (plan_used_cents / plan_limit_cents) * 100
    raw = plan.get("totalPercentUsed")
    if raw is not None:
        try:
            raw = float(raw)
            return raw * 100 if raw <= 1 else raw
        except (TypeError, ValueError):
            pass
    return 0.0


def _format_membership(membership: str) -> str:
    known = {
        "pro": "Cursor Pro",
        "hobby": "Cursor Hobby",
        "enterprise": "Cursor Enterprise",
        "team": "Cursor Team",
        "business": "Cursor Business",
    }
    return known.get(membership.lower(), f"Cursor {membership.capitalize()}")


# Module-level singleton — used by backend.py and importable as a callable.
fetch_cursor = CursorProvider()
