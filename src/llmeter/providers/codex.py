"""Codex provider — fetches usage via direct API or JSON-RPC fallback.

Credential resolution order:
1. llmeter's own OAuth credentials (~/.config/llmeter/codex_oauth.json)
   — supports automatic token refresh, calls the usage API directly
2. JSON-RPC to `codex app-server` (requires codex binary on PATH)

Run `llmeter --login-codex` to authenticate once.  Tokens are refreshed
automatically from then on, and the codex binary is no longer required.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from ..models import (
    CreditsInfo,
    ProviderIdentity,
    ProviderResult,
    RateWindow,
)
from . import codex_oauth

USAGE_URL = "https://chatgpt.com/backend-api/api/codex/usage"


async def fetch_codex(timeout: float = 20.0, settings: dict | None = None) -> ProviderResult:
    """Fetch Codex usage via direct API (preferred) or RPC fallback."""
    result = ProviderResult(
        provider_id="codex",
        display_name="Codex",
        icon="⬡",
        color="#10a37f",
        primary_label="Session (5h)",
        secondary_label="Weekly",
    )

    # 1. Try direct API with own OAuth credentials
    creds = await codex_oauth.get_valid_credentials(timeout=timeout)
    if creds:
        try:
            return await _fetch_via_api(creds, result, timeout=timeout)
        except Exception:
            # Direct API failed — fall through to RPC
            pass

    # 2. Fall back to JSON-RPC via codex binary
    binary = _find_codex_binary()
    if not binary:
        if creds is None:
            result.error = (
                "No Codex credentials found and codex CLI not on PATH. "
                "Run `llmeter --login-codex` to authenticate."
            )
        else:
            result.error = "Codex API request failed and codex CLI not on PATH."
        return result

    return await _fetch_via_rpc(binary, result, timeout=timeout)


# ── Direct API path ────────────────────────────────────────

async def _fetch_via_api(
    creds: dict,
    result: ProviderResult,
    timeout: float = 20.0,
) -> ProviderResult:
    """Fetch usage by calling the Codex backend API directly."""
    access_token = creds["access_token"]
    account_id = creds["account_id"]

    headers = {
        "Authorization": f"Bearer {access_token}",
        "chatgpt-account-id": account_id,
        "User-Agent": "LLMeter/0.1.0",
        "Accept": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(
            USAGE_URL,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status == 401:
                raise RuntimeError("Unauthorized")
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"HTTP {resp.status}: {body[:200]}")
            data = await resp.json()

    # Parse rate limit response
    # The API returns a list of rate limit snapshots
    _parse_usage_response(data, result)

    result.source = "oauth (own)"
    result.updated_at = datetime.now(timezone.utc)
    return result


def _parse_usage_response(data: dict | list, result: ProviderResult) -> None:
    """Parse the usage API response into the ProviderResult.

    The response format may be a single snapshot or a list.
    Each snapshot has: primary, secondary, credits, plan_type fields.
    """
    # Handle list of snapshots — use the first one
    snapshot = data
    if isinstance(data, list):
        if not data:
            return
        snapshot = data[0]
    elif isinstance(data, dict):
        # Might be wrapped: {"rate_limits": {...}} or direct
        if "rate_limits" in data:
            snapshot = data["rate_limits"]
        elif "rateLimits" in data:
            snapshot = data["rateLimits"]

    primary = snapshot.get("primary")
    if primary:
        result.primary = _parse_rate_window(primary)

    secondary = snapshot.get("secondary")
    if secondary:
        result.secondary = _parse_rate_window(secondary)

    credits_data = snapshot.get("credits")
    if credits_data:
        balance = credits_data.get("balance")
        if balance is not None:
            try:
                result.credits = CreditsInfo(remaining=float(balance))
            except (ValueError, TypeError):
                pass

    plan_type = snapshot.get("plan_type") or snapshot.get("planType")
    if plan_type:
        result.identity = ProviderIdentity(login_method=plan_type)


def _parse_rate_window(window: dict) -> RateWindow:
    """Parse a rate limit window from the API response."""
    # API may use snake_case or camelCase
    used_pct = window.get("used_percent") or window.get("usedPercent") or 0
    window_mins = window.get("window_minutes") or window.get("windowDurationMins")

    resets_at = None
    resets_raw = window.get("resets_at") or window.get("resetsAt")
    if resets_raw:
        if isinstance(resets_raw, (int, float)):
            resets_at = datetime.fromtimestamp(resets_raw, tz=timezone.utc)
        elif isinstance(resets_raw, str):
            try:
                resets_at = datetime.fromisoformat(resets_raw.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

    return RateWindow(
        used_percent=used_pct,
        window_minutes=window_mins,
        resets_at=resets_at,
    )


# ── JSON-RPC fallback path ─────────────────────────────────

async def _fetch_via_rpc(
    binary: str,
    result: ProviderResult,
    timeout: float = 20.0,
) -> ProviderResult:
    """Fetch usage via JSON-RPC to codex app-server (legacy path)."""
    try:
        client = await _start_rpc(binary, timeout=timeout)
    except Exception as e:
        result.error = f"Failed to start Codex RPC: {e}"
        return result

    try:
        await client.request("initialize", {
            "clientInfo": {"name": "llmeter", "version": "0.1.0"}
        })
        await client.notify("initialized")

        limits_resp = await client.request("account/rateLimits/read")
        rate_limits = limits_resp.get("rateLimits", {})

        primary = rate_limits.get("primary")
        secondary = rate_limits.get("secondary")
        credits_data = rate_limits.get("credits")

        if primary:
            resets_at = None
            if primary.get("resetsAt"):
                resets_at = datetime.fromtimestamp(primary["resetsAt"], tz=timezone.utc)
            result.primary = RateWindow(
                used_percent=primary.get("usedPercent", 0),
                window_minutes=primary.get("windowDurationMins"),
                resets_at=resets_at,
            )

        if secondary:
            resets_at = None
            if secondary.get("resetsAt"):
                resets_at = datetime.fromtimestamp(secondary["resetsAt"], tz=timezone.utc)
            result.secondary = RateWindow(
                used_percent=secondary.get("usedPercent", 0),
                window_minutes=secondary.get("windowDurationMins"),
                resets_at=resets_at,
            )

        if credits_data and credits_data.get("balance"):
            try:
                result.credits = CreditsInfo(remaining=float(credits_data["balance"]))
            except (ValueError, TypeError):
                pass

        try:
            account_resp = await client.request("account/read")
            account = account_resp.get("account")
            if account and account.get("type", "").lower() == "chatgpt":
                result.identity = ProviderIdentity(
                    account_email=account.get("email"),
                    login_method=account.get("planType"),
                )
        except Exception:
            pass

        result.source = "rpc"
        result.updated_at = datetime.now(timezone.utc)

    except Exception as e:
        result.error = f"Codex RPC error: {e}"
    finally:
        client.shutdown()

    return result


def _find_codex_binary() -> Optional[str]:
    """Locate the codex binary on PATH."""
    return shutil.which("codex")


class _RPCClient:
    """Minimal JSON-RPC client over stdin/stdout pipes."""

    def __init__(self, proc: asyncio.subprocess.Process):
        self._proc = proc
        self._next_id = 1
        self._buffer = b""

    async def request(self, method: str, params: dict | None = None) -> dict:
        req_id = self._next_id
        self._next_id += 1

        payload = {"id": req_id, "method": method, "params": params or {}}
        line = json.dumps(payload) + "\n"
        self._proc.stdin.write(line.encode())
        await self._proc.stdin.drain()

        while True:
            msg = await self._read_message()
            if msg is None:
                raise RuntimeError("Codex app-server closed stdout")

            if "id" not in msg:
                continue

            msg_id = msg.get("id")
            if isinstance(msg_id, (int, float)) and int(msg_id) == req_id:
                if "error" in msg and msg["error"]:
                    err_msg = msg["error"].get("message", "Unknown RPC error")
                    raise RuntimeError(err_msg)
                return msg.get("result", {})

    async def notify(self, method: str, params: dict | None = None) -> None:
        payload = {"method": method, "params": params or {}}
        line = json.dumps(payload) + "\n"
        self._proc.stdin.write(line.encode())
        await self._proc.stdin.drain()

    async def _read_message(self) -> dict | None:
        while True:
            newline_idx = self._buffer.find(b"\n")
            if newline_idx >= 0:
                line_data = self._buffer[:newline_idx]
                self._buffer = self._buffer[newline_idx + 1:]
                if line_data.strip():
                    try:
                        return json.loads(line_data)
                    except json.JSONDecodeError:
                        continue
                continue

            chunk = await self._proc.stdout.read(8192)
            if not chunk:
                return None
            self._buffer += chunk

    def shutdown(self) -> None:
        try:
            if self._proc.returncode is None:
                self._proc.terminate()
        except ProcessLookupError:
            pass


async def _start_rpc(binary: str, timeout: float = 20.0) -> _RPCClient:
    proc = await asyncio.create_subprocess_exec(
        binary,
        "-s", "read-only", "-a", "untrusted", "app-server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    return _RPCClient(proc)
