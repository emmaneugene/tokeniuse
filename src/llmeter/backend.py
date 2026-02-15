"""Backend — orchestrates provider fetches."""

from __future__ import annotations

import asyncio
from typing import Callable, Awaitable

from .models import ProviderResult, PROVIDERS
from .providers.codex import fetch_codex
from .providers.claude import fetch_claude
from .providers.gemini import fetch_gemini
from .providers.openai_api import fetch_openai_api
from .providers.anthropic_api import fetch_anthropic_api

# Type for provider fetch functions.
# All fetchers accept (timeout, settings) keyword args.
FetchFunc = Callable[..., Awaitable[ProviderResult]]

# Registry of supported providers and their default display order.
PROVIDER_FETCHERS: dict[str, FetchFunc] = {
    "codex": fetch_codex,
    "claude": fetch_claude,
    "gemini": fetch_gemini,
    "openai-api": fetch_openai_api,
    "anthropic-api": fetch_anthropic_api,
}

# Default order when no config file exists (only auto-detected providers)
DEFAULT_PROVIDER_ORDER = ["codex", "claude"]


def placeholder_result(provider_id: str) -> ProviderResult:
    """Create a loading placeholder for a provider."""
    meta = PROVIDERS.get(provider_id, {})
    return ProviderResult(
        provider_id=provider_id,
        display_name=meta.get("name", provider_id.title()),
        icon=meta.get("icon", "●"),
        color=meta.get("color", "#888888"),
        source="loading",
    )


async def fetch_one(
    provider_id: str,
    settings: dict | None = None,
    timeout: float = 30.0,
) -> ProviderResult:
    """Fetch usage data for a single provider."""
    fetcher = PROVIDER_FETCHERS.get(provider_id)
    if not fetcher:
        return ProviderResult(
            provider_id=provider_id,
            display_name=provider_id.title(),
            icon="●",
            color="#888888",
            error=f"Unknown provider: {provider_id}",
        )

    try:
        kwargs: dict = {"timeout": timeout}
        if settings:
            kwargs["settings"] = settings
        return await fetcher(**kwargs)
    except Exception as e:
        meta = PROVIDERS.get(provider_id, {})
        return ProviderResult(
            provider_id=provider_id,
            display_name=meta.get("name", provider_id.title()),
            icon=meta.get("icon", "●"),
            color=meta.get("color", "#888888"),
            error=str(e),
        )


async def fetch_all(
    provider_ids: list[str] | None = None,
    provider_settings: dict[str, dict] | None = None,
    timeout: float = 30.0,
) -> list[ProviderResult]:
    """Fetch usage data for all specified providers in parallel."""
    ids = provider_ids or DEFAULT_PROVIDER_ORDER
    settings_map = provider_settings or {}

    tasks = [
        fetch_one(pid, settings=settings_map.get(pid), timeout=timeout)
        for pid in ids
    ]

    return list(await asyncio.gather(*tasks))
