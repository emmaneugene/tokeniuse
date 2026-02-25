"""Tests for the opencode.ai Zen provider.

Covers:
1. API key resolution (settings, env var)
2. Fetch lifecycle (mocked HTML responses)
3. HTML/JS hydration parsing (_parse_html)
4. Provider metadata
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aioresponses import aioresponses

from llmeter.providers.api.opencode import (
    WORKSPACE_ENTRY_URL,
    fetch_opencode,
    _parse_html,
    _extract_int,
    COST_UNIT,
)
from llmeter.models import ProviderResult, PROVIDERS


# ── Minimal fake HTML matching the SolidStart hydration format ─────────────

def _make_html(
    balance: int = 1_708_723_204,        # → $17.09
    monthly_usage: int = 379_059_776,    # → $3.79
    monthly_limit: int = 20,             # → $20
    email: str = "user@example.com",
) -> str:
    """Build a minimal HTML stub with the same data patterns as the real page."""
    return (
        f'<html><head></head><body><script>'
        f'$R[40]($R[16]={{customerID:"cus_x",'
        f'balance:{balance},'
        f'monthlyUsage:{monthly_usage},'
        f'monthlyLimit:{monthly_limit},'
        f'reloadError:null}});'
        f'$R[40]($R[1],"{email}");'
        f'</script></body></html>'
    )


SAMPLE_HTML = _make_html()


# ── 1. API key resolution ──────────────────────────────────


class TestOpencodeApiKeyResolution:
    """resolve_api_key should check settings then env var."""

    def test_resolves_from_settings(self) -> None:
        provider = fetch_opencode
        key = provider.resolve_api_key({"api_key": "Fe26.2**from_settings"})
        assert key == "Fe26.2**from_settings"

    def test_resolves_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENCODE_AUTH_COOKIE", "Fe26.2**from_env")
        key = fetch_opencode.resolve_api_key({})
        assert key == "Fe26.2**from_env"

    def test_settings_takes_priority_over_env(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENCODE_AUTH_COOKIE", "Fe26.2**from_env")
        key = fetch_opencode.resolve_api_key({"api_key": "Fe26.2**from_settings"})
        assert key == "Fe26.2**from_settings"

    def test_returns_none_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENCODE_AUTH_COOKIE", raising=False)
        key = fetch_opencode.resolve_api_key({})
        assert key is None

    def test_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENCODE_AUTH_COOKIE", raising=False)
        key = fetch_opencode.resolve_api_key({"api_key": "  Fe26.2**trimmed  "})
        assert key == "Fe26.2**trimmed"

    def test_empty_string_treated_as_missing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("OPENCODE_AUTH_COOKIE", raising=False)
        key = fetch_opencode.resolve_api_key({"api_key": ""})
        assert key is None


# ── 2. Fetch lifecycle ─────────────────────────────────────


class TestOpencodeFetch:
    """Test the full fetch path with mocked HTTP."""

    async def test_fetch_with_valid_cookie_from_settings(self) -> None:
        with aioresponses() as mocked:
            mocked.get(WORKSPACE_ENTRY_URL, status=200, body=SAMPLE_HTML)
            result = await fetch_opencode(
                timeout=10.0,
                settings={"api_key": "Fe26.2**valid"},
            )

        assert result.error is None
        assert result.source == "api"
        assert result.primary is not None
        assert result.updated_at is not None

    async def test_fetch_with_valid_cookie_from_env(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENCODE_AUTH_COOKIE", "Fe26.2**from_env")

        with aioresponses() as mocked:
            mocked.get(WORKSPACE_ENTRY_URL, status=200, body=SAMPLE_HTML)
            result = await fetch_opencode(timeout=10.0)

        assert result.error is None
        assert result.source == "api"

    async def test_fetch_without_key_returns_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("OPENCODE_AUTH_COOKIE", raising=False)
        result = await fetch_opencode(timeout=5.0)
        assert result.error is not None
        assert "OPENCODE_AUTH_COOKIE" in result.error

    async def test_fetch_returns_error_on_401(self) -> None:
        with aioresponses() as mocked:
            mocked.get(WORKSPACE_ENTRY_URL, status=401)
            result = await fetch_opencode(
                timeout=5.0, settings={"api_key": "Fe26.2**expired"},
            )

        assert result.error is not None
        assert "expired" in result.error.lower() or "invalid" in result.error.lower()

    async def test_fetch_returns_error_on_403(self) -> None:
        with aioresponses() as mocked:
            mocked.get(WORKSPACE_ENTRY_URL, status=403)
            result = await fetch_opencode(
                timeout=5.0, settings={"api_key": "Fe26.2**forbidden"},
            )

        assert result.error is not None

    async def test_fetch_returns_error_on_500(self) -> None:
        with aioresponses() as mocked:
            mocked.get(WORKSPACE_ENTRY_URL, status=500)
            result = await fetch_opencode(
                timeout=5.0, settings={"api_key": "Fe26.2**valid"},
            )

        assert result.error is not None
        assert "500" in result.error

    async def test_fetch_provider_id_and_meta(self) -> None:
        with aioresponses() as mocked:
            mocked.get(WORKSPACE_ENTRY_URL, status=200, body=SAMPLE_HTML)
            result = await fetch_opencode(
                timeout=10.0, settings={"api_key": "Fe26.2**valid"},
            )

        assert result.provider_id == "opencode"
        assert result.display_name == "Opencode Zen API"


# ── 3. HTML parsing ────────────────────────────────────────


class TestOpencodeHTMLParsing:
    """Test _parse_html with synthetic HTML stubs."""

    def _result(self) -> ProviderResult:
        return PROVIDERS["opencode"].to_result()

    def test_parse_balance(self) -> None:
        result = self._result()
        _parse_html(_make_html(balance=1_000_000_000), result)  # → $10.00
        assert result.credits is not None
        assert result.credits.remaining == pytest.approx(10.0)

    def test_parse_monthly_spend_with_limit(self) -> None:
        # $3.79 spent / $20 limit  →  ~18.95 %
        result = self._result()
        _parse_html(SAMPLE_HTML, result)

        assert result.primary is not None
        pct = result.primary.used_percent
        assert pct == pytest.approx(3.79059776 / 20.0 * 100, rel=1e-4)

    def test_parse_primary_label_with_limit(self) -> None:
        result = self._result()
        _parse_html(SAMPLE_HTML, result)
        assert "$" in result.primary_label
        assert "20" in result.primary_label

    def test_parse_primary_label_without_limit(self) -> None:
        result = self._result()
        _parse_html(_make_html(monthly_limit=0), result)
        assert result.primary is not None
        assert result.primary.used_percent == 0.0
        assert "this month" in result.primary_label

    def test_parse_cost_info(self) -> None:
        result = self._result()
        _parse_html(SAMPLE_HTML, result)

        assert result.cost is not None
        assert result.cost.used == pytest.approx(3.79, rel=1e-3)
        assert result.cost.limit == 20.0
        assert result.cost.currency == "USD"
        assert result.cost.period == "Monthly"

    def test_parse_identity_email(self) -> None:
        result = self._result()
        _parse_html(_make_html(email="alice@example.com"), result)

        assert result.identity is not None
        assert result.identity.account_email == "alice@example.com"

    def test_parse_no_credits_when_balance_zero(self) -> None:
        result = self._result()
        _parse_html(_make_html(balance=0), result)
        assert result.credits is None

    def test_parse_spend_capped_at_100_pct(self) -> None:
        """Spend exceeding limit should be capped at 100%."""
        result = self._result()
        _parse_html(_make_html(monthly_usage=3_000_000_000, monthly_limit=20), result)
        assert result.primary.used_percent == 100.0

    def test_parse_missing_fields_defaults_to_zero(self) -> None:
        result = self._result()
        _parse_html("<html></html>", result)

        assert result.primary is not None
        assert result.primary.used_percent == 0.0
        assert result.credits is None
        assert result.identity is None

    def test_extract_int_helper(self) -> None:
        import re
        pattern = re.compile(r"val:(\d+)")
        assert _extract_int("val:42", pattern) == 42
        assert _extract_int("nothing", pattern) == 0


# ── 4. Provider metadata ───────────────────────────────────


class TestOpencodeProviderMeta:
    """Sanity-check the ProviderMeta registration."""

    def test_provider_registered(self) -> None:
        assert "opencode" in PROVIDERS

    def test_provider_meta_fields(self) -> None:
        meta = PROVIDERS["opencode"]
        assert meta.id == "opencode"
        assert meta.name == "Opencode Zen API"
        assert meta.icon
        assert meta.color.startswith("#")
        assert not meta.default_enabled

    def test_provider_in_fetchers(self) -> None:
        from llmeter.backend import PROVIDER_FETCHERS
        assert "opencode" in PROVIDER_FETCHERS

    def test_provider_in_order(self) -> None:
        from llmeter.backend import ALL_PROVIDER_ORDER
        assert "opencode" in ALL_PROVIDER_ORDER

    def test_no_login_handler(self) -> None:
        """opencode must not have a --login entry (it's API key based)."""
        # We verify this by checking __main__ login_handlers at import time
        # is tricky — instead confirm the login module does not exist
        import importlib.util
        spec = importlib.util.find_spec(
            "llmeter.providers.api.opencode_login"
        )
        assert spec is None
