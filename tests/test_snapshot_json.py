"""Tests for --snapshot JSON output."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from llmeter.cli.snapshot import run_snapshot as _run_snapshot
from llmeter.config import AppConfig, ProviderConfig
from llmeter.models import CostInfo, PROVIDERS, ProviderIdentity, RateWindow


def _config() -> AppConfig:
    return AppConfig(
        providers=[ProviderConfig(id="codex", enabled=True)],
        refresh_interval=120,
    )


def test_snapshot_json_outputs_serializable_payload(monkeypatch, capsys) -> None:
    async def fake_fetch_all(*args, **kwargs):
        return [
            PROVIDERS["codex"].to_result(
                source="oauth",
                primary=RateWindow(
                    used_percent=42.0,
                    resets_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                ),
                identity=ProviderIdentity(account_email="dev@example.com"),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        ]

    monkeypatch.setattr("llmeter.backend.fetch_all", fake_fetch_all)

    _run_snapshot(_config(), json_output=True)

    out = capsys.readouterr().out
    assert '"provider_id": "codex"' in out
    assert '"source": "oauth"' in out
    assert '"resets_at": "2026-01-01T00:00:00+00:00"' in out
    assert '"updated_at": "2026-01-01T00:00:00+00:00"' in out


def test_snapshot_json_empty_results_outputs_empty_array(monkeypatch, capsys) -> None:
    async def fake_fetch_all(*args, **kwargs):
        return []

    monkeypatch.setattr("llmeter.backend.fetch_all", fake_fetch_all)

    _run_snapshot(_config(), json_output=True)

    assert capsys.readouterr().out.strip() == "[]"


def test_json_flag_requires_snapshot(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["llmeter", "--json"],
    )

    from llmeter.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2
    assert "--json can only be used with --snapshot" in capsys.readouterr().err


def test_snapshot_rich_shows_text_only_spend_for_api_without_budget(
    monkeypatch,
    capsys,
) -> None:
    async def fake_fetch_all(*args, **kwargs):
        return [
            PROVIDERS["openai-api"].to_result(
                source="api",
                cost=CostInfo(used=12.34, limit=0.0, currency="USD", period="Monthly"),
            )
        ]

    monkeypatch.setattr("llmeter.backend.fetch_all", fake_fetch_all)

    _run_snapshot(
        AppConfig(providers=[ProviderConfig(id="openai-api", enabled=True)], refresh_interval=120),
        json_output=False,
    )

    out = capsys.readouterr().out
    assert "Spend: $12.34 this month" in out
    assert "0% used" not in out
