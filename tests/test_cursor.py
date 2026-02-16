"""Tests for the Cursor provider.

Covers:
1. Credential storage (cookie-based)
2. Usage endpoint calls
3. Parsing usage data into ProviderResult (dollar-based and request-based plans)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aioresponses import aioresponses

from llmeter.providers import cursor_auth
from llmeter.providers.cursor import (
    fetch_cursor,
    USAGE_SUMMARY_URL,
    USAGE_URL,
    AUTH_ME_URL,
    _parse_usage_response,
    _parse_request_usage,
    _format_membership,
)
from llmeter.models import ProviderResult, PROVIDERS


# ── 1. Credential storage ──────────────────────────────────


class TestCursorCredentials:
    """Test cookie credential storage via the unified auth store."""

    def test_save_and_load(self, tmp_config_dir: Path) -> None:
        cursor_auth.save_credentials(
            "WorkosCursorSessionToken=abc123; other=val",
            email="user@example.com",
        )
        loaded = cursor_auth.load_credentials()
        assert loaded is not None
        assert loaded["type"] == "cookie"
        assert loaded["cookie"] == "WorkosCursorSessionToken=abc123; other=val"
        assert loaded["email"] == "user@example.com"

    def test_load_returns_none_when_empty(self, tmp_config_dir: Path) -> None:
        assert cursor_auth.load_credentials() is None

    def test_load_requires_cookie_field(self, tmp_config_dir: Path) -> None:
        """Credentials without a cookie value should be treated as invalid."""
        from llmeter import auth
        auth.save_provider("cursor", {"type": "cookie", "cookie": ""})
        assert cursor_auth.load_credentials() is None

    def test_clear_credentials(self, tmp_config_dir: Path) -> None:
        cursor_auth.save_credentials("token=abc")
        cursor_auth.clear_credentials()
        assert cursor_auth.load_credentials() is None

    def test_save_without_email(self, tmp_config_dir: Path) -> None:
        cursor_auth.save_credentials("token=abc")
        loaded = cursor_auth.load_credentials()
        assert loaded is not None
        assert "email" not in loaded

    def test_coexists_with_other_providers(self, tmp_config_dir: Path) -> None:
        """Cursor credentials shouldn't interfere with OAuth providers."""
        from llmeter import auth
        auth.save_provider("anthropic", {
            "type": "oauth", "access": "x", "refresh": "y", "expires": 0,
        })
        cursor_auth.save_credentials("token=abc", email="me@x.com")

        # Both should be loadable
        assert auth.load_provider("anthropic") is not None
        assert cursor_auth.load_credentials() is not None


# ── 2. Usage endpoint calls ────────────────────────────────


SAMPLE_USAGE_SUMMARY = {
    "billingCycleStart": "2025-01-01T00:00:00.000Z",
    "billingCycleEnd": "2025-02-01T00:00:00.000Z",
    "membershipType": "pro",
    "individualUsage": {
        "plan": {
            "enabled": True,
            "used": 1500,
            "limit": 5000,
            "remaining": 3500,
            "totalPercentUsed": 30.0,
        },
        "onDemand": {
            "enabled": True,
            "used": 500,
            "limit": 10000,
            "remaining": 9500,
        },
    },
}

SAMPLE_USER_INFO = {
    "email": "user@example.com",
    "email_verified": True,
    "name": "Test User",
    "sub": "auth0|12345",
}

# Enterprise request-based plan
SAMPLE_ENTERPRISE_SUMMARY = {
    "billingCycleStart": "2026-02-01T00:00:00.000Z",
    "billingCycleEnd": "2026-03-01T00:00:00.000Z",
    "membershipType": "enterprise",
    "individualUsage": {
        "plan": {
            "enabled": True,
            "used": 40500,
            "limit": 50000,
        },
        "onDemand": {
            "enabled": True,
            "used": 0,
            "limit": 0,
        },
    },
}

SAMPLE_REQUEST_USAGE = {
    "gpt-4": {
        "numRequests": 138,
        "numRequestsTotal": 138,
        "numTokens": 50000,
        "maxRequestUsage": 500,
        "maxTokenUsage": None,
    },
    "startOfMonth": "2026-02-01",
}


