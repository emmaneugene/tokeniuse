"""GitHub Copilot provider — fetches monthly premium request usage.

Run ``llmeter --login copilot`` to authenticate via GitHub Device Flow.
The GitHub OAuth token is used directly against the Copilot internal API.

Only tracks ``premium_interactions`` (the limited monthly quota).
Chat and completions are unlimited and therefore skipped.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..models import (
    PROVIDERS,
    ProviderIdentity,
    ProviderResult,
    RateWindow,
)
from . import copilot_oauth
from .helpers import http_get, parse_iso8601

COPILOT_USER_URL = "https://api.github.com/copilot_internal/user"


async def fetch_copilot(timeout: float = 30.0, settings: dict | None = None) -> ProviderResult:
    """Fetch GitHub Copilot usage via the internal Copilot API."""
    result = PROVIDERS["copilot"].to_result()

    access_token = await copilot_oauth.get_valid_access_token(timeout=timeout)
    if not access_token:
        result.error = (
            "No Copilot credentials found. "
            "Run `llmeter --login copilot` to authenticate."
        )
        return result

    try:
        data = await _fetch_copilot_user(access_token, timeout=timeout)
    except Exception as e:
        result.error = f"Copilot API error: {e}"
        return result

    # Parse quota snapshots — only premium_interactions matters.
    # Chat and completions are unlimited and skipped.
    quota_snapshots = data.get("quota_snapshots") or {}
    premium = quota_snapshots.get("premium_interactions")

    # Reset date (monthly)
    reset_date_str = data.get("quota_reset_date_utc") or data.get("quota_reset_date")
    reset_dt: Optional[datetime] = None
    if reset_date_str:
        reset_dt = parse_iso8601(reset_date_str)

    # Primary: premium interactions (request count bar, like Cursor)
    if premium and not premium.get("unlimited", False):
        entitlement = int(premium.get("entitlement", 0))
        remaining = int(premium.get("remaining", 0))
        used = max(0, entitlement - remaining)
        used_pct = max(0.0, 100.0 - premium.get("percent_remaining", 100.0))

        result.primary = RateWindow(
            used_percent=used_pct,
            resets_at=reset_dt,
        )
        result.primary_label = f"Plan {used} / {entitlement} reqs"
    else:
        # No premium quota or unlimited — show 0% with no label override
        result.primary = RateWindow(used_percent=0.0, resets_at=reset_dt)

    # Identity
    copilot_plan = data.get("copilot_plan", "")
    login = data.get("login")
    result.identity = ProviderIdentity(
        account_email=login,
        login_method=copilot_plan.replace("_", " ").title() if copilot_plan else None,
    )

    result.source = "oauth"
    result.updated_at = datetime.now(timezone.utc)
    return result


async def _fetch_copilot_user(access_token: str, timeout: float = 30.0) -> dict:
    """Call the internal Copilot user/quota endpoint."""
    headers = {
        "Authorization": f"token {access_token}",
        "Accept": "application/json",
        "Editor-Version": "vscode/1.96.2",
        "Editor-Plugin-Version": "copilot-chat/0.26.7",
        "User-Agent": "GitHubCopilotChat/0.26.7",
        "X-Github-Api-Version": "2025-04-01",
    }
    return await http_get(
        "copilot", COPILOT_USER_URL, headers, timeout,
        label="usage",
        errors={
            401: (
                "Unauthorized — token may be invalid or revoked. "
                "Run `llmeter --login copilot` to re-authenticate."
            ),
            403: (
                "Forbidden — you may not have an active Copilot subscription. "
                "Check your GitHub Copilot plan."
            ),
            404: "Copilot endpoint not found — you may not have Copilot enabled.",
        },
    )
