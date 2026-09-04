from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import mean, median, pstdev
from typing import Any

from .equity_breadth import EquityBreadthObservation, EquityBreadthSnapshot
from .markets import (
    EQUITY_TARGET_MARKETS,
    KNOWN_MARKETS,
    MEGA_EQUITY_SPECS,
    TARGET_MARKETS,
    normalize_sierra_symbol,
    product_profile,
)
from .ofi_stream import OfiSample, effective_ofi, normalized_ofi


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
    ofi_norm: deque[tuple[datetime, float]] = field(default_factory=deque)
    spot: deque[tuple[datetime, float]] = field(default_factory=deque)
    spread: deque[tuple[datetime, float]] = field(default_factory=deque)
    large_trades: deque[tuple[datetime, float]] = field(default_factory=deque)
    trade_imbalance_norm: deque[tuple[datetime, float]] = field(default_factory=deque)
    last_sample: OfiSample | None = None
    contract_id: str | None = None
    contract_started_at_utc: datetime | None = None
    rolled_at_utc: datetime | None = None

    def ingest(self, sample: OfiSample, max_seconds: int = 1800) -> None:
        incoming_contract = str(sample.contract_id or "").strip() or None
        if incoming_contract is not None and self.contract_id is not None and incoming_contract != self.contract_id:
            # Sierra's chart contract is authoritative.  Clear every history
            # series on the observed identifier change; never infer a roll.
            self.ofi.clear()
            self.ofi_norm.clear()
            self.spot.clear()
            self.spread.clear()
            self.large_trades.clear()
            self.trade_imbalance_norm.clear()
            self.rolled_at_utc = sample.timestamp_utc
            self.contract_started_at_utc = sample.timestamp_utc
        elif incoming_contract is not None and self.contract_id is None:
            self.contract_started_at_utc = sample.timestamp_utc
        if incoming_contract is not None:
            self.contract_id = incoming_contract
        self.last_sample = sample
        cutoff = sample.timestamp_utc - timedelta(seconds=max_seconds)
        # 2026-07-01: use CKS-correct OFI when the exporter provides it
        # (falls back to legacy depth-delta transparently). See
        # ofi_stream.effective_ofi and CROSS_MARKET_EVIDENCE.md references.
        ofi_value = effective_ofi(sample)
        if ofi_value is not None:
            self.ofi.append((sample.timestamp_utc, ofi_value))
        normalized_value = normalized_ofi(sample)
        if normalized_value is not None:
            self.ofi_norm.append((sample.timestamp_utc, normalized_value))
        if sample.spot is not None:
            self.spot.append((sample.timestamp_utc, sample.spot))
        if sample.spread is not None:
            self.spread.append((sample.timestamp_utc, sample.spread))
        if sample.trade_imbalance_norm is not None:
            self.trade_imbalance_norm.append(
                (sample.timestamp_utc, float(sample.trade_imbalance_norm))
            )

        for series in (
            self.ofi,
            self.ofi_norm,
            self.spot,
            self.spread,
            self.large_trades,
            self.trade_imbalance_norm,
        ):
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

    def signed_age_seconds(self, now_utc: datetime) -> float | None:
        if self.last_sample is None:
            return None
        return (now_utc - self.last_sample.timestamp_utc).total_seconds()

    def latest_spot(self) -> float | None:
        if not self.spot:
            return None
        return self.spot[-1][1]

    def latest_spread(self) -> float | None:
        if not self.spread:
            return None
        return self.spread[-1][1]

    def latest_trade_imbalance_norm(self) -> float | None:
        if not self.trade_imbalance_norm:
            return None
        return self.trade_imbalance_norm[-1][1]

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

    def normalized_ofi_sum(
        self,
        seconds: int,
        *,
        now_utc: datetime,
        end_offset_seconds: int = 0,
        not_before_utc: datetime | None = None,
    ) -> float | None:
        if not self.ofi_norm:
            return None
        end_offset = max(0, int(end_offset_seconds))
        window_end = now_utc - timedelta(seconds=end_offset)
        window_start = window_end - timedelta(seconds=seconds)
        if not_before_utc is not None and not_before_utc > window_start:
            window_start = not_before_utc
        values = [value for ts, value in self.ofi_norm if window_start <= ts <= window_end]
        if not values:
            return None
        return float(sum(values))

    def roll_warmup_active(self, now_utc: datetime, warmup_seconds: int) -> bool:
        if self.rolled_at_utc is None or warmup_seconds <= 0:
            return False
        return (now_utc - self.rolled_at_utc).total_seconds() < float(warmup_seconds)

    def ofi_zscore(
        self,
        seconds: int,
        *,
        now_utc: datetime,
        lookback_seconds: int = 600,
        end_offset_seconds: int = 0,
    ) -> float | None:
        window_values = self._ofi_window_values(
            seconds, now_utc=now_utc, end_offset_seconds=end_offset_seconds
        )
        if not window_values:
            return None
        current = float(sum(window_values))
        end_offset = max(0, int(end_offset_seconds))
        lookback_end = now_utc - timedelta(seconds=end_offset)
        cutoff = lookback_end - timedelta(seconds=lookback_seconds)
        lookback = [value for ts, value in self.ofi if cutoff <= ts <= lookback_end]
        if len(lookback) < 20:
            return None
        # 2026-09-04 (IC memo s3 flaw 2): scale mu/sigma by the OBSERVED sample
        # count in the window, not by the nominal number of seconds. The old
        # form assumed exactly one sample per second; with two exporters feeding
        # ES/NQ (ports 5555 + 5563) or with gaps, z was mis-scaled.
        n = float(len(window_values))
        mu = mean(lookback) * n
        sigma = pstdev(lookback) * max(1.0, n ** 0.5)
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
    def __init__(self, *, smoke_validated_markets: set[str] | None = None) -> None:
        self._states: dict[str, MarketState] = {market: MarketState() for market in KNOWN_MARKETS}
        self._smoke_validated_markets = {
            str(market).upper() for market in (smoke_validated_markets or set())
        }
        self._equity_breadth_snapshot: EquityBreadthSnapshot | None = None

    def ingest(self, sample: OfiSample) -> None:
        if sample.market not in self._states:
            self._states[sample.market] = MarketState()
        self._states[sample.market].ingest(sample)

    def ingest_equity_breadth(self, snapshot: EquityBreadthSnapshot) -> None:
        self._equity_breadth_snapshot = snapshot

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
        profile = product_profile(target_market)
        return list(profile.primary_peers) if profile is not None else []

    @staticmethod
    def _fresh_equity_observation(
        observation: EquityBreadthObservation | None,
        *,
        now_utc: datetime,
        stale_seconds: float,
    ) -> dict[str, Any] | None:
        if observation is None or not observation.healthy:
            return None
        age = observation.signed_age_seconds(now_utc)
        if age > float(stale_seconds) or age < -2.0:
            return None
        if observation.return_z is None or observation.imbalance_z is None:
            return None
        return_z = float(observation.return_z)
        imbalance_z = float(observation.imbalance_z)
        return_sign = _sign(return_z)
        imbalance_sign = _sign(imbalance_z)
        conflict = return_sign * imbalance_sign < 0
        expected_vote = return_sign if return_sign == imbalance_sign else 0
        supplied_vote = (
            1
            if observation.vote == "bullish"
            else (-1 if observation.vote == "bearish" else 0)
        )
        if observation.composite_score is None:
            return None
        score = float(observation.composite_score)
        score_sign = _sign(score)
        schema_consistent = bool(
            supplied_vote == expected_vote
            and (
                (expected_vote == 0 and score_sign == 0)
                or (expected_vote != 0 and score_sign == expected_vote)
            )
        )
        if not schema_consistent:
            return None
        return {
            "root": observation.root,
            "issuer_id": observation.issuer_id,
            "roles": list(observation.roles),
            "vote": expected_vote,
            "vote_label": (
                "bullish"
                if expected_vote > 0
                else ("bearish" if expected_vote < 0 else "neutral")
            ),
            "score": round(float(score), 6),
            "return_z": observation.return_z,
            "imbalance_z": observation.imbalance_z,
            "component_conflict": conflict,
            "component_neutral": expected_vote == 0,
            "age_s": round(float(age), 6),
            "observed_at": observation.observed_at.isoformat(timespec="milliseconds"),
            "freshness": observation.freshness,
            "provenance": observation.provenance,
        }

    def _sierra_equity_peer_diagnostics(
        self,
        target: str,
        *,
        now_utc: datetime,
        stale_seconds: float,
    ) -> dict[str, Any]:
        """Non-authorizing diagnostics when the Theta breadth input is absent."""

        profile = product_profile(target)
        roots = tuple(profile.primary_peers) if profile is not None else ()
        result: dict[str, Any] = {}
        for root in roots:
            state = self._states.get(root)
            age = state.signed_age_seconds(now_utc) if state is not None else None
            if (
                state is None
                or state.last_sample is None
                or age is None
                or age > stale_seconds
                or age < -2.0
            ):
                continue
            latest = state.last_sample
            declared_mode = str(latest.input_mode or "").strip().lower() or None
            l2_current = bool(
                latest.l2_available is not False
                and latest.ofi_norm is not None
                and declared_mode in {None, "l2_ofi"}
            )
            trade_current = bool(
                latest.l2_available is False
                and latest.trade_imbalance_norm is not None
                and declared_mode in {None, "trade_imbalance"}
            )
            aggregate_now = max(now_utc, latest.timestamp_utc)
            l2_value = (
                state.normalized_ofi_sum(15, now_utc=aggregate_now) if l2_current else None
            )
            fallback_value = (
                float(latest.trade_imbalance_norm) if trade_current else None
            )
            result[root] = {
                "input_mode": (
                    "l2_ofi"
                    if l2_value is not None
                    else ("trade_imbalance" if fallback_value is not None else None)
                ),
                "l2_ofi_norm_15s": l2_value,
                "trade_imbalance_norm_15s": fallback_value,
                "age_s": age,
                "authorizing": False,
            }
        return result

    def _equity_peer_confirmation(
        self,
        target: str,
        *,
        target_sign: int,
        now_utc: datetime,
        stale_seconds: float,
    ) -> dict[str, Any]:
        profile = product_profile(target)
        snapshot = self._equity_breadth_snapshot
        diagnostics = self._sierra_equity_peer_diagnostics(
            target, now_utc=now_utc, stale_seconds=stale_seconds
        )
        base: dict[str, Any] = {
            "source": "theta_equity_breadth_v1" if snapshot is not None else "missing",
            "schema_version": snapshot.schema_version if snapshot is not None else None,
            "required_aligned": 2,
            "ready": 0,
            "ready_groups": 0,
            "aligned": 0,
            "aligned_group_count": 0,
            "opposing": 0,
            "opposing_group_count": 0,
            "qqq_strong_opposition": False,
            "qqq_opposition": False,
            "veto": False,
            "veto_reason": None,
            "strong_opposing_primary_count": 0,
            "strong_opposing_peers": [],
            "confirmed": False,
            "ok": False,
            "benchmark": None,
            "sector": None,
            "issuer_breadth": None,
            "sierra_diagnostics": diagnostics,
            "sierra_diagnostics_authorizing": False,
        }
        if snapshot is None or profile is None:
            return base
        snapshot_age = snapshot.signed_age_seconds(now_utc)
        base["snapshot_age_s"] = round(float(snapshot_age), 6)
        base["snapshot_calculated_at"] = snapshot.calculated_at.isoformat(timespec="milliseconds")
        base["snapshot_health"] = snapshot.healthy
        base["provenance"] = snapshot.provenance
        if not snapshot.healthy:
            base["source"] = "theta_equity_breadth_v1_unhealthy"
            return base
        if snapshot_age > float(stale_seconds) or snapshot_age < -2.0:
            base["source"] = "theta_equity_breadth_v1_stale"
            return base

        def root_detail(root: str | None) -> dict[str, Any] | None:
            if not root:
                return None
            return self._fresh_equity_observation(
                snapshot.observations.get(root),
                now_utc=now_utc,
                stale_seconds=stale_seconds,
            )

        benchmark = root_detail(profile.benchmark_market)
        sector = root_detail(profile.sector_market)
        base["benchmark"] = benchmark
        base["sector"] = sector

        issuer_scores: dict[str, list[float]] = {}
        issuer_members: dict[str, list[str]] = {}
        excluded_issuer_members: dict[str, dict[str, Any]] = {}
        for symbol, spec in MEGA_EQUITY_SPECS.items():
            detail = root_detail(symbol)
            if spec.issuer_id == profile.issuer_id:
                if detail is not None:
                    excluded_issuer_members[symbol] = detail
                continue
            if detail is None:
                continue
            issuer_scores.setdefault(spec.issuer_id, []).append(float(detail["score"]))
            issuer_members.setdefault(spec.issuer_id, []).append(symbol)
        collapsed_scores = {
            issuer: float(median(scores)) for issuer, scores in issuer_scores.items() if scores
        }
        breadth_ready = len(collapsed_scores) >= 3
        breadth_score = (
            float(median(collapsed_scores.values())) if breadth_ready else None
        )
        breadth_vote = _sign(breadth_score)
        breadth = {
            "excluded_issuer_id": profile.issuer_id,
            "excluded_issuer_members": excluded_issuer_members,
            "excluded_issuer_members_authorizing": False,
            "minimum_issuers": 3,
            "contributing_issuer_count": len(collapsed_scores),
            "contributing_issuers": sorted(collapsed_scores),
            "issuer_members": issuer_members,
            "issuer_scores": collapsed_scores,
            "ready": breadth_ready,
            "score": round(breadth_score, 6) if breadth_score is not None else None,
            "vote": breadth_vote,
            "vote_label": (
                "bullish" if breadth_vote > 0 else ("bearish" if breadth_vote < 0 else "neutral")
            ),
        }
        base["issuer_breadth"] = breadth

        groups: list[tuple[str, dict[str, Any]]] = []
        if benchmark is not None:
            groups.append(("benchmark", benchmark))
        if sector is not None:
            groups.append(("sector", sector))
        if breadth_ready:
            groups.append(("issuer_breadth", breadth))
        aligned_names = [name for name, group in groups if target_sign != 0 and int(group["vote"]) == target_sign]
        opposing_names = [name for name, group in groups if target_sign != 0 and int(group["vote"]) == -target_sign]
        qqq_strong_opposition = bool(
            benchmark is not None
            and target_sign != 0
            and int(benchmark["vote"]) == -target_sign
            and abs(float(benchmark["score"])) >= 1.0
        )
        ages = [float(group["age_s"]) for _, group in groups if group.get("age_s") is not None]
        ready_count = len(groups)
        aligned_count = len(aligned_names)
        opposing_count = len(opposing_names)
        base.update(
            {
                "ready": ready_count,
                "ready_groups": ready_count,
                "aligned": aligned_count,
                "aligned_group_count": aligned_count,
                "aligned_groups": aligned_names,
                "opposing": opposing_count,
                "opposing_group_count": opposing_count,
                "opposing_groups": opposing_names,
                "qqq_strong_opposition": qqq_strong_opposition,
                "qqq_opposition": qqq_strong_opposition,
                "veto": qqq_strong_opposition,
                "veto_reason": (
                    "strong_opposing_qqq" if qqq_strong_opposition else None
                ),
                "strong_opposing_primary_count": 1 if qqq_strong_opposition else 0,
                "strong_opposing_peers": ["QQQ"] if qqq_strong_opposition else [],
                "confirmed": ready_count >= 2 and aligned_count >= 2 and not qqq_strong_opposition,
                "ok": ready_count >= 2,
                "max_age_s": max(ages) if ages else None,
            }
        )
        return base

    def _build_equity_target_row(
        self,
        target: str,
        *,
        now_utc: datetime,
        stale_seconds: float,
    ) -> dict[str, Any]:
        profile = product_profile(target)
        target_state = self._states.get(target, MarketState())
        if profile is None or target_state.last_sample is None:
            return {
                "market": target,
                "profile_id": profile.profile_id if profile is not None else None,
                "status": "not_ready",
                "reason": "target_sample_missing",
            }
        signed_age = target_state.signed_age_seconds(now_utc)
        target_stale = (
            signed_age is None
            or signed_age > float(stale_seconds)
            or signed_age < -2.0
        )
        latest = target_state.last_sample
        declared_mode = str(latest.input_mode or "").strip().lower() or None
        l2_current = bool(
            latest.l2_available is not False
            and latest.ofi_norm is not None
            and declared_mode in {None, "l2_ofi"}
        )
        trade_current = bool(
            latest.l2_available is False
            and latest.trade_imbalance_norm is not None
            and declared_mode in {None, "trade_imbalance"}
        )
        aggregate_now = max(now_utc, latest.timestamp_utc)
        target_ofi_norm_15s = (
            target_state.normalized_ofi_sum(15, now_utc=aggregate_now)
            if l2_current
            else None
        )
        fallback_value = (
            float(latest.trade_imbalance_norm) if trade_current else None
        )
        if target_ofi_norm_15s is not None:
            input_mode = "l2_ofi"
            input_value = float(target_ofi_norm_15s)
            input_provenance = {
                "source": "sierra_l2_ofi",
                "window_seconds": 15,
            }
        elif fallback_value is not None:
            input_mode = "trade_imbalance"
            input_value = float(fallback_value)
            input_provenance = {
                "source": "sierra_time_and_sales",
                "window_seconds": 15,
            }
        else:
            input_mode = None
            input_value = None
            input_provenance = None
        target_sign = _sign(input_value)
        peer_confirmation = self._equity_peer_confirmation(
            target,
            target_sign=target_sign,
            now_utc=now_utc,
            stale_seconds=stale_seconds,
        )
        raw_identity = str(latest.contract_id or latest.symbol_raw or "").strip()
        ticker_identity_ok = normalize_sierra_symbol(raw_identity) == target
        aligned = int(peer_confirmation.get("aligned_group_count") or 0)
        target_strength = min(1.0, abs(float(input_value or 0.0)))
        rule_score = max(0.0, min(1.0, (0.5 * target_strength) + (0.25 * aligned)))
        snapshot_age = peer_confirmation.get("snapshot_age_s")
        return {
            "timestamp_utc": now_utc.isoformat(timespec="milliseconds"),
            "market": target,
            "ticker": target,
            "asset_class": "equity",
            "coordinate_space": "native_equity",
            "profile_id": profile.profile_id,
            "contract_id": raw_identity or None,
            "ticker_identity_ok": ticker_identity_ok,
            "signal_kind": "rules_first",
            "validation_status": (
                "smoke_passed"
                if target in self._smoke_validated_markets
                else "shadow_research"
            ),
            "status": "ok",
            "target_age_s": signed_age,
            "target_future_clock_skew": signed_age is not None and signed_age < -2.0,
            "target_stale": target_stale,
            "target_spot": target_state.latest_spot(),
            "target_spread": target_state.latest_spread(),
            "input_mode": input_mode,
            "input_provenance": input_provenance,
            "input_value_15s": input_value,
            "target_ofi_norm_15s": target_ofi_norm_15s,
            "target_trade_imbalance_norm_15s": (
                fallback_value if input_mode == "trade_imbalance" else None
            ),
            "target_direction_hint": target_sign,
            "peer_confirmation": peer_confirmation,
            "rule_score": round(rule_score, 4),
            "rule_candidate": bool(
                not target_stale
                and ticker_identity_ok
                and input_mode in {"l2_ofi", "trade_imbalance"}
                and input_value is not None
                and target_sign != 0
                and peer_confirmation.get("confirmed") is True
            ),
            "breadth_snapshot_age_s": snapshot_age,
            "issuer_id": profile.issuer_id,
            "benchmark_market": profile.benchmark_market,
            "sector_market": profile.sector_market,
            "roll_warmup_active": False,
            "roll_warmup_seconds": 0,
        }

    def build_target_row(self, target_market: str, *, now_utc: datetime, stale_seconds: float) -> dict[str, Any]:
        target = target_market.upper()
        profile = product_profile(target)
        if target not in TARGET_MARKETS or profile is None:
            return {"market": target, "status": "unsupported_target"}
        if target in EQUITY_TARGET_MARKETS:
            return self._build_equity_target_row(
                target,
                now_utc=now_utc,
                stale_seconds=stale_seconds,
            )

        target_state = self._states.get(target, MarketState())
        if target_state.last_sample is None:
            return {
                "market": target,
                "profile_id": profile.profile_id,
                "status": "not_ready",
                "reason": "target_sample_missing",
            }
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
        t_ofi_norm_15 = target_state.normalized_ofi_sum(15, now_utc=now_utc)
        t_ofi_norm_60 = target_state.normalized_ofi_sum(60, now_utc=now_utc)
        t_ltrade_60 = target_state.large_trade_signed_volume(60, now_utc=now_utc)
        t_ltrade_300 = target_state.large_trade_signed_volume(300, now_utc=now_utc)
        t_ltrade_count_60 = target_state.large_trade_count(60, now_utc=now_utc)
        t_ltrade_count_300 = target_state.large_trade_count(300, now_utc=now_utc)
        t_ltrade_buy_ratio_300 = target_state.large_trade_buy_ratio(300, now_utc=now_utc)

        if profile.rules_first:
            target_sign = _sign(t_ofi_norm_15)
        else:
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
        strong_opposing_peers: list[str] = []
        peer_roll_warmup_markets: list[str] = []
        cohort_start_utc = target_state.rolled_at_utc or target_state.contract_started_at_utc

        for peer in self._target_peers(target):
            state = self._states.get(peer)
            if state is None:
                continue
            if profile.rules_first and state.roll_warmup_active(
                now_utc, profile.roll_warmup_seconds
            ):
                # Each primary peer is quarantined independently following
                # its own authoritative Sierra contract-id change.
                peer_roll_warmup_markets.append(peer)
                continue
            age = state.age_seconds(now_utc)
            if age is None or age > stale_seconds:
                continue
            if profile.rules_first:
                # Cross-product comparisons must be scale-free. Missing
                # normalized OFI makes that peer unavailable (fail closed).
                peer_ofi = state.normalized_ofi_sum(
                    15,
                    now_utc=now_utc,
                    not_before_utc=cohort_start_utc,
                )
            else:
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
                if profile.rules_first and abs(float(peer_ofi)) >= 0.10:
                    strong_opposing_peers.append(peer)

            if profile.rules_first:
                peer_lag = state.normalized_ofi_sum(
                    15,
                    now_utc=now_utc,
                    end_offset_seconds=30,
                    not_before_utc=cohort_start_utc,
                )
            else:
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
        # It remains an ES/NQ compatibility layer and is intentionally not
        # reused for CL/UB, where those polarities have different semantics.
        equity_macro = (
            (("E6", 1), ("CL", 1), ("ZN", -1), ("ZB", -1), ("GC", -1), ("VX", -1))
            if not profile.rules_first
            else ()
        )
        for market, polarity in equity_macro:
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

        context_ofi_values: dict[str, float] = {}
        context_age_seconds: dict[str, float] = {}
        for market in profile.context_markets:
            if market == target:
                continue
            state = self._states.get(market)
            if state is None:
                continue
            age = state.age_seconds(now_utc)
            if age is None or age > stale_seconds:
                continue
            value = (
                state.normalized_ofi_sum(
                    15,
                    now_utc=now_utc,
                    not_before_utc=cohort_start_utc,
                )
                if profile.rules_first
                else state.ofi_sum(15, now_utc=now_utc)
            )
            if value is None:
                continue
            context_ofi_values[market] = float(value)
            context_age_seconds[market] = float(age)

        required_peers = profile.min_aligned_peers
        peer_ratio = min(1.0, peer_confirm / max(1, required_peers))
        target_strength = min(1.0, abs(float(t_ofi_norm_15 or 0.0)))
        rule_score = max(
            0.0,
            min(1.0, (0.5 * target_strength) + (0.5 * peer_ratio) - (0.25 * len(strong_opposing_peers))),
        )
        roll_warmup_active = target_state.roll_warmup_active(now_utc, profile.roll_warmup_seconds)

        return {
            "timestamp_utc": now_utc.isoformat(timespec="milliseconds"),
            "market": target,
            "profile_id": profile.profile_id,
            "contract_id": target_state.contract_id,
            "signal_kind": "rules_first" if profile.rules_first else "model_probability",
            "validation_status": (
                "smoke_passed"
                if profile.rules_first and target in self._smoke_validated_markets
                else ("experimental_unvalidated" if profile.rules_first else "calibrated_runtime")
            ),
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
            "target_ofi_norm_15s": t_ofi_norm_15,
            "target_ofi_norm_60s": t_ofi_norm_60,
            "target_large_trade_signed_60s": t_ltrade_60,
            "target_large_trade_signed_300s": t_ltrade_300,
            "target_large_trade_count_60s": t_ltrade_count_60,
            "target_large_trade_count_300s": t_ltrade_count_300,
            "target_large_trade_buy_ratio_300s": t_ltrade_buy_ratio_300,
            "target_direction_hint": target_sign,
            "roll_detected_at_utc": (
                target_state.rolled_at_utc.isoformat(timespec="milliseconds")
                if target_state.rolled_at_utc is not None
                else None
            ),
            "roll_warmup_active": roll_warmup_active,
            "roll_warmup_seconds": profile.roll_warmup_seconds,
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
            "peer_confirmation": {
                "primary_peers": list(profile.primary_peers),
                "normalized_only": bool(profile.rules_first),
                "required_aligned": required_peers,
                "ready": peer_ready,
                "aligned": peer_confirm,
                "opposing": peer_contra,
                "strong_opposing_peers": strong_opposing_peers,
                "aligned_primary_count": peer_confirm,
                "opposing_primary_count": peer_contra,
                "strong_opposing_primary_count": len(strong_opposing_peers),
                "confirmed": peer_confirm >= required_peers and not (
                    profile.block_strong_opposing_primary and bool(strong_opposing_peers)
                ),
                "ok": peer_ready >= required_peers,
                "block_strong_opposing_primary": profile.block_strong_opposing_primary,
                "peer_roll_warmup_seconds": profile.roll_warmup_seconds,
                "peer_roll_warmup_markets": peer_roll_warmup_markets,
                "peer_roll_warmup_active": bool(peer_roll_warmup_markets),
                "values": peer_values,
                "max_age_s": peer_age_max if peer_ready > 0 else None,
            },
            "rule_score": round(rule_score, 4) if profile.rules_first else None,
            "context_ofi_values": context_ofi_values,
            "context_age_seconds": context_age_seconds,
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
