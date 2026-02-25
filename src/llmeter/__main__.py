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
        "--snapshot",
        action="store_true",
        help="Fetch and print data once to stdout (no TUI), with Rich formatting.",
    )
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="Create a default config file and exit.",
    )
    parser.add_argument(
        "--login",
        metavar="PROVIDER",
        help="Authenticate with an auth provider.",
    )
    parser.add_argument(
        "--logout",
        metavar="PROVIDER",
        help="Remove stored credentials for an auth provider.",
    )
    args = parser.parse_args()

    if args.init_config:
        from .config import init_config
        init_config()
        return

    if args.login and args.logout:
        print("Specify only one of --login or --logout.", file=sys.stderr)
        sys.exit(2)

    def _enable_and_login(provider_id: str, login_func) -> None:
        login_func()
        from .config import enable_provider
        enable_provider(provider_id)

    def _clear_credentials(label: str, load_func, clear_func) -> None:
        if load_func():
            clear_func()
            print(f"✓ Removed {label} credentials.")
        else:
            print(f"No {label} credentials stored.")

    def _login_claude() -> None:
        from .providers.subscription.claude_login import interactive_login
        _enable_and_login("claude", interactive_login)

    def _login_codex() -> None:
        from .providers.subscription.codex_login import interactive_login
        _enable_and_login("codex", interactive_login)

    def _login_gemini() -> None:
        from .providers.subscription.gemini_login import interactive_login
        _enable_and_login("gemini", interactive_login)

    def _login_copilot() -> None:
        from .providers.subscription.copilot_login import interactive_login
        _enable_and_login("copilot", interactive_login)

    def _login_cursor() -> None:
        from .providers.subscription.cursor_login import interactive_login
        _enable_and_login("cursor", interactive_login)

    def _logout_claude() -> None:
        from .providers.subscription.claude import clear_credentials, load_credentials
        _clear_credentials("Claude", load_credentials, clear_credentials)

    def _logout_codex() -> None:
        from .providers.subscription.codex import clear_credentials, load_credentials
        _clear_credentials("Codex", load_credentials, clear_credentials)

    def _logout_gemini() -> None:
        from .providers.subscription.gemini import clear_credentials, load_credentials
        _clear_credentials("Gemini", load_credentials, clear_credentials)

    def _logout_copilot() -> None:
        from .providers.subscription.copilot import clear_credentials, load_credentials
        _clear_credentials("Copilot", load_credentials, clear_credentials)

    def _logout_cursor() -> None:
        from .providers.subscription.cursor import clear_credentials, load_credentials
        _clear_credentials("Cursor", load_credentials, clear_credentials)

    login_handlers = {
        "claude": _login_claude,
        "codex": _login_codex,
        "gemini": _login_gemini,
        "copilot": _login_copilot,
        "cursor": _login_cursor,
    }

    logout_handlers = {
        "claude": _logout_claude,
        "codex": _logout_codex,
        "gemini": _logout_gemini,
        "copilot": _logout_copilot,
        "cursor": _logout_cursor,
    }

    if args.login:
        provider = args.login.strip().lower()
        handler = login_handlers.get(provider)
        if not handler:
            available = ", ".join(sorted(login_handlers))
            print(f"Unknown provider for --login: {provider}. Choose one of: {available}", file=sys.stderr)
            sys.exit(2)
        try:
            handler()
        except (RuntimeError, KeyboardInterrupt) as e:
            print(f"Login failed: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if args.logout:
        provider = args.logout.strip().lower()
        handler = logout_handlers.get(provider)
        if not handler:
            available = ", ".join(sorted(logout_handlers))
            print(f"Unknown provider for --logout: {provider}. Choose one of: {available}", file=sys.stderr)
            sys.exit(2)
        handler()
        return

    from .config import load_config

    config = load_config()

    # CLI --refresh overrides config (clamped to 60s–3600s)
    if args.refresh is not None:
        from .config import AppConfig
        config.refresh_interval = max(
            AppConfig.MIN_REFRESH, min(AppConfig.MAX_REFRESH, args.refresh)
        )

    if args.snapshot:
        _run_snapshot(config)
        return

    from .app import LLMeterApp

    app = LLMeterApp(config=config)
    app.run()


def _run_snapshot(config) -> None:
    """Non-interactive mode: fetch data once and print with Rich."""
    import asyncio

    from rich.console import Console
    from rich.panel import Panel

    from .backend import fetch_all

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

        if p.primary:
            pct = p.primary.used_percent
            bar = _rich_bar(pct, width=bar_width)
            lines.append(f"  [bold]{p.primary_label}:[/bold]")
            lines.append(f"  {bar} {pct:3.0f}% used")
            reset = p.primary.reset_text()
            if reset:
                lines.append(f"    [dim]{reset}[/dim]")

        if p.secondary:
            pct = p.secondary.used_percent
            bar = _rich_bar(pct, width=bar_width)
            lines.append(f"  [bold]{p.secondary_label}:[/bold]")
            lines.append(f"  {bar} {pct:3.0f}% used")
            reset = p.secondary.reset_text()
            if reset:
                lines.append(f"    [dim]{reset}[/dim]")

        if p.tertiary:
            pct = p.tertiary.used_percent
            bar = _rich_bar(pct, width=bar_width)
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
