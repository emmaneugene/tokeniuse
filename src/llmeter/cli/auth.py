"""Login/logout dispatch for the llmeter CLI.

Subscription providers have interactive OAuth flows exposed via --login
and --logout.  API providers (anthropic-api, openai-api, opencode) use
API keys or env vars and have no interactive flow.
"""

from __future__ import annotations

import sys
from typing import Callable


# ── Helpers ────────────────────────────────────────────────


def _enable_and_login(provider_id: str, login_func) -> None:
    login_func()
    from ..config import enable_provider
    enable_provider(provider_id)


def _clear_credentials(label: str, load_func, clear_func) -> None:
    if load_func():
        clear_func()
        print(f"✓ Removed {label} credentials.")
    else:
        print(f"No {label} credentials stored.")


# ── Login handlers ─────────────────────────────────────────


def _login_claude() -> None:
    from ..providers.subscription.claude_login import interactive_login
    _enable_and_login("claude", interactive_login)


def _login_codex() -> None:
    from ..providers.subscription.codex_login import interactive_login
    _enable_and_login("codex", interactive_login)


def _login_gemini() -> None:
    from ..providers.subscription.gemini_login import interactive_login
    _enable_and_login("gemini", interactive_login)


def _login_copilot() -> None:
    from ..providers.subscription.copilot_login import interactive_login
    _enable_and_login("copilot", interactive_login)


def _login_cursor() -> None:
    from ..providers.subscription.cursor_login import interactive_login
    _enable_and_login("cursor", interactive_login)


# ── Logout handlers ────────────────────────────────────────


def _logout_claude() -> None:
    from ..providers.subscription.claude import clear_credentials, load_credentials
    _clear_credentials("Claude", load_credentials, clear_credentials)


def _logout_codex() -> None:
    from ..providers.subscription.codex import clear_credentials, load_credentials
    _clear_credentials("Codex", load_credentials, clear_credentials)


def _logout_gemini() -> None:
    from ..providers.subscription.gemini import clear_credentials, load_credentials
    _clear_credentials("Gemini", load_credentials, clear_credentials)


def _logout_copilot() -> None:
    from ..providers.subscription.copilot import clear_credentials, load_credentials
    _clear_credentials("Copilot", load_credentials, clear_credentials)


def _logout_cursor() -> None:
    from ..providers.subscription.cursor import clear_credentials, load_credentials
    _clear_credentials("Cursor", load_credentials, clear_credentials)


# ── Dispatch tables ────────────────────────────────────────

_SUBSCRIPTION_PROVIDERS = {"claude", "codex", "gemini", "copilot", "cursor"}

LOGIN_HANDLERS: dict[str, Callable[[], None]] = {
    "claude":  _login_claude,
    "codex":   _login_codex,
    "gemini":  _login_gemini,
    "copilot": _login_copilot,
    "cursor":  _login_cursor,
}

LOGOUT_HANDLERS: dict[str, Callable[[], None]] = {
    "claude":  _logout_claude,
    "codex":   _logout_codex,
    "gemini":  _logout_gemini,
    "copilot": _logout_copilot,
    "cursor":  _logout_cursor,
}

assert LOGIN_HANDLERS.keys() == _SUBSCRIPTION_PROVIDERS, (
    f"LOGIN_HANDLERS missing: {_SUBSCRIPTION_PROVIDERS - LOGIN_HANDLERS.keys()}"
)
assert LOGOUT_HANDLERS.keys() == _SUBSCRIPTION_PROVIDERS, (
    f"LOGOUT_HANDLERS missing: {_SUBSCRIPTION_PROVIDERS - LOGOUT_HANDLERS.keys()}"
)


# ── Public dispatch ────────────────────────────────────────


def login_provider(provider: str) -> None:
    """Run the interactive login flow for *provider*.

    Exits with code 2 on unknown provider; raises RuntimeError on failure.
    """
    handler = LOGIN_HANDLERS.get(provider)
    if not handler:
        available = ", ".join(sorted(LOGIN_HANDLERS))
        print(
            f"Unknown provider for --login: {provider}. "
            f"Choose one of: {available}",
            file=sys.stderr,
        )
        sys.exit(2)
    handler()


def logout_provider(provider: str) -> None:
    """Clear stored credentials for *provider*.

    Exits with code 2 on unknown provider.
    """
    handler = LOGOUT_HANDLERS.get(provider)
    if not handler:
        available = ", ".join(sorted(LOGOUT_HANDLERS))
        print(
            f"Unknown provider for --logout: {provider}. "
            f"Choose one of: {available}",
            file=sys.stderr,
        )
        sys.exit(2)
    handler()
