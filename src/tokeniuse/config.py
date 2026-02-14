"""Configuration file handling for tokeniuse.

Config lives at ~/.config/tokeniuse/config.json (XDG).

Example config:
{
  "providers": [
    { "id": "claude" },
    { "id": "codex" },
    { "id": "gemini" },
    { "id": "openai-api", "api_key": "sk-...", "monthly_budget": 100.0 },
    { "id": "anthropic-api", "api_key": "sk-ant-...", "monthly_budget": 50.0 }
  ],
  "refresh_interval": 120
}

The order of the providers array controls the card display order.
Only listed providers are shown. Omit the file to get all providers in default order.
Provider-specific settings (api_key, monthly_budget, etc.) are passed through.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProviderConfig:
    """Configuration for a single provider."""
    id: str
    settings: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "ProviderConfig":
        settings = {k: v for k, v in d.items() if k != "id"}
        return cls(id=d["id"], settings=settings)


@dataclass
class AppConfig:
    """Top-level application configuration."""
    providers: list[ProviderConfig] = field(default_factory=list)
    refresh_interval: float = 300.0  # 5 minutes

    MIN_REFRESH = 60.0    # 1 minute
    MAX_REFRESH = 3600.0  # 1 hour

    @property
    def provider_ids(self) -> list[str]:
        return [p.id for p in self.providers]

    def provider_settings(self, provider_id: str) -> dict:
        """Get settings dict for a specific provider."""
        for p in self.providers:
            if p.id == provider_id:
                return p.settings
        return {}

    @classmethod
    def from_dict(cls, d: dict) -> "AppConfig":
        providers = [ProviderConfig.from_dict(p) for p in d.get("providers", [])]
        raw_interval = d.get("refresh_interval", 300.0)
        clamped = max(cls.MIN_REFRESH, min(cls.MAX_REFRESH, float(raw_interval)))
        return cls(
            providers=providers,
            refresh_interval=clamped,
        )

    @classmethod
    def default(cls) -> "AppConfig":
        """Default config: all providers in default order."""
        from .backend import DEFAULT_PROVIDER_ORDER
        return cls(
            providers=[ProviderConfig(id=pid) for pid in DEFAULT_PROVIDER_ORDER],
        )


def config_path() -> Path:
    """Return the config file path, preferring XDG."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        return Path(xdg) / "tokeniuse" / "config.json"
    return Path.home() / ".config" / "tokeniuse" / "config.json"


def load_config() -> AppConfig:
    """Load config from disk, or return defaults if no file exists."""
    path = config_path()
    if not path.exists():
        return AppConfig.default()

    try:
        data = json.loads(path.read_text())
        cfg = AppConfig.from_dict(data)
        # Filter out unknown provider IDs
        from .backend import PROVIDER_FETCHERS
        valid = [p for p in cfg.providers if p.id in PROVIDER_FETCHERS]
        unknown = [p.id for p in cfg.providers if p.id not in PROVIDER_FETCHERS]
        if unknown:
            import sys
            print(
                f"tokeniuse: ignoring unknown providers in config: {', '.join(unknown)}",
                file=sys.stderr,
            )
        cfg.providers = valid
        if not cfg.providers:
            cfg = AppConfig.default()
        return cfg
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        import sys
        print(f"tokeniuse: bad config ({path}): {e} — using defaults", file=sys.stderr)
        return AppConfig.default()


def init_config() -> None:
    """Create a default config file if one doesn't exist."""
    path = config_path()
    if path.exists():
        print(f"Config already exists: {path}")
        return

    from .backend import DEFAULT_PROVIDER_ORDER

    data = {
        "providers": [{"id": pid} for pid in DEFAULT_PROVIDER_ORDER],
        "refresh_interval": 300,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Created config: {path}")
