from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ShadowQualityGateConfig:
    enabled: bool = False
    apply_statuses: tuple[str, ...] = ("post", "high_priority")


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _layer(payload: dict[str, Any], *path: str) -> Any:
    cur: Any = payload
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _cross_confirm(payload: dict[str, Any]) -> int:
    value = payload.get("cross_index_confirmation")
    if value is None:
        value = _layer(payload, "layers", "cross_index", "confirmation")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _cross_contradiction(payload: dict[str, Any]) -> int:
    value = payload.get("cross_index_contradiction")
    if value is None:
        value = _layer(payload, "layers", "cross_index", "contradiction")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _ofi_60s_z(payload: dict[str, Any]) -> float | None:
    value = payload.get("target_ofi_60s_z")
    if value is None:
        value = _layer(payload, "layers", "ofi", "z60")
    return _num(value)


def _session_bucket(payload: dict[str, Any]) -> str:
    value = _layer(payload, "layers", "context", "session_bucket")
    return str(value or "unknown").upper()


def _ofi_aligned(direction: str, ofi60z: float | None) -> bool:
    if ofi60z is None:
        return False
    if direction == "long":
        return ofi60z > 0
    if direction == "short":
        return ofi60z < 0
    return False


def _append_if(condition: bool, failed: list[str], code: str) -> None:
    if condition:
        failed.append(code)


def _failed_es_5m(payload: dict[str, Any]) -> list[str]:
    failed: list[str] = []
    _append_if(_cross_contradiction(payload) != 0, failed, "cross_contradiction_not_zero")
    _append_if(_cross_confirm(payload) < 2, failed, "cross_confirm_below_2")
    direction = str(payload.get("direction") or "").lower()
    _append_if(not _ofi_aligned(direction, _ofi_60s_z(payload)), failed, "ofi_60s_not_aligned")
    return failed


def _failed_es_10m(payload: dict[str, Any]) -> list[str]:
    failed = _failed_es_5m(payload)
    direction_gap = _num(_layer(payload, "layers", "model", "direction_gap"))
    raw_edge = _num(_layer(payload, "layers", "model", "raw_edge"))
    confidence = _num(payload.get("confidence"))
    _append_if(direction_gap is None or direction_gap < 0.06, failed, "direction_gap_below_0_06")
    _append_if(raw_edge is None or raw_edge < 1.25, failed, "raw_edge_below_1_25")
    _append_if(confidence is None or confidence < 0.62, failed, "confidence_below_0_62")
    return failed


def _failed_nq_5m(payload: dict[str, Any]) -> list[str]:
    direction = str(payload.get("direction") or "").lower()
    failed: list[str] = []
    _append_if(not _ofi_aligned(direction, _ofi_60s_z(payload)), failed, "ofi_60s_not_aligned")
    return failed


def _failed_nq_10m(payload: dict[str, Any]) -> list[str]:
    session = _session_bucket(payload)
    if session == "RTH_CLOSE":
        failed: list[str] = []
        _append_if(_cross_contradiction(payload) != 0, failed, "cross_contradiction_not_zero")
        return failed

    failed = []
    _append_if(_cross_contradiction(payload) != 0, failed, "cross_contradiction_not_zero")
    _append_if(_cross_confirm(payload) < 2, failed, "cross_confirm_below_2")
    direction_gap = _num(_layer(payload, "layers", "model", "direction_gap"))
    raw_edge = _num(_layer(payload, "layers", "model", "raw_edge"))
    p_hit = _num(payload.get("p_hit"))
    _append_if(direction_gap is None or direction_gap < 0.06, failed, "direction_gap_below_0_06")
    _append_if(raw_edge is None or raw_edge < 1.25, failed, "raw_edge_below_1_25")
    _append_if(p_hit is None or p_hit < 0.72, failed, "p_hit_below_0_72")
    return failed


_PROFILE_BY_KEY: dict[tuple[str, int], str] = {
    ("ES", 5): "es_5m_cross_ofi",
    ("ES", 10): "es_10m_combo",
    ("NQ", 5): "nq_5m_ofi_only",
    ("NQ", 10): "nq_10m_session_split",
}

_EVALUATORS = {
    ("ES", 5): _failed_es_5m,
    ("ES", 10): _failed_es_10m,
    ("NQ", 5): _failed_nq_5m,
    ("NQ", 10): _failed_nq_10m,
}


def evaluate_shadow_quality_gate(payload: dict[str, Any]) -> dict[str, Any]:
    market = str(payload.get("market") or "").upper()
    try:
        horizon = int(payload.get("horizon_minutes") or 0)
    except (TypeError, ValueError):
        horizon = 0
    profile = _PROFILE_BY_KEY.get((market, horizon))
    evaluator = _EVALUATORS.get((market, horizon))
    if profile is None or evaluator is None:
        return {
            "passed": True,
            "profile": "none",
            "session_bucket": _session_bucket(payload),
            "failed_checks": [],
        }

    failed_checks = evaluator(payload)
    return {
        "passed": not failed_checks,
        "profile": profile,
        "session_bucket": _session_bucket(payload),
        "failed_checks": failed_checks,
    }


def apply_shadow_quality_gate(payload: dict[str, Any], *, config: ShadowQualityGateConfig) -> dict[str, Any]:
    if not config.enabled:
        return {"passed": True, "profile": "disabled", "failed_checks": [], "applied": False}

    status = str(payload.get("status") or "").lower()
    if status not in config.apply_statuses:
        return {"passed": True, "profile": "not_applicable", "failed_checks": [], "applied": False}

    meta = evaluate_shadow_quality_gate(payload)
    meta["applied"] = True
    meta["original_status"] = status
    meta["original_reason"] = str(payload.get("reason") or "")

    layers = payload.get("layers")
    if not isinstance(layers, dict):
        layers = {}
        payload["layers"] = layers
    layers["shadow_quality_gate"] = meta

    if meta["passed"]:
        return meta

    payload["status"] = "watch"
    payload["reason"] = "shadow_quality_gate"
    flags = payload.get("risk_flags")
    if not isinstance(flags, list):
        flags = []
        payload["risk_flags"] = flags
    if "shadow_quality_gate" not in flags:
        flags.append("shadow_quality_gate")
    return meta
