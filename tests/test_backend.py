"""Tests for backend fetch orchestration."""

from __future__ import annotations

from llmeter.backend import fetch_all


async def test_fetch_all_respects_explicit_empty_provider_list() -> None:
    results = await fetch_all(provider_ids=[])
    assert results == []
