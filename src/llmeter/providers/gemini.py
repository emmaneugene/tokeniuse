"""Gemini provider — fetches usage via Google Cloud Code Private API.

Run `llmeter --login-gemini` to authenticate once.  Tokens are refreshed
automatically from then on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import aiohttp

from ..models import (
    PROVIDERS,
    ProviderIdentity,
    ProviderResult,
    RateWindow,
)
from . import gemini_oauth
from .helpers import parse_iso8601, http_debug_log

QUOTA_ENDPOINT = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota"
LOAD_CODE_ASSIST_ENDPOINT = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"


async def fetch_gemini(
    timeout: float = 30.0,
    settings: dict | None = None,
) -> ProviderResult:
    """Fetch Gemini CLI usage quotas."""
    result = PROVIDERS["gemini"].to_result(source="api")

    creds = await gemini_oauth.get_valid_credentials(timeout=timeout)
    if creds is None:
        result.error = (
            "No Gemini credentials found. "
            "Run `llmeter --login-gemini` to authenticate."
        )
        return result

    access_token = creds.get("access", "")
    project_id = creds.get("projectId")
    email = creds.get("email")

    if not access_token:
        result.error = "Gemini access token missing. Run `llmeter --login-gemini` to authenticate."
        return result

    # Load Code Assist status (tier + project discovery)
    tier, discovered_project = await _load_code_assist(access_token, timeout)
    if not project_id:
        project_id = discovered_project

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
    plan = _tier_to_plan(tier)
    result.identity = ProviderIdentity(
        account_email=email,
        login_method=plan,
    )

    result.source = "oauth"
    result.updated_at = datetime.now(timezone.utc)
    return result


# ── API Calls ──────────────────────────────────────────────

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
        http_debug_log(
            "gemini",
            "load_code_assist_request",
            method="POST",
            url=LOAD_CODE_ASSIST_ENDPOINT,
            headers=headers,
            payload=payload,
        )
        async with aiohttp.ClientSession() as session:
            async with session.post(
                LOAD_CODE_ASSIST_ENDPOINT,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                http_debug_log(
                    "gemini",
                    "load_code_assist_response",
                    method="POST",
                    url=LOAD_CODE_ASSIST_ENDPOINT,
                    status=resp.status,
                )
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
    http_debug_log(
        "gemini",
        "quota_request",
        method="POST",
        url=QUOTA_ENDPOINT,
        headers=headers,
        payload=body,
    )

    async with aiohttp.ClientSession() as session:
        async with session.post(
            QUOTA_ENDPOINT,
            json=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            http_debug_log(
                "gemini",
                "quota_response",
                method="POST",
                url=QUOTA_ENDPOINT,
                status=resp.status,
            )
            if resp.status == 401:
                raise RuntimeError("Unauthorized — run `llmeter --login-gemini` to re-authenticate.")
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"HTTP {resp.status}: {text[:200]}")
            data = await resp.json()

    buckets = data.get("buckets", [])
    if not buckets:
        raise RuntimeError("No quota buckets in response")

    model_map: dict[str, tuple[float, Optional[datetime]]] = {}

    for bucket in buckets:
        model_id = bucket.get("modelId")
        fraction = bucket.get("remainingFraction")
        if model_id is None or fraction is None:
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
    """Map tier ID to human-readable plan name."""
    if tier == "standard-tier":
        return "Paid"
    if tier == "free-tier":
        return "Free"
    if tier == "legacy-tier":
        return "Legacy"
    return None
