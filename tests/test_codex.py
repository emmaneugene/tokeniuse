"""Tests for the Codex (OpenAI) provider.

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
from llmeter.providers.subscription.codex import (
    save_credentials,
    load_credentials,
    clear_credentials,
    TOKEN_URL,
    extract_account_id,
    refresh_access_token,
    fetch_codex,
    USAGE_URL,
)


# ── 1. Credential generation / persistence ─────────────────


class TestCodexCredentials:
    """Test credential storage and refresh via the unified auth store."""

    def test_save_and_load(self, tmp_config_dir: Path) -> None:
        creds = {
            "type": "oauth",
            "access": "codex-access",
            "refresh": "codex-refresh",
            "expires": int(time.time() * 1000) + 3600_000,
            "accountId": "acct-123",
        }
        save_credentials(creds)

        loaded = load_credentials()
        assert loaded is not None
        assert loaded["access"] == "codex-access"
        assert loaded["accountId"] == "acct-123"

    def test_load_returns_none_when_empty(self, tmp_config_dir: Path) -> None:
        assert load_credentials() is None

    def test_load_requires_account_id(self, tmp_config_dir: Path) -> None:
        """Credentials without accountId should be treated as invalid."""
        auth.save_provider("openai-codex", {
            "type": "oauth", "access": "tok", "refresh": "ref", "expires": 0,
            # missing accountId
        })
        assert load_credentials() is None

    def test_clear_credentials(self, tmp_config_dir: Path) -> None:
        save_credentials({
            "type": "oauth", "access": "x", "refresh": "y",
            "expires": 0, "accountId": "a",
        })
        clear_credentials()
        assert load_credentials() is None

    def test_extract_account_id_from_jwt(self) -> None:
        """Test JWT parsing for account ID extraction."""
        import base64

        header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
        payload_data = {
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "test-account-456"
            }
        }
        payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).rstrip(b"=").decode()
        fake_jwt = f"{header}.{payload}.fakesig"

        account_id = extract_account_id(fake_jwt)
        assert account_id == "test-account-456"

    def test_extract_account_id_returns_none_for_bad_jwt(self) -> None:
        assert extract_account_id("not.a.valid-jwt") is None

    async def test_refresh_success(self, tmp_config_dir: Path) -> None:
        import base64

        header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
        payload_data = {
            "https://api.openai.com/auth": {"chatgpt_account_id": "acct-refreshed"}
        }
        payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).rstrip(b"=").decode()
        new_jwt = f"{header}.{payload}.sig"

        old_creds = {
            "type": "oauth",
            "access": "old-access",
            "refresh": "old-refresh",
            "expires": 0,
            "accountId": "acct-old",
        }
        save_credentials(old_creds)

        with aioresponses() as mocked:
            mocked.post(TOKEN_URL, payload={
                "access_token": new_jwt,
                "refresh_token": "new-refresh",
                "expires_in": 7200,
            })

            new_creds = await refresh_access_token(old_creds)

        assert new_creds["access"] == new_jwt
        assert new_creds["refresh"] == "new-refresh"
        assert new_creds["accountId"] == "acct-refreshed"

    async def test_refresh_failure(self, tmp_config_dir: Path) -> None:
        creds = {
            "type": "oauth", "access": "x", "refresh": "bad",
            "expires": 0, "accountId": "a",
        }

        with aioresponses() as mocked:
            mocked.post(TOKEN_URL, status=400, body="Bad Request")

            with pytest.raises(RuntimeError, match="Token refresh failed"):
                await refresh_access_token(creds)

    async def test_fetch_reports_refresh_failure(self, tmp_config_dir: Path) -> None:
        save_credentials({
            "type": "oauth", "access": "x", "refresh": "bad",
            "expires": 0, "accountId": "acct",
        })

        with aioresponses() as mocked:
            mocked.post(TOKEN_URL, status=400, body="Bad Request")

            result = await fetch_codex(timeout=5.0)

        assert result.error is not None
        assert "token refresh failed" in result.error.lower()
        assert "re-authenticate" in result.error


# ── 2. Usage endpoint calls ────────────────────────────────


# Real /wham/usage response format per CodexBar docs
SAMPLE_USAGE_RESPONSE = {
    "plan_type": "plus",
    "rate_limit": {
        "primary_window": {
            "used_percent": 35,
            "reset_at": 1771200000,
            "limit_window_seconds": 18000,
        },
        "secondary_window": {
            "used_percent": 12,
            "reset_at": 1771920000,
            "limit_window_seconds": 604800,
        },
    },
    "credits": {
        "has_credits": True,
        "unlimited": False,
        "balance": 42.50,
    },
}


class TestCodexUsageEndpoint:
    """Test the direct API usage call."""

    def test_usage_url_is_wham(self) -> None:
        """Verify we're hitting the correct endpoint."""
        assert USAGE_URL == "https://chatgpt.com/backend-api/wham/usage"

    async def test_fetch_with_valid_credentials(self, tmp_config_dir: Path) -> None:
        future = int(time.time() * 1000) + 3600_000
        save_credentials({
            "type": "oauth", "access": "test-tok", "refresh": "ref",
            "expires": future, "accountId": "acct-test",
        })

        with aioresponses() as mocked:
            mocked.get(USAGE_URL, payload=SAMPLE_USAGE_RESPONSE)

            result = await fetch_codex(timeout=10.0)

        assert result.error is None
        assert result.primary is not None
        assert result.primary.used_percent == 35

    async def test_fetch_without_credentials(self, tmp_config_dir: Path) -> None:
        result = await fetch_codex(timeout=5.0)
        assert result.error is not None
        assert "No Codex credentials" in result.error


