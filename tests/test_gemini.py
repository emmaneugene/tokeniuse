"""Tests for the Gemini (Google Cloud Code Assist) provider.

Covers:
1. Credential generation / persistence (self-contained, no gemini CLI)
2. Usage endpoint calls (quota + loadCodeAssist)
3. Parsing quota buckets into ProviderResult
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from aioresponses import aioresponses

from llmeter import auth
from llmeter.providers.subscription.gemini import (
    save_credentials,
    load_credentials,
    clear_credentials,
    TOKEN_URL,
    refresh_access_token,
    get_valid_credentials,
    fetch_gemini,
    _fetch_quota,
    _load_code_assist,
    _tier_to_plan,
)


QUOTA_URL = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota"
LOAD_CA_URL = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"


# ── 1. Credential generation / persistence ─────────────────


class TestGeminiCredentials:
    """Test credential storage and refresh via the unified auth store."""

    def test_save_and_load(self, tmp_config_dir: Path) -> None:
        creds = {
            "type": "oauth",
            "refresh": "gem-refresh",
            "access": "gem-access",
            "expires": int(time.time() * 1000) + 3600_000,
            "projectId": "gen-lang-client-1234",
            "email": "user@gmail.com",
        }
        save_credentials(creds)

        loaded = load_credentials()
        assert loaded is not None
        assert loaded["access"] == "gem-access"
        assert loaded["projectId"] == "gen-lang-client-1234"
        assert loaded["email"] == "user@gmail.com"

    def test_load_returns_none_when_empty(self, tmp_config_dir: Path) -> None:
        assert load_credentials() is None

    def test_clear_credentials(self, tmp_config_dir: Path) -> None:
        save_credentials({
            "type": "oauth", "access": "x", "refresh": "y",
            "expires": 0, "projectId": "p", "email": "e",
        })
        clear_credentials()
        assert load_credentials() is None

    def test_stored_under_correct_provider_key(self, tmp_config_dir: Path) -> None:
        """Verify credentials are stored under 'google-gemini-cli' in auth.json."""
        save_credentials({
            "type": "oauth", "access": "tok", "refresh": "ref",
            "expires": 0, "projectId": "p", "email": "e",
        })
        all_data = auth.load_all()
        assert "google-gemini-cli" in all_data
        assert all_data["google-gemini-cli"]["access"] == "tok"

    def test_coexists_with_other_providers(self, tmp_config_dir: Path) -> None:
        """Multiple providers in auth.json don't interfere."""
        auth.save_provider("anthropic", {"type": "oauth", "access": "a1", "refresh": "r1", "expires": 0})
        save_credentials({
            "type": "oauth", "access": "g1", "refresh": "gr1",
            "expires": 0, "projectId": "p", "email": "e",
        })

        assert auth.load_provider("anthropic")["access"] == "a1"
        assert load_credentials()["access"] == "g1"

    async def test_refresh_success(self, tmp_config_dir: Path) -> None:
        old_creds = {
            "type": "oauth",
            "refresh": "old-refresh",
            "access": "old-access",
            "expires": 0,
            "projectId": "my-project",
            "email": "user@test.com",
        }
        save_credentials(old_creds)

        with aioresponses() as mocked:
            mocked.post(TOKEN_URL, payload={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            })

            new_creds = await refresh_access_token(old_creds)

        assert new_creds["access"] == "new-access"
        assert new_creds["refresh"] == "new-refresh"
        assert new_creds["projectId"] == "my-project"
        assert new_creds["expires"] > int(time.time() * 1000)

        # Should be persisted
        loaded = load_credentials()
        assert loaded["access"] == "new-access"

    async def test_refresh_failure_raises(self, tmp_config_dir: Path) -> None:
        creds = {
            "type": "oauth", "refresh": "bad-token", "access": "old",
            "expires": 0, "projectId": "p", "email": "e",
        }

        with aioresponses() as mocked:
            mocked.post(TOKEN_URL, status=401, body="Invalid grant")

            with pytest.raises(RuntimeError, match="Token refresh failed"):
                await refresh_access_token(creds)

    async def test_get_valid_credentials_returns_none_when_empty(self, tmp_config_dir: Path) -> None:
        result = await get_valid_credentials()
        assert result is None

    async def test_get_valid_credentials_refreshes_expired(self, tmp_config_dir: Path) -> None:
        save_credentials({
            "type": "oauth", "refresh": "ref", "access": "expired-tok",
            "expires": 0, "projectId": "p", "email": "e",
        })

        with aioresponses() as mocked:
            mocked.post(TOKEN_URL, payload={
                "access_token": "refreshed-tok",
                "expires_in": 3600,
            })

            creds = await get_valid_credentials()

        assert creds is not None
        assert creds["access"] == "refreshed-tok"

    async def test_fetch_reports_refresh_failure(self, tmp_config_dir: Path) -> None:
        save_credentials({
            "type": "oauth", "refresh": "bad-token", "access": "old",
            "expires": 0, "projectId": "p", "email": "e",
        })

        with aioresponses() as mocked:
            mocked.post(TOKEN_URL, status=401, body="Invalid grant")

            result = await fetch_gemini(timeout=5.0)

        assert result.error is not None
        assert "token refresh failed" in result.error.lower()
        assert "re-authenticate" in result.error


