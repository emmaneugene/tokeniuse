"""Gemini provider — fetches usage via Google Cloud Code Private API.

Run `llmeter --login gemini` to authenticate once.  Tokens are refreshed
automatically from then on.
"""

from __future__ import annotations

import base64
import os
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

import aiohttp

from ... import auth
from ...models import (
    PROVIDERS,
    ProviderIdentity,
    ProviderResult,
    RateWindow,
)
from ..helpers import parse_iso8601, http_post, http_debug_log
from .base import SubscriptionProvider

# ── OAuth constants ────────────────────────────────────────

_CLIENT_ID_B64 = (
    "NjgxMjU1ODA5Mzk1LW9vOGZ0Mm9wcmRybnA5ZTNhcWY2YXYzaG1kaWIxMzVq"
    "LmFwcHMuZ29vZ2xldXNlcmNvbnRlbnQuY29t"
)
_CLIENT_SECRET_B64 = "R09DU1BYLTR1SGdNUG0tMW83U2stZ2VWNkN1NWNsWEZzeGw="

CLIENT_ID = base64.b64decode(_CLIENT_ID_B64).decode()
CLIENT_SECRET = base64.b64decode(_CLIENT_SECRET_B64).decode()
TOKEN_URL = "https://oauth2.googleapis.com/token"
REDIRECT_URI = "http://localhost:8085/oauth2callback"
SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform "
    "https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/userinfo.profile"
)
CODE_ASSIST_ENDPOINT = "https://cloudcode-pa.googleapis.com"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"
PROVIDER_ID = "google-gemini-cli"

# ── Provider API constants ─────────────────────────────────

QUOTA_ENDPOINT = f"{CODE_ASSIST_ENDPOINT}/v1internal:retrieveUserQuota"
LOAD_CODE_ASSIST_ENDPOINT = f"{CODE_ASSIST_ENDPOINT}/v1internal:loadCodeAssist"


# ── Credential management ──────────────────────────────────

def _now_ms() -> int:
    return int(time.time() * 1000)


def load_credentials() -> Optional[dict]:
    """Load Gemini credentials from the unified auth store."""
    return auth.load_provider(PROVIDER_ID)


def save_credentials(creds: dict) -> None:
    """Save Gemini credentials to the unified auth store."""
    auth.save_provider(PROVIDER_ID, creds)


def clear_credentials() -> None:
    """Remove Gemini credentials."""
    auth.clear_provider(PROVIDER_ID)


def is_token_expired(creds: dict) -> bool:
    """Check if the access token has expired (with buffer)."""
    return auth.is_expired(creds)


