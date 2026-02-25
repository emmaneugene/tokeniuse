"""Tests for config loading with enabled/disabled providers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llmeter.config import (
    AppConfig,
    ProviderConfig,
    enable_provider,
    init_config,
    load_config,
    config_path,
)


class TestProviderConfig:
    """Test ProviderConfig serialization."""

    def test_from_dict_defaults_enabled_true(self) -> None:
        pc = ProviderConfig.from_dict({"id": "codex"})
        assert pc.id == "codex"
        assert pc.enabled is True

    def test_from_dict_explicit_enabled(self) -> None:
        pc = ProviderConfig.from_dict({"id": "codex", "enabled": True})
        assert pc.enabled is True

    def test_from_dict_explicit_disabled(self) -> None:
        pc = ProviderConfig.from_dict({"id": "gemini", "enabled": False})
        assert pc.enabled is False

    def test_from_dict_preserves_settings(self) -> None:
        pc = ProviderConfig.from_dict(
            {
                "id": "openai-api",
                "enabled": True,
                "api_key": "sk-test",
                "monthly_budget": 50.0,
            }
        )
        assert pc.settings == {"api_key": "sk-test", "monthly_budget": 50.0}

    def test_to_dict_roundtrip(self) -> None:
        original = {"id": "codex", "enabled": True}
        pc = ProviderConfig.from_dict(original)
        assert pc.to_dict() == original

    def test_to_dict_with_settings(self) -> None:
        pc = ProviderConfig(id="openai-api", enabled=False, settings={"api_key": "sk"})
        d = pc.to_dict()
        assert d == {"id": "openai-api", "enabled": False, "api_key": "sk"}


class TestAppConfig:
    """Test AppConfig enabled filtering."""

    def test_provider_ids_returns_only_enabled(self) -> None:
        cfg = AppConfig(
            providers=[
                ProviderConfig(id="codex", enabled=True),
                ProviderConfig(id="gemini", enabled=False),
                ProviderConfig(id="claude", enabled=True),
            ]
        )
        assert cfg.provider_ids == ["codex", "claude"]

    def test_all_provider_ids_returns_all(self) -> None:
        cfg = AppConfig(
            providers=[
                ProviderConfig(id="codex", enabled=True),
                ProviderConfig(id="gemini", enabled=False),
            ]
        )
        assert cfg.all_provider_ids == ["codex", "gemini"]

    def test_enabled_providers_preserves_order(self) -> None:
        cfg = AppConfig(
            providers=[
                ProviderConfig(id="gemini", enabled=True),
                ProviderConfig(id="codex", enabled=False),
                ProviderConfig(id="claude", enabled=True),
            ]
        )
        ids = [p.id for p in cfg.enabled_providers]
        assert ids == ["gemini", "claude"]

    def test_default_has_all_providers(self) -> None:
        cfg = AppConfig.default()
        assert len(cfg.providers) == 8
        assert set(cfg.all_provider_ids) == {
            "codex",
            "claude",
            "cursor",
            "gemini",
            "copilot",
            "openai-api",
            "anthropic-api",
            "opencode",
        }

    def test_default_enables_codex_and_claude_only(self) -> None:
        cfg = AppConfig.default()
        assert cfg.provider_ids == ["codex", "claude"]

    def test_from_dict_with_enabled_field(self) -> None:
        data = {
            "providers": [
                {"id": "codex", "enabled": True},
                {"id": "gemini", "enabled": True},
                {"id": "claude", "enabled": False},
            ],
        }
        cfg = AppConfig.from_dict(data)
        assert cfg.provider_ids == ["codex", "gemini"]


class TestInitConfig:
    """Test config file generation."""

    def test_init_creates_config_with_all_providers(self, tmp_config_dir: Path) -> None:
        init_config()

        path = config_path()
        assert path.exists()
        data = json.loads(path.read_text())

        ids = [p["id"] for p in data["providers"]]
        assert "codex" in ids
        assert "claude" in ids
        assert "gemini" in ids
        assert "openai-api" in ids
        assert "anthropic-api" in ids

    def test_init_config_default_enabled(self, tmp_config_dir: Path) -> None:
        init_config()

        data = json.loads(config_path().read_text())
        by_id = {p["id"]: p for p in data["providers"]}

        assert by_id["codex"]["enabled"] is True
        assert by_id["claude"]["enabled"] is True
        assert by_id["gemini"]["enabled"] is False
        assert by_id["openai-api"]["enabled"] is False
        assert by_id["anthropic-api"]["enabled"] is False

    def test_init_does_not_overwrite_existing(self, tmp_config_dir: Path) -> None:
        init_config()
        first_content = config_path().read_text()

        init_config()  # second call should be a no-op
        assert config_path().read_text() == first_content


class TestLoadConfig:
    """Test config loading from disk."""

    def test_load_returns_default_when_no_file(self, tmp_config_dir: Path) -> None:
        cfg = load_config()
        assert cfg.provider_ids == ["codex", "claude"]
        assert len(cfg.providers) == 8

    def test_load_respects_enabled_flag(self, tmp_config_dir: Path) -> None:
        data = {
            "providers": [
                {"id": "gemini", "enabled": True},
                {"id": "codex", "enabled": False},
                {"id": "claude", "enabled": True},
            ],
            "refresh_interval": 120,
        }
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

        cfg = load_config()
        assert cfg.provider_ids == ["gemini", "claude"]
        assert cfg.refresh_interval == 120

    def test_load_auto_discovers_new_providers(self, tmp_config_dir: Path) -> None:
        """Providers added after config was created should appear as disabled."""
        data = {
            "providers": [
                {"id": "codex", "enabled": True},
                {"id": "claude", "enabled": True},
            ],
        }
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

        cfg = load_config()

        # Original two are enabled
        assert cfg.provider_ids == ["codex", "claude"]

        # All known providers should be present (originals + auto-discovered)
        all_ids = cfg.all_provider_ids
        assert "codex" in all_ids
        assert "claude" in all_ids
        assert "cursor" in all_ids
        assert "gemini" in all_ids
        assert "openai-api" in all_ids
        assert "anthropic-api" in all_ids

        # Auto-discovered ones are disabled
        by_id = {p.id: p for p in cfg.providers}
        assert by_id["cursor"].enabled is False
        assert by_id["gemini"].enabled is False

    def test_load_preserves_existing_order(self, tmp_config_dir: Path) -> None:
        """Auto-discovered providers should appear after existing ones."""
        data = {
            "providers": [
                {"id": "gemini", "enabled": True},
                {"id": "codex", "enabled": True},
            ],
        }
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

        cfg = load_config()

        # Existing order is preserved, new ones appended
        all_ids = cfg.all_provider_ids
        assert all_ids.index("gemini") < all_ids.index("codex")
        # Auto-discovered come after existing
        assert all_ids.index("codex") < all_ids.index("cursor")

    def test_enable_provider_in_existing_config(self, tmp_config_dir: Path) -> None:
        """enable_provider should flip enabled=true for a disabled provider."""
        data = {
            "providers": [
                {"id": "codex", "enabled": True},
                {"id": "cursor", "enabled": False},
            ],
        }
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

        enable_provider("cursor")

        reloaded = json.loads(path.read_text())
        by_id = {p["id"]: p for p in reloaded["providers"]}
        assert by_id["cursor"]["enabled"] is True
        assert by_id["codex"]["enabled"] is True  # unchanged

    def test_enable_provider_appends_if_missing(self, tmp_config_dir: Path) -> None:
        """enable_provider should append a new entry if provider not listed."""
        data = {
            "providers": [{"id": "codex", "enabled": True}],
        }
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

        enable_provider("cursor")

        reloaded = json.loads(path.read_text())
        ids = [p["id"] for p in reloaded["providers"]]
        assert "cursor" in ids
        by_id = {p["id"]: p for p in reloaded["providers"]}
        assert by_id["cursor"]["enabled"] is True

    def test_enable_provider_creates_config_if_missing(
        self, tmp_config_dir: Path
    ) -> None:
        """enable_provider should create settings.json if it doesn't exist."""
        path = config_path()
        assert not path.exists()

        enable_provider("cursor")

        assert path.exists()
        reloaded = json.loads(path.read_text())
        by_id = {p["id"]: p for p in reloaded["providers"]}
        assert by_id["cursor"]["enabled"] is True

    def test_enable_provider_noop_if_already_enabled(
        self, tmp_config_dir: Path
    ) -> None:
        data = {
            "providers": [{"id": "codex", "enabled": True}],
            "refresh_interval": 120,
        }
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        original = path.read_text()

        enable_provider("codex")

        assert path.read_text() == original  # file unchanged

    def test_load_falls_back_when_nothing_enabled(self, tmp_config_dir: Path) -> None:
        data = {
            "providers": [
                {"id": "codex", "enabled": False},
                {"id": "claude", "enabled": False},
            ],
        }
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

        cfg = load_config()
        # Falls back to default since nothing was enabled
        assert "codex" in cfg.provider_ids
        assert "claude" in cfg.provider_ids
