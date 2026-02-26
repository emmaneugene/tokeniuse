"""Snapshot (non-interactive) output for the llmeter CLI.

Called when `--snapshot` is passed.  Fetches all providers once and
prints Rich panels to stdout, or emits JSON when `--json` is also set.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime


def run_snapshot(config, json_output: bool = False) -> None:
    """Fetch data once and print with Rich panels or JSON."""
    import asyncio

    from rich.console import Console
    from rich.panel import Panel

    from ..backend import fetch_all

    console = Console()
    # Bar width is responsive: fill the panel minus fixed overhead.
    # Panel border (2) + inner padding (4) + bar prefix (2) + brackets (2)
    # + suffix " XXX% used" (10) = 20 chars overhead.
    bar_width = max(10, console.width - 20)

    results = asyncio.run(fetch_all(
        provider_ids=config.provider_ids,
        provider_settings={
            p.id: p.settings for p in config.enabled_providers if p.settings
        },
    ))

    if json_output:
        payload = [_to_jsonable(result) for result in results]
        print(json.dumps(payload, indent=2))
        return

    if not results:
        console.print("[yellow]No providers enabled.[/]")
        console.print("Run [bold]llmeter --login claude[/] or [bold]llmeter --login codex[/] to get started.")
        console.print("Or edit [dim]~/.config/llmeter/settings.json[/dim] and set provider [bold]enabled[/] to [bold]true[/].")
        sys.exit(0)

    for p in results:
        version = f" {p.version}" if p.version else ""
        title = f"{p.icon}  {p.display_name}{version}"

        if p.error:
            console.print(Panel(
                f"[red]✗ {p.error}[/]",
                title=title,
                border_style="red",
            ))
            continue

        lines: list[str] = []

        for label, window in p.windows():
            pct = window.used_percent
            bar = _rich_bar(pct, width=bar_width)
            lines.append(f"  [bold]{label}:[/bold]")
            lines.append(f"  {bar} {pct:3.0f}% used")
            reset = window.reset_text()
            if reset:
                lines.append(f"    [dim]{reset}[/dim]")

        if p.credits and p.credits.remaining > 0:
            lines.append(f"  [bright_cyan]Credits: {p.credits.remaining:,.2f} left[/bright_cyan]")

        if p.cost and not p.cost_is_primary_display:
            cost = p.cost
            if cost.limit > 0:
                cost_pct = min(100.0, (cost.used / cost.limit) * 100.0)
            else:
                cost_pct = 0.0
            bar = _rich_bar(cost_pct, width=bar_width)
            lines.append(
                f"  [bold]Extra ({cost.period}) ${cost.used:,.2f} / ${cost.limit:,.2f}:[/bold]"
            )
            lines.append(f"  {bar} {cost_pct:3.0f}% used")

        if p.identity:
            if p.identity.account_email:
                lines.append(f"  [dim]Account: {p.identity.account_email}[/dim]")
            if p.identity.account_organization:
                lines.append(f"  [dim]Org: {p.identity.account_organization}[/dim]")
            if p.identity.login_method:
                lines.append(f"  [dim]Plan: {p.identity.login_method}[/dim]")

        body = "\n".join(lines) if lines else "[dim]No data[/dim]"
        console.print(Panel(body, title=title, border_style=p.color))

    console.print()


def _to_jsonable(value):
    """Recursively convert dataclasses/datetimes to JSON-serializable values."""
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {k: _to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


def _rich_bar(used_pct: float, width: int = 20) -> str:
    """Create a text-based bar for Rich markup (fills up as usage grows)."""
    from ..widgets.usage_bar import _bar_color

    filled = round((used_pct / 100.0) * width)
    filled = max(0, min(width, filled))
    empty = width - filled
    color = _bar_color(used_pct)

    bar_filled = f"[{color}]{'━' * filled}[/{color}]" if filled else ""
    bar_empty = f"[dim]{'─' * empty}[/dim]" if empty else ""
    return f"[dim]\\[[/dim]{bar_filled}{bar_empty}[dim]][/dim]"
