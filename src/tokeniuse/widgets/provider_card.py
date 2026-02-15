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
    }

    ProviderCard:focus-within {
        border: round $accent;
    }

    ProviderCard .card-body {
        height: auto;
    }

    ProviderCard .card-header {
        text-style: bold;
        margin-bottom: 1;
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
    """

    def __init__(self, data: ProviderResult, **kwargs) -> None:
        super().__init__(**kwargs)
        self.data = data

    @property
    def is_loading(self) -> bool:
        return self.data.source == "loading"

    pass  # Update is handled by the app replacing the card

    def compose(self) -> ComposeResult:
        yield from self._build_children()

    def _build_children(self) -> ComposeResult:
        d = self.data
        version = f" {d.version}" if d.version else ""
        source = f" ({d.source})" if d.source and d.source not in ("unknown", "loading") else ""
        display_name = f"{d.display_name}*" if d.provider_id == "anthropic-api" else d.display_name

        yield Static(
            Text.assemble(
                (f" {d.icon} ", "bold"),
                (display_name, "bold"),
                (version, "dim"),
                (source, "dim italic"),
            ),
            classes="card-header",
        )

        # Loading placeholder
        if self.is_loading:
            yield Static(
                Text("  ⏳ Loading…", style="dim italic"),
                classes="card-loading",
            )
            return

        if d.error:
            yield Static(
                Text(f"  ✗ {d.error}", style="bold red"),
                classes="card-error",
            )
            return

        with Vertical(classes="card-body"):
            # Primary window
            if d.primary:
                yield UsageBar(
                    used_percent=d.primary.used_percent,
                    label=d.primary_label,
                )
                reset = d.primary.reset_text()
                if reset:
                    yield Static(f"    {reset}", classes="reset-info")

            # Secondary window
            if d.secondary:
                yield UsageBar(
                    used_percent=d.secondary.used_percent,
                    label=d.secondary_label,
                )
                reset = d.secondary.reset_text()
                if reset:
                    yield Static(f"    {reset}", classes="reset-info")

            # Tertiary window
            if d.tertiary:
                yield UsageBar(
                    used_percent=d.tertiary.used_percent,
                    label=d.tertiary_label,
                )
                reset = d.tertiary.reset_text()
                if reset:
                    yield Static(f"    {reset}", classes="reset-info")

            # Credits
            if d.credits and d.credits.remaining > 0:
                yield Static(
                    Text.assemble(
                        ("  Credits: ", "bold"),
                        (f"{d.credits.remaining:,.2f} left", "bright_cyan"),
                    ),
                )

            # Cost / extra usage — shown as a bar.
            # Skip if the provider already uses cost as its primary display.
            is_cost_primary = "$" in d.primary_label if d.primary else False
            if d.cost and not is_cost_primary:
                cost = d.cost
                if cost.limit > 0:
                    cost_pct = min(100.0, (cost.used / cost.limit) * 100.0)
                else:
                    cost_pct = 0.0
                cost_label = f"💰 ${cost.used:,.2f}/${cost.limit:,.2f} {cost.currency}"
                yield UsageBar(
                    used_percent=cost_pct,
                    label=cost_label,
                    suffix=f"({cost.period})",
                )

            # Identity metadata
            if d.identity:
                if d.identity.account_email:
                    yield Static(
                        Text.assemble(
                            ("  Account: ", "dim"), (d.identity.account_email, "")
                        ),
                        classes="card-meta",
                    )
                if d.identity.account_organization:
                    yield Static(
                        Text.assemble(
                            ("  Org: ", "dim"), (d.identity.account_organization, "")
                        ),
                        classes="card-meta",
                    )
                if d.identity.login_method:
                    yield Static(
                        Text.assemble(
                            ("  Plan: ", "dim"), (d.identity.login_method, "")
                        ),
                        classes="card-meta",
                    )

            # Updated timestamp (shown in local timezone to match header clock)
            if d.updated_at:
                local_dt = d.updated_at.astimezone()
                ts = local_dt.strftime("%H:%M:%S")
                tz = local_dt.strftime("%Z")
                suffix = f" {tz}" if tz else ""
                yield Static(
                    Text(f"  Updated at {ts}{suffix}", style="dim italic"),
                    classes="card-meta",
                )