async def _get_user_email(access_token: str, timeout: float = 10.0) -> Optional[str]:
    """Fetch user email from the Google userinfo endpoint."""
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        http_debug_log(
            "gemini-oauth", "userinfo_request",
            method="GET", url=USERINFO_ENDPOINT, headers=headers,
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(
                USERINFO_ENDPOINT, headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                http_debug_log(
                    "gemini-oauth", "userinfo_response",
                    method="GET", url=USERINFO_ENDPOINT, status=resp.status,
                )
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("email")
    except Exception:
        pass
    return None


async def refresh_access_token(creds: dict, timeout: float = 30.0) -> dict:
    """Use the refresh token to obtain a new access token.

    Updates and persists the credentials on success.
    """
    refresh_token = creds.get("refresh")
    if not refresh_token:
        raise RuntimeError("No refresh token — run `llmeter --login gemini` to re-authenticate.")

    body = urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    http_debug_log(
        "gemini-oauth", "token_refresh_request",
        method="POST", url=TOKEN_URL, headers=headers,
        payload={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                 "refresh_token": refresh_token, "grant_type": "refresh_token"},
    )

    async with aiohttp.ClientSession() as session:
        async with session.post(
            TOKEN_URL, data=body, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            http_debug_log(
                "gemini-oauth", "token_refresh_response",
                method="POST", url=TOKEN_URL, status=resp.status,
            )
            if resp.status != 200:
                resp_body = await resp.text()
                raise RuntimeError(
                    f"Token refresh failed (HTTP {resp.status}): {resp_body[:200]}"
                )
            token_data = await resp.json()

    new_access = token_data.get("access_token", "")
    new_refresh = token_data.get("refresh_token", refresh_token)
    expires_in = token_data.get("expires_in", 3600)

    if not new_access:
        raise RuntimeError("Token refresh response missing access_token.")

    email = creds.get("email")
    if not email:
        try:
            email = await _get_user_email(new_access)
        except Exception:
            pass

    new_creds = {
        "type": "oauth",
        "refresh": new_refresh,
        "access": new_access,
        "expires": _now_ms() + int(expires_in) * 1000 - auth.EXPIRY_BUFFER_MS,
        "projectId": creds.get("projectId", ""),
        "email": email,
    }
    save_credentials(new_creds)
    return new_creds


async def get_valid_credentials(timeout: float = 30.0) -> Optional[dict]:
    """Load credentials, refresh if expired, return full creds dict or None."""
    creds = load_credentials()
    if creds is None:
        return None
    if is_token_expired(creds):
        try:
            creds = await refresh_access_token(creds, timeout=timeout)
        except RuntimeError:
            return None
    return creds


# ── Provider class ─────────────────────────────────────────

class GeminiProvider(SubscriptionProvider):
    """Fetches Gemini CLI usage quotas."""

    @property
    def provider_id(self) -> str:
        return "gemini"

    @property
    def no_credentials_error(self) -> str:
        return (
            "No Gemini credentials found. "
            "Run `llmeter --login gemini` to authenticate."
        )

    async def get_credentials(self, timeout: float) -> Optional[dict]:
        return await get_valid_credentials(timeout=timeout)

    async def _fetch(
        self,
        creds: dict,
        timeout: float,
        settings: dict,
    ) -> ProviderResult:
        result = PROVIDERS["gemini"].to_result(source="api")

        access_token = creds.get("access", "")
        project_id = creds.get("projectId")
        email = creds.get("email")

        if not access_token:
            result.error = "Gemini access token missing. Run `llmeter --login gemini` to authenticate."
            return result

        tier, discovered_project = await _load_code_assist(access_token, timeout)
        if not project_id:
            project_id = discovered_project

        try:
            quotas = await _fetch_quota(access_token, project_id, timeout)
        except Exception as e:
            result.error = f"Gemini API error: {e}"
            return result

        if not quotas:
            result.error = "No quota data returned from Gemini API."
            return result

        pro_quotas = [(mid, frac, reset) for mid, frac, reset in quotas if "pro" in mid.lower()]
        flash_quotas = [(mid, frac, reset) for mid, frac, reset in quotas if "flash" in mid.lower()]

        if pro_quotas:
            worst = min(pro_quotas, key=lambda x: x[1])
            result.primary = RateWindow(
                used_percent=max(0.0, 100.0 - worst[1] * 100.0),
                window_minutes=24 * 60,
                resets_at=worst[2],
            )

        if flash_quotas:
            worst = min(flash_quotas, key=lambda x: x[1])
            result.secondary = RateWindow(
                used_percent=max(0.0, 100.0 - worst[1] * 100.0),
                window_minutes=24 * 60,
                resets_at=worst[2],
            )

        result.identity = ProviderIdentity(
            account_email=email,
            login_method=_tier_to_plan(tier),
        )
        result.source = "oauth"
        result.updated_at = datetime.now(timezone.utc)
        return result


# ── Internal API helpers ───────────────────────────────────

async def _load_code_assist(
    access_token: str, timeout: float
) -> tuple[Optional[str], Optional[str]]:
    """Call loadCodeAssist to get tier and project ID."""
    try:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        payload = {"metadata": {"ideType": "GEMINI_CLI", "pluginType": "GEMINI"}}
        data = await http_post(
            "gemini", LOAD_CODE_ASSIST_ENDPOINT, headers, payload, timeout,
            label="load_code_assist",
        )
    except Exception:
        return (None, None)

    tier_id = None
    current_tier = data.get("currentTier")
    if isinstance(current_tier, dict):
        tier_id = current_tier.get("id")

    project_id = None
    proj = data.get("cloudaicompanionProject")
    if isinstance(proj, str) and proj.strip():
        project_id = proj.strip()
    elif isinstance(proj, dict):
        project_id = proj.get("id") or proj.get("projectId")

    return (tier_id, project_id)


async def _fetch_quota(
    access_token: str,
    project_id: Optional[str],
    timeout: float,
) -> list[tuple[str, float, Optional[datetime]]]:
    """Fetch per-model quota buckets.

    Returns list of (model_id, remaining_fraction, reset_time).
    """
    body: dict = {}
    if project_id:
        body["project"] = project_id

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    data = await http_post(
        "gemini", QUOTA_ENDPOINT, headers, body, timeout,
        label="quota",
        errors={401: "Unauthorized — run `llmeter --login gemini` to re-authenticate."},
    )

    buckets = data.get("buckets", [])
    if not buckets:
        raise RuntimeError("No quota buckets in response")

    model_map: dict[str, tuple[float, Optional[datetime]]] = {}
    for bucket in buckets:
        model_id = bucket.get("modelId")
        if model_id is None:
            continue
        raw_fraction = bucket.get("remainingFraction")
        if raw_fraction is None:
            continue
        try:
            fraction = float(raw_fraction)
        except (TypeError, ValueError):
            continue
        reset_time = parse_iso8601(bucket.get("resetTime"))
        if model_id in model_map:
            if fraction < model_map[model_id][0]:
                model_map[model_id] = (fraction, reset_time)
        else:
            model_map[model_id] = (fraction, reset_time)

    return [
        (model_id, frac, reset_dt)
        for model_id, (frac, reset_dt) in sorted(model_map.items())
    ]


def _tier_to_plan(tier: Optional[str]) -> Optional[str]:
    if tier == "standard-tier":
        return "Paid"
    if tier == "free-tier":
        return "Free"
    if tier == "legacy-tier":
        return "Legacy"
    return None


# Module-level singleton — used by backend.py and importable as a callable.
fetch_gemini = GeminiProvider()
