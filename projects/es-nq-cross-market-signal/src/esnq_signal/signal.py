from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .model import LinearLogitModel, build_model_features

_ET = ZoneInfo("America/New_York")


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _session_bucket_from_timestamp(ts_utc: str) -> str:
    text = str(ts_utc or "").strip().replace("Z", "+00:00")
    if not text:
        return "unknown"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    hour = dt.astimezone(_ET).hour
    if hour in {9, 10}:
        return "RTH_OPEN"
    if hour in {11, 12, 13}:
        return "RTH_MID"
    if hour in {14, 15}:
        return "RTH_CLOSE"
    return "OVERNIGHT"


@dataclass(frozen=True)
class DirectionSignal:
    market: str
    timestamp_utc: str
    horizon_minutes: int
    direction: str
    p_up: float
    p_hit: float
    confidence: float
    status: str
    reason: str
    layers: dict[str, Any]
    risk_flags: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "timestamp_utc": self.timestamp_utc,
            "horizon_minutes": self.horizon_minutes,
            "direction": self.direction,
            "p_up": round(self.p_up, 4),
            "p_hit": round(self.p_hit, 4),
            "confidence": round(self.confidence, 4),
            "status": self.status,
            "reason": self.reason,
            "layers": self.layers,
            "risk_flags": self.risk_flags,
        }


@dataclass(frozen=True)
class SignalThresholds:
    post_p_hit_min: float = 0.65
    post_confidence_min: float = 0.57
    post_cross_confirm_min: int = 1
    post_direction_gap_min: float = 0.03
    post_raw_edge_min: float = 0.75
    allowed_session: str = "any"
    allowed_direction: str = "any"
    high_p_hit_min: float = 0.72
    high_confidence_min: float = 0.62
    high_cross_confirm_min: int = 2
    high_direction_gap_min: float = 0.06
    high_raw_edge_min: float = 1.25

    @staticmethod
    def from_dict(raw: dict[str, Any] | None) -> "SignalThresholds":
        if not isinstance(raw, dict):
            return SignalThresholds()

        def _f(name: str, default: float) -> float:
            value = raw.get(name)
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return default
            return parsed

        def _i(name: str, default: int) -> int:
            value = raw.get(name)
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return default
            return parsed

        return SignalThresholds(
            post_p_hit_min=_f("post_p_hit_min", 0.65),
            post_confidence_min=_f("post_confidence_min", 0.57),
            post_cross_confirm_min=_i("post_cross_confirm_min", 1),
            post_direction_gap_min=_f("post_direction_gap_min", 0.03),
            post_raw_edge_min=_f("post_raw_edge_min", 0.75),
            allowed_session=str(raw.get("allowed_session") or "any").strip() or "any",
            allowed_direction=str(raw.get("allowed_direction") or "any").strip() or "any",
            high_p_hit_min=_f("high_p_hit_min", 0.72),
            high_confidence_min=_f("high_confidence_min", 0.62),
            high_cross_confirm_min=_i("high_cross_confirm_min", 2),
            high_direction_gap_min=_f("high_direction_gap_min", 0.06),
            high_raw_edge_min=_f("high_raw_edge_min", 1.25),
        )


