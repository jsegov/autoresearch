from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev
from typing import Any

from .markets import CROSS_INDEX_MARKETS, KNOWN_MARKETS, TARGET_MARKETS
from .ofi_stream import OfiSample


def _sign(value: float | None, *, eps: float = 1e-9) -> int:
    if value is None:
        return 0
    if value > eps:
        return 1
    if value < -eps:
        return -1
    return 0


@dataclass
class MarketState:
    ofi: deque[tuple[datetime, float]] = field(default_factory=deque)
    spot: deque[tuple[datetime, float]] = field(default_factory=deque)
    spread: deque[tuple[datetime, float]] = field(default_factory=deque)
    large_trades: deque[tuple[datetime, float]] = field(default_factory=deque)
    last_sample: OfiSample | None = None

    def ingest(self, sample: OfiSample, max_seconds: int = 1800) -> None:
        self.last_sample = sample
        cutoff = sample.timestamp_utc - timedelta(seconds=max_seconds)
        if sample.ofi_1s is not None:
            self.ofi.append((sample.timestamp_utc, sample.ofi_1s))
        if sample.spot is not None:
            self.spot.append((sample.timestamp_utc, sample.spot))
        if sample.spread is not None:
            self.spread.append((sample.timestamp_utc, sample.spread))

        for series in (self.ofi, self.spot, self.spread, self.large_trades):
            while series and series[0][0] < cutoff:
                series.popleft()

    def ingest_large_trade(self, ts_utc: datetime, signed_volume: float, max_seconds: int = 1800) -> None:
        cutoff = ts_utc - timedelta(seconds=max_seconds)
        self.large_trades.append((ts_utc, float(signed_volume)))
        while self.large_trades and self.large_trades[0][0] < cutoff:
            self.large_trades.popleft()

    def age_seconds(self, now_utc: datetime) -> float | None:
        if self.last_sample is None:
            return None
        return max(0.0, (now_utc - self.last_sample.timestamp_utc).total_seconds())

    def latest_spot(self) -> float | None:
        if not self.spot:
            return None
        return self.spot[-1][1]

    def latest_spread(self) -> float | None:
        if not self.spread:
            return None
        return self.spread[-1][1]

    def _ofi_window_values(self, seconds: int, *, now_utc: datetime, end_offset_seconds: int = 0) -> list[float]:
        if not self.ofi:
            return []
        end_offset = max(0, int(end_offset_seconds))
        window_end = now_utc - timedelta(seconds=end_offset)
        window_start = window_end - timedelta(seconds=seconds)
        return [value for ts, value in self.ofi if window_start <= ts <= window_end]

    def ofi_sum(self, seconds: int, *, now_utc: datetime, end_offset_seconds: int = 0) -> float | None:
        values = self._ofi_window_values(seconds, now_utc=now_utc, end_offset_seconds=end_offset_seconds)
        if not values:
            return None
        return float(sum(values))

    def ofi_zscore(
        self,
        seconds: int,
        *,
        now_utc: datetime,
        lookback_seconds: int = 600,
        end_offset_seconds: int = 0,
    ) -> float | None:
        current = self.ofi_sum(seconds, now_utc=now_utc, end_offset_seconds=end_offset_seconds)
        if current is None:
            return None
        end_offset = max(0, int(end_offset_seconds))
        lookback_end = now_utc - timedelta(seconds=end_offset)
        cutoff = lookback_end - timedelta(seconds=lookback_seconds)
        lookback = [value for ts, value in self.ofi if cutoff <= ts <= lookback_end]
        if len(lookback) < 20:
            return None
        mu = mean(lookback) * float(seconds)
        sigma = pstdev(lookback) * max(1.0, float(seconds) ** 0.5)
        if sigma <= 1e-9:
            return None
        return float((current - mu) / sigma)

    def _large_trade_values(self, seconds: int, *, now_utc: datetime) -> list[float]:
        if not self.large_trades:
            return []
        cutoff = now_utc - timedelta(seconds=seconds)
        return [value for ts, value in self.large_trades if ts >= cutoff]

    def large_trade_signed_volume(self, seconds: int, *, now_utc: datetime) -> float:
        values = self._large_trade_values(seconds, now_utc=now_utc)
        if not values:
            return 0.0
        return float(sum(values))

    def large_trade_count(self, seconds: int, *, now_utc: datetime) -> int:
        values = self._large_trade_values(seconds, now_utc=now_utc)
        return int(len(values))

    def large_trade_buy_ratio(self, seconds: int, *, now_utc: datetime) -> float | None:
        values = self._large_trade_values(seconds, now_utc=now_utc)
        if not values:
            return None
        buys = sum(1.0 for value in values if value > 0)
        return float(buys / len(values))


