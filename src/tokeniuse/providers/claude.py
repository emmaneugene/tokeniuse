"""Claude provider — fetches usage via OAuth API with automatic token refresh.

Credential resolution order:
1. tokeniuse's own OAuth credentials (~/.config/tokeniuse/claude_oauth.json)
   — supports automatic token refresh via refresh_token
2. Environment variable override (CODEXBAR_CLAUDE_OAUTH_TOKEN)
3. Claude Code CLI credentials (~/.claude/.credentials.json or macOS Keychain)
   — read-only fallback, cannot auto-refresh

Run `tokeniuse --login-claude` to authenticate once.  Tokens are refreshed
automatically from then on.
"""

from __future__ import annotations

import json
import os
import sys
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
from . import claude_oauth

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

    # --- Resolve an access token ---
    access_token, source, tier = await _resolve_access_token(timeout=timeout)

    if not access_token:
        result.error = (
            "No Claude credentials found. "
            "Run `tokeniuse --login-claude` to authenticate."
        )
        return result

    # --- Fetch usage ---
    try:
        usage = await _fetch_oauth_usage(access_token, timeout=timeout)
    except Exception as e:
        error_msg = str(e)
        # If we got a 401 using legacy creds, nudge toward own login
        if "Unauthorized" in error_msg and source == "legacy":
            error_msg += " Run `tokeniuse --login-claude` for auto-refreshing auth."
        result.error = f"Claude API error: {error_msg}"
        return result

    # --- Parse windows ---
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

    seven_day = usage.get("seven_day")
    if seven_day and seven_day.get("utilization") is not None:
        result.secondary = RateWindow(
            used_percent=seven_day["utilization"],
            window_minutes=7 * 24 * 60,
            resets_at=_parse_iso8601(seven_day.get("resets_at")),
        )

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
            result.cost = CostInfo(
                used=used / 100.0,
                limit=limit / 100.0,
                currency=extra.get("currency", "USD") or "USD",
            )

    # Identity: fetch from OAuth profile API, fall back to legacy tier
    email = None
    plan = None

    profile = await _fetch_account_info(access_token, timeout=timeout)
    if profile:
        email = profile.get("email")
        plan = profile.get("plan")

    # Fall back to legacy credentials for plan if profile API didn't provide one
    if not plan and tier:
        plan = _infer_plan(tier)

    if plan or email:
        result.identity = ProviderIdentity(
            account_email=email,
            login_method=plan,
        )

    result.source = f"oauth ({source})"
    result.updated_at = datetime.now(timezone.utc)
    return result


# ── Token resolution ───────────────────────────────────────

async def _resolve_access_token(
    timeout: float = 30.0,
) -> tuple[Optional[str], str, Optional[str]]:
    """Try to get a valid access token.

    Returns (access_token, source_label, rate_limit_tier).
    source_label is one of: "own", "env", "legacy".
    """
    # 1. tokeniuse's own OAuth credentials (with auto-refresh)
    own_creds = claude_oauth.load_credentials()
    if own_creds:
        if claude_oauth.is_token_expired(own_creds):
            try:
                own_creds = await claude_oauth.refresh_access_token(own_creds, timeout=timeout)
            except RuntimeError:
                # Refresh failed — fall through to legacy sources
                own_creds = None
        if own_creds and own_creds.get("access_token"):
            # Supplement with rateLimitTier from Claude CLI credentials
            tier = _get_legacy_rate_limit_tier()
            return own_creds["access_token"], "own", tier

    # 2. Environment variable override
    env_token = os.environ.get("CODEXBAR_CLAUDE_OAUTH_TOKEN")
    if env_token:
        return env_token, "env", None

    # 3. Legacy: Claude Code CLI credentials (read-only, no refresh)
    legacy = _load_legacy_credentials()
    if legacy:
        token = legacy.get("access_token", "")
        if token:
            # Check expiry — warn but still try (might work for a bit)
            expires_at = legacy.get("expires_at")
            if expires_at:
                try:
                    exp_ms = float(expires_at)
                    if claude_oauth._now_ms() >= exp_ms:
                        # Expired legacy token — still return it so the caller
                        # can show a more specific "Unauthorized" error
                        return token, "legacy", legacy.get("rate_limit_tier")
                except (ValueError, TypeError):
                    pass
            return token, "legacy", legacy.get("rate_limit_tier")

    return None, "", None


