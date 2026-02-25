"""Tests for the GitHub Copilot provider.

Covers:
1. Credential persistence (save/load/clear)
2. Usage endpoint calls and parsing (request-count style)
3. Error handling
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aioresponses import aioresponses

from llmeter import auth
from llmeter.providers.subscription.copilot import (
    save_credentials,
    load_credentials,
    clear_credentials,
    fetch_copilot,
    COPILOT_USER_URL,
)


# ── 1. Credential persistence ─────────────────────────────


class TestCopilotCredentials:
    """Test credential storage via the unified auth store."""

    def test_save_and_load(self, tmp_config_dir: Path) -> None:
        creds = {
            "type": "oauth",
            "access": "gho_test_token_123",
        }
        save_credentials(creds)

        loaded = load_credentials()
        assert loaded is not None
        assert loaded["access"] == "gho_test_token_123"
        # GitHub device flow tokens have no refresh token or expiry
        assert "refresh" not in loaded
        assert "expires" not in loaded

    def test_load_returns_none_when_empty(self, tmp_config_dir: Path) -> None:
        assert load_credentials() is None

    def test_clear_credentials(self, tmp_config_dir: Path) -> None:
        save_credentials({
            "type": "oauth",
            "access": "tok",
        })
        assert load_credentials() is not None

        clear_credentials()
        assert load_credentials() is None

    def test_load_requires_access_token(self, tmp_config_dir: Path) -> None:
        """Credentials without an access token should be treated as invalid."""
        auth.save_provider("github-copilot", {
            "type": "oauth",
        })
        assert load_credentials() is None


# ── 2. Usage fetch and parsing ─────────────────────────────


# Real-shaped response from the Copilot internal API.
SAMPLE_COPILOT_RESPONSE = {
    "login": "testuser",
    "copilot_plan": "individual",
    "assigned_date": None,
    "quota_reset_date": "2026-03-01",
    "quota_reset_date_utc": "2026-03-01T00:00:00.000Z",
    "quota_snapshots": {
        "chat": {
            "entitlement": 0,
            "remaining": 0,
            "percent_remaining": 100.0,
            "quota_id": "chat",
            "unlimited": True,
        },
        "completions": {
            "entitlement": 0,
            "remaining": 0,
            "percent_remaining": 100.0,
            "quota_id": "completions",
            "unlimited": True,
        },
        "premium_interactions": {
            "entitlement": 300,
            "remaining": 279,
            "percent_remaining": 93.0,
            "quota_id": "premium_interactions",
            "unlimited": False,
        },
    },
}


class TestCopilotFetch:
    """Test the fetch_copilot function."""

    def _setup_creds(self, tmp_config_dir: Path) -> None:
        save_credentials({
            "type": "oauth",
            "access": "gho_test_token",
        })

    async def test_no_credentials(self, tmp_config_dir: Path) -> None:
        result = await fetch_copilot()
        assert result.error is not None
        assert "--login copilot" in result.error

    async def test_successful_fetch(self, tmp_config_dir: Path) -> None:
        self._setup_creds(tmp_config_dir)

        with aioresponses() as m:
            m.get(COPILOT_USER_URL, payload=SAMPLE_COPILOT_RESPONSE)

            result = await fetch_copilot()

        assert result.error is None
        assert result.provider_id == "copilot"
        assert result.source == "oauth"

        # Primary: premium interactions (100 - 93 = 7% used, 21/300 reqs)
        assert result.primary is not None
        assert abs(result.primary.used_percent - 7.0) < 0.01
        assert result.primary.resets_at is not None
        assert result.primary_label == "Plan 21 / 300 reqs"

        # No secondary — chat/completions are unlimited and skipped
        assert result.secondary is None

        # Identity
        assert result.identity is not None
        assert result.identity.account_email == "testuser"
        assert result.identity.login_method == "Individual"

    async def test_unlimited_premium_shows_zero(self, tmp_config_dir: Path) -> None:
        """If premium_interactions is unlimited, show 0% used."""
        self._setup_creds(tmp_config_dir)

        resp = {
            **SAMPLE_COPILOT_RESPONSE,
            "quota_snapshots": {
                "premium_interactions": {
                    "entitlement": 0,
                    "remaining": 0,
                    "percent_remaining": 100.0,
                    "quota_id": "premium_interactions",
                    "unlimited": True,
                },
            },
        }

        with aioresponses() as m:
            m.get(COPILOT_USER_URL, payload=resp)

            result = await fetch_copilot()

        assert result.error is None
        assert result.primary is not None
        assert result.primary.used_percent == 0.0
        # Default label (no request count override for unlimited)
        assert result.primary_label == "Premium (Monthly)"

    async def test_empty_quota_snapshots(self, tmp_config_dir: Path) -> None:
        """Empty quota_snapshots should return 0% used."""
        self._setup_creds(tmp_config_dir)

        resp = {
            "login": "testuser",
            "copilot_plan": "individual",
            "quota_reset_date": "2026-03-01",
            "quota_reset_date_utc": "2026-03-01T00:00:00.000Z",
            "quota_snapshots": {},
        }

        with aioresponses() as m:
            m.get(COPILOT_USER_URL, payload=resp)

            result = await fetch_copilot()

        assert result.error is None
        assert result.primary is not None
        assert result.primary.used_percent == 0.0

    async def test_fully_used_quota(self, tmp_config_dir: Path) -> None:
        """All premium requests used up."""
        self._setup_creds(tmp_config_dir)

        resp = {
            **SAMPLE_COPILOT_RESPONSE,
            "quota_snapshots": {
                "premium_interactions": {
                    "entitlement": 300,
                    "remaining": 0,
                    "percent_remaining": 0.0,
                    "quota_id": "premium_interactions",
                    "unlimited": False,
                },
            },
        }

        with aioresponses() as m:
            m.get(COPILOT_USER_URL, payload=resp)

            result = await fetch_copilot()

        assert result.error is None
        assert result.primary is not None
        assert abs(result.primary.used_percent - 100.0) < 0.01
        assert result.primary_label == "Plan 300 / 300 reqs"

    async def test_401_unauthorized(self, tmp_config_dir: Path) -> None:
        self._setup_creds(tmp_config_dir)

        with aioresponses() as m:
            m.get(COPILOT_USER_URL, status=401, body="Unauthorized")

            result = await fetch_copilot()

        assert result.error is not None
        assert "Unauthorized" in result.error
        # Stale credentials should be cleared so re-login starts clean.
        assert load_credentials() is None

    async def test_403_forbidden(self, tmp_config_dir: Path) -> None:
        self._setup_creds(tmp_config_dir)

        with aioresponses() as m:
            m.get(COPILOT_USER_URL, status=403, body="Forbidden")

            result = await fetch_copilot()

        assert result.error is not None
        assert "Forbidden" in result.error

    async def test_404_not_found(self, tmp_config_dir: Path) -> None:
        self._setup_creds(tmp_config_dir)

        with aioresponses() as m:
            m.get(COPILOT_USER_URL, status=404, body="Not Found")

            result = await fetch_copilot()

        assert result.error is not None
        assert "not found" in result.error.lower()

    async def test_copilot_plan_in_identity(self, tmp_config_dir: Path) -> None:
        """Plan name should be title-cased in identity."""
        self._setup_creds(tmp_config_dir)

        resp = {
            **SAMPLE_COPILOT_RESPONSE,
            "copilot_plan": "copilot_for_enterprise",
        }

        with aioresponses() as m:
            m.get(COPILOT_USER_URL, payload=resp)

            result = await fetch_copilot()

        assert result.identity is not None
        assert result.identity.login_method == "Copilot For Enterprise"
