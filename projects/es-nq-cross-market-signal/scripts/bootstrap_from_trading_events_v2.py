from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _clamp01(value: float | None, default: float = 0.5) -> float:
    if value is None:
        return default
    return max(0.0, min(1.0, float(value)))


def _direction_from_features(features: dict[str, Any], alpha_signal: dict[str, Any]) -> int:
    direction = features.get("signal_direction")
    parsed = _num(direction)
    if parsed is not None:
        if parsed > 0:
            return 1
        if parsed < 0:
            return -1
    klass = str((alpha_signal or {}).get("class") or "").upper()
    if "LONG" in klass or "PUT_WALL" in klass:
        return 1
    if "SHORT" in klass or "CALL_WALL" in klass:
        return -1
    score = _num((alpha_signal or {}).get("score"))
    if score is None:
        return 0
    return 1 if score > 0 else (-1 if score < 0 else 0)


def _direction_text(sign: int) -> str:
    if sign > 0:
        return "long"
    if sign < 0:
        return "short"
    return "neutral"


def _safe_jsonl_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, default=str) + "\n")


def _macro_score(features: dict[str, Any]) -> int:
    score = 0
    term_state = str(features.get("term_state") or "").upper()
    premium_state = str(features.get("premium_state") or "").upper()
    skew = str(features.get("iv_skew_bias") or "").lower()
    gamma_pin = str(features.get("gamma_pin_risk") or "").lower()
    if term_state == "CONTANGO":
        score += 1
    elif term_state == "BACKWARDATION":
        score -= 1
    if premium_state == "CHEAP":
        score += 1
    elif premium_state == "EXPENSIVE":
        score -= 1
    if skew == "upside":
        score += 1
    elif skew == "downside":
        score -= 1
    if gamma_pin == "elevated":
        score -= 1
    return score


def _status_from_stage(stage: str, decision: dict[str, Any]) -> str:
    action = str(decision.get("action") or "").strip().lower()
    if action in {"post", "high_priority", "watch", "suppress"}:
        if action == "suppress":
            return "blocked"
        return action
    stage_l = stage.lower()
    if "suppress" in stage_l:
        return "blocked"
    if "policy_decision" in stage_l:
        return "watch"
    return "watch"


