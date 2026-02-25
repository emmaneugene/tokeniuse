"""Tests for widget rendering states and threshold styles."""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Static

from llmeter.models import ProviderIdentity, PROVIDERS, RateWindow
from llmeter.widgets.provider_card import ProviderCard
from llmeter.widgets.usage_bar import UsageBar


def _styles_of_bar(pct: float) -> list[str]:
    text = UsageBar(used_percent=pct).render()
    return [str(span.style) for span in text.spans]


def test_usage_bar_color_thresholds() -> None:
    assert "green" in _styles_of_bar(10)
    assert "bright_green" in _styles_of_bar(30)
    assert "yellow" in _styles_of_bar(55)
    assert "red" in _styles_of_bar(80)
    assert "bold red" in _styles_of_bar(95)


def test_provider_card_loading_state() -> None:
    result = PROVIDERS["codex"].to_result(source="loading")
    card = ProviderCard(result)

    children = card._make_children()

    assert len(children) == 1
    assert isinstance(children[0], Static)
    assert "card-loading" in children[0].classes


def test_provider_card_error_state() -> None:
    result = PROVIDERS["codex"].to_result(error="boom")
    card = ProviderCard(result)

    children = card._make_children()

    assert len(children) == 1
    assert isinstance(children[0], Static)
    assert "card-error" in children[0].classes


def test_provider_card_data_state_contains_expected_rows() -> None:
    result = PROVIDERS["codex"].to_result(
        primary=RateWindow(used_percent=40, reset_description="in 1h"),
        secondary=RateWindow(used_percent=65),
        identity=ProviderIdentity(account_email="dev@example.com"),
    )
    card = ProviderCard(result)

    children = card._make_children()

    assert len(children) == 1
    assert isinstance(children[0], Vertical)

    rows = list(children[0]._pending_children)
    assert sum(isinstance(row, UsageBar) for row in rows) == 2
    assert any(isinstance(row, Static) and "bar-label" in row.classes for row in rows)
    assert any(isinstance(row, Static) and "reset-info" in row.classes for row in rows)
    assert any(isinstance(row, Static) and "card-meta" in row.classes for row in rows)
