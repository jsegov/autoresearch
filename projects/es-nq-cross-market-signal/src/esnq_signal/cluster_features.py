from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

NUMERIC_FEATURES = (
    "p_hit",
    "p_up",
    "confidence",
    "target_spot",
    "target_spread",
    "target_ofi_5s",
    "target_ofi_15s",
    "target_ofi_60s",
    "target_ofi_300s",
    "target_ofi_15s_z",
    "target_ofi_60s_z",
    "target_ofi_300s_z",
    "target_large_trade_signed_60s",
    "target_large_trade_signed_300s",
    "target_large_trade_count_60s",
    "target_large_trade_count_300s",
    "target_large_trade_buy_ratio_300s",
    "cross_index_confirmation",
    "cross_index_contradiction",
    "cross_index_balance",
    "cross_index_lead_confirmation",
    "cross_index_lead_contradiction",
    "cross_index_lead_balance",
    "cross_index_score_avg",
    "cross_index_max_age_s",
    "macro_regime_score",
    "macro_lead_score",
    "macro_ready",
    "target_age_s",
    "direction_gap",
    "raw_edge",
    "p_up_model",
    "p_hit_model",
)

CATEGORICAL_FEATURES = (
    "market",
    "direction",
    "status",
    "reason",
    "session_bucket",
)

_ET = ZoneInfo("America/New_York")


def _num(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed == parsed else 0.0  # NaN guard


def _layer(payload: dict[str, Any], *path: str) -> Any:
    cur: Any = payload
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def session_bucket_from_timestamp(ts_utc: str) -> str:
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


def flatten_signal_features(payload: dict[str, Any]) -> dict[str, Any]:
    layers = payload.get("layers") if isinstance(payload.get("layers"), dict) else {}
    model = layers.get("model") if isinstance(layers.get("model"), dict) else {}
    context = layers.get("context") if isinstance(layers.get("context"), dict) else {}
    out: dict[str, Any] = dict(payload)
    out["session_bucket"] = context.get("session_bucket") or session_bucket_from_timestamp(
        str(payload.get("timestamp_utc") or "")
    )
    out["direction_gap"] = model.get("direction_gap")
    out["raw_edge"] = model.get("raw_edge")
    out["p_up_model"] = model.get("p_up_model")
    out["p_hit_model"] = model.get("p_hit_model")
    if out.get("cross_index_confirmation") is None:
        cross = layers.get("cross_index") if isinstance(layers.get("cross_index"), dict) else {}
        out["cross_index_confirmation"] = cross.get("confirmation")
        out["cross_index_contradiction"] = cross.get("contradiction")
        out["cross_index_lead_confirmation"] = cross.get("lead_confirmation")
        out["cross_index_lead_contradiction"] = cross.get("lead_contradiction")
    if out.get("target_ofi_60s_z") is None:
        ofi = layers.get("ofi") if isinstance(layers.get("ofi"), dict) else {}
        out["target_ofi_15s_z"] = ofi.get("z15")
        out["target_ofi_60s_z"] = ofi.get("z60")
        out["target_ofi_300s_z"] = ofi.get("z300")
    out["macro_regime_score"] = out.get("macro_regime_score", _layer(layers, "macro", "score"))
    out["macro_lead_score"] = out.get("macro_lead_score", _layer(layers, "macro", "lead_score"))
    out["macro_ready"] = out.get("macro_ready", _layer(layers, "macro", "ready"))
    return out


def encode_signal_vector(
    payload: dict[str, Any],
    *,
    scaler_mean: list[float],
    scaler_scale: list[float],
    categorical_categories: dict[str, list[str]],
) -> list[float]:
    flat = flatten_signal_features(payload)
    numeric = [_num(flat.get(key)) for key in NUMERIC_FEATURES]
    scaled = [
        (value - mean) / scale if scale else 0.0
        for value, mean, scale in zip(numeric, scaler_mean, scaler_scale, strict=True)
    ]
    cat_values: list[float] = []
    for feature in CATEGORICAL_FEATURES:
        raw = str(flat.get(feature) if flat.get(feature) is not None else "__missing__")
        categories = categorical_categories.get(feature, [])
        for category in categories:
            cat_values.append(1.0 if raw == category else 0.0)
    return [*scaled, *cat_values]
