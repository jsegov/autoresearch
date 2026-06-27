from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .signal import SignalThresholds


def load_thresholds_config(path: Path | None) -> dict[str, dict[int, SignalThresholds]]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    thresholds_block = payload.get("thresholds")
    if not isinstance(thresholds_block, dict):
        return {}
    out: dict[str, dict[int, SignalThresholds]] = {}
    for market, by_horizon in thresholds_block.items():
        if not isinstance(market, str) or not isinstance(by_horizon, dict):
            continue
        mkt = market.strip().upper()
        horizon_map: dict[int, SignalThresholds] = {}
        for horizon_raw, row in by_horizon.items():
            try:
                horizon = int(horizon_raw)
            except (TypeError, ValueError):
                continue
            if horizon <= 0:
                continue
            threshold = SignalThresholds.from_dict(row if isinstance(row, dict) else None)
            horizon_map[horizon] = threshold
        if horizon_map:
            out[mkt] = horizon_map
    return out


def serialize_thresholds_config(thresholds: dict[str, dict[int, SignalThresholds]]) -> dict[str, Any]:
    payload: dict[str, Any] = {"thresholds": {}}
    for market, by_horizon in thresholds.items():
        mkt = str(market).upper()
        payload["thresholds"][mkt] = {}
        for horizon, threshold in by_horizon.items():
            payload["thresholds"][mkt][str(int(horizon))] = {
                "post_p_hit_min": threshold.post_p_hit_min,
                "post_confidence_min": threshold.post_confidence_min,
                "post_cross_confirm_min": threshold.post_cross_confirm_min,
                "post_direction_gap_min": threshold.post_direction_gap_min,
                "post_raw_edge_min": threshold.post_raw_edge_min,
                "allowed_session": threshold.allowed_session,
                "allowed_direction": threshold.allowed_direction,
                "high_p_hit_min": threshold.high_p_hit_min,
                "high_confidence_min": threshold.high_confidence_min,
                "high_cross_confirm_min": threshold.high_cross_confirm_min,
                "high_direction_gap_min": threshold.high_direction_gap_min,
                "high_raw_edge_min": threshold.high_raw_edge_min,
            }
    return payload


def load_live_ok_horizons(path: Path | None) -> dict[str, set[int]]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    summary = payload.get("summary")
    if not isinstance(summary, list):
        return {}
    out: dict[str, set[int]] = {}
    for row in summary:
        if not isinstance(row, dict):
            continue
        market = str(row.get("market") or "").strip().upper()
        status = str(row.get("status") or "").strip().lower()
        try:
            horizon = int(row.get("horizon_minutes") or 0)
        except (TypeError, ValueError):
            continue
        if not market or horizon <= 0:
            continue
        if status != "live_ok":
            continue
        out.setdefault(market, set()).add(horizon)
    return out