# ── 2. Usage endpoint calls ────────────────────────────────


SAMPLE_QUOTA_RESPONSE = {
    "buckets": [
        {
            "modelId": "gemini-2.5-pro",
            "remainingFraction": 0.75,
            "resetTime": "2026-02-16T22:00:00Z",
            "tokenType": "INPUT",
        },
        {
            "modelId": "gemini-2.5-pro",
            "remainingFraction": 0.60,
            "resetTime": "2026-02-16T22:00:00Z",
            "tokenType": "OUTPUT",
        },
        {
            "modelId": "gemini-2.5-flash",
            "remainingFraction": 0.90,
            "resetTime": "2026-02-16T22:00:00Z",
            "tokenType": "INPUT",
        },
        {
            "modelId": "gemini-2.5-flash",
            "remainingFraction": 0.85,
            "resetTime": "2026-02-16T22:00:00Z",
            "tokenType": "OUTPUT",
        },
    ]
}

SAMPLE_LOAD_CODE_ASSIST_RESPONSE = {
    "currentTier": {"id": "standard-tier"},
    "cloudaicompanionProject": "gen-lang-client-test",
}


class TestGeminiUsageEndpoint:
    """Test the Gemini usage API calls."""

    async def test_fetch_with_valid_credentials(self, tmp_config_dir: Path) -> None:
        future = int(time.time() * 1000) + 3600_000
        save_credentials({
            "type": "oauth", "access": "test-token", "refresh": "ref",
            "expires": future, "projectId": "my-project", "email": "user@test.com",
        })

        with aioresponses() as mocked:
            mocked.post(LOAD_CA_URL, payload=SAMPLE_LOAD_CODE_ASSIST_RESPONSE)
            mocked.post(QUOTA_URL, payload=SAMPLE_QUOTA_RESPONSE)

            result = await fetch_gemini(timeout=10.0)

        assert result.error is None
        assert result.primary is not None
        assert result.secondary is not None

    async def test_fetch_without_credentials(self, tmp_config_dir: Path) -> None:
        result = await fetch_gemini(timeout=5.0)
        assert result.error is not None
        assert "No Gemini credentials" in result.error

    async def test_fetch_handles_401(self, tmp_config_dir: Path) -> None:
        future = int(time.time() * 1000) + 3600_000
        save_credentials({
            "type": "oauth", "access": "bad-token", "refresh": "ref",
            "expires": future, "projectId": "p", "email": "e",
        })

        with aioresponses() as mocked:
            mocked.post(LOAD_CA_URL, payload={"currentTier": {"id": "free-tier"}})
            mocked.post(QUOTA_URL, status=401, body="Unauthorized")

            result = await fetch_gemini(timeout=5.0)

        assert result.error is not None
        assert "Unauthorized" in result.error
        assert result.source == "oauth"

    async def test_load_code_assist_parsing(self, tmp_config_dir: Path) -> None:
        """Test the loadCodeAssist helper directly."""
        with aioresponses() as mocked:
            mocked.post(LOAD_CA_URL, payload={
                "currentTier": {"id": "free-tier"},
                "cloudaicompanionProject": "gen-lang-client-xyz",
            })

            tier, project = await _load_code_assist("fake-token", timeout=5.0)

        assert tier == "free-tier"
        assert project == "gen-lang-client-xyz"

    async def test_load_code_assist_dict_project(self, tmp_config_dir: Path) -> None:
        """Project can also be a dict with id/projectId."""
        with aioresponses() as mocked:
            mocked.post(LOAD_CA_URL, payload={
                "currentTier": {"id": "standard-tier"},
                "cloudaicompanionProject": {"id": "project-from-dict"},
            })

            tier, project = await _load_code_assist("fake-token", timeout=5.0)

        assert project == "project-from-dict"


