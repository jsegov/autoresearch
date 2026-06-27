from __future__ import annotations

from datetime import datetime, timedelta, timezone

from esnq_signal.side_guard import RollingSideGuard


def _signal_payload(*, market: str = "NQ", horizon: int = 5, direction: str = "short", status: str = "post") -> dict[str, object]:
    return {
        "market": market,
        "horizon_minutes": horizon,
        "direction": direction,
        "status": status,
    }


def test_side_guard_blocks_side_after_poor_realized_rate() -> None:
    guard = RollingSideGuard(lookback_trades=10, min_samples=3, min_win_rate=0.6)
    base = datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc)
    # Register 3 short entries.
    for i in range(3):
        guard.register_signal(
            signal_payload=_signal_payload(direction="short"),
            entry_spot=100.0,
            now_utc=base + timedelta(minutes=i),
        )
    # Resolve them as losses for short side (spot moves up).
    guard.observe_snapshot(market="NQ", now_utc=base + timedelta(minutes=8), spot=101.0)
    allowed_short, meta_short = guard.should_allow(signal_payload=_signal_payload(direction="short"))
    allowed_long, _ = guard.should_allow(signal_payload=_signal_payload(direction="long"))
    assert allowed_short is False
    assert meta_short.get("reason") == "below_min_win_rate"
    assert allowed_long is True


def test_side_guard_warmup_until_min_samples() -> None:
    guard = RollingSideGuard(lookback_trades=10, min_samples=4, min_win_rate=0.6)
    base = datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc)
    for i in range(3):
        guard.register_signal(
            signal_payload=_signal_payload(direction="long"),
            entry_spot=100.0,
            now_utc=base + timedelta(minutes=i),
        )
    guard.observe_snapshot(market="NQ", now_utc=base + timedelta(minutes=8), spot=99.0)
    allowed, meta = guard.should_allow(signal_payload=_signal_payload(direction="long"))
    assert allowed is True
    assert meta.get("reason") == "warmup"
