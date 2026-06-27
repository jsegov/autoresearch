from __future__ import annotations

from esnq_signal.shadow_quality_gate import (
    ShadowQualityGateConfig,
    apply_shadow_quality_gate,
    evaluate_shadow_quality_gate,
)


def _base_payload(**overrides: object) -> dict:
    payload = {
        "market": "ES",
        "horizon_minutes": 5,
        "direction": "long",
        "status": "post",
        "reason": "trade_filter_pass",
        "p_hit": 0.80,
        "confidence": 0.70,
        "cross_index_confirmation": 2,
        "cross_index_contradiction": 0,
        "target_ofi_60s_z": 1.2,
        "risk_flags": [],
        "layers": {
            "cross_index": {"confirmation": 2, "contradiction": 0},
            "context": {"session_bucket": "RTH_OPEN"},
            "model": {"direction_gap": 0.08, "raw_edge": 1.5},
            "ofi": {"z60": 1.2},
        },
    }
    payload.update(overrides)
    return payload


def test_es_5m_passes_when_cross_clean_and_ofi_aligned() -> None:
    meta = evaluate_shadow_quality_gate(_base_payload())
    assert meta["passed"] is True
    assert meta["profile"] == "es_5m_cross_ofi"


def test_es_5m_fails_on_cross_contradiction() -> None:
    payload = _base_payload(cross_index_contradiction=1)
    payload["layers"]["cross_index"]["contradiction"] = 1
    meta = evaluate_shadow_quality_gate(payload)
    assert meta["passed"] is False
    assert "cross_contradiction_not_zero" in meta["failed_checks"]


def test_es_5m_fails_when_ofi_not_aligned() -> None:
    payload = _base_payload(direction="long", target_ofi_60s_z=-0.5)
    payload["layers"]["ofi"]["z60"] = -0.5
    meta = evaluate_shadow_quality_gate(payload)
    assert meta["passed"] is False
    assert "ofi_60s_not_aligned" in meta["failed_checks"]


def test_es_10m_requires_combo_thresholds() -> None:
    payload = _base_payload(horizon_minutes=10, confidence=0.55)
    payload["layers"]["model"]["direction_gap"] = 0.04
    meta = evaluate_shadow_quality_gate(payload)
    assert meta["profile"] == "es_10m_combo"
    assert meta["passed"] is False
    assert "confidence_below_0_62" in meta["failed_checks"]
    assert "direction_gap_below_0_06" in meta["failed_checks"]


def test_nq_5m_requires_ofi_alignment_only() -> None:
    payload = _base_payload(market="NQ", horizon_minutes=5, target_ofi_60s_z=0.4)
    payload["layers"]["ofi"]["z60"] = 0.4
    meta = evaluate_shadow_quality_gate(payload)
    assert meta["passed"] is True

    payload = _base_payload(market="NQ", horizon_minutes=5, direction="short", target_ofi_60s_z=0.4)
    payload["layers"]["ofi"]["z60"] = 0.4
    meta = evaluate_shadow_quality_gate(payload)
    assert meta["passed"] is False


def test_nq_10m_session_split_close_is_looser() -> None:
    payload = _base_payload(
        market="NQ",
        horizon_minutes=10,
        confidence=0.50,
        p_hit=0.60,
        cross_index_confirmation=1,
    )
    payload["layers"]["context"]["session_bucket"] = "RTH_CLOSE"
    payload["layers"]["cross_index"]["confirmation"] = 1
    payload["layers"]["model"]["direction_gap"] = 0.02
    payload["layers"]["model"]["raw_edge"] = 0.5
    meta = evaluate_shadow_quality_gate(payload)
    assert meta["passed"] is True


def test_nq_10m_open_mid_requires_strict_combo() -> None:
    payload = _base_payload(
        market="NQ",
        horizon_minutes=10,
        confidence=0.50,
        p_hit=0.60,
        cross_index_confirmation=1,
    )
    payload["layers"]["context"]["session_bucket"] = "RTH_OPEN"
    payload["layers"]["cross_index"]["confirmation"] = 1
    payload["layers"]["model"]["direction_gap"] = 0.02
    payload["layers"]["model"]["raw_edge"] = 0.5
    meta = evaluate_shadow_quality_gate(payload)
    assert meta["passed"] is False
    assert "cross_confirm_below_2" in meta["failed_checks"]
    assert "p_hit_below_0_72" in meta["failed_checks"]


def test_apply_downgrades_post_to_watch_when_gate_fails() -> None:
    payload = _base_payload(cross_index_contradiction=1)
    payload["layers"]["cross_index"]["contradiction"] = 1
    meta = apply_shadow_quality_gate(
        payload,
        config=ShadowQualityGateConfig(enabled=True, apply_statuses=("post", "high_priority")),
    )
    assert meta["applied"] is True
    assert payload["status"] == "watch"
    assert payload["reason"] == "shadow_quality_gate"
    assert "shadow_quality_gate" in payload["risk_flags"]
    assert payload["layers"]["shadow_quality_gate"]["original_status"] == "post"


def test_apply_leaves_watch_status_untouched() -> None:
    payload = _base_payload(status="watch", reason="below_post_threshold", cross_index_contradiction=1)
    payload["layers"]["cross_index"]["contradiction"] = 1
    meta = apply_shadow_quality_gate(
        payload,
        config=ShadowQualityGateConfig(enabled=True, apply_statuses=("post", "high_priority")),
    )
    assert meta["applied"] is False
    assert payload["status"] == "watch"
    assert payload["reason"] == "below_post_threshold"
