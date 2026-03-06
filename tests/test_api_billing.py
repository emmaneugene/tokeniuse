"""Tests for API billing provider budget coercion."""

from __future__ import annotations

from llmeter.providers.api.anthropic import AnthropicApiProvider
from llmeter.providers.api.openai import OpenAIApiProvider


async def test_openai_monthly_budget_accepts_numeric_string(monkeypatch) -> None:
    async def fake_fetch_costs(*args, **kwargs) -> float:
        return 10.0

    monkeypatch.setattr("llmeter.providers.api.openai._fetch_costs", fake_fetch_costs)

    provider = OpenAIApiProvider()
    result = await provider._fetch("sk-test", timeout=1.0, settings={"monthly_budget": "50"})

    assert result.primary is not None
    assert result.primary.used_percent == 20.0
    assert result.cost is not None
    assert result.cost.limit == 50.0


async def test_openai_monthly_budget_invalid_value_disables_budget(monkeypatch) -> None:
    async def fake_fetch_costs(*args, **kwargs) -> float:
        return 10.0

    monkeypatch.setattr("llmeter.providers.api.openai._fetch_costs", fake_fetch_costs)

    provider = OpenAIApiProvider()
    result = await provider._fetch("sk-test", timeout=1.0, settings={"monthly_budget": "abc"})

    assert result.primary is None
    assert result.cost is not None
    assert result.cost.used == 10.0
    assert result.cost.limit == 0.0


async def test_anthropic_monthly_budget_accepts_numeric_string(monkeypatch) -> None:
    async def fake_fetch_cost_report(*args, **kwargs) -> float:
        return 12.5

    monkeypatch.setattr(
        "llmeter.providers.api.anthropic._fetch_cost_report",
        fake_fetch_cost_report,
    )

    provider = AnthropicApiProvider()
    result = await provider._fetch(
        "sk-ant-test",
        timeout=1.0,
        settings={"monthly_budget": "25"},
    )

    assert result.primary is not None
    assert result.primary.used_percent == 50.0
    assert result.cost is not None
    assert result.cost.limit == 25.0


async def test_anthropic_monthly_budget_negative_disables_budget(monkeypatch) -> None:
    async def fake_fetch_cost_report(*args, **kwargs) -> float:
        return 12.5

    monkeypatch.setattr(
        "llmeter.providers.api.anthropic._fetch_cost_report",
        fake_fetch_cost_report,
    )

    provider = AnthropicApiProvider()
    result = await provider._fetch(
        "sk-ant-test",
        timeout=1.0,
        settings={"monthly_budget": -1},
    )

    assert result.primary is None
    assert result.cost is not None
    assert result.cost.used == 12.5
    assert result.cost.limit == 0.0
