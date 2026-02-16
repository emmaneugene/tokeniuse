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

    MIN_BAR_WIDTH = 10

    def __init__(
        self,
        used_percent: float,
        label: str = "",
        suffix: str = "used",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._used = max(0.0, min(100.0, used_percent))
        self._label = label
        self._suffix = suffix

    def render(self) -> Text:
        pct = self._used

        # Compute bar width from available widget width.
        # Fixed overhead: prefix + "[" + "]" + " XXX% {suffix}"
        prefix_len = len(f"  {self._label}: ") if self._label else 2
        suffix_len = len(f" {pct:3.0f}% {self._suffix}")
        overhead = prefix_len + 1 + 1 + suffix_len  # [ and ]
        bar_width = max(self.MIN_BAR_WIDTH, self.size.width - overhead)

        filled = round((pct / 100.0) * bar_width)
        filled = max(0, min(bar_width, filled))
        empty = bar_width - filled

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
        else:
            t.append("  ", style="")
        t.append("[", style="dim")
        t.append("━" * filled, style=bar_style)
        t.append("─" * empty, style="dim")
        t.append("]", style="dim")
        t.append(f" {pct:3.0f}% {self._suffix}", style=pct_style)
        return t
