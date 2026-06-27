from __future__ import annotations

from datetime import datetime, time, timezone

from esnq_signal.service import _is_within_session, _parse_session_time


def test_parse_session_time_falls_back_on_invalid_value() -> None:
    fallback = time(9, 30)

    assert _parse_session_time("10:15", fallback) == time(10, 15)
    assert _parse_session_time("bad", fallback) == fallback


def test_rth_session_window_is_inclusive_start_exclusive_end() -> None:
    start = time(9, 30)
    end = time(16, 0)

    assert not _is_within_session(datetime(2026, 6, 4, 13, 29, tzinfo=timezone.utc), start, end)
    assert _is_within_session(datetime(2026, 6, 4, 13, 30, tzinfo=timezone.utc), start, end)
    assert _is_within_session(datetime(2026, 6, 4, 19, 59, tzinfo=timezone.utc), start, end)
    assert not _is_within_session(datetime(2026, 6, 4, 20, 0, tzinfo=timezone.utc), start, end)
