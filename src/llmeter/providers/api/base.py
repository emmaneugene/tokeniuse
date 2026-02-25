"""Base class for API billing providers (API key auth)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ...models import PROVIDERS, ProviderMeta, ProviderResult


class ApiProvider(ABC):
    """Base class for providers that authenticate via an API key.

    Subclasses must implement:
    - ``provider_id`` – ID matching a key in ``models.PROVIDERS``
    - ``resolve_api_key(settings)`` – extract the key from config/env, or return ``None``
    - ``_fetch(api_key, timeout, settings)`` – perform the actual HTTP fetch

    ``__call__`` handles the shared lifecycle:
    1. Resolve the API key → return an error result if missing
    2. Delegate to ``_fetch``
    3. Catch any unhandled exception → return an error result
    """

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Provider ID matching a key in ``models.PROVIDERS``."""
        ...

    @property
    def no_api_key_error(self) -> str:
        """Error message shown when no API key is found."""
        return (
            f"{self.provider_id} API key not configured. "
            "Set the relevant environment variable or add api_key to config."
        )

    @abstractmethod
    def resolve_api_key(self, settings: dict) -> str | None:
        """Extract and validate the API key from *settings* or the environment."""
        ...

    @abstractmethod
    async def _fetch(
        self,
        api_key: str,
        timeout: float,
        settings: dict,
    ) -> ProviderResult:
        """Perform the provider-specific fetch using the resolved API key."""
        ...

    async def __call__(
        self,
        timeout: float = 30.0,
        settings: dict | None = None,
    ) -> ProviderResult:
        settings = settings or {}
        meta = PROVIDERS.get(self.provider_id) or ProviderMeta(
            id=self.provider_id, name=self.provider_id, icon="●", color="#888888"
        )
        result = meta.to_result(source="api")

        api_key = self.resolve_api_key(settings)
        if not api_key:
            result.error = self.no_api_key_error
            return result

        try:
            return await self._fetch(api_key, timeout=timeout, settings=settings)
        except Exception as e:
            result.error = f"{self.provider_id} API error: {e}"
            return result
