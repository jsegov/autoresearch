from __future__ import annotations

import json
from pathlib import Path

from esnq_signal.calibration import load_live_ok_horizons, load_thresholds_config, serialize_thresholds_config
from esnq_signal.signal import SignalThresholds


def test_calibration_roundtrip(tmp_path: Path) -> None:
    thresholds = {
        "ES": {
            5: SignalThresholds(
                post_p_hit_min=0.66,
                post_confidence_min=0.58,
                post_cross_confirm_min=1,
                post_direction_gap_min=0.04,
                post_raw_edge_min=0.9,
                allowed_session="RTH_OPEN",
                allowed_direction="short",
            ),
            10: SignalThresholds(
                post_p_hit_min=0.68,
                post_confidence_min=0.60,
                post_cross_confirm_min=1,
                post_direction_gap_min=0.05,
                post_raw_edge_min=1.0,
                allowed_session="OVERNIGHT",
                allowed_direction="long",
            ),
        }
    }
    payload = serialize_thresholds_config(thresholds)
    path = tmp_path / "signal_calibration.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_thresholds_config(path)
    assert "ES" in loaded
    assert 5 in loaded["ES"]
    assert abs(loaded["ES"][5].post_p_hit_min - 0.66) < 1e-9
    assert abs(loaded["ES"][10].post_confidence_min - 0.60) < 1e-9
    assert abs(loaded["ES"][5].post_direction_gap_min - 0.04) < 1e-9
    assert abs(loaded["ES"][10].post_raw_edge_min - 1.0) < 1e-9
    assert loaded["ES"][5].allowed_session == "RTH_OPEN"
    assert loaded["ES"][10].allowed_direction == "long"


def test_load_live_ok_horizons(tmp_path: Path) -> None:
    payload = {
        "summary": [
            {"market": "ES", "horizon_minutes": 5, "status": "live_ok"},
            {"market": "ES", "horizon_minutes": 10, "status": "research_only"},
            {"market": "NQ", "horizon_minutes": 5, "status": "no_fit"},
        ]
    }
    path = tmp_path / "signal_calibration.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    live_ok = load_live_ok_horizons(path)
    assert live_ok == {"ES": {5}}