class CrossMarketFeatureStore:
    def __init__(self) -> None:
        self._states: dict[str, MarketState] = {market: MarketState() for market in KNOWN_MARKETS}

    def ingest(self, sample: OfiSample) -> None:
        if sample.market not in self._states:
            self._states[sample.market] = MarketState()
        self._states[sample.market].ingest(sample)

    def ingest_large_trade(self, market: str, *, ts_utc: datetime, side: str, volume: float) -> None:
        mkt = str(market or "").upper()
        if not mkt:
            return
        if mkt not in self._states:
            self._states[mkt] = MarketState()
        side_l = str(side or "").lower()
        direction = 1.0 if side_l == "buy" else (-1.0 if side_l == "sell" else 0.0)
        if direction == 0.0:
            return
        signed_volume = direction * max(0.0, float(volume))
        self._states[mkt].ingest_large_trade(ts_utc, signed_volume)

    def _target_peers(self, target_market: str) -> list[str]:
        target = target_market.upper()
        peers = sorted(list(CROSS_INDEX_MARKETS))
        if target == "ES":
            peers.insert(0, "NQ")
        elif target == "NQ":
            peers.insert(0, "ES")
        return peers

    def build_target_row(self, target_market: str, *, now_utc: datetime, stale_seconds: float) -> dict[str, Any]:
        target = target_market.upper()
        if target not in TARGET_MARKETS:
            return {"market": target, "status": "unsupported_target"}

        target_state = self._states.get(target, MarketState())
        target_age = target_state.age_seconds(now_utc)
        target_stale = target_age is None or target_age > stale_seconds

        t_ofi_5 = target_state.ofi_sum(5, now_utc=now_utc)
        t_ofi_15 = target_state.ofi_sum(15, now_utc=now_utc)
        t_ofi_60 = target_state.ofi_sum(60, now_utc=now_utc)
        t_ofi_300 = target_state.ofi_sum(300, now_utc=now_utc)
        t_ofi_600 = target_state.ofi_sum(600, now_utc=now_utc)
        t_ofi_15_z = target_state.ofi_zscore(15, now_utc=now_utc)
        t_ofi_60_z = target_state.ofi_zscore(60, now_utc=now_utc)
        t_ofi_300_z = target_state.ofi_zscore(300, now_utc=now_utc)
        t_ltrade_60 = target_state.large_trade_signed_volume(60, now_utc=now_utc)
        t_ltrade_300 = target_state.large_trade_signed_volume(300, now_utc=now_utc)
        t_ltrade_count_60 = target_state.large_trade_count(60, now_utc=now_utc)
        t_ltrade_count_300 = target_state.large_trade_count(300, now_utc=now_utc)
        t_ltrade_buy_ratio_300 = target_state.large_trade_buy_ratio(300, now_utc=now_utc)

        target_sign = _sign(t_ofi_15_z if t_ofi_15_z is not None else t_ofi_15)
        peer_confirm = 0
        peer_contra = 0
        peer_lead_confirm = 0
        peer_lead_contra = 0
        peer_scores: list[float] = []
        peer_values: dict[str, float] = {}
        peer_lag30_values: dict[str, float] = {}
        peer_ready = 0
        peer_age_max = 0.0

        for peer in self._target_peers(target):
            state = self._states.get(peer)
            if state is None:
                continue
            age = state.age_seconds(now_utc)
            if age is None or age > stale_seconds:
                continue
            peer_ofi = state.ofi_zscore(15, now_utc=now_utc)
            if peer_ofi is None:
                peer_ofi = state.ofi_sum(15, now_utc=now_utc)
            s = _sign(peer_ofi)
            if s == 0:
                continue
            peer_ready += 1
            peer_scores.append(float(peer_ofi))
            peer_values[peer] = float(peer_ofi)
            peer_age_max = max(peer_age_max, age)
            if target_sign != 0 and s == target_sign:
                peer_confirm += 1
            elif target_sign != 0 and s == -target_sign:
                peer_contra += 1

            peer_lag = state.ofi_zscore(15, now_utc=now_utc, end_offset_seconds=30)
            if peer_lag is None:
                peer_lag = state.ofi_sum(15, now_utc=now_utc, end_offset_seconds=30)
            s_lag = _sign(peer_lag)
            if peer_lag is not None:
                peer_lag30_values[peer] = float(peer_lag)
            if target_sign != 0 and s_lag == target_sign:
                peer_lead_confirm += 1
            elif target_sign != 0 and s_lag == -target_sign:
                peer_lead_contra += 1

        # Positive = risk-on (equity supportive), negative = risk-off.
        macro_score = 0
        macro_lead_score = 0
        macro_ready = 0
        macro_votes: dict[str, int] = {}
        macro_ofi_values: dict[str, float] = {}
        macro_lead_votes: dict[str, int] = {}
        macro_lag30_ofi_values: dict[str, float] = {}
        # Polarity maps market OFI direction into equity risk-on/risk-off sign.
        # E6 and CL tend to align with risk-on when positive; rates, gold, and vol futures are treated as risk-off.
        for market, polarity in (("E6", 1), ("CL", 1), ("ZN", -1), ("ZB", -1), ("GC", -1), ("VX", -1)):
            state = self._states.get(market)
            if state is None:
                continue
            age = state.age_seconds(now_utc)
            if age is None or age > stale_seconds:
                continue
            ofi = state.ofi_zscore(15, now_utc=now_utc)
            if ofi is None:
                ofi = state.ofi_sum(15, now_utc=now_utc)
            s = _sign(ofi)
            if s == 0:
                continue
            macro_ready += 1
            vote = s * polarity
            macro_votes[market] = vote
            macro_ofi_values[market] = float(ofi)
            macro_score += vote

            lag_ofi = state.ofi_zscore(15, now_utc=now_utc, end_offset_seconds=30)
            if lag_ofi is None:
                lag_ofi = state.ofi_sum(15, now_utc=now_utc, end_offset_seconds=30)
            lag_sign = _sign(lag_ofi)
            if lag_ofi is not None:
                macro_lag30_ofi_values[market] = float(lag_ofi)
            if lag_sign != 0:
                lag_vote = lag_sign * polarity
                macro_lead_votes[market] = lag_vote
                macro_lead_score += lag_vote

        return {
            "timestamp_utc": now_utc.isoformat(timespec="milliseconds"),
            "market": target,
            "status": "ok",
            "target_age_s": target_age,
            "target_stale": target_stale,
            "target_spot": target_state.latest_spot(),
            "target_spread": target_state.latest_spread(),
            "target_ofi_5s": t_ofi_5,
            "target_ofi_15s": t_ofi_15,
            "target_ofi_60s": t_ofi_60,
            "target_ofi_300s": t_ofi_300,
            "target_ofi_600s": t_ofi_600,
            "target_ofi_15s_z": t_ofi_15_z,
            "target_ofi_60s_z": t_ofi_60_z,
            "target_ofi_300s_z": t_ofi_300_z,
            "target_large_trade_signed_60s": t_ltrade_60,
            "target_large_trade_signed_300s": t_ltrade_300,
            "target_large_trade_count_60s": t_ltrade_count_60,
            "target_large_trade_count_300s": t_ltrade_count_300,
            "target_large_trade_buy_ratio_300s": t_ltrade_buy_ratio_300,
            "target_direction_hint": target_sign,
            "cross_index_ready": peer_ready,
            "cross_index_confirmation": peer_confirm,
            "cross_index_contradiction": peer_contra,
            "cross_index_balance": peer_confirm - peer_contra,
            "cross_index_lead_confirmation": peer_lead_confirm,
            "cross_index_lead_contradiction": peer_lead_contra,
            "cross_index_lead_balance": peer_lead_confirm - peer_lead_contra,
            "cross_index_score_avg": (sum(peer_scores) / len(peer_scores)) if peer_scores else None,
            "cross_index_values": peer_values,
            "cross_index_lag30_values": peer_lag30_values,
            "cross_index_max_age_s": peer_age_max if peer_ready > 0 else None,
            "macro_ready": macro_ready,
            "macro_regime_score": macro_score,
            "macro_lead_score": macro_lead_score,
            "macro_votes": macro_votes,
            "macro_ofi_values": macro_ofi_values,
            "macro_lead_votes": macro_lead_votes,
            "macro_lag30_ofi_values": macro_lag30_ofi_values,
        }

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(timezone.utc)
