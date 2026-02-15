"""Entry point for llmeter — run with `python -m llmeter` or `llmeter`."""

from __future__ import annotations

import argparse
import sys

from . import __version__


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="llmeter",
        description="llmeter — Terminal dashboard for AI coding assistant usage limits.",
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"llmeter {__version__}",
    )
    parser.add_argument(
        "--refresh",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Auto-refresh interval in seconds (60–3600, default: 300).",
    )
    parser.add_argument(
        "--one-shot",
        action="store_true",
        help="Fetch and print data once to stdout (no TUI), with Rich formatting.",
    )
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="Create a default config file and exit.",
    )
    parser.add_argument(
        "--login-claude",
        action="store_true",
        help="Authenticate with Claude via OAuth (one-time setup).",
    )
    parser.add_argument(
        "--logout-claude",
        action="store_true",
        help="Remove stored Claude OAuth credentials.",
    )
    parser.add_argument(
        "--login-codex",
        action="store_true",
        help="Authenticate with Codex via OAuth (one-time setup).",
    )
    parser.add_argument(
        "--logout-codex",
        action="store_true",
        help="Remove stored Codex OAuth credentials.",
    )

    args = parser.parse_args()

    if args.init_config:
        from .config import init_config
        init_config()
        return

    if args.login_claude:
        from .providers.claude_oauth import interactive_login
        try:
            interactive_login()
        except (RuntimeError, KeyboardInterrupt) as e:
            print(f"Login failed: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if args.logout_claude:
        from .providers.claude_oauth import clear_credentials, _creds_path
        path = _creds_path()
        if path.exists():
            clear_credentials()
            print(f"✓ Removed Claude credentials from {path}")
        else:
            print("No Claude credentials stored.")
        return

    if args.login_codex:
        from .providers.codex_oauth import interactive_login
        try:
            interactive_login()
        except (RuntimeError, KeyboardInterrupt) as e:
            print(f"Login failed: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if args.logout_codex:
        from .providers.codex_oauth import clear_credentials, _creds_path
        path = _creds_path()
        if path.exists():
            clear_credentials()
            print(f"✓ Removed Codex credentials from {path}")
        else:
            print("No Codex credentials stored.")
        return

    from .config import load_config

    config = load_config()

    # CLI --refresh overrides config (clamped to 60s–3600s)
    if args.refresh is not None:
        from .config import AppConfig
        config.refresh_interval = max(
            AppConfig.MIN_REFRESH, min(AppConfig.MAX_REFRESH, args.refresh)
        )

    if args.one_shot:
        _run_one_shot(config)
        return

    from .app import LLMeterApp

    app = LLMeterApp(config=config)
    app.run()


def _run_one_shot(config) -> None:
    """Non-interactive mode: fetch data once and print with Rich."""
    import asyncio

    from rich.console import Console
    from rich.panel import Panel

    from .backend import fetch_all

    console = Console()

    results = asyncio.run(fetch_all(
        provider_ids=config.provider_ids,
        provider_settings={
            p.id: p.settings for p in config.providers if p.settings
        },
    ))

    if not results:
        console.print("[yellow]No provider data returned.[/]")
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

        if p.source and p.source not in ("unknown", "loading"):
            lines.append(f"  [dim italic]Source: {p.source}[/dim italic]")

        if p.primary:
            pct = p.primary.used_percent
            bar = _rich_bar(pct)
            lines.append(f"  [bold]{p.primary_label}:[/bold]")
            lines.append(f"  {bar} {pct:3.0f}% used")
            reset = p.primary.reset_text()
            if reset:
                lines.append(f"    [dim]{reset}[/dim]")

        if p.secondary:
            pct = p.secondary.used_percent
            bar = _rich_bar(pct)
            lines.append(f"  [bold]{p.secondary_label}:[/bold]")
            lines.append(f"  {bar} {pct:3.0f}% used")
            reset = p.secondary.reset_text()
            if reset:
                lines.append(f"    [dim]{reset}[/dim]")

        if p.tertiary:
            pct = p.tertiary.used_percent
            bar = _rich_bar(pct)
            lines.append(f"  [bold]{p.tertiary_label}:[/bold]")
            lines.append(f"  {bar} {pct:3.0f}% used")
            reset = p.tertiary.reset_text()
            if reset:
                lines.append(f"    [dim]{reset}[/dim]")

        if p.credits and p.credits.remaining > 0:
            lines.append(f"  [bright_cyan]Credits: {p.credits.remaining:,.2f} left[/bright_cyan]")

        is_cost_primary = "$" in p.primary_label if p.primary else False
        if p.cost and not is_cost_primary:
            cost = p.cost
            if cost.limit > 0:
                cost_pct = min(100.0, (cost.used / cost.limit) * 100.0)
            else:
                cost_pct = 0.0
            bar = _rich_bar(cost_pct)
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


def _rich_bar(used_pct: float, width: int = 20) -> str:
    """Create a small text-based bar for Rich markup (fills up as usage grows)."""
    filled = round((used_pct / 100.0) * width)
    filled = max(0, min(width, filled))
    empty = width - filled

    if used_pct >= 90:
        color = "bold red"
    elif used_pct >= 75:
        color = "red"
    elif used_pct >= 50:
        color = "yellow"
    elif used_pct >= 25:
        color = "bright_green"
    else:
        color = "green"

    bar_filled = f"[{color}]{'━' * filled}[/{color}]" if filled else ""
    bar_empty = f"[dim]{'─' * empty}[/dim]" if empty else ""
    return f"[dim]\\[[/dim]{bar_filled}{bar_empty}[dim]][/dim]"


if __name__ == "__main__":
    main()
