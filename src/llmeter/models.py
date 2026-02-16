"""Data models for llmeter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class RateWindow:
    """A single usage rate window (session, weekly, opus, etc.)."""

    used_percent: float
    window_minutes: Optional[int] = None
    resets_at: Optional[datetime] = None
    reset_description: Optional[str] = None

    @property
    def remaining_percent(self) -> float:
        return max(0.0, 100.0 - self.used_percent)

    def reset_text(self, now: datetime | None = None) -> str:
        """Human-readable reset countdown."""
        if self.resets_at:
            now = now or datetime.now(timezone.utc)
            delta = self.resets_at - now
            secs = max(0, int(delta.total_seconds()))
            if secs < 60:
                return "Resets now"
            mins = secs // 60
            hours = mins // 60
            days = hours // 24
            if days > 0:
                h = hours % 24
                return f"Resets in {days}d {h}h" if h else f"Resets in {days}d"
            if hours > 0:
                m = mins % 60
                return f"Resets in {hours}h {m}m" if m else f"Resets in {hours}h"
            return f"Resets in {mins}m"
        if self.reset_description:
            desc = self.reset_description.strip()
            if desc.lower().startswith("resets"):
                return desc
            return f"Resets {desc}"
        return ""


@dataclass
class ProviderIdentity:
    account_email: Optional[str] = None
    account_organization: Optional[str] = None
    login_method: Optional[str] = None


@dataclass
class CreditsInfo:
    remaining: float = 0.0


@dataclass
class CostInfo:
    used: float = 0.0
    limit: float = 0.0
    currency: str = "USD"
    period: str = "Monthly"


@dataclass
class ProviderResult:
    """Complete result for one provider fetch."""

    provider_id: str
    display_name: str
    icon: str
    color: str
    source: str = "unknown"
    primary: Optional[RateWindow] = None
    secondary: Optional[RateWindow] = None
    tertiary: Optional[RateWindow] = None
    credits: Optional[CreditsInfo] = None
    cost: Optional[CostInfo] = None
    identity: Optional[ProviderIdentity] = None
    version: Optional[str] = None
    error: Optional[str] = None
    updated_at: Optional[datetime] = None

    # Labels for each window
    primary_label: str = "Session"
    secondary_label: str = "Weekly"
    tertiary_label: str = "Sonnet"


# ── Provider display metadata ──────────────────────────────────────────────


@dataclass(frozen=True)
class ProviderMeta:
    """Display metadata for a provider (single source of truth)."""
    id: str
    name: str
    icon: str
    color: str
    primary_label: str = "Session"
    secondary_label: str = "Weekly"
    tertiary_label: str = "Sonnet"
    default_enabled: bool = False

    def to_result(self, **overrides) -> ProviderResult:
        """Create a ProviderResult pre-filled with this provider's metadata."""
        kwargs: dict = dict(
            provider_id=self.id,
            display_name=self.name,
            icon=self.icon,
            color=self.color,
            primary_label=self.primary_label,
            secondary_label=self.secondary_label,
            tertiary_label=self.tertiary_label,
        )
        kwargs.update(overrides)
        return ProviderResult(**kwargs)


PROVIDERS: dict[str, ProviderMeta] = {
    "codex": ProviderMeta(
        id="codex", name="Codex", icon="⬡", color="#10a37f",
        primary_label="Session (5h)", secondary_label="Weekly",
        default_enabled=True,
    ),
    "claude": ProviderMeta(
        id="claude", name="Claude", icon="◈", color="#d4a27f",
        primary_label="Session (5h)", secondary_label="Weekly",
        tertiary_label="Sonnet",
        default_enabled=True,
    ),
    "cursor": ProviderMeta(
        id="cursor", name="Cursor", icon="⦿", color="#00bfa5",
        primary_label="Plan", secondary_label="On-Demand",
    ),
    "gemini": ProviderMeta(
        id="gemini", name="Gemini", icon="✦", color="#ab87ea",
        primary_label="Pro (24h)", secondary_label="Flash (24h)",
    ),
    "openai-api": ProviderMeta(
        id="openai-api", name="OpenAI API", icon="⬡", color="#10a37f",
        primary_label="Spend",
    ),
    "anthropic-api": ProviderMeta(
        id="anthropic-api", name="Anthropic API", icon="◈", color="#d4a27f",
        primary_label="Spend",
    ),
}
