"""GitHub Copilot provider — fetches monthly premium request usage.

Run ``llmeter --login copilot`` to authenticate via GitHub Device Flow.
The GitHub OAuth token is used directly against the Copilot internal API.

Only tracks ``premium_interactions`` (the limited monthly quota).
Chat and completions are unlimited and therefore skipped.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ... import auth
from ...models import (
    PROVIDERS,
    ProviderIdentity,
    ProviderResult,
    RateWindow,
)
from ..helpers import http_get, parse_iso8601
from .base import SubscriptionProvider

# ── Auth constants ─────────────────────────────────────────

PROVIDER_ID = "github-copilot"

# ── Provider API constants ─────────────────────────────────

COPILOT_USER_URL = "https://api.github.com/copilot_internal/user"


# ── Credential management ──────────────────────────────────

def load_credentials() -> Optional[dict]:
    """Load Copilot OAuth credentials from the unified auth store."""
    creds = auth.load_provider(PROVIDER_ID)
    if creds and creds.get("access"):
        return creds
    return None


def save_credentials(creds: dict) -> None:
    """Persist credentials to the unified auth store."""
    auth.save_provider(PROVIDER_ID, creds)


def clear_credentials() -> None:
    """Remove stored credentials."""
    auth.clear_provider(PROVIDER_ID)


async def get_valid_access_token(timeout: float = 30.0) -> Optional[str]:
    """Load credentials and return the access token, or None.

    GitHub OAuth tokens obtained via the device flow are long-lived
    and don't have a refresh mechanism.
    """
    creds = load_credentials()
    if creds is None:
        return None
    return creds.get("access")


# ── Provider class ─────────────────────────────────────────

class CopilotProvider(SubscriptionProvider):
    """Fetches GitHub Copilot usage via the internal Copilot API."""

    @property
    def provider_id(self) -> str:
        return "copilot"

    @property
    def no_credentials_error(self) -> str:
        return (
            "No Copilot credentials found. "
            "Run `llmeter --login copilot` to authenticate."
        )

    async def get_credentials(self, timeout: float) -> Optional[str]:
        return await get_valid_access_token(timeout=timeout)

    async def _fetch(
        self,
        creds: str,
        timeout: float,
        settings: dict,
    ) -> ProviderResult:
        result = PROVIDERS["copilot"].to_result()
        access_token = creds

        try:
            data = await _fetch_copilot_user(access_token, timeout=timeout)
        except RuntimeError as e:
            msg = str(e)
            # Clear stale credentials on auth failures so re-login starts clean.
            if "Unauthorized" in msg:
                clear_credentials()
            result.error = msg
            return result
        except Exception as e:
            result.error = f"Copilot API error: {e}"
            return result

        quota_snapshots = data.get("quota_snapshots") or {}
        premium = quota_snapshots.get("premium_interactions")

        reset_date_str = data.get("quota_reset_date_utc") or data.get("quota_reset_date")
        reset_dt: Optional[datetime] = None
        if reset_date_str:
            reset_dt = parse_iso8601(reset_date_str)

        if premium and not premium.get("unlimited", False):
            entitlement = int(premium.get("entitlement", 0))
            remaining = int(premium.get("remaining", 0))
            used = max(0, entitlement - remaining)
            used_pct = max(0.0, 100.0 - premium.get("percent_remaining", 100.0))
            result.primary = RateWindow(used_percent=used_pct, resets_at=reset_dt)
            result.primary_label = f"Plan {used} / {entitlement} reqs"
        else:
            result.primary = RateWindow(used_percent=0.0, resets_at=reset_dt)

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


# Module-level singleton — used by backend.py and importable as a callable.
fetch_copilot = CopilotProvider()