class TestCursorUsageEndpoint:
    """Test the cookie-authenticated API calls."""

    async def test_fetch_with_valid_cookie(self, tmp_config_dir: Path) -> None:
        cursor_auth.save_credentials("WorkosCursorSessionToken=valid")

        with aioresponses() as mocked:
            mocked.get(USAGE_SUMMARY_URL, payload=SAMPLE_USAGE_SUMMARY)
            mocked.get(AUTH_ME_URL, payload=SAMPLE_USER_INFO)
            mocked.get(
                f"{USAGE_URL}?user=auth0%7C12345",
                payload={"gpt-4": {"numRequests": 10, "maxRequestUsage": None}},
            )

            result = await fetch_cursor(timeout=10.0)

        assert result.error is None
        assert result.source == "cookie"
        assert result.primary is not None

    async def test_fetch_enterprise_with_requests(self, tmp_config_dir: Path) -> None:
        """Enterprise plan should use request counts, not dollar amounts."""
        cursor_auth.save_credentials("WorkosCursorSessionToken=valid")

        with aioresponses() as mocked:
            mocked.get(USAGE_SUMMARY_URL, payload=SAMPLE_ENTERPRISE_SUMMARY)
            mocked.get(AUTH_ME_URL, payload=SAMPLE_USER_INFO)
            mocked.get(
                f"{USAGE_URL}?user=auth0%7C12345",
                payload=SAMPLE_REQUEST_USAGE,
            )

            result = await fetch_cursor(timeout=10.0)

        assert result.error is None
        # 138/500 = 27.6%, NOT the dollar-based 81%
        assert result.primary is not None
        assert result.primary.used_percent == pytest.approx(27.6)
        assert result.primary_label == "Plan 138 / 500 reqs"

    async def test_fetch_without_credentials(self, tmp_config_dir: Path) -> None:
        result = await fetch_cursor(timeout=5.0)
        assert result.error is not None
        assert "No Cursor credentials" in result.error

    async def test_fetch_clears_on_401(self, tmp_config_dir: Path) -> None:
        cursor_auth.save_credentials("expired=cookie")

        with aioresponses() as mocked:
            mocked.get(USAGE_SUMMARY_URL, status=401)

            result = await fetch_cursor(timeout=5.0)

        assert result.error is not None
        assert "expired" in result.error.lower()
        assert cursor_auth.load_credentials() is None

    async def test_fetch_clears_on_403(self, tmp_config_dir: Path) -> None:
        cursor_auth.save_credentials("forbidden=cookie")

        with aioresponses() as mocked:
            mocked.get(USAGE_SUMMARY_URL, status=403)

            result = await fetch_cursor(timeout=5.0)

        assert result.error is not None
        assert cursor_auth.load_credentials() is None

    async def test_fetch_persists_email(self, tmp_config_dir: Path) -> None:
        """Should save email to auth.json after learning it from /auth/me."""
        cursor_auth.save_credentials("WorkosCursorSessionToken=tok")

        with aioresponses() as mocked:
            mocked.get(USAGE_SUMMARY_URL, payload=SAMPLE_USAGE_SUMMARY)
            mocked.get(AUTH_ME_URL, payload=SAMPLE_USER_INFO)
            # No request usage endpoint needed — no sub-based fetch will fail gracefully

            result = await fetch_cursor(timeout=10.0)

        creds = cursor_auth.load_credentials()
        assert creds is not None
        assert creds["email"] == "user@example.com"

    async def test_fetch_survives_auth_me_failure(self, tmp_config_dir: Path) -> None:
        """Should still return usage if /auth/me fails."""
        cursor_auth.save_credentials("WorkosCursorSessionToken=tok")

        with aioresponses() as mocked:
            mocked.get(USAGE_SUMMARY_URL, payload=SAMPLE_USAGE_SUMMARY)
            mocked.get(AUTH_ME_URL, status=500)

            result = await fetch_cursor(timeout=10.0)

        assert result.error is None
        assert result.primary is not None


# ── 3. Parsing usage data ──────────────────────────────────


