from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


def _num(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


@dataclass(frozen=True)
class PendingTrade:
    market: str
    horizon_minutes: int
    direction: str
    entry_time_utc: datetime
    entry_spot: float


class RollingSideGuard:
    def __init__(
        self,
        *,
        lookback_trades: int,
        min_samples: int,
        min_win_rate: float,
    ) -> None:
        self._lookback_trades = max(5, int(lookback_trades))
        self._min_samples = max(3, int(min_samples))
        self._min_win_rate = min(0.99, max(0.01, float(min_win_rate)))
        self._pending_by_market: dict[str, deque[PendingTrade]] = defaultdict(deque)
        self._history: dict[tuple[str, int, str], deque[int]] = defaultdict(
            lambda: deque(maxlen=self._lookback_trades)
        )

    def observe_snapshot(self, *, market: str, now_utc: datetime, spot: float | None) -> None:
        if spot is None:
            return
        key_market = str(market or "").upper()
        if not key_market:
            return
        pending = self._pending_by_market.get(key_market)
        if not pending:
            return
        while pending:
            trade = pending[0]
            maturity = trade.entry_time_utc + timedelta(minutes=max(1, trade.horizon_minutes))
            if now_utc < maturity:
                break
            pending.popleft()
            signed_ret = ((spot - trade.entry_spot) / trade.entry_spot) if trade.direction == "long" else (
                (trade.entry_spot - spot) / trade.entry_spot
            )
            hit = 1 if signed_ret > 0 else 0
            hist_key = (trade.market, trade.horizon_minutes, trade.direction)
            self._history[hist_key].append(hit)

    def register_signal(self, *, signal_payload: dict[str, Any], entry_spot: float | None, now_utc: datetime) -> None:
        if entry_spot is None:
            return
        market = str(signal_payload.get("market") or "").upper()
        direction = str(signal_payload.get("direction") or "").lower()
        status = str(signal_payload.get("status") or "").lower()
        if not market or direction not in {"long", "short"}:
            return
        if status not in {"post", "high_priority"}:
            return
        try:
            horizon_minutes = int(signal_payload.get("horizon_minutes") or 0)
        except (TypeError, ValueError):
            return
        if horizon_minutes <= 0:
            return
        self._pending_by_market[market].append(
            PendingTrade(
                market=market,
                horizon_minutes=horizon_minutes,
                direction=direction,
                entry_time_utc=now_utc.astimezone(timezone.utc),
                entry_spot=float(entry_spot),
            )
        )

    def should_allow(self, *, signal_payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        market = str(signal_payload.get("market") or "").upper()
        direction = str(signal_payload.get("direction") or "").lower()
        try:
            horizon = int(signal_payload.get("horizon_minutes") or 0)
        except (TypeError, ValueError):
            horizon = 0
        if not market or horizon <= 0 or direction not in {"long", "short"}:
            return True, {"enabled": True, "reason": "invalid_side_key"}
        history = self._history.get((market, horizon, direction))
        if not history:
            return True, {"enabled": True, "samples": 0, "min_samples": self._min_samples, "reason": "warmup"}
        samples = len(history)
        win_rate = float(sum(history)) / float(samples)
        if samples < self._min_samples:
            return True, {
                "enabled": True,
                "samples": samples,
                "win_rate": round(win_rate, 4),
                "min_samples": self._min_samples,
                "reason": "warmup",
            }
        allowed = win_rate >= self._min_win_rate
        return allowed, {
            "enabled": True,
            "samples": samples,
            "win_rate": round(win_rate, 4),
            "min_samples": self._min_samples,
            "min_win_rate": round(self._min_win_rate, 4),
            "reason": "ok" if allowed else "below_min_win_rate",
        }

    @staticmethod
    def parse_statuses(raw: Any) -> tuple[str, ...]:
        text = str(raw or "")
        values = [part.strip().lower() for part in text.split(",")]
        allowed = tuple(v for v in values if v)
        return allowed if allowed else ("post", "high_priority")
