from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_gexbot_market_context(cache_file: Path | None, market: str) -> dict[str, Any] | None:
    if cache_file is None or not cache_file.exists():
        return None
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    markets = payload.get("markets") if isinstance(payload, dict) else None
    if not isinstance(markets, dict):
        return None
    context = markets.get(str(market or "").upper())
    return context if isinstance(context, dict) else None


def nearest_gex_key_name(context: dict[str, Any] | None) -> str | None:
    if not isinstance(context, dict):
        return None
    flows = context.get("flows") if isinstance(context.get("flows"), dict) else {}
    levels = context.get("key_levels") if isinstance(context.get("key_levels"), dict) else {}
    spot = _num(levels.get("spot"))
    if spot is None:
        return None
    candidates: list[tuple[str, float]] = []
    for name in ("zero_gamma", "major_call", "major_put"):
        level = _num(levels.get(name))
        if level is None:
            continue
        distance_pct = abs(level - spot) / spot * 100.0
        candidates.append((name, distance_pct))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[1])
    return candidates[0][0]


def prop_alpha_flags_from_context(context: dict[str, Any] | None) -> set[str]:
    flags: set[str] = set()
    if not isinstance(context, dict):
        return flags
    bus = context.get("sierra_signal_bus")
    if not isinstance(bus, dict):
        return flags
    events: list[dict[str, Any]] = []
    for bucket in ("active_signals", "recent_events"):
        raw = bus.get(bucket)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, dict):
                events.append(item)
    for event in events:
        signal_name = str(event.get("signal_name") or "").upper()
        features = event.get("features") if isinstance(event.get("features"), dict) else {}
        if signal_name == "IFVG_BEAR_BREAK":
            timeframe = str(features.get("timeframe") or features.get("fvg_timeframe") or "").lower()
            if _truthy(features.get("mth_fvg_bearish_break")) or timeframe in {"mth", "monthly", "month"}:
                flags.add("pa__ifvg__mth_fvg_bearish_break")
        if _truthy(features.get("mth_fvg_bearish_break")):
            flags.add("pa__ifvg__mth_fvg_bearish_break")
    return flags


def evaluate_cross_market_anomaly_overlay(
    *,
    signal_payload: dict[str, Any],
    gexbot_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    horizon = int(signal_payload.get("horizon_minutes") or 0)
    status = str(signal_payload.get("status") or "").lower()
    if horizon != 5 or status not in {"post", "high_priority"}:
        return None
    pa_flags = prop_alpha_flags_from_context(gexbot_context)
    if "pa__ifvg__mth_fvg_bearish_break" not in pa_flags:
        return None
    nearest_key = nearest_gex_key_name(gexbot_context)
    if nearest_key != "zero_gamma":
        return None
    market = str(signal_payload.get("market") or "").upper()
    return {
        "profile": "cross_market_zero_gamma_ifvg",
        "source": "cross_market_anomaly_overlay",
        "label": "Cross-market 5m + monthly IFVG bear break + nearby zero gamma",
        "matched": True,
        "market": market,
        "horizon_minutes": horizon,
        "status": status,
        "nearest_gex_key": nearest_key,
        "pa_flags": sorted(pa_flags),
        "reason": (
            "xm 5m alert + pa__ifvg__mth_fvg_bearish_break + gex__nearest_key=zero_gamma"
        ),
    }


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) != 0.0
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on", "active", "positive", "bull", "bullish"}
