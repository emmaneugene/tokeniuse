"""Gemini provider — fetches usage via Google Cloud Code Private API.

Reads OAuth credentials from ~/.gemini/oauth_creds.json (written by `gemini` CLI).
Refreshes expired tokens using client_id/secret extracted from the gemini CLI binary.
Calls the retrieveUserQuota endpoint for per-model quota buckets.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
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

QUOTA_ENDPOINT = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota"
LOAD_CODE_ASSIST_ENDPOINT = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
TOKEN_REFRESH_ENDPOINT = "https://oauth2.googleapis.com/token"

CREDS_PATH = ".gemini/oauth_creds.json"
SETTINGS_PATH = ".gemini/settings.json"


async def fetch_gemini(
    timeout: float = 30.0,
    settings: dict | None = None,
) -> ProviderResult:
    """Fetch Gemini CLI usage quotas."""
    result = ProviderResult(
        provider_id="gemini",
        display_name="Gemini",
        icon="✦",
        color="#ab87ea",
        primary_label="Pro (24h)",
        secondary_label="Flash (24h)",
        source="api",
    )

    home = Path.home()

    # Check auth type
    auth_type = _get_auth_type(home)
    if auth_type == "api-key":
        result.error = "Gemini API key auth not supported. Use Google account (OAuth) instead."
        return result
    if auth_type == "vertex-ai":
        result.error = "Gemini Vertex AI auth not supported. Use Google account (OAuth) instead."
        return result

    # Load credentials
    creds = _load_credentials(home)
    if creds is None:
        result.error = "Gemini not logged in. Run `gemini` to authenticate."
        return result

    access_token = creds.get("access_token", "")
    if not access_token:
        result.error = "Gemini access token missing. Run `gemini` to authenticate."
        return result

    # Refresh token if expired
    expiry = creds.get("expiry_date")
    if expiry and isinstance(expiry, (int, float)):
        expiry_dt = datetime.fromtimestamp(expiry / 1000.0, tz=timezone.utc)
        if datetime.now(timezone.utc) >= expiry_dt:
            try:
                access_token = await _refresh_token(
                    creds.get("refresh_token", ""),
                    home,
                    timeout,
                )
            except Exception as e:
                result.error = f"Token refresh failed: {e}"
                return result

    # Extract email from ID token (JWT)
    email = _extract_email_from_jwt(creds.get("id_token"))

    # Load Code Assist status (tier + project)
    tier, project_id = await _load_code_assist(access_token, timeout)

    # Fetch quota
    try:
        quotas = await _fetch_quota(access_token, project_id, timeout)
    except Exception as e:
        result.error = f"Gemini API error: {e}"
        return result

    if not quotas:
        result.error = "No quota data returned from Gemini API."
        return result

    # Group by Pro vs Flash
    pro_quotas = [(mid, frac, reset) for mid, frac, reset in quotas if "pro" in mid.lower()]
    flash_quotas = [(mid, frac, reset) for mid, frac, reset in quotas if "flash" in mid.lower()]

    # Use lowest remaining fraction per group
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

    # Identity
    plan = _tier_to_plan(tier, _extract_hd_from_jwt(creds.get("id_token")))
    result.identity = ProviderIdentity(
        account_email=email,
        login_method=plan,
    )

    result.updated_at = datetime.now(timezone.utc)
    return result


# ── Credentials ────────────────────────────────────────────────


def _get_auth_type(home: Path) -> str:
    """Read current auth type from ~/.gemini/settings.json."""
    settings_path = home / SETTINGS_PATH
    if not settings_path.exists():
        return "unknown"
    try:
        data = json.loads(settings_path.read_text())
        return (
            data.get("security", {})
            .get("auth", {})
            .get("selectedType", "unknown")
        )
    except (json.JSONDecodeError, OSError):
        return "unknown"


def _load_credentials(home: Path) -> Optional[dict]:
    """Load OAuth credentials from ~/.gemini/oauth_creds.json."""
    creds_path = home / CREDS_PATH
    if not creds_path.exists():
        return None
    try:
        return json.loads(creds_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _extract_email_from_jwt(id_token: Optional[str]) -> Optional[str]:
    """Extract email claim from a JWT id_token (no verification)."""
    claims = _decode_jwt_claims(id_token)
    return claims.get("email") if claims else None


def _extract_hd_from_jwt(id_token: Optional[str]) -> Optional[str]:
    """Extract hosted domain (hd) claim from a JWT id_token."""
    claims = _decode_jwt_claims(id_token)
    return claims.get("hd") if claims else None


def _decode_jwt_claims(id_token: Optional[str]) -> Optional[dict]:
    """Decode JWT payload without verification."""
    if not id_token:
        return None
    parts = id_token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    # Fix base64url padding
    payload = payload.replace("-", "+").replace("_", "/")
    remainder = len(payload) % 4
    if remainder:
        payload += "=" * (4 - remainder)
    try:
        data = base64.b64decode(payload)
        return json.loads(data)
    except Exception:
        return None


# ── Token Refresh ──────────────────────────────────────────────


async def _refresh_token(
    refresh_token: str,
    home: Path,
    timeout: float,
) -> str:
    """Refresh the access token using client_id/secret from the gemini CLI."""
    if not refresh_token:
        raise RuntimeError("No refresh token — run `gemini` to re-authenticate.")

    client_id, client_secret = _extract_oauth_client_creds()
    if not client_id or not client_secret:
        raise RuntimeError("Could not find Gemini CLI OAuth config. Is `gemini` installed?")

    body = (
        f"client_id={client_id}"
        f"&client_secret={client_secret}"
        f"&refresh_token={refresh_token}"
        f"&grant_type=refresh_token"
    )

    async with aiohttp.ClientSession() as session:
        async with session.post(
            TOKEN_REFRESH_ENDPOINT,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Token refresh HTTP {resp.status}")
            data = await resp.json()

    new_token = data.get("access_token", "")
    if not new_token:
        raise RuntimeError("No access_token in refresh response")

    # Update stored credentials
    _update_stored_credentials(home, data)

    return new_token


def _update_stored_credentials(home: Path, refresh_response: dict) -> None:
    """Write refreshed tokens back to ~/.gemini/oauth_creds.json."""
    creds_path = home / CREDS_PATH
    try:
        existing = json.loads(creds_path.read_text())
    except Exception:
        return

    if "access_token" in refresh_response:
        existing["access_token"] = refresh_response["access_token"]
    if "expires_in" in refresh_response:
        existing["expiry_date"] = (
            datetime.now(timezone.utc).timestamp() + refresh_response["expires_in"]
        ) * 1000
    if "id_token" in refresh_response:
        existing["id_token"] = refresh_response["id_token"]

    try:
        creds_path.write_text(json.dumps(existing, indent=2))
    except OSError:
        pass


def _extract_oauth_client_creds() -> tuple[str, str]:
    """Extract OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET from the gemini CLI's oauth2.js."""
    gemini_bin = shutil.which("gemini")
    if not gemini_bin:
        return ("", "")

    # Resolve symlinks to find the actual installation
    real_path = Path(gemini_bin).resolve()
    bin_dir = real_path.parent
    base_dir = bin_dir.parent

    # Try common installation layouts
    oauth_subpaths = [
        # Homebrew nested
        "libexec/lib/node_modules/@google/gemini-cli/node_modules/@google/gemini-cli-core/dist/src/code_assist/oauth2.js",
        "lib/node_modules/@google/gemini-cli/node_modules/@google/gemini-cli-core/dist/src/code_assist/oauth2.js",
        # Nix
        "share/gemini-cli/node_modules/@google/gemini-cli-core/dist/src/code_assist/oauth2.js",
        # npm nested inside gemini-cli
        "node_modules/@google/gemini-cli-core/dist/src/code_assist/oauth2.js",
    ]

    # Also try sibling package structure
    sibling_path = base_dir.parent / "gemini-cli-core" / "dist" / "src" / "code_assist" / "oauth2.js"

    for subpath in oauth_subpaths:
        candidate = base_dir / subpath
        if candidate.exists():
            return _parse_oauth_js(candidate.read_text())

    if sibling_path.exists():
        return _parse_oauth_js(sibling_path.read_text())

    return ("", "")


