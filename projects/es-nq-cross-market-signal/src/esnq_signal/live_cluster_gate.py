from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cluster_features import encode_signal_vector


@dataclass(frozen=True)
class LiveClusterGateConfig:
    enabled: bool = False
    apply_statuses: tuple[str, ...] = ("post", "high_priority")
    models_file: Path | None = None


def _lane_key(market: str, horizon_minutes: int) -> str:
    return f"{str(market).upper()}_{int(horizon_minutes)}m"


def _nearest_cluster(vector: list[float], centroids: list[list[float]]) -> int:
    best_id = 0
    best_dist = math.inf
    for cluster_id, centroid in enumerate(centroids):
        dist = sum((left - right) ** 2 for left, right in zip(vector, centroid, strict=True))
        if dist < best_dist:
            best_dist = dist
            best_id = cluster_id
    return best_id


class LiveClusterGate:
    def __init__(self, models: dict[str, Any]) -> None:
        self._models = models

    @classmethod
    def from_file(cls, path: Path | None) -> LiveClusterGate | None:
        if path is None or not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        return cls(payload)

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        market = str(payload.get("market") or "").upper()
        try:
            horizon = int(payload.get("horizon_minutes") or 0)
        except (TypeError, ValueError):
            horizon = 0
        key = _lane_key(market, horizon)
        lane = self._models.get("lanes", {}).get(key)
        if not isinstance(lane, dict):
            return {"passed": True, "profile": "none", "lane": key, "failed_checks": []}

        vector = encode_signal_vector(
            payload,
            scaler_mean=[float(value) for value in lane["scaler_mean"]],
            scaler_scale=[float(value) for value in lane["scaler_scale"]],
            categorical_categories={
                str(feature): [str(item) for item in values]
                for feature, values in lane["categorical_categories"].items()
            },
        )
        centroids = lane["centroids"]
        cluster_id = _nearest_cluster(vector, centroids)
        best_cluster_id = int(lane["best_cluster_id"])
        stats = lane.get("best_cluster_stats") if isinstance(lane.get("best_cluster_stats"), dict) else {}
        passed = cluster_id == best_cluster_id
        return {
            "passed": passed,
            "profile": f"{key}_cluster_{best_cluster_id}",
            "lane": key,
            "assigned_cluster_id": cluster_id,
            "best_cluster_id": best_cluster_id,
            "best_cluster_hit_rate": stats.get("hit_rate"),
            "best_cluster_n": stats.get("n"),
            "failed_checks": [] if passed else [f"not_in_best_cluster_{best_cluster_id}"],
        }


def apply_live_cluster_gate(payload: dict[str, Any], *, gate: LiveClusterGate | None, config: LiveClusterGateConfig) -> dict[str, Any]:
    if not config.enabled or gate is None:
        return {"passed": True, "profile": "disabled", "failed_checks": [], "applied": False}

    status = str(payload.get("status") or "").lower()
    if status not in config.apply_statuses:
        return {"passed": True, "profile": "not_applicable", "failed_checks": [], "applied": False}

    meta = gate.evaluate(payload)
    meta["applied"] = True
    meta["original_status"] = status
    meta["original_reason"] = str(payload.get("reason") or "")

    layers = payload.get("layers")
    if not isinstance(layers, dict):
        layers = {}
        payload["layers"] = layers
    layers["live_cluster_gate"] = meta

    if meta["passed"]:
        return meta

    payload["status"] = "watch"
    payload["reason"] = "live_cluster_gate"
    flags = payload.get("risk_flags")
    if not isinstance(flags, list):
        flags = []
        payload["risk_flags"] = flags
    if "live_cluster_gate" not in flags:
        flags.append("live_cluster_gate")
    return meta