def _get_legacy_rate_limit_tier() -> Optional[str]:
    """Read rateLimitTier from Claude CLI credentials (supplementary data)."""
    legacy = _load_legacy_credentials()
    if legacy:
        return legacy.get("rate_limit_tier")
    return None


# ── Legacy credential loading ──────────────────────────────

def _load_legacy_credentials() -> Optional[dict]:
    """Load Claude OAuth credentials from Claude Code CLI files."""
    # Credentials file: ~/.claude/.credentials.json
    creds_path = Path.home() / ".claude" / ".credentials.json"
    if creds_path.exists():
        try:
            data = json.loads(creds_path.read_text())
            parsed = _parse_credentials_json(data)
            if parsed:
                return parsed
        except (json.JSONDecodeError, OSError):
            pass

    # macOS Keychain: "Claude Code-credentials"
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


# ── API call ───────────────────────────────────────────────

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
                raise RuntimeError("Unauthorized — token may be invalid or expired.")
            if resp.status == 403:
                body = await resp.text()
                if "user:profile" in body:
                    raise RuntimeError(
                        "Token missing 'user:profile' scope. "
                        "Re-authenticate with `tokeniuse --login-claude`."
                    )
                raise RuntimeError(f"Forbidden (HTTP 403): {body[:200]}")
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"HTTP {resp.status}: {body[:200]}")
            return await resp.json()


# ── Account info via OAuth profile API ──────────────────────

OAUTH_PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"


async def _fetch_account_info(
    access_token: str,
    timeout: float = 30.0,
) -> Optional[dict]:
    """Fetch account email, plan, and org from the OAuth profile endpoint.

    Returns dict with 'email', 'organization', 'plan' keys, or None.
    """
    try:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "anthropic-beta": BETA_HEADER,
            "User-Agent": "TokenIUse/0.1.0",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                OAUTH_PROFILE_URL,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=min(timeout, 10)),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        result: dict = {}

        # Account info
        account = data.get("account")
        if isinstance(account, dict):
            email = (account.get("email") or "").strip()
            if email:
                result["email"] = email

            # Infer plan from account flags
            if account.get("has_claude_max"):
                result["plan"] = "Claude Max"
            elif account.get("has_claude_pro"):
                result["plan"] = "Claude Pro"

        # Organization info
        org = data.get("organization")
        if isinstance(org, dict):
            org_type = (org.get("organization_type") or "").strip()
            billing = (org.get("billing_type") or "").strip()
            tier = (org.get("rate_limit_tier") or "").strip()

            # More specific plan from org type
            if not result.get("plan"):
                plan = _infer_plan_from_org(org_type, billing, tier)
                if plan:
                    result["plan"] = plan

        if result:
            return result
    except Exception:
        pass

    return None


def _infer_plan_from_org(
    org_type: str,
    billing: str = "",
    tier: str = "",
) -> Optional[str]:
    """Infer Claude plan from organization_type, billing_type, and tier."""
    ot = org_type.lower()
    if "max" in ot:
        return "Claude Max"
    if "pro" in ot:
        return "Claude Pro"
    if "team" in ot:
        return "Claude Team"
    if "enterprise" in ot:
        return "Claude Enterprise"

    # Fallback to tier
    t = tier.lower()
    if "max" in t:
        return "Claude Max"
    if "pro" in t:
        return "Claude Pro"
    if "team" in t:
        return "Claude Team"
    if "enterprise" in t:
        return "Claude Enterprise"

    # Fallback: stripe billing suggests paid plan
    if "stripe" in billing.lower():
        return "Claude Pro"

    return None


# ── Helpers ────────────────────────────────────────────────

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
