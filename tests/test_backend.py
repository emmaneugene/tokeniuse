"""Tests for backend fetch orchestration."""

from __future__ import annotations

from llmeter import backend


async def test_fetch_all_respects_explicit_empty_provider_list() -> None:
    results = await backend.fetch_all(provider_ids=[])
    assert results == []


async def test_fetch_one_returns_unknown_provider_error() -> None:
    result = await backend.fetch_one("does-not-exist")

    assert result.provider_id == "does-not-exist"
    assert result.error == "Unknown provider: does-not-exist"


async def test_fetch_all_isolates_provider_errors(
    monkeypatch,
) -> None:
    async def ok_fetcher(*, timeout: float, settings: dict | None = None):
        return backend.PROVIDERS["codex"].to_result(source="test")

    async def bad_fetcher(*, timeout: float, settings: dict | None = None):
        raise RuntimeError("boom")

    monkeypatch.setitem(backend.PROVIDER_FETCHERS, "codex", ok_fetcher)
    monkeypatch.setitem(backend.PROVIDER_FETCHERS, "claude", bad_fetcher)

    results = await backend.fetch_all(provider_ids=["codex", "claude"])

    by_id = {r.provider_id: r for r in results}
    assert by_id["codex"].error is None
    assert by_id["claude"].error == "boom"
