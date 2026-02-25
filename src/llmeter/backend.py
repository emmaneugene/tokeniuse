"""Backend — orchestrates provider fetches."""

from __future__ import annotations

import asyncio
from typing import Callable, Awaitable

from .models import ProviderMeta, ProviderResult, PROVIDERS
from .providers.subscription.codex import fetch_codex
from .providers.subscription.claude import fetch_claude
from .providers.subscription.cursor import fetch_cursor
from .providers.subscription.gemini import fetch_gemini
from .providers.subscription.copilot import fetch_copilot
from .providers.api.openai import fetch_openai_api
from .providers.api.anthropic import fetch_anthropic_api
from .providers.api.opencode import fetch_opencode_api

# Type for provider fetch functions.
# All fetchers accept (timeout, settings) keyword args.
FetchFunc = Callable[..., Awaitable[ProviderResult]]

# Registry of supported providers and their default display order.
PROVIDER_FETCHERS: dict[str, FetchFunc] = {
    "codex": fetch_codex,
    "claude": fetch_claude,
    "cursor": fetch_cursor,
    "gemini": fetch_gemini,
    "openai-api": fetch_openai_api,
    "anthropic-api": fetch_anthropic_api,
    "copilot": fetch_copilot,
    "opencode": fetch_opencode_api,
}

# Canonical order for all providers (used by init_config and default config).
# The `default_enabled` flag on each ProviderMeta controls which are active
# out of the box.
ALL_PROVIDER_ORDER = ["codex", "claude", "cursor", "gemini", "copilot", "openai-api", "anthropic-api", "opencode"]


_FALLBACK_META = ProviderMeta(id="?", name="Unknown", icon="●", color="#888888")


def placeholder_result(provider_id: str) -> ProviderResult:
    """Create a loading placeholder for a provider."""
    meta = PROVIDERS.get(provider_id, _FALLBACK_META)
    return meta.to_result(provider_id=provider_id, source="loading")


async def fetch_one(
    provider_id: str,
    settings: dict | None = None,
    timeout: float = 30.0,
) -> ProviderResult:
    """Fetch usage data for a single provider."""
    fetcher = PROVIDER_FETCHERS.get(provider_id)
    meta = PROVIDERS.get(provider_id, _FALLBACK_META)

    if not fetcher:
        return meta.to_result(
            provider_id=provider_id, error=f"Unknown provider: {provider_id}",
        )

    try:
        kwargs: dict = {"timeout": timeout}
        if settings:
            kwargs["settings"] = settings
        return await fetcher(**kwargs)
    except Exception as e:
        return meta.to_result(provider_id=provider_id, error=str(e))


async def fetch_all(
    provider_ids: list[str] | None = None,
    provider_settings: dict[str, dict] | None = None,
    timeout: float = 30.0,
) -> list[ProviderResult]:
    """Fetch usage data for all specified providers in parallel."""
    ids = (
        provider_ids
        if provider_ids is not None
        else [
            pid for pid in ALL_PROVIDER_ORDER
            if PROVIDERS.get(pid, ProviderMeta(id=pid, name="", icon="", color="")).default_enabled
        ]
    )
    settings_map = provider_settings or {}

    tasks = [
        fetch_one(pid, settings=settings_map.get(pid), timeout=timeout)
        for pid in ids
    ]

    return list(await asyncio.gather(*tasks))
