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

    def __post_init__(self) -> None:
        # Clamp at the model layer so callers never see out-of-range values.
        self.used_percent = max(0.0, min(100.0, self.used_percent))

    @property
    def remaining_percent(self) -> float:
        return max(0.0, 100.0 - self.used_percent)

    def reset_text(self, now: datetime | None = None) -> str:
        """Human-readable reset time in local timezone + relative countdown."""
        if self.resets_at:
            # Use system local timezone by default.
            now_local = now or datetime.now().astimezone()
            if now_local.tzinfo is None:
                now_local = now_local.astimezone()

            reset_dt = self.resets_at
            # Provider timestamps should be timezone-aware; if not, assume UTC.
            if reset_dt.tzinfo is None:
                reset_dt = reset_dt.replace(tzinfo=timezone.utc)

            now_utc = now_local.astimezone(timezone.utc)
            reset_utc = reset_dt.astimezone(timezone.utc)
            secs = max(0, int((reset_utc - now_utc).total_seconds()))
            if secs < 60:
                return "Resets now"

            reset_local = reset_utc.astimezone(now_local.tzinfo)
            relative = self._format_relative(secs)

            if secs >= 24 * 60 * 60:
                absolute = self._format_date(reset_local)
            else:
                absolute = self._format_clock_time(reset_local)

            return f"Resets {absolute} ({relative})"

        if self.reset_description:
            desc = self.reset_description.strip()
            if desc.lower().startswith("resets"):
                return desc
            return f"Resets {desc}"
        return ""

    @staticmethod
    def _format_clock_time(dt: datetime) -> str:
        """Format local time as 12-hour clock (e.g. 3am, 3:05pm)."""
        hour12 = dt.hour % 12 or 12
        suffix = "am" if dt.hour < 12 else "pm"
        if dt.minute == 0:
            return f"{hour12}{suffix}"
        return f"{hour12}:{dt.minute:02d}{suffix}"

    @staticmethod
    def _format_date(dt: datetime) -> str:
        """Format local date as dd Mmm (e.g. 01 Mar)."""
        return dt.strftime("%d %b")

    @staticmethod
    def _format_relative(secs: int) -> str:
        """Format relative remaining time (e.g. 3h 55min)."""
        mins = secs // 60
        hours = mins // 60
        days = hours // 24

        if days > 0:
            h = hours % 24
            return f"{days}d {h}h" if h else f"{days}d"

        if hours > 0:
            m = mins % 60
            return f"{hours}h {m}min" if m else f"{hours}h"

        return f"{mins}min"


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

    # Labels for each window — always set via ProviderMeta.to_result();
    # empty string signals a result constructed without going through metadata.
    primary_label: str = ""
    secondary_label: str = ""
    tertiary_label: str = ""

    @property
    def cost_is_primary_display(self) -> bool:
        """True for API billing providers, whose primary bar shows spend not %.

        API providers (source == "api") use cost as their primary metric.
        Subscription providers (source == "oauth" / "cookie") are percentage-based;
        any CostInfo they carry is extra/secondary usage (e.g. Claude overage).
        """
        return self.source == "api"

    def windows(self) -> list[tuple[str, RateWindow]]:
        """Return (label, window) pairs for each non-None rate window, in order."""
        return [
            (label, window)
            for label, window in [
                (self.primary_label,   self.primary),
                (self.secondary_label, self.secondary),
                (self.tertiary_label,  self.tertiary),
            ]
            if window is not None
        ]


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


# Ordered dict — insertion order is the canonical display order used throughout
# the app (cards, snapshot, config init).  Add new providers here; the order
# is automatically picked up by backend.ALL_PROVIDER_ORDER.
PROVIDERS: dict[str, ProviderMeta] = {
    "claude": ProviderMeta(
        id="claude",
        name="Claude",
        icon="◈",
        color="#d4a27f",
        primary_label="Session (5h)",
        secondary_label="Weekly",
        tertiary_label="Sonnet",
        default_enabled=True,
    ),
    "codex": ProviderMeta(
        id="codex",
        name="Codex",
        icon="⬡",
        color="#10a37f",
        primary_label="Session (5h)",
        secondary_label="Weekly",
        default_enabled=True,
    ),
    "gemini": ProviderMeta(
        id="gemini",
        name="Gemini",
        icon="✦",
        color="#ab87ea",
        primary_label="Pro (24h)",
        secondary_label="Flash (24h)",
    ),
    "cursor": ProviderMeta(
        id="cursor",
        name="Cursor",
        icon="⦿",
        color="#848484",
        primary_label="Plan",
        secondary_label="On-Demand",
    ),
    "copilot": ProviderMeta(
        id="copilot",
        name="Copilot",
        icon="⬠",
        color="#6e40c9",
        primary_label="Premium (Monthly)",
    ),
    "anthropic-api": ProviderMeta(
        id="anthropic-api",
        name="Anthropic API",
        icon="◈",
        color="#d4a27f",
        primary_label="Spend",
    ),
    "openai-api": ProviderMeta(
        id="openai-api",
        name="OpenAI API",
        icon="⬡",
        color="#10a37f",
        primary_label="Spend",
    ),
    "opencode": ProviderMeta(
        id="opencode",
        name="Opencode Zen API",
        icon="◆",
        color="#F5EFEA",
        primary_label="Monthly",
    ),
}
