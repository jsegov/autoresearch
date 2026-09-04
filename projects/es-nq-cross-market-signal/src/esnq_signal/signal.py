from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .equity_breadth import EQUITY_BREADTH_SCHEMA_VERSION
from .markets import EQUITY_TARGET_MARKETS, normalize_sierra_symbol, product_profile
from .model import LinearLogitModel, build_model_features

_ET = ZoneInfo("America/New_York")
_FUTURES_MONTH_CODES = "FGHJKMNQUVXZ"


def _has_explicit_contract_month_year(contract_id: str | None, market: str) -> bool:
    """Require a raw target root followed by futures month code and year."""
    raw = str(contract_id or "").strip().upper()
    root = str(market or "").strip().upper()
    if not raw or not root:
        return False
    pattern = (
        rf"(?:^|[._-]){re.escape(root)}[{_FUTURES_MONTH_CODES}]"
        rf"\d{{1,4}}(?:$|[._-][A-Z0-9._-]*$)"
    )
    return re.search(pattern, raw) is not None


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _nonnegative_int(value: Any, *, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed) or parsed < 0.0 or not parsed.is_integer():
        return default
    return int(parsed)


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
    p_up: float | None
    p_hit: float | None
    confidence: float | None
    status: str
    reason: str
    layers: dict[str, Any]
    risk_flags: list[str]
    profile_id: str | None = None
    contract_id: str | None = None
    signal_kind: str = "model_probability"
    validation_status: str = "calibrated_runtime"
    rule_score: float | None = None
    peer_confirmation: dict[str, Any] | None = None
    asset_class: str | None = None
    input_mode: str | None = None
    candidate_eligible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "timestamp_utc": self.timestamp_utc,
            "horizon_minutes": self.horizon_minutes,
            "direction": self.direction,
            "p_up": round(self.p_up, 4) if self.p_up is not None else None,
            "p_hit": round(self.p_hit, 4) if self.p_hit is not None else None,
            "confidence": round(self.confidence, 4) if self.confidence is not None else None,
            "status": self.status,
            "reason": self.reason,
            "layers": self.layers,
            "risk_flags": self.risk_flags,
            "profile_id": self.profile_id,
            "contract_id": self.contract_id,
            "signal_kind": self.signal_kind,
            "validation_status": self.validation_status,
            "rule_score": round(self.rule_score, 4) if self.rule_score is not None else None,
            "peer_confirmation": self.peer_confirmation,
            "asset_class": self.asset_class,
            "input_mode": self.input_mode,
            "candidate_eligible": self.candidate_eligible,
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
        max_spread_cl: float = 0.05,
        max_spread_ub: float = 0.0625,
        experimental_posting_enabled: set[str] | None = None,
        experimental_smoke_validated: set[str] | None = None,
        thresholds_by_market_horizon: dict[str, dict[int, SignalThresholds]] | None = None,
        models_by_market_horizon: dict[str, dict[int, dict[str, LinearLogitModel]]] | None = None,
        live_ok_horizons: dict[str, set[int]] | None = None,
    ) -> None:
        self._max_spread_es = max_spread_es
        self._max_spread_nq = max_spread_nq
        self._max_spreads = {
            "ES": float(max_spread_es),
            "NQ": float(max_spread_nq),
            "CL": float(max_spread_cl),
            "UB": float(max_spread_ub),
        }
        self._experimental_posting_enabled = {
            str(market).upper() for market in (experimental_posting_enabled or set())
        }
        self._experimental_smoke_validated = {
            str(market).upper() for market in (experimental_smoke_validated or set())
        }
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
        return self._max_spreads.get(market.upper(), self._max_spread_nq)

    def _thresholds_for(self, market: str, horizon_minutes: int) -> SignalThresholds:
        mkt = market.upper()
        by_horizon = self._thresholds_by_market_horizon.get(mkt, {})
        return by_horizon.get(int(horizon_minutes), SignalThresholds())

    def _models_for(self, market: str, horizon_minutes: int) -> dict[str, LinearLogitModel]:
        mkt = market.upper()
        by_horizon = self._models_by_market_horizon.get(mkt, {})
        return by_horizon.get(int(horizon_minutes), {})

    def _evaluate_equity_rules_first(
        self,
        feature_row: dict[str, Any],
        *,
        horizon_minutes: int,
    ) -> DirectionSignal:
        market = str(feature_row.get("market") or "").upper()
        ts = str(feature_row.get("timestamp_utc") or "")
        profile = product_profile(market)
        risk_flags = ["experimental_rules_first", "native_equity_research"]
        peer_confirmation = feature_row.get("peer_confirmation")
        if not isinstance(peer_confirmation, dict):
            peer_confirmation = {}
        input_mode = str(feature_row.get("input_mode") or "").strip().lower()
        input_value = _finite_float(feature_row.get("input_value_15s"))
        l2_value = _finite_float(feature_row.get("target_ofi_norm_15s"))
        fallback_value = _finite_float(
            feature_row.get("target_trade_imbalance_norm_15s")
        )
        if input_mode == "l2_ofi":
            selected_value = l2_value
            input_sources_separate = l2_value is not None and fallback_value is None
        elif input_mode == "trade_imbalance":
            selected_value = fallback_value
            input_sources_separate = fallback_value is not None and l2_value is None
        else:
            selected_value = None
            input_sources_separate = False
        input_value_matches = bool(
            input_value is not None
            and selected_value is not None
            and abs(input_value - selected_value) <= 1e-9
        )
        direction = "neutral"
        if input_value is not None and input_value > 0.0:
            direction = "long"
        elif input_value is not None and input_value < 0.0:
            direction = "short"

        target_stale = bool(feature_row.get("target_stale"))
        target_age = _finite_float(feature_row.get("target_age_s"))
        target_age_ok = target_age is not None and -2.0 <= target_age <= 2.0
        target_spot = _finite_float(feature_row.get("target_spot"))
        contract_id = str(feature_row.get("contract_id") or "").strip() or None
        ticker = str(feature_row.get("ticker") or "").strip().upper()
        ticker_identity_ok = bool(
            feature_row.get("ticker_identity_ok") is True
            and ticker == market
            and normalize_sierra_symbol(contract_id) == market
        )
        peer_source = str(peer_confirmation.get("source") or "")
        peer_schema = str(peer_confirmation.get("schema_version") or "").strip()
        snapshot_healthy = peer_confirmation.get("snapshot_health") is True
        ready = _nonnegative_int(
            peer_confirmation.get("ready_groups", peer_confirmation.get("ready", 0))
        )
        aligned = _nonnegative_int(
            peer_confirmation.get("aligned_group_count", peer_confirmation.get("aligned", 0))
        )
        opposing = _nonnegative_int(
            peer_confirmation.get("opposing_group_count", peer_confirmation.get("opposing", 0))
        )
        required = max(
            2,
            _nonnegative_int(peer_confirmation.get("required_aligned"), default=2),
        )
        qqq_veto = bool(
            peer_confirmation.get("qqq_strong_opposition")
            or peer_confirmation.get("qqq_opposition")
            or peer_confirmation.get("veto")
        )
        snapshot_age = _finite_float(peer_confirmation.get("snapshot_age_s"))
        alert_session_open = feature_row.get("alert_session_open") is True
        supported_horizon = profile is not None and int(horizon_minutes) in profile.horizons_minutes
        primary_horizon = bool(
            profile is not None
            and profile.primary_alert_horizon_minutes is not None
            and int(horizon_minutes) == profile.primary_alert_horizon_minutes
        )

        if target_stale:
            risk_flags.append("stale_target_input")
        if not target_age_ok:
            risk_flags.append("target_input_age_invalid")
        if target_spot is None or target_spot <= 0.0:
            risk_flags.append("native_spot_missing")
        if input_mode not in {"l2_ofi", "trade_imbalance"}:
            risk_flags.append("target_input_mode_missing")
        if input_value is None:
            risk_flags.append("target_input_missing")
        if not input_sources_separate:
            risk_flags.append("target_input_mode_blended")
        if not input_value_matches:
            risk_flags.append("target_input_value_mismatch")
        if direction == "neutral":
            risk_flags.append("weak_directional_edge")
        if not ticker_identity_ok:
            risk_flags.append("ticker_identity_mismatch")
        if peer_source != "theta_equity_breadth_v1":
            risk_flags.append("theta_equity_breadth_required")
        if peer_schema != EQUITY_BREADTH_SCHEMA_VERSION:
            risk_flags.append("theta_equity_breadth_schema_invalid")
        if not snapshot_healthy:
            risk_flags.append("theta_equity_breadth_unhealthy")
        if snapshot_age is None or snapshot_age > 2.0 or snapshot_age < -2.0:
            risk_flags.append("theta_equity_breadth_stale")
        if ready < 2:
            risk_flags.append("equity_peer_groups_incomplete")
        if aligned < required:
            risk_flags.append("peer_alignment_below_rule_min")
        if qqq_veto:
            risk_flags.append("strong_opposing_qqq")
        if not supported_horizon:
            risk_flags.append("unsupported_product_horizon")
        if not primary_horizon:
            risk_flags.append("research_only_horizon")
        if not alert_session_open:
            risk_flags.append("outside_alert_session")

        data_blocked = bool(
            target_stale
            or not target_age_ok
            or target_spot is None
            or target_spot <= 0.0
            or input_mode not in {"l2_ofi", "trade_imbalance"}
            or input_value is None
            or not input_sources_separate
            or not input_value_matches
            or not ticker_identity_ok
            or peer_source != "theta_equity_breadth_v1"
            or peer_schema != EQUITY_BREADTH_SCHEMA_VERSION
            or not snapshot_healthy
            or snapshot_age is None
            or snapshot_age > 2.0
            or snapshot_age < -2.0
            or not supported_horizon
        )
        rule_pass = bool(
            direction in {"long", "short"}
            and ready >= 2
            and aligned >= required
            and not qqq_veto
            and peer_confirmation.get("confirmed") is True
        )
        candidate_eligible = bool(
            not data_blocked
            and rule_pass
            and alert_session_open
            and primary_horizon
        )
        posting_enabled = market in self._experimental_posting_enabled
        smoke_validated = market in self._experimental_smoke_validated
        if not posting_enabled:
            risk_flags.append("experimental_posting_disabled")
        if not smoke_validated:
            risk_flags.append("smoke_validation_required")

        if not alert_session_open:
            status = "watch"
            reason = "outside_alert_session"
        elif data_blocked:
            status = "blocked"
            reason = "equity_data_quality_gate"
        elif not rule_pass:
            status = "watch"
            reason = "rules_first_alignment_not_met"
        elif not primary_horizon:
            status = "watch"
            reason = "research_only_horizon"
        elif posting_enabled and smoke_validated:
            status = "post"
            reason = "rules_first_alignment"
        elif posting_enabled:
            status = "watch"
            reason = "smoke_validation_required"
        else:
            status = "watch"
            reason = "experimental_posting_disabled"

        rule_score_raw = feature_row.get("rule_score")
        try:
            rule_score = float(rule_score_raw) if rule_score_raw is not None else None
        except (TypeError, ValueError):
            rule_score = None
        layers = {
            "rules_first": {
                "input_mode": input_mode or None,
                "input_value_15s": input_value,
                "selected_source_value_15s": selected_value,
                "input_sources_separate": input_sources_separate,
                "input_value_matches_source": input_value_matches,
                "ready_groups": ready,
                "required_aligned": required,
                "aligned_groups": aligned,
                "opposing_groups": opposing,
                "qqq_strong_opposition": qqq_veto,
                "rule_pass": rule_pass,
                "candidate_eligible": candidate_eligible,
                "primary_alert_horizon": primary_horizon,
                "posting_enabled": posting_enabled,
                "smoke_validated": smoke_validated,
                "alert_session_open": alert_session_open,
            },
            "data_quality": {
                "target_stale": target_stale,
                "target_age_s": target_age,
                "target_age_ok": target_age_ok,
                "native_spot_present": target_spot is not None and target_spot > 0.0,
                "ticker_identity_ok": ticker_identity_ok,
                "theta_breadth_source": peer_source,
                "theta_breadth_schema_version": peer_schema or None,
                "theta_breadth_healthy": snapshot_healthy,
                "theta_breadth_age_s": snapshot_age,
            },
        }
        return DirectionSignal(
            market=market,
            timestamp_utc=ts,
            horizon_minutes=int(horizon_minutes),
            direction=direction,
            p_up=None,
            p_hit=None,
            confidence=None,
            status=status,
            reason=reason,
            layers=layers,
            risk_flags=risk_flags,
            profile_id=profile.profile_id if profile is not None else None,
            contract_id=contract_id,
            signal_kind="rules_first",
            validation_status="smoke_passed" if smoke_validated else "shadow_research",
            rule_score=rule_score,
            peer_confirmation=peer_confirmation,
            asset_class="equity",
            input_mode=input_mode or None,
            candidate_eligible=candidate_eligible,
        )

    def _evaluate_rules_first(
        self,
        feature_row: dict[str, Any],
        *,
        horizon_minutes: int,
    ) -> DirectionSignal:
        market = str(feature_row.get("market") or "").upper()
        if market in EQUITY_TARGET_MARKETS:
            return self._evaluate_equity_rules_first(
                feature_row,
                horizon_minutes=horizon_minutes,
            )
        ts = str(feature_row.get("timestamp_utc") or "")
        profile = product_profile(market)
        risk_flags = ["experimental_rules_first"]
        peer_confirmation = feature_row.get("peer_confirmation")
        if not isinstance(peer_confirmation, dict):
            peer_confirmation = {}

        target_stale = bool(feature_row.get("target_stale"))
        spread_raw = feature_row.get("target_spread")
        try:
            spread_ok = spread_raw is not None and float(spread_raw) <= self._max_spread(market)
        except (TypeError, ValueError):
            spread_ok = False
        contract_id = str(feature_row.get("contract_id") or "").strip() or None
        contract_postable = _has_explicit_contract_month_year(contract_id, market)
        target_norm_raw = feature_row.get("target_ofi_norm_15s")
        try:
            target_norm = float(target_norm_raw) if target_norm_raw is not None else None
        except (TypeError, ValueError):
            target_norm = None
        roll_warmup = bool(feature_row.get("roll_warmup_active"))
        alert_session_open = feature_row.get("alert_session_open") is not False
        ready = int(peer_confirmation.get("ready") or 0)
        aligned = int(
            peer_confirmation.get("aligned_primary_count", peer_confirmation.get("aligned", 0))
            or 0
        )
        opposing = int(
            peer_confirmation.get("opposing_primary_count", peer_confirmation.get("opposing", 0))
            or 0
        )
        required = int(
            peer_confirmation.get("required_aligned")
            or (profile.min_aligned_peers if profile is not None else 1)
        )
        strong_opposing = [
            str(value)
            for value in (peer_confirmation.get("strong_opposing_peers") or [])
            if str(value)
        ]
        strong_opposing_count = int(
            peer_confirmation.get("strong_opposing_primary_count", len(strong_opposing))
            or 0
        )
        rule_score_raw = feature_row.get("rule_score")
        try:
            rule_score = float(rule_score_raw) if rule_score_raw is not None else None
        except (TypeError, ValueError):
            rule_score = None

        direction = "neutral"
        if target_norm is not None and target_norm > 0.0:
            direction = "long"
        elif target_norm is not None and target_norm < 0.0:
            direction = "short"

        if target_stale:
            risk_flags.append("stale_target_ofi")
        if not spread_ok:
            risk_flags.append("spread_too_wide_or_missing")
        if contract_id is None:
            risk_flags.append("contract_id_missing")
        elif not contract_postable:
            risk_flags.append("contract_month_year_missing")
        if target_norm is None:
            risk_flags.append("normalized_target_ofi_missing")
        if direction == "neutral":
            risk_flags.append("weak_directional_edge")
        if ready < required:
            risk_flags.append("normalized_peer_data_incomplete")
        if aligned < required:
            risk_flags.append("peer_alignment_below_rule_min")
        if profile is not None and profile.block_strong_opposing_primary and strong_opposing_count > 0:
            risk_flags.append("strong_opposing_primary_peer")
        if roll_warmup:
            risk_flags.append("contract_roll_warmup")
        if profile is None or int(horizon_minutes) not in profile.horizons_minutes:
            risk_flags.append("unsupported_product_horizon")
        if not alert_session_open:
            risk_flags.append("outside_alert_session")

        data_blocked = (
            target_stale
            or not spread_ok
            or not contract_postable
            or target_norm is None
            or roll_warmup
            or profile is None
            or int(horizon_minutes) not in (profile.horizons_minutes if profile is not None else ())
        )
        rule_pass = (
            direction in {"long", "short"}
            and ready >= required
            and aligned >= required
            and not (
                profile is not None
                and profile.block_strong_opposing_primary
                and strong_opposing_count > 0
            )
        )
        posting_enabled = market in self._experimental_posting_enabled
        smoke_validated = market in self._experimental_smoke_validated
        if not posting_enabled:
            risk_flags.append("experimental_posting_disabled")
        if not smoke_validated:
            risk_flags.append("smoke_validation_required")

        if not alert_session_open:
            status = "watch"
            reason = "outside_alert_session"
        elif data_blocked:
            status = "blocked"
            reason = "experimental_data_quality_gate"
        elif not rule_pass:
            status = "watch"
            reason = "rules_first_alignment_not_met"
        elif posting_enabled and smoke_validated:
            status = "post"
            reason = "rules_first_alignment"
        elif posting_enabled:
            status = "watch"
            reason = "smoke_validation_required"
        else:
            status = "watch"
            reason = "experimental_posting_disabled"

        layers = {
            "rules_first": {
                "target_ofi_norm_15s": target_norm,
                "required_aligned": required,
                "ready": ready,
                "aligned": aligned,
                "opposing": opposing,
                "strong_opposing_peers": strong_opposing,
                "strong_opposing_primary_count": strong_opposing_count,
                "rule_pass": rule_pass,
                "posting_enabled": posting_enabled,
                "smoke_validated": smoke_validated,
                "alert_session_open": alert_session_open,
            },
            "data_quality": {
                "target_stale": target_stale,
                "spread": spread_raw,
                "spread_limit": self._max_spread(market),
                "contract_id_present": contract_id is not None,
                "contract_month_year_present": contract_postable,
                "roll_warmup_active": roll_warmup,
            },
        }
        return DirectionSignal(
            market=market,
            timestamp_utc=ts,
            horizon_minutes=int(horizon_minutes),
            direction=direction,
            p_up=None,
            p_hit=None,
            confidence=None,
            status=status,
            reason=reason,
            layers=layers,
            risk_flags=risk_flags,
            profile_id=profile.profile_id if profile is not None else None,
            contract_id=contract_id,
            signal_kind="rules_first",
            validation_status="smoke_passed" if smoke_validated else "experimental_unvalidated",
            rule_score=rule_score,
            peer_confirmation=peer_confirmation,
        )

    def evaluate(self, feature_row: dict[str, Any], *, horizon_minutes: int) -> DirectionSignal:
        market = str(feature_row.get("market") or "").upper()
        ts = str(feature_row.get("timestamp_utc") or "")
        profile = product_profile(market)
        if profile is not None and profile.rules_first:
            return self._evaluate_rules_first(feature_row, horizon_minutes=int(horizon_minutes))
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
            profile_id=profile.profile_id if profile is not None else None,
            contract_id=str(feature_row.get("contract_id") or "").strip() or None,
            signal_kind="model_probability",
            validation_status="live_ok" if horizon_live_ok else "research_only",
            rule_score=None,
            peer_confirmation=(
                feature_row.get("peer_confirmation")
                if isinstance(feature_row.get("peer_confirmation"), dict)
                else None
            ),
        )
