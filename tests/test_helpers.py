"""Unit tests for shared HTTP helpers (http_get, http_post)."""

from __future__ import annotations

from pathlib import Path

import aiohttp
import pytest
from aioresponses import aioresponses

from llmeter.providers.helpers import http_debug_log, http_get, http_post

TEST_URL = "https://example.test/api/v1/resource"


# ── http_get ──────────────────────────────────────────────


class TestHttpGet:
    async def test_returns_parsed_json_on_200(self) -> None:
        with aioresponses() as m:
            m.get(TEST_URL, payload={"ok": True})
            result = await http_get("test", TEST_URL, {}, timeout=5.0)
        assert result == {"ok": True}

    async def test_custom_error_message_for_mapped_status(self) -> None:
        with aioresponses() as m:
            m.get(TEST_URL, status=401, body="Unauthorized")
            with pytest.raises(RuntimeError, match="Custom 401 message"):
                await http_get(
                    "test", TEST_URL, {}, timeout=5.0,
                    errors={401: "Custom 401 message"},
                )

    async def test_fallback_error_includes_status_and_body(self) -> None:
        with aioresponses() as m:
            m.get(TEST_URL, status=500, body="Internal Server Error")
            with pytest.raises(RuntimeError, match="HTTP 500"):
                await http_get("test", TEST_URL, {}, timeout=5.0)

    async def test_body_preview_truncated_to_200_chars(self) -> None:
        long_body = "x" * 400
        with aioresponses() as m:
            m.get(TEST_URL, status=503, body=long_body)
            with pytest.raises(RuntimeError) as exc_info:
                await http_get("test", TEST_URL, {}, timeout=5.0)
        # Message should contain status + truncated body, not the full 400 chars.
        assert "503" in str(exc_info.value)
        assert len(str(exc_info.value)) < 300

    async def test_non_json_200_raises_clear_error(self) -> None:
        with aioresponses() as m:
            m.get(
                TEST_URL, status=200,
                body="<html><body>maintenance</body></html>",
                headers={"Content-Type": "text/html"},
            )
            with pytest.raises(RuntimeError, match="Expected JSON"):
                await http_get("test", TEST_URL, {}, timeout=5.0)

    async def test_params_forwarded_to_request(self) -> None:
        # aiohttp appends params to the URL (sorted); register the full URL.
        with aioresponses() as m:
            m.get(f"{TEST_URL}?limit=10&page=2", payload={"paged": True})
            result = await http_get(
                "test", TEST_URL, {}, timeout=5.0,
                params={"page": "2", "limit": "10"},
            )
        assert result == {"paged": True}

    async def test_caller_supplied_session_is_not_closed(self) -> None:
        with aioresponses() as m:
            m.get(TEST_URL, payload={"ok": True})
            async with aiohttp.ClientSession() as session:
                result = await http_get(
                    "test", TEST_URL, {}, timeout=5.0,
                    session=session,
                )
                assert not session.closed
        assert result == {"ok": True}

    async def test_self_managed_session_is_closed_on_success(self) -> None:
        # No direct handle to the session, but the call must complete cleanly.
        with aioresponses() as m:
            m.get(TEST_URL, payload={"ok": True})
            result = await http_get("test", TEST_URL, {}, timeout=5.0)
        assert result == {"ok": True}

    async def test_self_managed_session_is_closed_on_error(self) -> None:
        with aioresponses() as m:
            m.get(TEST_URL, status=500, body="boom")
            with pytest.raises(RuntimeError):
                await http_get("test", TEST_URL, {}, timeout=5.0)


# ── http_post ─────────────────────────────────────────────


class TestHttpPost:
    async def test_returns_parsed_json_on_200(self) -> None:
        with aioresponses() as m:
            m.post(TEST_URL, payload={"created": True})
            result = await http_post("test", TEST_URL, {}, {"key": "val"}, timeout=5.0)
        assert result == {"created": True}

    async def test_custom_error_message_for_mapped_status(self) -> None:
        with aioresponses() as m:
            m.post(TEST_URL, status=403, body="Forbidden")
            with pytest.raises(RuntimeError, match="Custom 403 message"):
                await http_post(
                    "test", TEST_URL, {}, {}, timeout=5.0,
                    errors={403: "Custom 403 message"},
                )

    async def test_fallback_error_includes_status_and_body(self) -> None:
        with aioresponses() as m:
            m.post(TEST_URL, status=422, body="Unprocessable")
            with pytest.raises(RuntimeError, match="HTTP 422"):
                await http_post("test", TEST_URL, {}, {}, timeout=5.0)

    async def test_non_json_200_raises_clear_error(self) -> None:
        with aioresponses() as m:
            m.post(
                TEST_URL, status=200,
                body="<html><body>maintenance</body></html>",
                headers={"Content-Type": "text/html"},
            )
            with pytest.raises(RuntimeError, match="Expected JSON"):
                await http_post("test", TEST_URL, {}, {}, timeout=5.0)

    async def test_caller_supplied_session_is_not_closed(self) -> None:
        with aioresponses() as m:
            m.post(TEST_URL, payload={"ok": True})
            async with aiohttp.ClientSession() as session:
                result = await http_post(
                    "test", TEST_URL, {}, {}, timeout=5.0,
                    session=session,
                )
                assert not session.closed
        assert result == {"ok": True}


class TestHttpDebugLog:
    def test_debug_log_created_with_private_permissions(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        log_path = tmp_path / "debug.log"
        monkeypatch.setenv("LLMETER_DEBUG_HTTP", "1")
        monkeypatch.setenv("LLMETER_DEBUG_LOG_PATH", str(log_path))

        http_debug_log("test", "request", method="GET", url="https://example.test")

        assert log_path.exists()
        mode = log_path.stat().st_mode & 0o777
        assert mode == 0o600
