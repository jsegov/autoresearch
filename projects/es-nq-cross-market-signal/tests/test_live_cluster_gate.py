from __future__ import annotations

import json
from pathlib import Path

from esnq_signal.live_cluster_gate import LiveClusterGate, LiveClusterGateConfig, apply_live_cluster_gate

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "data" / "live_cluster_models_2026-06-14.json"


def _sample_payload(**overrides: object) -> dict:
    payload = {
        "market": "ES",
        "horizon_minutes": 5,
        "timestamp_utc": "2026-05-29T14:30:00.000+00:00",
        "direction": "long",
        "status": "post",
        "reason": "trade_filter_pass",
        "p_hit": 0.80,
        "p_up": 0.72,
        "confidence": 0.70,
        "target_spot": 5900.0,
        "target_spread": 0.25,
        "target_ofi_5s": 1.0,
        "target_ofi_15s": 1.5,
        "target_ofi_60s": 2.0,
        "target_ofi_300s": 3.0,
        "target_ofi_15s_z": 1.2,
        "target_ofi_60s_z": 1.0,
        "target_ofi_300s_z": 0.5,
        "cross_index_confirmation": 2,
        "cross_index_contradiction": 0,
        "cross_index_balance": 2,
        "cross_index_lead_confirmation": 1,
        "cross_index_lead_contradiction": 0,
        "cross_index_lead_balance": 1,
        "cross_index_score_avg": 1.5,
        "cross_index_max_age_s": 5.0,
        "macro_regime_score": 1,
        "macro_lead_score": 0,
        "macro_ready": 4,
        "target_age_s": 0.5,
        "risk_flags": [],
        "layers": {
            "cross_index": {"confirmation": 2, "contradiction": 0},
            "context": {"session_bucket": "RTH_OPEN"},
            "model": {
                "direction_gap": 0.08,
                "raw_edge": 1.5,
                "p_up_model": 0.72,
                "p_hit_model": 0.80,
            },
            "ofi": {"z15": 1.2, "z60": 1.0, "z300": 0.5},
        },
    }
    payload.update(overrides)
    return payload


def test_live_cluster_models_file_loads() -> None:
    gate = LiveClusterGate.from_file(MODELS)
    assert gate is not None
    payload = json.loads(MODELS.read_text(encoding="utf-8"))
    assert set(payload["lanes"].keys()) == {"ES_5m", "ES_10m", "NQ_5m", "NQ_10m"}


def test_best_cluster_ids_match_research() -> None:
    payload = json.loads(MODELS.read_text(encoding="utf-8"))
    lanes = payload["lanes"]
    assert lanes["ES_5m"]["best_cluster_stats"]["hit_rate"] > 0.60
    assert lanes["ES_10m"]["best_cluster_stats"]["hit_rate"] > 0.61
    assert lanes["NQ_5m"]["best_cluster_id"] == 0
    assert lanes["NQ_5m"]["best_cluster_stats"]["hit_rate"] >= 0.607
    assert lanes["NQ_5m"]["best_cluster_stats"]["n"] >= 50
    assert lanes["NQ_10m"]["best_cluster_stats"]["hit_rate"] > 0.51


def test_apply_disabled_leaves_post_status() -> None:
    gate = LiveClusterGate.from_file(MODELS)
    payload = _sample_payload()
    meta = apply_live_cluster_gate(
        payload,
        gate=gate,
        config=LiveClusterGateConfig(enabled=False, apply_statuses=("post", "high_priority"), models_file=MODELS),
    )
    assert meta["applied"] is False
    assert payload["status"] == "post"


def test_apply_assigns_cluster_metadata() -> None:
    gate = LiveClusterGate.from_file(MODELS)
    assert gate is not None
    payload = json.loads(MODELS.read_text(encoding="utf-8"))
    expected_best = int(payload["lanes"]["ES_5m"]["best_cluster_id"])
    sample = _sample_payload()
    meta = gate.evaluate(sample)
    assert meta["lane"] == "ES_5m"
    assert isinstance(meta["assigned_cluster_id"], int)
    assert meta["best_cluster_id"] == expected_best


def test_apply_downgrades_when_not_in_best_cluster() -> None:
    gate = LiveClusterGate.from_file(MODELS)
    payload = _sample_payload()
    meta = apply_live_cluster_gate(
        payload,
        gate=gate,
        config=LiveClusterGateConfig(enabled=True, apply_statuses=("post", "high_priority"), models_file=MODELS),
    )
    assert "live_cluster_gate" in payload.get("layers", {})
    if not meta["passed"]:
        assert payload["status"] == "watch"
        assert payload["reason"] == "live_cluster_gate"
        assert "live_cluster_gate" in payload["risk_flags"]


def test_labeled_alert_can_match_best_nq_5m_cluster() -> None:
    gate = LiveClusterGate.from_file(MODELS)
    assert gate is not None
    labeled = ROOT / "data" / "labeled_alert_signals_5m_10m_2026-06-14.jsonl"
    if not labeled.exists():
        return
    matched = 0
    with labeled.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if str(row.get("market")).upper() != "NQ":
                continue
            if int(row.get("horizon_minutes") or 0) != 5:
                continue
            row["status"] = "post"
            meta = gate.evaluate(row)
            if meta["passed"] and meta["best_cluster_id"] == 0:
                matched += 1
                break
    assert matched == 1


def test_labeled_alert_can_match_best_es_5m_cluster() -> None:
    gate = LiveClusterGate.from_file(MODELS)
    assert gate is not None
    labeled = ROOT / "data" / "labeled_alert_signals_5m_10m_2026-06-14.jsonl"
    if not labeled.exists():
        return
    matched = 0
    with labeled.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if str(row.get("market")).upper() != "ES":
                continue
            if int(row.get("horizon_minutes") or 0) != 5:
                continue
            row["status"] = "post"
            meta = gate.evaluate(row)
            if meta["passed"]:
                matched += 1
                break
    assert matched == 1
