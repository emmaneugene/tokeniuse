"""Provider card widget — displays a single provider's usage data."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Static

from ..models import ProviderResult
from .usage_bar import UsageBar


class ProviderCard(Widget):
    """A card-style display for a single AI provider's usage data."""

    DEFAULT_CSS = """
    ProviderCard {
        height: auto;
        width: 1fr;
        margin: 0 1;
        padding: 1 2;
        border: round $secondary;
        background: $surface;
        margin-bottom: 1;
        border-title-align: center;
    }

    ProviderCard:focus-within {
        border: round $accent;
    }

    ProviderCard .card-body {
        height: auto;
    }

    ProviderCard .card-meta {
        color: $text-muted;
    }

    ProviderCard .card-error {
        color: $error;
        margin-top: 1;
    }

    ProviderCard .reset-info {
        color: $text-muted;
        padding-left: 2;
    }

    ProviderCard .card-loading {
        color: $text-muted;
    }

    ProviderCard .bar-label {
        height: 1;
    }
    """

    def __init__(self, data: ProviderResult, **kwargs) -> None:
        super().__init__(**kwargs)
        self.data = data

    def _apply_border(self) -> None:
        """Update the border title and colour from current data."""
        d = self.data
        version = f" {d.version}" if d.version else ""
        display_name = (
            f"{d.display_name}*"
            if d.provider_id == "anthropic-api"
            else d.display_name
        )
        self.border_title = f"{d.icon}  {display_name}{version}"
        self.styles.border = ("round", d.color)

    def on_mount(self) -> None:
        self._apply_border()

    @property
    def is_loading(self) -> bool:
        return self.data.source == "loading"

    def compose(self) -> ComposeResult:
        yield from self._make_children()

    async def update_data(self, data: ProviderResult) -> None:
        """Replace displayed data in-place without touching the DOM above this card."""
        self.data = data
        self._apply_border()
        await self.remove_children()
        await self.mount(*self._make_children())

    def _make_children(self) -> list[Widget]:
        """Build the list of child widgets for the current data state."""
        d = self.data

        # Loading placeholder
        if self.is_loading:
            return [Static(
                Text("  ⏳ Loading…", style="dim italic"),
                classes="card-loading",
            )]

        if d.error:
            return [Static(
                Text(f"  ✗ {d.error}", style="bold red"),
                classes="card-error",
            )]

        rows: list[Widget] = []

        # Primary window
        if d.primary:
            rows.append(Static(
                Text(f"  {d.primary_label}:", style="bold"),
                classes="bar-label",
            ))
            rows.append(UsageBar(used_percent=d.primary.used_percent))
            reset = d.primary.reset_text()
            if reset:
                rows.append(Static(f"    {reset}", classes="reset-info"))

        # Secondary window
        if d.secondary:
            rows.append(Static(
                Text(f"  {d.secondary_label}:", style="bold"),
                classes="bar-label",
            ))
            rows.append(UsageBar(used_percent=d.secondary.used_percent))
            reset = d.secondary.reset_text()
            if reset:
                rows.append(Static(f"    {reset}", classes="reset-info"))

        # Tertiary window
        if d.tertiary:
            rows.append(Static(
                Text(f"  {d.tertiary_label}:", style="bold"),
                classes="bar-label",
            ))
            rows.append(UsageBar(used_percent=d.tertiary.used_percent))
            reset = d.tertiary.reset_text()
            if reset:
                rows.append(Static(f"    {reset}", classes="reset-info"))

        # Credits
        if d.credits and d.credits.remaining > 0:
            rows.append(Static(
                Text.assemble(
                    ("  Credits: ", "bold"),
                    (f"{d.credits.remaining:,.2f} left", "bright_cyan"),
                ),
            ))

        # Cost / extra usage — shown as a bar.
        # Skip if the provider already uses cost as its primary display.
        is_cost_primary = "$" in d.primary_label if d.primary else False
        if d.cost and not is_cost_primary:
            cost = d.cost
            if cost.limit > 0:
                cost_pct = min(100.0, (cost.used / cost.limit) * 100.0)
            else:
                cost_pct = 0.0
            cost_label = f"Extra ({cost.period}) ${cost.used:,.2f} / ${cost.limit:,.2f}"
            rows.append(Static(
                Text(f"  {cost_label}:", style="bold"),
                classes="bar-label",
            ))
            rows.append(UsageBar(used_percent=cost_pct))

        # Identity metadata
        if d.identity:
            if d.identity.account_email:
                rows.append(Static(
                    Text.assemble(
                        ("  Account: ", "dim"), (d.identity.account_email, "")
                    ),
                    classes="card-meta",
                ))
            if d.identity.account_organization:
                rows.append(Static(
                    Text.assemble(
                        ("  Org: ", "dim"), (d.identity.account_organization, "")
                    ),
                    classes="card-meta",
                ))
            if d.identity.login_method:
                rows.append(Static(
                    Text.assemble(
                        ("  Plan: ", "dim"), (d.identity.login_method, "")
                    ),
                    classes="card-meta",
                ))

        return [Vertical(*rows, classes="card-body")]