def _normalize_source_outcomes(source_outcomes: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(source_outcomes, dict):
        return None

    def _direct_block(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        fwd_ret = _num(raw.get("fwd_ret"))
        hit = raw.get("directional_hit")
        if fwd_ret is None:
            return None
        out: dict[str, Any] = {
            "fwd_ret": float(fwd_ret),
            "directional_hit": hit if isinstance(hit, bool) else None,
        }
        if _num(raw.get("future_spot")) is not None:
            out["future_spot"] = float(_num(raw.get("future_spot")) or 0.0)
        return out

    normalized: dict[str, Any] = {}
    # Format A: already in { "5m": {...}, "10m": {...} } style.
    for key in ("5m", "10m"):
        block = _direct_block(source_outcomes.get(key))
        if block is not None:
            normalized[key] = block

    # Format B: v2 label payload with outcomes.horizons.* blocks.
    horizons = source_outcomes.get("horizons")
    if isinstance(horizons, dict):
        for key in ("5m", "10m"):
            block = horizons.get(key)
            if not isinstance(block, dict):
                continue
            directional_return_pct = _num(block.get("directional_return_pct"))
            simple_return_pct = _num(block.get("simple_return_pct"))
            ret_pct = directional_return_pct if directional_return_pct is not None else simple_return_pct
            if ret_pct is None:
                continue
            forward_price = _num(block.get("forward_price"))
            hit = block.get("directional_hit")
            normalized[key] = {
                # v2 labeled files store return in percent units (e.g., 0.0068 means 0.0068%).
                "fwd_ret": float(ret_pct) / 100.0,
                "future_spot": float(forward_price) if forward_price is not None else None,
                "directional_hit": hit if isinstance(hit, bool) else None,
            }

    return normalized if normalized else None


def _derive_probabilities(
    *,
    direction_sign: int,
    decision: dict[str, Any],
    alpha_signal: dict[str, Any],
    meta_label: dict[str, Any],
) -> tuple[float, float, float]:
    confidence = _num(decision.get("confidence"))
    confidence_prob = (confidence / 100.0) if confidence is not None else None
    p_hit = _num(meta_label.get("probability"))
    if p_hit is None:
        p_hit = confidence_prob
    if p_hit is None:
        score = abs(_num(alpha_signal.get("score")) or 0.0)
        p_hit = 1.0 / (1.0 + math.exp(-(score - 1.0)))
    p_hit = _clamp01(p_hit, default=0.5)

    if direction_sign > 0:
        p_up = 0.5 + (0.5 * p_hit)
    elif direction_sign < 0:
        p_up = 0.5 - (0.5 * p_hit)
    else:
        p_up = 0.5
    p_up = _clamp01(p_up, default=0.5)

    confidence_out = _clamp01(confidence_prob, default=p_hit)
    return p_up, p_hit, confidence_out


def _build_feature_row(features: dict[str, Any], ts_utc: str, market: str) -> dict[str, Any] | None:
    spot = _num(features.get("spot"))
    if spot is None:
        return None
    ofi = _num(features.get("ofi_1s"))
    if ofi is None:
        ofi = _num(features.get("ofi_proxy_combo"))
    ofi_15 = ofi if ofi is not None else _num(features.get("ofi_proxy_combo"))
    ofi_60 = _num(features.get("ofi_proxy_combo"))
    ofi_300 = _num(features.get("convexity_orderflow"))
    cross_confirm = int(_num(features.get("alignment_count")) or 0)
    cross_contra = int(_num(features.get("contradiction_count")) or 0)
    macro_score = _macro_score(features)

    return {
        "timestamp_utc": ts_utc,
        "market": market,
        "status": "ok",
        "target_age_s": _num(features.get("ofi_sample_age_s")),
        "target_stale": bool(features.get("ofi_stale")) if features.get("ofi_stale") is not None else False,
        "target_spot": spot,
        "target_spread": _num(features.get("book_spread")),
        "target_ofi_5s": ofi,
        "target_ofi_15s": ofi_15,
        "target_ofi_60s": ofi_60,
        "target_ofi_300s": ofi_300,
        "target_ofi_600s": _num(features.get("gex_orderflow")),
        "target_ofi_15s_z": ofi_15,
        "target_ofi_60s_z": ofi_60,
        "target_ofi_300s_z": ofi_300,
        "target_direction_hint": int(_num(features.get("signal_direction")) or 0),
        "cross_index_ready": max(0, cross_confirm + cross_contra),
        "cross_index_confirmation": max(0, cross_confirm),
        "cross_index_contradiction": max(0, cross_contra),
        "cross_index_balance": cross_confirm - cross_contra,
        "cross_index_lead_confirmation": max(0, cross_confirm),
        "cross_index_lead_contradiction": max(0, cross_contra),
        "cross_index_lead_balance": cross_confirm - cross_contra,
        "cross_index_score_avg": _num(features.get("ofi_proxy_combo")),
        "cross_index_values": {
            "ES": _num(features.get("ofi_proxy_combo")) if market == "NQ" else None,
            "NQ": _num(features.get("ofi_proxy_combo")) if market == "ES" else None,
        },
        "cross_index_lag30_values": {
            "ES": _num(features.get("ofi_proxy_combo")) if market == "NQ" else None,
            "NQ": _num(features.get("ofi_proxy_combo")) if market == "ES" else None,
        },
        "cross_index_max_age_s": _num(features.get("ofi_sample_age_s")),
        "macro_ready": 1 if macro_score != 0 else 0,
        "macro_regime_score": macro_score,
        "macro_lead_score": macro_score,
        "macro_votes": {},
        "macro_ofi_values": {},
        "macro_lead_votes": {},
        "macro_lag30_ofi_values": {},
    }


def _build_signal_row(
    *,
    features: dict[str, Any],
    alpha_signal: dict[str, Any],
    decision: dict[str, Any],
    meta_label: dict[str, Any],
    ts_utc: str,
    market: str,
    horizon_minutes: int,
    status: str,
    source_outcomes: dict[str, Any] | None,
) -> dict[str, Any]:
    direction_sign = _direction_from_features(features, alpha_signal)
    direction = _direction_text(direction_sign)
    p_up, p_hit, confidence = _derive_probabilities(
        direction_sign=direction_sign,
        decision=decision,
        alpha_signal=alpha_signal,
        meta_label=meta_label,
    )
    raw_score = _num(alpha_signal.get("score"))
    layers = {
        "direction_core_raw": round(float(raw_score or 0.0), 4),
        "ofi": {
            "z15": _num(features.get("ofi_1s")) if _num(features.get("ofi_1s")) is not None else _num(features.get("ofi_proxy_combo")),
            "z60": _num(features.get("ofi_proxy_combo")),
            "z300": _num(features.get("convexity_orderflow")),
        },
        "cross_index": {
            "confirmation": int(_num(features.get("alignment_count")) or 0),
            "contradiction": int(_num(features.get("contradiction_count")) or 0),
            "lead_confirmation": int(_num(features.get("alignment_count")) or 0),
            "lead_contradiction": int(_num(features.get("contradiction_count")) or 0),
            "support_used": int(_num(features.get("alignment_count")) or 0),
        },
        "macro": {
            "score": _macro_score(features),
            "lead_score": _macro_score(features),
            "ready": 1 if _macro_score(features) != 0 else 0,
        },
        "model": {
            "p_up_model_used": False,
            "p_hit_model_used": False,
            "p_up_heuristic": p_up,
            "p_hit_heuristic": p_hit,
            "p_up_model": None,
            "p_hit_model": None,
        },
    }
    out = {
        "market": market,
        "timestamp_utc": ts_utc,
        "horizon_minutes": int(horizon_minutes),
        "direction": direction,
        "p_up": p_up,
        "p_hit": p_hit,
        "confidence": confidence,
        "status": status,
        "reason": f"bootstrap_from_candidates:{status}",
        "layers": layers,
        "risk_flags": [],
        "target_spot": _num(features.get("spot")),
        "target_spread": _num(features.get("book_spread")),
    }
    if isinstance(source_outcomes, dict):
        out["outcomes"] = source_outcomes
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap standalone feature/signal logs from trading-events-v2 labeled candidates."
    )
    parser.add_argument(
        "--source",
        default="C:/Users/14342/autoresearch-win-rtx/projects/trading-events-v2/data/gexbot_v2_candidates.labeled.jsonl",
    )
    parser.add_argument("--feature-log", default="data/cross_market_features.jsonl")
    parser.add_argument("--signal-log", default="data/es_nq_direction_signals.jsonl")
    parser.add_argument("--reset", action="store_true", help="Truncate output files before writing")
    parser.add_argument("--max-rows", type=int, default=0, help="0 means no limit")
    parser.add_argument("--include-blocked", action="store_true", help="Include blocked/watch rows in signal log")
    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.exists():
        raise SystemExit(f"source file not found: {source_path}")
    feature_log = Path(args.feature_log)
    signal_log = Path(args.signal_log)

    if args.reset:
        feature_log.parent.mkdir(parents=True, exist_ok=True)
        signal_log.parent.mkdir(parents=True, exist_ok=True)
        feature_log.write_text("", encoding="utf-8")
        signal_log.write_text("", encoding="utf-8")

    processed = 0
    feature_rows = 0
    signal_rows = 0

    with source_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue

            market = str(row.get("market") or "").upper()
            if market not in {"ES", "NQ"}:
                continue
            features = row.get("features")
            if not isinstance(features, dict):
                continue
            ts_utc = str(features.get("timestamp_utc") or row.get("context_timestamp_utc") or "").strip()
            if not ts_utc:
                continue
            alpha_signal = row.get("alpha_signal") if isinstance(row.get("alpha_signal"), dict) else {}
            decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
            meta_label = row.get("meta_label") if isinstance(row.get("meta_label"), dict) else {}
            source_outcomes = _normalize_source_outcomes(
                row.get("outcomes") if isinstance(row.get("outcomes"), dict) else None
            )
            stage = str(row.get("stage") or "")
            status = _status_from_stage(stage, decision)

            feature_row = _build_feature_row(features, ts_utc, market)
            if feature_row is None:
                continue
            _safe_jsonl_write(feature_log, feature_row)
            feature_rows += 1

            if args.include_blocked or status in {"post", "high_priority", "watch"}:
                for horizon in (5, 10):
                    signal_row = _build_signal_row(
                        features=features,
                        alpha_signal=alpha_signal,
                        decision=decision,
                        meta_label=meta_label,
                        ts_utc=ts_utc,
                        market=market,
                        horizon_minutes=horizon,
                        status=status,
                        source_outcomes=source_outcomes,
                    )
                    _safe_jsonl_write(signal_log, signal_row)
                    signal_rows += 1

            processed += 1
            if args.max_rows > 0 and processed >= args.max_rows:
                break

    print(f"processed={processed}")
    print(f"feature_rows={feature_rows}")
    print(f"signal_rows={signal_rows}")
    print(f"wrote feature_log={feature_log}")
    print(f"wrote signal_log={signal_log}")


if __name__ == "__main__":
    main()
