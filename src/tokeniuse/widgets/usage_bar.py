"""Usage bar widget — a horizontal progress bar that fills from 0% to 100%."""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget


class UsageBar(Widget):
    """A horizontal bar showing how much has been used (fills up as usage grows)."""

    DEFAULT_CSS = """
    UsageBar {
        height: 1;
        width: 1fr;
    }
    """

    def __init__(
        self,
        used_percent: float,
        label: str = "",
        bar_width: int = 24,
        suffix: str = "used",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._used = max(0.0, min(100.0, used_percent))
        self._label = label
        self._bar_width = bar_width
        self._suffix = suffix

    def render(self) -> Text:
        pct = self._used
        filled = round((pct / 100.0) * self._bar_width)
        filled = max(0, min(self._bar_width, filled))
        empty = self._bar_width - filled

        # Low usage = green, high usage = red
        if pct >= 90:
            bar_style = "bold red"
            pct_style = "bold red"
        elif pct >= 75:
            bar_style = "red"
            pct_style = "red"
        elif pct >= 50:
            bar_style = "yellow"
            pct_style = "yellow"
        elif pct >= 25:
            bar_style = "bright_green"
            pct_style = "bright_green"
        else:
            bar_style = "green"
            pct_style = "green"

        t = Text()
        if self._label:
            t.append(f"  {self._label}: ", style="bold")
        t.append("[", style="dim")
        t.append("━" * filled, style=bar_style)
        t.append("─" * empty, style="dim")
        t.append("]", style="dim")
        t.append(f" {pct:3.0f}% {self._suffix}", style=pct_style)
        return t
