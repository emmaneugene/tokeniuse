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


def test_refresh_all_with_no_enabled_providers_shows_empty_status() -> None:
    app = LLMeterApp(config=AppConfig(providers=[], refresh_interval=120))

    app._refresh_all()

    assert app._refresh_in_progress is False
    assert app._pending_provider_ids == set()
    assert "No providers enabled" in app.sub_title


def test_reload_config_replaces_runtime_config(monkeypatch) -> None:
    app = _make_app()

    def fake_load_config() -> AppConfig:
        return AppConfig(
            providers=[ProviderConfig(id="gemini", enabled=True)],
            refresh_interval=120,
        )

    monkeypatch.setattr("llmeter.config.load_config", fake_load_config)

    app._reload_config()

    assert app._config.provider_ids == ["gemini"]


# ── action_refresh ─────────────────────────────────────────


async def test_action_refresh_skips_dom_rebuild_when_providers_unchanged(
    monkeypatch,
) -> None:
    """action_refresh should not rebuild cards when the provider list is the same."""
    app = _make_app()
    rebuild_calls = []

    async def fake_rebuild():
        rebuild_calls.append(1)

    monkeypatch.setattr(app, "_rebuild_provider_views", fake_rebuild)
    monkeypatch.setattr(app, "_refresh_all", lambda: None)
    monkeypatch.setattr("llmeter.config.load_config", lambda: app._config)

    await app.action_refresh()

    assert rebuild_calls == []


async def test_action_refresh_rebuilds_dom_when_provider_list_changes(
    monkeypatch,
) -> None:
    """action_refresh should rebuild cards when the enabled provider list changes."""
    app = _make_app()
    rebuild_calls = []

    async def fake_rebuild():
        rebuild_calls.append(1)

    new_config = AppConfig(
        providers=[ProviderConfig(id="gemini", enabled=True)],
        refresh_interval=120,
    )

    monkeypatch.setattr(app, "_rebuild_provider_views", fake_rebuild)
    monkeypatch.setattr(app, "_refresh_all", lambda: None)
    monkeypatch.setattr("llmeter.config.load_config", lambda: new_config)

    await app.action_refresh()

    assert rebuild_calls == [1]
