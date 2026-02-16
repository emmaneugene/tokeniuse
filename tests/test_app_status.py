"""Tests for app status subtitle refresh-cycle accounting."""

from __future__ import annotations

from llmeter.app import LLMeterApp
from llmeter.config import AppConfig, ProviderConfig
from llmeter.models import PROVIDERS


def _make_app() -> LLMeterApp:
    cfg = AppConfig(
        providers=[
            ProviderConfig(id="codex", enabled=True),
            ProviderConfig(id="claude", enabled=True),
        ],
        refresh_interval=120,
    )
    return LLMeterApp(config=cfg)


def test_update_status_counts_only_current_cycle_loaded() -> None:
    app = _make_app()

    # Simulate stale results from a previous cycle for both providers.
    app._providers["codex"] = PROVIDERS["codex"].to_result()
    app._providers["claude"] = PROVIDERS["claude"].to_result()

    # Current cycle has only codex completed so far.
    app._pending_provider_ids = {"claude"}

    app._update_status()

    assert "Loading 1/2" in app.sub_title
    assert "1 ok" in app.sub_title
    assert "2 ok" not in app.sub_title


def test_update_status_complete_cycle_shows_last_and_error_counts() -> None:
    app = _make_app()

    app._providers["codex"] = PROVIDERS["codex"].to_result()
    app._providers["claude"] = PROVIDERS["claude"].to_result(error="boom")
    app._pending_provider_ids = set()

    app._update_status()

    assert "Last:" in app.sub_title
    assert "1 ok, 1 err" in app.sub_title
