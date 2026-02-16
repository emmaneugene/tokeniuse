"""Tests for the Cursor provider.

Covers:
1. Credential storage (cookie-based)
2. Usage endpoint calls
3. Parsing usage data into ProviderResult
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
    AUTH_ME_URL,
    _parse_usage_response,
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


class TestCursorUsageEndpoint:
    """Test the cookie-authenticated API calls."""

    async def test_fetch_with_valid_cookie(self, tmp_config_dir: Path) -> None:
        cursor_auth.save_credentials("WorkosCursorSessionToken=valid")

        with aioresponses() as mocked:
            mocked.get(USAGE_SUMMARY_URL, payload=SAMPLE_USAGE_SUMMARY)
            mocked.get(AUTH_ME_URL, payload=SAMPLE_USER_INFO)

            result = await fetch_cursor(timeout=10.0)

        assert result.error is None
        assert result.source == "cookie"
        assert result.primary is not None

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
        # Cookie should be cleared
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

            await fetch_cursor(timeout=10.0)

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
    """Test parsing of the /api/usage-summary response."""

    def _make_result(self) -> ProviderResult:
        return PROVIDERS["cursor"].to_result()

    def test_parse_pro_plan(self) -> None:
        result = self._make_result()
        _parse_usage_response(SAMPLE_USAGE_SUMMARY, SAMPLE_USER_INFO, result)

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
        _parse_usage_response(data, None, result)

        assert result.primary is not None
        assert result.primary.used_percent == 25.0
        assert result.secondary is None
        assert result.cost is None
        assert result.identity.login_method == "Cursor Hobby"

    def test_parse_enterprise_high_usage(self) -> None:
        data = {
            "membershipType": "enterprise",
            "billingCycleEnd": "2025-03-01T00:00:00.000Z",
            "individualUsage": {
                "plan": {"used": 45000, "limit": 50000},
                "onDemand": {"used": 8000, "limit": 20000},
            },
        }
        result = self._make_result()
        _parse_usage_response(data, None, result)

        assert result.primary.used_percent == 90.0
        assert result.secondary.used_percent == 40.0
        assert result.cost.used == 80.0
        assert result.cost.limit == 200.0

    def test_parse_uses_percent_when_no_limit(self) -> None:
        """When limit is 0, fall back to totalPercentUsed field."""
        data = {
            "individualUsage": {
                "plan": {"used": 0, "limit": 0, "totalPercentUsed": 0.42},
            },
        }
        result = self._make_result()
        _parse_usage_response(data, None, result)

        # 0.42 should be interpreted as 42%
        assert result.primary.used_percent == 42.0

    def test_parse_percent_already_0_to_100(self) -> None:
        """totalPercentUsed > 1 should be used as-is."""
        data = {
            "individualUsage": {
                "plan": {"used": 0, "limit": 0, "totalPercentUsed": 65.0},
            },
        }
        result = self._make_result()
        _parse_usage_response(data, None, result)

        assert result.primary.used_percent == 65.0

    def test_parse_empty_response(self) -> None:
        result = self._make_result()
        _parse_usage_response({}, None, result)

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
        _parse_usage_response(data, None, result)

        assert result.cost is None  # No cost if on_demand used is 0

    def test_format_membership_types(self) -> None:
        assert _format_membership("pro") == "Cursor Pro"
        assert _format_membership("hobby") == "Cursor Hobby"
        assert _format_membership("enterprise") == "Cursor Enterprise"
        assert _format_membership("team") == "Cursor Team"
        assert _format_membership("business") == "Cursor Business"
        assert _format_membership("custom") == "Cursor Custom"
        assert _format_membership("PRO") == "Cursor Pro"  # case-insensitive