# ── 3. Parsing usage data ─────────────────────────────────


class TestCodexUsageParsing:
    """Test parsing of the /wham/usage response into ProviderResult fields."""

    async def test_parse_full_response(self, tmp_config_dir: Path) -> None:
        future = int(time.time() * 1000) + 3600_000
        save_credentials({
            "type": "oauth", "access": "tok", "refresh": "ref",
            "expires": future, "accountId": "acct",
        })

        with aioresponses() as mocked:
            mocked.get(USAGE_URL, payload=SAMPLE_USAGE_RESPONSE)

            result = await fetch_codex(timeout=10.0)

        # Primary (5h window: 18000 seconds = 300 minutes)
        assert result.primary is not None
        assert result.primary.used_percent == 35
        assert result.primary.window_minutes == 300
        assert result.primary.resets_at is not None

        # Secondary (7d window: 604800 seconds = 10080 minutes)
        assert result.secondary is not None
        assert result.secondary.used_percent == 12
        assert result.secondary.window_minutes == 10080
        assert result.secondary.resets_at is not None

        # Credits
        assert result.credits is not None
        assert result.credits.remaining == 42.50

        # Identity
        assert result.identity is not None
        assert result.identity.login_method == "ChatGPT Plus"

    async def test_parse_pro_plan(self, tmp_config_dir: Path) -> None:
        future = int(time.time() * 1000) + 3600_000
        save_credentials({
            "type": "oauth", "access": "tok", "refresh": "ref",
            "expires": future, "accountId": "acct",
        })

        pro_response = {
            "plan_type": "pro",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 50,
                    "reset_at": 1771200000,
                    "limit_window_seconds": 18000,
                },
            },
            "credits": {
                "has_credits": True,
                "unlimited": True,
                "balance": None,
            },
        }

        with aioresponses() as mocked:
            mocked.get(USAGE_URL, payload=pro_response)

            result = await fetch_codex(timeout=10.0)

        assert result.primary is not None
        assert result.primary.used_percent == 50
        assert result.secondary is None
        assert result.identity is not None
        assert result.identity.login_method == "ChatGPT Pro"

    async def test_parse_no_rate_limit(self, tmp_config_dir: Path) -> None:
        """Response with plan_type but no rate_limit."""
        future = int(time.time() * 1000) + 3600_000
        save_credentials({
            "type": "oauth", "access": "tok", "refresh": "ref",
            "expires": future, "accountId": "acct",
        })

        with aioresponses() as mocked:
            mocked.get(USAGE_URL, payload={"plan_type": "free"})

            result = await fetch_codex(timeout=10.0)

        assert result.error is None
        assert result.primary is None
        assert result.secondary is None
        assert result.identity is not None
        assert result.identity.login_method == "ChatGPT Free"

    async def test_parse_empty_response(self, tmp_config_dir: Path) -> None:
        """Empty object should result in no windows but no error either."""
        future = int(time.time() * 1000) + 3600_000
        save_credentials({
            "type": "oauth", "access": "tok", "refresh": "ref",
            "expires": future, "accountId": "acct",
        })

        with aioresponses() as mocked:
            mocked.get(USAGE_URL, payload={})

            result = await fetch_codex(timeout=10.0)

        assert result.error is None
        assert result.primary is None
        assert result.secondary is None
