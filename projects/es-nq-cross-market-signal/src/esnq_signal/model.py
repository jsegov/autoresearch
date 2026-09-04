from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _num(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(parsed) or math.isinf(parsed):
        return 0.0
    return parsed


@dataclass(frozen=True)
class LinearLogitModel:
    feature_order: tuple[str, ...]
    means: tuple[float, ...]
    stds: tuple[float, ...]
    weights: tuple[float, ...]
    bias: float

    @staticmethod
    def from_dict(raw: dict[str, Any] | None) -> "LinearLogitModel | None":
        if not isinstance(raw, dict):
            return None
        order = raw.get("feature_order")
        means = raw.get("means")
        stds = raw.get("stds")
        weights = raw.get("weights")
        bias = _num(raw.get("bias"))
        if not isinstance(order, list) or not isinstance(means, list) or not isinstance(stds, list) or not isinstance(weights, list):
            return None
        if not order or not (len(order) == len(means) == len(stds) == len(weights)):
            return None
        return LinearLogitModel(
            feature_order=tuple(str(item) for item in order),
            means=tuple(_num(item) for item in means),
            stds=tuple(max(1e-9, _num(item)) for item in stds),
            weights=tuple(_num(item) for item in weights),
            bias=bias,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_order": list(self.feature_order),
            "means": list(self.means),
            "stds": list(self.stds),
            "weights": list(self.weights),
            "bias": self.bias,
        }

    def predict_proba(self, features: dict[str, float]) -> float:
        score = self.bias
        for idx, feature in enumerate(self.feature_order):
            value = _num(features.get(feature))
            z = (value - self.means[idx]) / self.stds[idx]
            score += z * self.weights[idx]
        return float(_sigmoid(score))


def build_model_features(feature_row: dict[str, Any], *, raw_score: float, p_up_heuristic: float, p_hit_heuristic: float) -> dict[str, float]:
    direction_gap = abs(float(p_up_heuristic) - 0.5)
    raw_edge = abs(float(raw_score))
    return {
        "raw_score": float(raw_score),
        "target_ofi_15s_z": _num(feature_row.get("target_ofi_15s_z")),
        "target_ofi_60s_z": _num(feature_row.get("target_ofi_60s_z")),
        "target_ofi_300s_z": _num(feature_row.get("target_ofi_300s_z")),
        "target_large_trade_signed_60s": _num(feature_row.get("target_large_trade_signed_60s")),
        "target_large_trade_signed_300s": _num(feature_row.get("target_large_trade_signed_300s")),
        "target_large_trade_count_60s": _num(feature_row.get("target_large_trade_count_60s")),
        "target_large_trade_count_300s": _num(feature_row.get("target_large_trade_count_300s")),
        "target_large_trade_buy_ratio_300s": _num(feature_row.get("target_large_trade_buy_ratio_300s")),
        "cross_index_confirmation": _num(feature_row.get("cross_index_confirmation")),
        "cross_index_contradiction": _num(feature_row.get("cross_index_contradiction")),
        "cross_index_balance": _num(feature_row.get("cross_index_balance")),
        "cross_index_lead_confirmation": _num(feature_row.get("cross_index_lead_confirmation")),
        "cross_index_lead_contradiction": _num(feature_row.get("cross_index_lead_contradiction")),
        "cross_index_lead_balance": _num(feature_row.get("cross_index_lead_balance")),
        "macro_regime_score": _num(feature_row.get("macro_regime_score")),
        "macro_lead_score": _num(feature_row.get("macro_lead_score")),
        "macro_ready": _num(feature_row.get("macro_ready")),
        "target_spread": _num(feature_row.get("target_spread")),
        "direction_gap": float(direction_gap),
        "raw_edge": float(raw_edge),
        "p_up_heuristic": float(p_up_heuristic),
        "p_hit_heuristic": float(p_hit_heuristic),
    }


def load_models(path: Path | None) -> dict[str, dict[int, dict[str, LinearLogitModel]]]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    root = payload.get("models")
    if not isinstance(root, dict):
        return {}
    out: dict[str, dict[int, dict[str, LinearLogitModel]]] = {}
    for market, by_horizon in root.items():
        if not isinstance(market, str) or not isinstance(by_horizon, dict):
            continue
        mkt = market.strip().upper()
        h_map: dict[int, dict[str, LinearLogitModel]] = {}
        for horizon_raw, models in by_horizon.items():
            try:
                horizon = int(horizon_raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(models, dict):
                continue
            p_up = LinearLogitModel.from_dict(models.get("p_up") if isinstance(models.get("p_up"), dict) else None)
            p_hit = LinearLogitModel.from_dict(models.get("p_hit") if isinstance(models.get("p_hit"), dict) else None)
            model_map: dict[str, LinearLogitModel] = {}
            if p_up is not None:
                model_map["p_up"] = p_up
            if p_hit is not None:
                model_map["p_hit"] = p_hit
            if model_map:
                h_map[horizon] = model_map
        if h_map:
            out[mkt] = h_map
    return out