def _parse_oauth_js(content: str) -> tuple[str, str]:
    """Extract client_id and client_secret from oauth2.js source."""
    client_id = ""
    client_secret = ""

    m = re.search(r'OAUTH_CLIENT_ID\s*=\s*["\']([^"\']+)["\']', content)
    if m:
        client_id = m.group(1)

    m = re.search(r'OAUTH_CLIENT_SECRET\s*=\s*["\']([^"\']+)["\']', content)
    if m:
        client_secret = m.group(1)

    return (client_id, client_secret)


# ── API Calls ──────────────────────────────────────────────────


async def _load_code_assist(
    access_token: str, timeout: float
) -> tuple[Optional[str], Optional[str]]:
    """Call loadCodeAssist to get tier and project ID."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                LOAD_CODE_ASSIST_ENDPOINT,
                json={"metadata": {"ideType": "GEMINI_CLI", "pluginType": "GEMINI"}},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    return (None, None)
                data = await resp.json()

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
    except Exception:
        return (None, None)


async def _fetch_quota(
    access_token: str,
    project_id: Optional[str],
    timeout: float,
) -> list[tuple[str, float, Optional[datetime]]]:
    """Fetch per-model quota buckets. Returns list of (model_id, remaining_fraction, reset_time)."""
    body: dict = {}
    if project_id:
        body["project"] = project_id

    async with aiohttp.ClientSession() as session:
        async with session.post(
            QUOTA_ENDPOINT,
            json=body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status == 401:
                raise RuntimeError("Unauthorized — run `gemini` to re-authenticate.")
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"HTTP {resp.status}: {text[:200]}")
            data = await resp.json()

    buckets = data.get("buckets", [])
    if not buckets:
        raise RuntimeError("No quota buckets in response")

    # Group by model, keep lowest remaining fraction per model
    model_map: dict[str, tuple[float, Optional[datetime]]] = {}

    for bucket in buckets:
        model_id = bucket.get("modelId")
        fraction = bucket.get("remainingFraction")
        if model_id is None or fraction is None:
            continue

        reset_time = _parse_reset_time(bucket.get("resetTime"))

        if model_id in model_map:
            if fraction < model_map[model_id][0]:
                model_map[model_id] = (fraction, reset_time)
        else:
            model_map[model_id] = (fraction, reset_time)

    return [
        (model_id, frac, reset_dt)
        for model_id, (frac, reset_dt) in sorted(model_map.items())
    ]


def _parse_reset_time(s: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 datetime string."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _tier_to_plan(tier: Optional[str], hosted_domain: Optional[str]) -> Optional[str]:
    """Map tier ID to human-readable plan name."""
    if tier == "standard-tier":
        return "Paid"
    if tier == "free-tier":
        if hosted_domain:
            return "Workspace"
        return "Free"
    if tier == "legacy-tier":
        return "Legacy"
    return None