class TestCursorUsageParsing:
    """Test parsing of the API responses."""

    def _make_result(self) -> ProviderResult:
        return PROVIDERS["cursor"].to_result()

    # ── Dollar-based plans ──────────────────────────

    def test_parse_pro_plan_dollar_based(self) -> None:
        result = self._make_result()
        _parse_usage_response(SAMPLE_USAGE_SUMMARY, SAMPLE_USER_INFO, None, result)

        # Plan: 1500/5000 cents = 30%
        assert result.primary is not None
        assert result.primary.used_percent == 30.0
        assert result.primary.resets_at is not None

        # On-demand: 500/10000 cents = 5%
        assert result.secondary is not None
        assert result.secondary.used_percent == 5.0

        # Cost: $5.00 used / $100.00 limit
        assert result.cost is not None
        assert result.cost.used == 5.0
        assert result.cost.limit == 100.0

        # Identity
        assert result.identity is not None
        assert result.identity.login_method == "Cursor Pro"
        assert result.identity.account_email == "user@example.com"

    def test_parse_hobby_no_on_demand(self) -> None:
        data = {
            "membershipType": "hobby",
            "individualUsage": {
                "plan": {"used": 500, "limit": 2000},
            },
        }
        result = self._make_result()
        _parse_usage_response(data, None, None, result)

        assert result.primary is not None
        assert result.primary.used_percent == 25.0
        assert result.secondary is None
        assert result.cost is None
        assert result.identity.login_method == "Cursor Hobby"

    def test_parse_uses_percent_when_no_limit(self) -> None:
        """When limit is 0, fall back to totalPercentUsed field."""
        data = {
            "individualUsage": {
                "plan": {"used": 0, "limit": 0, "totalPercentUsed": 0.42},
            },
        }
        result = self._make_result()
        _parse_usage_response(data, None, None, result)

        assert result.primary.used_percent == 42.0

    def test_parse_percent_already_0_to_100(self) -> None:
        """totalPercentUsed > 1 should be used as-is."""
        data = {
            "individualUsage": {
                "plan": {"used": 0, "limit": 0, "totalPercentUsed": 65.0},
            },
        }
        result = self._make_result()
        _parse_usage_response(data, None, None, result)

        assert result.primary.used_percent == 65.0

    def test_parse_empty_response(self) -> None:
        result = self._make_result()
        _parse_usage_response({}, None, None, result)

        assert result.primary is not None
        assert result.primary.used_percent == 0.0
        assert result.secondary is None
        assert result.cost is None

    def test_parse_no_on_demand_cost_when_zero(self) -> None:
        data = {
            "individualUsage": {
                "plan": {"used": 100, "limit": 5000},
                "onDemand": {"used": 0, "limit": 10000},
            },
        }
        result = self._make_result()
        _parse_usage_response(data, None, None, result)

        assert result.cost is None

    # ── Request-based plans (enterprise) ────────────

    def test_parse_enterprise_request_plan(self) -> None:
        """Enterprise plan with maxRequestUsage should use request counts."""
        result = self._make_result()
        _parse_usage_response(
            SAMPLE_ENTERPRISE_SUMMARY,
            {"email": "user@company.com"},
            SAMPLE_REQUEST_USAGE,
            result,
        )

        # 138/500 requests = 27.6%
        assert result.primary is not None
        assert result.primary.used_percent == pytest.approx(27.6)
        assert result.primary_label == "Plan 138 / 500 reqs"
        assert result.identity.login_method == "Cursor Enterprise"
        assert result.identity.account_email == "user@company.com"

    def test_parse_request_plan_at_limit(self) -> None:
        request_data = {
            "gpt-4": {
                "numRequests": 500,
                "numRequestsTotal": 500,
                "maxRequestUsage": 500,
            },
        }
        result = self._make_result()
        _parse_usage_response(
            SAMPLE_ENTERPRISE_SUMMARY, None, request_data, result,
        )

        assert result.primary.used_percent == 100.0
        assert result.primary_label == "Plan 500 / 500 reqs"

    def test_parse_request_plan_prefers_total(self) -> None:
        """Should prefer numRequestsTotal over numRequests."""
        request_data = {
            "gpt-4": {
                "numRequests": 120,
                "numRequestsTotal": 240,
                "maxRequestUsage": 500,
            },
        }
        result = self._make_result()
        _parse_usage_response({}, None, request_data, result)

        assert result.primary.used_percent == pytest.approx(48.0)
        assert result.primary_label == "Plan 240 / 500 reqs"

    def test_parse_request_usage_no_max_is_not_request_plan(self) -> None:
        """Without maxRequestUsage, should fall back to dollar-based."""
        request_data = {
            "gpt-4": {
                "numRequests": 100,
                "maxRequestUsage": None,
            },
        }
        used, limit = _parse_request_usage(request_data)
        assert limit is None  # not a request-based plan

    def test_parse_request_usage_missing_gpt4(self) -> None:
        used, limit = _parse_request_usage({})
        assert limit is None

    def test_parse_request_usage_none(self) -> None:
        used, limit = _parse_request_usage(None)
        assert limit is None

    # ── Dollar-based still works when request data absent ───

    def test_dollar_plan_when_no_request_data(self) -> None:
        """Without request data, enterprise plan uses dollar amounts."""
        result = self._make_result()
        _parse_usage_response(
            SAMPLE_ENTERPRISE_SUMMARY, None, None, result,
        )

        # Dollar-based: 40500/50000 = 81%
        assert result.primary.used_percent == 81.0
        assert result.primary_label == "Plan"  # default, no request counts

    # ── Misc ────────────────────────────────────────

    def test_format_membership_types(self) -> None:
        assert _format_membership("pro") == "Cursor Pro"
        assert _format_membership("hobby") == "Cursor Hobby"
        assert _format_membership("enterprise") == "Cursor Enterprise"
        assert _format_membership("team") == "Cursor Team"
        assert _format_membership("business") == "Cursor Business"
        assert _format_membership("custom") == "Cursor Custom"
        assert _format_membership("PRO") == "Cursor Pro"
