"""Tests for the Claude (Anthropic) provider.

Covers:
1. Credential generation / persistence
2. Usage endpoint calls
3. Parsing usage data into ProviderResult
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from aioresponses import aioresponses

from llmeter import auth
from llmeter.providers.subscription.claude import (
    save_credentials,
    load_credentials,
    clear_credentials,
    is_token_expired,
    TOKEN_URL,
    refresh_access_token,
    get_valid_access_token,
    fetch_claude,
)


# ── 1. Credential generation / persistence ─────────────────


class TestClaudeCredentials:
    """Test credential storage and refresh via the unified auth store."""

    def test_save_and_load(self, tmp_config_dir: Path) -> None:
        creds = {
            "type": "oauth",
            "refresh": "ref-tok",
            "access": "acc-tok",
            "expires": int(time.time() * 1000) + 3600_000,
        }
        save_credentials(creds)

        loaded = load_credentials()
        assert loaded is not None
        assert loaded["access"] == "acc-tok"
        assert loaded["refresh"] == "ref-tok"

    def test_load_returns_none_when_empty(self, tmp_config_dir: Path) -> None:
        assert load_credentials() is None

    def test_clear_credentials(self, tmp_config_dir: Path) -> None:
        save_credentials({
            "type": "oauth", "access": "x", "refresh": "y", "expires": 0,
        })
        clear_credentials()
        assert load_credentials() is None

    def test_expired_token_detected(self, tmp_config_dir: Path) -> None:
        creds = {"type": "oauth", "access": "x", "refresh": "y", "expires": 0}
        assert is_token_expired(creds) is True

    def test_valid_token_not_expired(self, tmp_config_dir: Path) -> None:
        future = int(time.time() * 1000) + 3600_000
        creds = {"type": "oauth", "access": "x", "refresh": "y", "expires": future}
        assert is_token_expired(creds) is False

    async def test_refresh_success(self, tmp_config_dir: Path) -> None:
        old_creds = {
            "type": "oauth",
            "refresh": "old-refresh",
            "access": "old-access",
            "expires": 0,
        }
        save_credentials(old_creds)

        with aioresponses() as mocked:
            mocked.post(TOKEN_URL, payload={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 7200,
            })

            new_creds = await refresh_access_token(old_creds)

        assert new_creds["access"] == "new-access"
        assert new_creds["refresh"] == "new-refresh"
        assert new_creds["expires"] > int(time.time() * 1000)

        # Should be persisted
        loaded = load_credentials()
        assert loaded["access"] == "new-access"

    async def test_refresh_failure_raises(self, tmp_config_dir: Path) -> None:
        creds = {"type": "oauth", "refresh": "bad-token", "access": "old", "expires": 0}

        with aioresponses() as mocked:
            mocked.post(TOKEN_URL, status=401, body="Unauthorized")

            with pytest.raises(RuntimeError, match="Token refresh failed"):
                await refresh_access_token(creds)

    async def test_get_valid_access_token_returns_none_when_no_creds(self, tmp_config_dir: Path) -> None:
        result = await get_valid_access_token()
        assert result is None

    async def test_fetch_reports_refresh_failure(self, tmp_config_dir: Path) -> None:
        save_credentials({
            "type": "oauth", "refresh": "bad-token", "access": "old", "expires": 0,
        })

        with aioresponses() as mocked:
            mocked.post(TOKEN_URL, status=401, body="Unauthorized")

            result = await fetch_claude(timeout=5.0)

        assert result.error is not None
        assert "token refresh failed" in result.error.lower()
        assert "re-authenticate" in result.error


# ── 2. Usage endpoint calls ────────────────────────────────


SAMPLE_USAGE_RESPONSE = {
    "five_hour": {
        "utilization": 42.5,
        "resets_at": "2026-02-16T06:00:00Z",
    },
    "seven_day": {
        "utilization": 15.0,
        "resets_at": "2026-02-22T00:00:00Z",
    },
    "seven_day_sonnet": {
        "utilization": 8.0,
        "resets_at": "2026-02-22T00:00:00Z",
    },
    "extra_usage": {
        "is_enabled": True,
        "used_credits": 350,
        "monthly_limit": 10000,
        "currency": "USD",
    },
}


class TestClaudeUsageEndpoint:
    """Test the usage API call and response handling."""

    async def test_fetch_with_valid_credentials(self, tmp_config_dir: Path) -> None:
        future = int(time.time() * 1000) + 3600_000
        save_credentials({
            "type": "oauth", "access": "test-token", "refresh": "ref", "expires": future,
        })

        with aioresponses() as mocked:
            mocked.get(
                "https://api.anthropic.com/api/oauth/usage",
                payload=SAMPLE_USAGE_RESPONSE,
            )
            mocked.get(
                "https://api.anthropic.com/api/oauth/profile",
                payload={
                    "account": {"email": "user@example.com", "has_claude_pro": True},
                    "organization": {},
                },
            )

            result = await fetch_claude(timeout=10.0)

        assert result.error is None
        assert result.primary is not None
        assert result.primary.used_percent == 42.5

    async def test_fetch_without_credentials(self, tmp_config_dir: Path) -> None:
        result = await fetch_claude(timeout=5.0)
        assert result.error is not None
        assert "No Claude credentials" in result.error


# ── 3. Parsing usage data ─────────────────────────────────


class TestClaudeUsageParsing:
    """Test parsing of the Claude usage API response into ProviderResult fields."""

    async def test_parse_all_windows(self, tmp_config_dir: Path) -> None:
        future = int(time.time() * 1000) + 3600_000
        save_credentials({
            "type": "oauth", "access": "test-token", "refresh": "ref", "expires": future,
        })

        with aioresponses() as mocked:
            mocked.get(
                "https://api.anthropic.com/api/oauth/usage",
                payload=SAMPLE_USAGE_RESPONSE,
            )
            mocked.get(
                "https://api.anthropic.com/api/oauth/profile",
                payload={
                    "account": {"email": "test@test.com", "has_claude_max": True},
                    "organization": {},
                },
            )

            result = await fetch_claude(timeout=10.0)

        # Primary (5h session)
        assert result.primary is not None
        assert result.primary.used_percent == 42.5
        assert result.primary.window_minutes == 300
        assert result.primary.resets_at is not None

        # Secondary (7-day)
        assert result.secondary is not None
        assert result.secondary.used_percent == 15.0
        assert result.secondary.window_minutes == 10080

        # Tertiary (Sonnet)
        assert result.tertiary is not None
        assert result.tertiary.used_percent == 8.0
        assert result.tertiary_label == "Sonnet"

        # Cost (extra usage)
        assert result.cost is not None
        assert result.cost.used == 3.50  # 350 cents → $3.50
        assert result.cost.limit == 100.0  # 10000 cents → $100

        # Identity
        assert result.identity is not None
        assert result.identity.account_email == "test@test.com"
        assert result.identity.login_method == "Claude Max"

    async def test_parse_minimal_response(self, tmp_config_dir: Path) -> None:
        """Only five_hour present, everything else absent."""
        future = int(time.time() * 1000) + 3600_000
        save_credentials({
            "type": "oauth", "access": "tok", "refresh": "ref", "expires": future,
        })

        with aioresponses() as mocked:
            mocked.get(
                "https://api.anthropic.com/api/oauth/usage",
                payload={"five_hour": {"utilization": 10.0}},
            )
            mocked.get(
                "https://api.anthropic.com/api/oauth/profile",
                status=404,
            )

            result = await fetch_claude(timeout=10.0)

        assert result.error is None
        assert result.primary is not None
        assert result.primary.used_percent == 10.0
        assert result.secondary is None
        assert result.tertiary is None
        assert result.cost is None
