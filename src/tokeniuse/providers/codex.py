"""Codex provider — fetches usage via JSON-RPC to `codex app-server`."""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timezone
from typing import Optional

from ..models import (
    CreditsInfo,
    ProviderIdentity,
    ProviderResult,
    RateWindow,
)


async def fetch_codex(timeout: float = 20.0, settings: dict | None = None) -> ProviderResult:
    """Fetch Codex usage via the RPC app-server protocol."""
    result = ProviderResult(
        provider_id="codex",
        display_name="Codex",
        icon="⬡",
        color="#10a37f",
        primary_label="Session (5h)",
        secondary_label="Weekly",
    )

    binary = _find_codex_binary()
    if not binary:
        result.error = "Codex CLI not found. Install with `npm i -g @openai/codex`."
        return result

    try:
        client = await _start_rpc(binary, timeout=timeout)
    except Exception as e:
        result.error = f"Failed to start Codex RPC: {e}"
        return result

    try:
        # Initialize
        await client.request("initialize", {
            "clientInfo": {"name": "tokeniuse", "version": "0.1.0"}
        })
        await client.notify("initialized")

        # Fetch rate limits
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

        # Fetch account info
        try:
            account_resp = await client.request("account/read")
            account = account_resp.get("account")
            if account and account.get("type", "").lower() == "chatgpt":
                result.identity = ProviderIdentity(
                    account_email=account.get("email"),
                    login_method=account.get("planType"),
                )
        except Exception:
            pass  # Account info is optional

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
        """Send a JSON-RPC request and wait for the matching response."""
        req_id = self._next_id
        self._next_id += 1

        payload = {"id": req_id, "method": method, "params": params or {}}
        line = json.dumps(payload) + "\n"
        self._proc.stdin.write(line.encode())
        await self._proc.stdin.drain()

        # Read responses, skipping notifications
        while True:
            msg = await self._read_message()
            if msg is None:
                raise RuntimeError("Codex app-server closed stdout")

            # Skip notifications (no id)
            if "id" not in msg:
                continue

            # Check if it's our response
            msg_id = msg.get("id")
            if isinstance(msg_id, (int, float)) and int(msg_id) == req_id:
                if "error" in msg and msg["error"]:
                    err_msg = msg["error"].get("message", "Unknown RPC error")
                    raise RuntimeError(err_msg)
                return msg.get("result", {})

    async def notify(self, method: str, params: dict | None = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        payload = {"method": method, "params": params or {}}
        line = json.dumps(payload) + "\n"
        self._proc.stdin.write(line.encode())
        await self._proc.stdin.drain()

    async def _read_message(self) -> dict | None:
        """Read one newline-delimited JSON message from stdout."""
        while True:
            # Check buffer for complete line
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

            # Read more data
            chunk = await self._proc.stdout.read(8192)
            if not chunk:
                return None
            self._buffer += chunk

    def shutdown(self) -> None:
        """Terminate the RPC process."""
        try:
            if self._proc.returncode is None:
                self._proc.terminate()
        except ProcessLookupError:
            pass


async def _start_rpc(binary: str, timeout: float = 20.0) -> _RPCClient:
    """Start the codex app-server and return an RPC client."""
    proc = await asyncio.create_subprocess_exec(
        binary,
        "-s", "read-only", "-a", "untrusted", "app-server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    return _RPCClient(proc)