# ── 3. Parsing quota data ─────────────────────────────────


class TestGeminiQuotaParsing:
    """Test parsing of quota buckets into ProviderResult fields."""

    async def test_parse_pro_and_flash(self, tmp_config_dir: Path) -> None:
        future = int(time.time() * 1000) + 3600_000
        save_credentials({
            "type": "oauth", "access": "tok", "refresh": "ref",
            "expires": future, "projectId": "p", "email": "user@test.com",
        })

        with aioresponses() as mocked:
            mocked.post(LOAD_CA_URL, payload=SAMPLE_LOAD_CODE_ASSIST_RESPONSE)
            mocked.post(QUOTA_URL, payload=SAMPLE_QUOTA_RESPONSE)

            result = await fetch_gemini(timeout=10.0)

        # Pro: worst is 0.60 remaining → 40% used
        assert result.primary is not None
        assert abs(result.primary.used_percent - 40.0) < 0.1
        assert result.primary.window_minutes == 1440

        # Flash: worst is 0.85 remaining → 15% used
        assert result.secondary is not None
        assert abs(result.secondary.used_percent - 15.0) < 0.1

    async def test_parse_identity(self, tmp_config_dir: Path) -> None:
        future = int(time.time() * 1000) + 3600_000
        save_credentials({
            "type": "oauth", "access": "tok", "refresh": "ref",
            "expires": future, "projectId": "p", "email": "user@test.com",
        })

        with aioresponses() as mocked:
            mocked.post(LOAD_CA_URL, payload=SAMPLE_LOAD_CODE_ASSIST_RESPONSE)
            mocked.post(QUOTA_URL, payload=SAMPLE_QUOTA_RESPONSE)

            result = await fetch_gemini(timeout=10.0)

        assert result.identity is not None
        assert result.identity.account_email == "user@test.com"
        assert result.identity.login_method == "Paid"

    def test_tier_mapping(self) -> None:
        assert _tier_to_plan("standard-tier") == "Paid"
        assert _tier_to_plan("free-tier") == "Free"
        assert _tier_to_plan("legacy-tier") == "Legacy"
        assert _tier_to_plan(None) is None

    async def test_parse_only_flash(self, tmp_config_dir: Path) -> None:
        """When only Flash models are in quotas, primary should be None."""
        future = int(time.time() * 1000) + 3600_000
        save_credentials({
            "type": "oauth", "access": "tok", "refresh": "ref",
            "expires": future, "projectId": "p", "email": "e",
        })

        flash_only = {
            "buckets": [
                {"modelId": "gemini-2.5-flash", "remainingFraction": 0.5, "resetTime": None},
            ]
        }

        with aioresponses() as mocked:
            mocked.post(LOAD_CA_URL, payload={"currentTier": {"id": "free-tier"}})
            mocked.post(QUOTA_URL, payload=flash_only)

            result = await fetch_gemini(timeout=10.0)

        assert result.primary is None
        assert result.secondary is not None
        assert abs(result.secondary.used_percent - 50.0) < 0.1

    async def test_parse_no_buckets_errors(self, tmp_config_dir: Path) -> None:
        """Empty buckets should produce an error."""
        future = int(time.time() * 1000) + 3600_000
        save_credentials({
            "type": "oauth", "access": "tok", "refresh": "ref",
            "expires": future, "projectId": "p", "email": "e",
        })

        with aioresponses() as mocked:
            mocked.post(LOAD_CA_URL, payload={"currentTier": {"id": "free-tier"}})
            mocked.post(QUOTA_URL, payload={"buckets": []})

            result = await fetch_gemini(timeout=10.0)

        assert result.error is not None
        assert "No quota buckets" in result.error
