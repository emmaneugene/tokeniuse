"""llmeter — main Textual application."""

from __future__ import annotations

import asyncio
from datetime import datetime

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Static

from . import __version__
from .backend import fetch_one, placeholder_result
from .config import AppConfig
from .models import ProviderResult
from .widgets.provider_card import ProviderCard


# ── Help screen ────────────────────────────────────────────

HELP_TEXT = """\
[bold cyan]llmeter — Keyboard Shortcuts[/]

  [bold]r[/]       Refresh all providers
  [bold]t[/]       Cycle theme (dark → light → monokai → …)
  [bold]?[/]       Show this help screen
  [bold]q[/]       Quit

[dim]Data is fetched directly from provider APIs.
Configure providers in ~/.config/llmeter/config.json[/]

[dim]Press [bold]Escape[/bold] to close this dialog.[/]
"""

DELAY_DISCLAIMER = "* May not reflect current usage due to reporting delays"
DELAY_PRONE_PROVIDER_IDS = {
    "anthropic-api",
}


class HelpScreen(ModalScreen[None]):
    """Modal overlay showing keybindings and help text."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("question_mark", "dismiss", "Close"),
    ]

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }

    #help-box {
        width: 56;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $accent;
        padding: 2 3;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(HELP_TEXT, id="help-box")

    def action_dismiss(self) -> None:
        self.dismiss(None)


# ── Main app ───────────────────────────────────────────────


class LLMeterApp(App):
    """The llmeter dashboard application."""

    TITLE = "llmeter"
    SUB_TITLE = "AI Usage Dashboard"

    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("r", "refresh", "Refresh"),
        Binding("t", "cycle_theme", "Theme"),
        Binding("question_mark", "show_help", "Help"),
    ]

    _themes = [
        "textual-dark", "textual-light", "monokai",
        "dracula", "nord", "tokyo-night",
    ]

    def __init__(self, config: AppConfig):
        super().__init__()
        self._config = config
        self._providers: dict[str, ProviderResult] = {}
        self._cards: dict[str, ProviderCard] = {}
        self._provider_locks: dict[str, asyncio.Lock] = {}
        self._pending_provider_ids: set[str] = set()
        self._refresh_in_progress = False
        self._refresh_queued = False
        self._last_refresh: datetime | None = None
        self._theme_idx = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with ScrollableContainer(id="main-body"):
            yield Vertical(id="provider-list")
        if any(pcfg.id in DELAY_PRONE_PROVIDER_IDS for pcfg in self._config.providers):
            yield Static(DELAY_DISCLAIMER, id="legend-bar")
        yield Footer()

    def _refresh_interval_text(self) -> str:
        interval = float(self._config.refresh_interval)
        if not interval.is_integer():
            return f"{interval:g}s"

        total_seconds = int(interval)
        if total_seconds < 60:
            return f"{total_seconds}s"

        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)

        parts: list[str] = []
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}min")
        if seconds:
            parts.append(f"{seconds}s")

        return " ".join(parts) if parts else "0s"

    async def on_mount(self) -> None:
        interval = self._config.refresh_interval
        self.sub_title = f"v{__version__}  •  refresh every {self._refresh_interval_text()}"

        # Mount placeholder cards immediately
        container = self.query_one("#provider-list", Vertical)
        for pcfg in self._config.providers:
            placeholder = placeholder_result(pcfg.id)
            card = ProviderCard(placeholder, id=f"card-{pcfg.id}")
            self._cards[pcfg.id] = card
            container.mount(card)

        # Kick off all provider fetches
        self._refresh_all()

        # Set up auto-refresh timer
        self.set_interval(interval, self._refresh_all)

    def _refresh_all(self) -> None:
        """Launch a fetch worker for each provider."""
        if self._refresh_in_progress:
            self._refresh_queued = True
            return

        self._refresh_in_progress = True
        self._refresh_queued = False
        self._pending_provider_ids = {pcfg.id for pcfg in self._config.providers}

        self._update_status("Refreshing…")
        for pcfg in self._config.providers:
            self._fetch_provider(pcfg.id, pcfg.settings)

    @work(thread=False, group="providers")
    async def _fetch_provider(self, provider_id: str, settings: dict) -> None:
        """Fetch a single provider and update its card."""
        try:
            result = await fetch_one(
                provider_id,
                settings=settings or None,
            )
            self._providers[provider_id] = result

            lock = self._provider_locks.setdefault(provider_id, asyncio.Lock())
            async with lock:
                # Replace the card with a fresh one
                old_card = self._cards.get(provider_id)
                if old_card:
                    container = self.query_one("#provider-list", Vertical)
                    # Find position of old card
                    children = list(container.children)
                    try:
                        idx = children.index(old_card)
                    except ValueError:
                        idx = -1

                    new_card = ProviderCard(result, id=f"card-{provider_id}")

                    if idx >= 0:
                        await old_card.remove()
                        if idx < len(list(container.children)):
                            await container.mount(new_card, before=list(container.children)[idx])
                        else:
                            await container.mount(new_card)
                    else:
                        # Old card may have been replaced by a newer refresh; clean up by ID.
                        card_id = f"card-{provider_id}"
                        for child in list(container.children):
                            if child.id == card_id:
                                await child.remove()
                        await container.mount(new_card)

                    self._cards[provider_id] = new_card

                self._update_status()
        finally:
            self._pending_provider_ids.discard(provider_id)
            if not self._pending_provider_ids:
                self._refresh_in_progress = False
                if self._refresh_queued:
                    self._refresh_queued = False
                    self._refresh_all()

    def _update_status(self, message: str | None = None) -> None:
        parts = [f"v{__version__}", f"Every {self._refresh_interval_text()}"]

        if message and not self._providers:
            # First load — show "Refreshing…"
            parts.append(message)
        else:
            # Count completed providers
            loaded = len(self._providers)
            total = len(self._config.providers)
            if loaded < total:
                parts.append(f"Loading {loaded}/{total}")
            else:
                now = datetime.now().astimezone()
                self._last_refresh = now
                parts.append(f"Last: {now.strftime('%H:%M:%S')}")

            ok = len([p for p in self._providers.values() if not p.error])
            err = len([p for p in self._providers.values() if p.error])
            if ok or err:
                s = f"{ok} ok"
                if err:
                    s += f", {err} err"
                parts.append(s)

        self.sub_title = "  •  ".join(parts)

    # ── Actions ────────────────────────────────────────────

    def action_refresh(self) -> None:
        self._refresh_all()

    def action_cycle_theme(self) -> None:
        self._theme_idx = (self._theme_idx + 1) % len(self._themes)
        self.theme = self._themes[self._theme_idx]
        self.notify(f"Theme: {self.theme}", title="Theme", timeout=2)

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())
