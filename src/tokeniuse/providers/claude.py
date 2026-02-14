"""Claude provider — fetches usage via OAuth API or credentials file."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp

from ..models import (
    CostInfo,
    ProviderIdentity,
    ProviderResult,
    RateWindow,
)

OAUTH_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
BETA_HEADER = "oauth-2025-04-20"


async def fetch_claude(timeout: float = 30.0, settings: dict | None = None) -> ProviderResult:
    """Fetch Claude usage via the OAuth usage API."""
    result = ProviderResult(
        provider_id="claude",
        display_name="Claude",
        icon="◈",
        color="#d4a27f",
        primary_label="Session (5h)",
        secondary_label="Weekly",
        tertiary_label="Sonnet",
    )

    # Load credentials
    creds = _load_credentials()
    if creds is None:
        result.error = "Claude OAuth credentials not found. Run `claude` to authenticate."
        return result

    access_token = creds.get("access_token", "")
    if not access_token:
        result.error = "Claude OAuth access token missing. Run `claude` to authenticate."
        return result

    # Check expiry
    expires_at = creds.get("expires_at")
    if expires_at:
        try:
            exp_dt = datetime.fromtimestamp(expires_at / 1000.0, tz=timezone.utc)
            if datetime.now(timezone.utc) >= exp_dt:
                result.error = "Claude OAuth token expired. Run `claude login` to refresh."
                return result
        except (ValueError, TypeError, OSError):
            pass

    # Fetch usage
    try:
        usage = await _fetch_oauth_usage(access_token, timeout=timeout)
    except Exception as e:
        result.error = f"Claude API error: {e}"
        return result

    # Parse five_hour (primary)
    five_hour = usage.get("five_hour")
    if five_hour and five_hour.get("utilization") is not None:
        result.primary = RateWindow(
            used_percent=five_hour["utilization"],
            window_minutes=5 * 60,
            resets_at=_parse_iso8601(five_hour.get("resets_at")),
        )
    else:
        result.error = "Claude API returned no session usage data."
        return result

    # Parse seven_day (secondary)
    seven_day = usage.get("seven_day")
    if seven_day and seven_day.get("utilization") is not None:
        result.secondary = RateWindow(
            used_percent=seven_day["utilization"],
            window_minutes=7 * 24 * 60,
            resets_at=_parse_iso8601(seven_day.get("resets_at")),
        )

    # Parse model-specific (tertiary): sonnet or opus
    for key in ("seven_day_sonnet", "seven_day_opus"):
        model_window = usage.get(key)
        if model_window and model_window.get("utilization") is not None:
            result.tertiary = RateWindow(
                used_percent=model_window["utilization"],
                window_minutes=7 * 24 * 60,
                resets_at=_parse_iso8601(model_window.get("resets_at")),
            )
            result.tertiary_label = "Sonnet" if "sonnet" in key else "Opus"
            break

    # Extra usage (cost info)
    extra = usage.get("extra_usage")
    if extra and extra.get("is_enabled"):
        used = extra.get("used_credits")
        limit = extra.get("monthly_limit")
        if used is not None and limit is not None:
            # API returns cents; convert to dollars
            result.cost = CostInfo(
                used=used / 100.0,
                limit=limit / 100.0,
                currency=extra.get("currency", "USD") or "USD",
            )

    # Infer plan from rate_limit_tier
    tier = creds.get("rate_limit_tier", "")
    if tier:
        result.identity = ProviderIdentity(login_method=_infer_plan(tier))

    result.source = "oauth"
    result.updated_at = datetime.now(timezone.utc)
    return result


def _load_credentials() -> Optional[dict]:
    """Load Claude OAuth credentials from environment, file, or macOS Keychain."""
    # 1. Environment variable override
    env_token = os.environ.get("CODEXBAR_CLAUDE_OAUTH_TOKEN")
    if env_token:
        scopes_raw = os.environ.get("CODEXBAR_CLAUDE_OAUTH_SCOPES", "user:profile")
        return {
            "access_token": env_token,
            "scopes": scopes_raw.split(","),
        }

    # 2. Credentials file: ~/.claude/.credentials.json
    creds_path = Path.home() / ".claude" / ".credentials.json"
    if creds_path.exists():
        try:
            data = json.loads(creds_path.read_text())
            parsed = _parse_credentials_json(data)
            if parsed:
                return parsed
        except (json.JSONDecodeError, OSError):
            pass

    # 3. macOS Keychain: "Claude Code-credentials"
    keychain_data = _load_from_keychain()
    if keychain_data:
        try:
            data = json.loads(keychain_data)
            parsed = _parse_credentials_json(data)
            if parsed:
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _parse_credentials_json(data: dict) -> Optional[dict]:
    """Extract OAuth credentials from the Claude credentials JSON structure."""
    oauth = data.get("claudeAiOauth")
    if not oauth:
        return None

    access_token = (oauth.get("accessToken") or "").strip()
    if not access_token:
        return None

    return {
        "access_token": access_token,
        "refresh_token": oauth.get("refreshToken"),
        "expires_at": oauth.get("expiresAt"),
        "scopes": oauth.get("scopes", []),
        "rate_limit_tier": oauth.get("rateLimitTier"),
    }


def _load_from_keychain() -> Optional[str]:
    """Try to load Claude credentials from macOS Keychain."""
    import sys
    if sys.platform != "darwin":
        return None

    import subprocess
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


async def _fetch_oauth_usage(access_token: str, timeout: float = 30.0) -> dict:
    """Call the Claude OAuth usage endpoint."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "anthropic-beta": BETA_HEADER,
        "User-Agent": "TokenIUse/0.1.0",
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(
            OAUTH_USAGE_URL,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status == 401:
                raise RuntimeError("Unauthorized — run `claude login` to re-authenticate.")
            if resp.status == 403:
                body = await resp.text()
                if "user:profile" in body:
                    raise RuntimeError(
                        "Token missing 'user:profile' scope. "
                        "Run `claude setup-token` to re-generate."
                    )
                raise RuntimeError(f"Forbidden (HTTP 403): {body[:200]}")
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"HTTP {resp.status}: {body[:200]}")
            return await resp.json()


def _parse_iso8601(s: str | None) -> Optional[datetime]:
    """Parse an ISO 8601 datetime string."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _infer_plan(tier: str) -> Optional[str]:
    """Infer Claude plan name from the rate_limit_tier."""
    t = tier.lower()
    if "max" in t:
        return "Claude Max"
    if "pro" in t:
        return "Claude Pro"
    if "team" in t:
        return "Claude Team"
    if "enterprise" in t:
        return "Claude Enterprise"
    return None
