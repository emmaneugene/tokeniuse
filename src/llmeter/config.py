"""Configuration file handling for llmeter.

Config lives at ~/.config/llmeter/settings.json (XDG).

All known providers are listed in the config.  The ``enabled`` flag on each
entry determines whether it is displayed.  Provider order in the array
controls the card display order.

Example config:
{
  "providers": [
    { "id": "codex",         "enabled": true },
    { "id": "claude",        "enabled": true },
    { "id": "gemini",        "enabled": false },
    { "id": "openai-api",    "enabled": false, "api_key": "sk-admin-..." },
    { "id": "anthropic-api", "enabled": false, "api_key": "sk-ant-admin01-..." }
  ],
  "refresh_interval": 120
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .providers.helpers import config_dir


@dataclass
class ProviderConfig:
    """Configuration for a single provider."""
    id: str
    enabled: bool = True
    settings: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "ProviderConfig":
        raw_enabled = d.get("enabled", True)
        if isinstance(raw_enabled, bool):
            enabled = raw_enabled
        elif isinstance(raw_enabled, int):
            # Accept 0/1 as boolean integers (common JSON mistake).
            enabled = bool(raw_enabled)
        else:
            import sys
            print(
                f"llmeter: provider '{d.get('id', '?')}' has non-bool 'enabled' "
                f"value {raw_enabled!r} — treating as enabled. Use true/false in JSON.",
                file=sys.stderr,
            )
            enabled = True
        settings = {k: v for k, v in d.items() if k not in ("id", "enabled")}
        return cls(id=d["id"], enabled=enabled, settings=settings)

    def to_dict(self) -> dict:
        """Serialize back to a JSON-friendly dict."""
        d: dict = {"id": self.id, "enabled": self.enabled}
        d.update(self.settings)
        return d


@dataclass
class AppConfig:
    """Top-level application configuration."""
    providers: list[ProviderConfig] = field(default_factory=list)
    refresh_interval: float = 300.0  # 5 minutes

    MIN_REFRESH = 60.0    # 1 minute
    MAX_REFRESH = 3600.0  # 1 hour

    @property
    def enabled_providers(self) -> list[ProviderConfig]:
        """Only the enabled provider configs, preserving order."""
        return [p for p in self.providers if p.enabled]

    @property
    def provider_ids(self) -> list[str]:
        """IDs of *enabled* providers only."""
        return [p.id for p in self.providers if p.enabled]

    @property
    def all_provider_ids(self) -> list[str]:
        """IDs of all providers (enabled and disabled)."""
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
        """Default runtime config (no providers enabled unless user configures them)."""
        return cls(providers=[])


def config_path() -> Path:
    """Return the config file path, preferring XDG."""
    return config_dir("settings.json")


def load_config() -> AppConfig:
    """Load config from disk, or return defaults if no file exists."""
    path = config_path()
    if not path.exists():
        return AppConfig.default()

    try:
        data = json.loads(path.read_text())
        cfg = AppConfig.from_dict(data)

        # Filter out unknown provider IDs
        from .backend import PROVIDER_FETCHERS, ALL_PROVIDER_ORDER
        valid = [p for p in cfg.providers if p.id in PROVIDER_FETCHERS]
        unknown = [p.id for p in cfg.providers if p.id not in PROVIDER_FETCHERS]
        if unknown:
            import sys
            print(
                f"llmeter: ignoring unknown providers in config: {', '.join(unknown)}",
                file=sys.stderr,
            )

        # Auto-discover new providers not yet in config (append as disabled)
        existing_ids = {p.id for p in valid}
        for pid in ALL_PROVIDER_ORDER:
            if pid not in existing_ids and pid in PROVIDER_FETCHERS:
                valid.append(ProviderConfig(id=pid, enabled=False))

        cfg.providers = valid
        return cfg
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        import sys
        print(f"llmeter: bad config ({path}): {e} — using defaults", file=sys.stderr)
        return AppConfig.default()


def init_config() -> None:
    """Create a config file with all known providers pre-populated (disabled)."""
    path = config_path()
    if path.exists():
        print(f"Config already exists: {path}")
        return

    from .backend import ALL_PROVIDER_ORDER

    providers = [ProviderConfig(id=pid, enabled=False) for pid in ALL_PROVIDER_ORDER]
    cfg = AppConfig(providers=providers)
    data = {
        "providers": [p.to_dict() for p in cfg.providers],
        "refresh_interval": int(cfg.refresh_interval),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Created config: {path}")


def enable_provider(provider_id: str) -> None:
    """Enable a provider in settings.json, creating the file if needed.

    If the provider isn't listed yet, it is appended.  If it's already
    enabled, this is a no-op.
    """
    path = config_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}

    providers = data.get("providers", [])

    # Find existing entry
    for p in providers:
        if p.get("id") == provider_id:
            if p.get("enabled", True) is True:
                return  # already enabled
            p["enabled"] = True
            break
    else:
        # Not listed — append as enabled
        providers.append({"id": provider_id, "enabled": True})

    data["providers"] = providers
    if "refresh_interval" not in data:
        data["refresh_interval"] = 300

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
