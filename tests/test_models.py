"""Tests for model helpers."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from llmeter.models import RateWindow


def test_reset_text_shows_absolute_and_relative_time() -> None:
    local_tz = timezone(timedelta(hours=8))
    now = datetime(2026, 2, 16, 23, 5, tzinfo=local_tz)
    resets_at = datetime(2026, 2, 16, 19, 0, tzinfo=timezone.utc)  # 3:00am local

    text = RateWindow(used_percent=10.0, resets_at=resets_at).reset_text(now=now)

    assert text == "Resets 3am (3h 55min)"


def test_reset_text_shows_minutes_in_absolute_time_when_needed() -> None:
    local_tz = timezone(timedelta(hours=8))
    now = datetime(2026, 2, 16, 23, 0, tzinfo=local_tz)
    resets_at = datetime(2026, 2, 16, 19, 30, tzinfo=timezone.utc)  # 3:30am local

    text = RateWindow(used_percent=10.0, resets_at=resets_at).reset_text(now=now)

    assert text == "Resets 3:30am (4h 30min)"


def test_reset_text_now_when_under_one_minute() -> None:
    now = datetime(2026, 2, 16, 10, 0, tzinfo=timezone.utc)
    resets_at = datetime(2026, 2, 16, 10, 0, 30, tzinfo=timezone.utc)

    text = RateWindow(used_percent=10.0, resets_at=resets_at).reset_text(now=now)

    assert text == "Resets now"


def test_reset_text_shows_date_for_windows_more_than_one_day() -> None:
    local_tz = timezone(timedelta(hours=8))
    now = datetime(2026, 2, 27, 23, 5, tzinfo=local_tz)
    resets_at = datetime(2026, 2, 28, 20, 0, tzinfo=timezone.utc)  # 01-Mar 04:00 local

    text = RateWindow(used_percent=10.0, resets_at=resets_at).reset_text(now=now)

    assert text == "Resets 01 Mar (1d 4h)"


def test_reset_text_uses_description_when_no_timestamp() -> None:
    text = RateWindow(used_percent=10.0, reset_description="in about 2 hours").reset_text()
    assert text == "Resets in about 2 hours"