class DirectionSignalEngine:
    def __init__(
        self,
        *,
        max_spread_es: float,
        max_spread_nq: float,
        thresholds_by_market_horizon: dict[str, dict[int, SignalThresholds]] | None = None,
        models_by_market_horizon: dict[str, dict[int, dict[str, LinearLogitModel]]] | None = None,
        live_ok_horizons: dict[str, set[int]] | None = None,
    ) -> None:
        self._max_spread_es = max_spread_es
        self._max_spread_nq = max_spread_nq
        self._thresholds_by_market_horizon = thresholds_by_market_horizon or {}
        self._models_by_market_horizon = models_by_market_horizon or {}
        self._live_ok_horizons = {str(m).upper(): set(v) for m, v in (live_ok_horizons or {}).items()}

    def _is_live_ok(self, market: str, horizon_minutes: int) -> bool:
        if not self._live_ok_horizons:
            return True
        allowed = self._live_ok_horizons.get(str(market).upper())
        if not allowed:
            return False
        return int(horizon_minutes) in allowed

    def _max_spread(self, market: str) -> float:
        return self._max_spread_es if market.upper() == "ES" else self._max_spread_nq

    def _thresholds_for(self, market: str, horizon_minutes: int) -> SignalThresholds:
        mkt = market.upper()
        by_horizon = self._thresholds_by_market_horizon.get(mkt, {})
        return by_horizon.get(int(horizon_minutes), SignalThresholds())

    def _models_for(self, market: str, horizon_minutes: int) -> dict[str, LinearLogitModel]:
        mkt = market.upper()
        by_horizon = self._models_by_market_horizon.get(mkt, {})
        return by_horizon.get(int(horizon_minutes), {})

    def evaluate(self, feature_row: dict[str, Any], *, horizon_minutes: int) -> DirectionSignal:
        market = str(feature_row.get("market") or "").upper()
        ts = str(feature_row.get("timestamp_utc") or "")
        risk_flags: list[str] = []
        layers: dict[str, Any] = {}
        thresholds = self._thresholds_for(market, int(horizon_minutes))

        target_stale = bool(feature_row.get("target_stale"))
        target_spread = feature_row.get("target_spread")
        spread_ok = target_spread is not None and float(target_spread) <= self._max_spread(market)
        if target_stale:
            risk_flags.append("stale_target_ofi")
        if not spread_ok:
            risk_flags.append("spread_too_wide_or_missing")

        z15 = feature_row.get("target_ofi_15s_z")
        z60 = feature_row.get("target_ofi_60s_z")
        z300 = feature_row.get("target_ofi_300s_z")
        if z15 is None:
            z15 = feature_row.get("target_ofi_15s")
        if z60 is None:
            z60 = feature_row.get("target_ofi_60s")
        if z300 is None:
            z300 = feature_row.get("target_ofi_300s")
        z15 = float(z15 or 0.0)
        z60 = float(z60 or 0.0)
        z300 = float(z300 or 0.0)
        ltrade_60 = float(feature_row.get("target_large_trade_signed_60s") or 0.0)
        ltrade_300 = float(feature_row.get("target_large_trade_signed_300s") or 0.0)
        ltrade_count_60 = int(feature_row.get("target_large_trade_count_60s") or 0)
        ltrade_count_300 = int(feature_row.get("target_large_trade_count_300s") or 0)

        cross_confirm = int(feature_row.get("cross_index_confirmation") or 0)
        cross_contra = int(feature_row.get("cross_index_contradiction") or 0)
        cross_lead_confirm = int(feature_row.get("cross_index_lead_confirmation") or 0)
        cross_lead_contra = int(feature_row.get("cross_index_lead_contradiction") or 0)
        cross_support = max(cross_confirm, cross_lead_confirm)
        macro_score = int(feature_row.get("macro_regime_score") or 0)
        macro_lead_score = int(feature_row.get("macro_lead_score") or 0)
        macro_ready = int(feature_row.get("macro_ready") or 0)

        # Direction core: OFI stack + cross-index support.
        raw = (0.85 * z15) + (0.45 * z60) + (0.20 * z300)
        raw += 0.55 * cross_confirm
        raw -= 0.75 * cross_contra
        raw += 0.35 * cross_lead_confirm
        raw -= 0.45 * cross_lead_contra
        raw += 0.15 * macro_score
        raw += 0.12 * macro_lead_score
        # Large-trade layer: bounded contribution from aggressive print imbalance.
        ltrade_layer = math.tanh(ltrade_60 / 60.0) + (0.6 * math.tanh(ltrade_300 / 180.0))
        raw += 0.35 * ltrade_layer

        edge_strength = abs(raw)
        lead_alignment = max(0, cross_lead_confirm - cross_lead_contra)
        p_up_heuristic = _sigmoid(raw)
        p_hit_heuristic = _sigmoid(
            (edge_strength - 0.75)
            + (0.35 * cross_confirm)
            - (0.45 * cross_contra)
            + (0.25 * lead_alignment)
        )
        model_features = build_model_features(
            feature_row,
            raw_score=raw,
            p_up_heuristic=p_up_heuristic,
            p_hit_heuristic=p_hit_heuristic,
        )
        model_map = self._models_for(market, int(horizon_minutes))
        p_up_model = model_map["p_up"].predict_proba(model_features) if "p_up" in model_map else None
        p_hit_model = model_map["p_hit"].predict_proba(model_features) if "p_hit" in model_map else None

        p_up = float(p_up_model) if p_up_model is not None else p_up_heuristic
        p_hit = float(p_hit_model) if p_hit_model is not None else p_hit_heuristic
        p_down = 1.0 - p_up
        direction_gap = abs(p_up - 0.5)
        raw_edge = abs(raw)
        session_bucket = _session_bucket_from_timestamp(ts)
        direction = "neutral"
        if p_up >= 0.53:
            direction = "long"
        elif p_up <= 0.47:
            direction = "short"
        allowed_session = str(thresholds.allowed_session or "any").strip()
        allowed_direction = str(thresholds.allowed_direction or "any").strip()
        session_ok = allowed_session.lower() in {"any", "*"} or allowed_session.upper() == session_bucket
        direction_ok = (
            allowed_direction.lower() in {"any", "*"}
            or (direction in {"long", "short"} and allowed_direction.lower() == direction.lower())
        )
        confidence = max(p_up, p_down) * p_hit

        layers["direction_core_raw"] = round(raw, 4)
        layers["ofi"] = {"z15": round(z15, 4), "z60": round(z60, 4), "z300": round(z300, 4)}
        layers["large_trades"] = {
            "signed_60s": round(ltrade_60, 4),
            "signed_300s": round(ltrade_300, 4),
            "count_60s": ltrade_count_60,
            "count_300s": ltrade_count_300,
            "layer_score": round(ltrade_layer, 4),
        }
        layers["cross_index"] = {
            "confirmation": cross_confirm,
            "contradiction": cross_contra,
            "lead_confirmation": cross_lead_confirm,
            "lead_contradiction": cross_lead_contra,
            "support_used": cross_support,
        }
        layers["macro"] = {"score": macro_score, "lead_score": macro_lead_score, "ready": macro_ready}
        layers["thresholds"] = {
            "post_p_hit_min": thresholds.post_p_hit_min,
            "post_confidence_min": thresholds.post_confidence_min,
            "post_cross_confirm_min": thresholds.post_cross_confirm_min,
            "post_direction_gap_min": thresholds.post_direction_gap_min,
            "post_raw_edge_min": thresholds.post_raw_edge_min,
            "allowed_session": thresholds.allowed_session,
            "allowed_direction": thresholds.allowed_direction,
            "high_p_hit_min": thresholds.high_p_hit_min,
            "high_confidence_min": thresholds.high_confidence_min,
            "high_cross_confirm_min": thresholds.high_cross_confirm_min,
            "high_direction_gap_min": thresholds.high_direction_gap_min,
            "high_raw_edge_min": thresholds.high_raw_edge_min,
        }
        layers["context"] = {"session_bucket": session_bucket}
        layers["model"] = {
            "p_up_model_used": p_up_model is not None,
            "p_hit_model_used": p_hit_model is not None,
            "p_up_heuristic": round(p_up_heuristic, 4),
            "p_hit_heuristic": round(p_hit_heuristic, 4),
            "p_up_model": round(float(p_up_model), 4) if p_up_model is not None else None,
            "p_hit_model": round(float(p_hit_model), 4) if p_hit_model is not None else None,
            "direction_gap": round(float(direction_gap), 4),
            "raw_edge": round(float(raw_edge), 4),
        }

        if direction == "neutral":
            risk_flags.append("weak_directional_edge")
        if cross_support == 0:
            risk_flags.append("no_cross_index_confirmation")
        if cross_lead_contra > cross_lead_confirm:
            risk_flags.append("cross_index_lead_contradiction")
        if macro_ready == 0:
            risk_flags.append("macro_context_missing")
        if direction_gap < thresholds.post_direction_gap_min:
            risk_flags.append("direction_gap_below_post_min")
        if raw_edge < thresholds.post_raw_edge_min:
            risk_flags.append("raw_edge_below_post_min")
        if not session_ok:
            risk_flags.append("session_filter_mismatch")
        if not direction_ok and direction in {"long", "short"}:
            risk_flags.append("direction_filter_mismatch")
        horizon_live_ok = self._is_live_ok(market, int(horizon_minutes))
        if not horizon_live_ok:
            risk_flags.append("research_only_horizon")

        status = "watch"
        reason = "candidate_ready"
        if target_stale or not spread_ok:
            status = "blocked"
            reason = "data_quality_gate"
        elif direction == "neutral":
            status = "blocked"
            reason = "weak_edge"
        elif (
            p_hit >= thresholds.high_p_hit_min
            and confidence >= thresholds.high_confidence_min
            and cross_support >= thresholds.high_cross_confirm_min
            and direction_gap >= thresholds.high_direction_gap_min
            and raw_edge >= thresholds.high_raw_edge_min
            and session_ok
            and direction_ok
        ):
            status = "high_priority"
            reason = "strong_multilayer_alignment"
        elif (
            p_hit >= thresholds.post_p_hit_min
            and confidence >= thresholds.post_confidence_min
            and cross_support >= thresholds.post_cross_confirm_min
            and direction_gap >= thresholds.post_direction_gap_min
            and raw_edge >= thresholds.post_raw_edge_min
            and session_ok
            and direction_ok
        ):
            status = "post"
            reason = "trade_filter_pass"
        else:
            status = "watch"
            reason = "below_post_threshold"

        if status in {"post", "high_priority"} and not horizon_live_ok:
            status = "watch"
            reason = "research_only_horizon"

        return DirectionSignal(
            market=market,
            timestamp_utc=ts,
            horizon_minutes=int(horizon_minutes),
            direction=direction,
            p_up=float(p_up),
            p_hit=float(p_hit),
            confidence=float(confidence),
            status=status,
            reason=reason,
            layers=layers,
            risk_flags=risk_flags,
        )
