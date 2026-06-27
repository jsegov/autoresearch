from esnq_signal.signal import DirectionSignalEngine, SignalThresholds


def test_signal_engine_posts_for_aligned_inputs() -> None:
    engine = DirectionSignalEngine(max_spread_es=1.0, max_spread_nq=2.0)
    feature_row = {
        "timestamp_utc": "2026-05-29T14:30:00.000+00:00",
        "market": "ES",
        "target_stale": False,
        "target_spread": 0.5,
        "target_ofi_15s_z": 2.0,
        "target_ofi_60s_z": 1.6,
        "target_ofi_300s_z": 0.6,
        "cross_index_confirmation": 2,
        "cross_index_contradiction": 0,
        "macro_regime_score": 1,
        "macro_ready": 2,
    }
    signal = engine.evaluate(feature_row, horizon_minutes=5)
    assert signal.status in {"post", "high_priority"}
    assert signal.direction == "long"


def test_signal_engine_blocks_stale_data() -> None:
    engine = DirectionSignalEngine(max_spread_es=1.0, max_spread_nq=2.0)
    feature_row = {
        "timestamp_utc": "2026-05-29T14:30:00.000+00:00",
        "market": "NQ",
        "target_stale": True,
        "target_spread": 1.0,
        "target_ofi_15s_z": -1.8,
        "target_ofi_60s_z": -1.2,
        "target_ofi_300s_z": -0.4,
        "cross_index_confirmation": 1,
        "cross_index_contradiction": 0,
        "macro_regime_score": -1,
        "macro_ready": 2,
    }
    signal = engine.evaluate(feature_row, horizon_minutes=10)
    assert signal.status == "blocked"


def test_signal_engine_uses_threshold_overrides() -> None:
    thresholds = {
        "ES": {
            5: SignalThresholds(
                post_p_hit_min=0.90,
                post_confidence_min=0.90,
                post_cross_confirm_min=3,
                high_p_hit_min=0.95,
                high_confidence_min=0.95,
                high_cross_confirm_min=3,
            )
        }
    }
    engine = DirectionSignalEngine(
        max_spread_es=1.0,
        max_spread_nq=2.0,
        thresholds_by_market_horizon=thresholds,
    )
    feature_row = {
        "timestamp_utc": "2026-05-29T14:30:00.000+00:00",
        "market": "ES",
        "target_stale": False,
        "target_spread": 0.5,
        "target_ofi_15s_z": 2.0,
        "target_ofi_60s_z": 1.5,
        "target_ofi_300s_z": 0.8,
        "cross_index_confirmation": 2,
        "cross_index_contradiction": 0,
        "macro_regime_score": 1,
        "macro_ready": 2,
    }
    signal = engine.evaluate(feature_row, horizon_minutes=5)
    assert signal.status in {"watch", "blocked"}


def test_signal_engine_uses_lead_confirmation_for_support_gate() -> None:
    engine = DirectionSignalEngine(max_spread_es=1.0, max_spread_nq=2.0)
    feature_row = {
        "timestamp_utc": "2026-05-29T14:30:00.000+00:00",
        "market": "NQ",
        "target_stale": False,
        "target_spread": 0.8,
        "target_ofi_15s_z": 2.1,
        "target_ofi_60s_z": 1.4,
        "target_ofi_300s_z": 0.9,
        "cross_index_confirmation": 0,
        "cross_index_contradiction": 0,
        "cross_index_lead_confirmation": 2,
        "cross_index_lead_contradiction": 0,
        "macro_regime_score": 1,
        "macro_lead_score": 1,
        "macro_ready": 2,
    }
    signal = engine.evaluate(feature_row, horizon_minutes=5)
    assert signal.status in {"post", "high_priority"}


def test_signal_engine_respects_research_only_horizon_gate() -> None:
    engine = DirectionSignalEngine(
        max_spread_es=1.0,
        max_spread_nq=2.0,
        live_ok_horizons={"ES": {5}},
    )
    feature_row = {
        "timestamp_utc": "2026-05-29T14:30:00.000+00:00",
        "market": "NQ",
        "target_stale": False,
        "target_spread": 1.0,
        "target_ofi_15s_z": 2.2,
        "target_ofi_60s_z": 1.7,
        "target_ofi_300s_z": 1.1,
        "cross_index_confirmation": 2,
        "cross_index_contradiction": 0,
        "cross_index_lead_confirmation": 1,
        "cross_index_lead_contradiction": 0,
        "macro_regime_score": 1,
        "macro_lead_score": 1,
        "macro_ready": 2,
    }
    signal = engine.evaluate(feature_row, horizon_minutes=5)
    assert signal.status == "watch"
    assert signal.reason == "research_only_horizon"


def test_signal_engine_applies_session_and_direction_filters() -> None:
    thresholds = {
        "ES": {
            5: SignalThresholds(
                post_p_hit_min=0.45,
                post_confidence_min=0.2,
                post_cross_confirm_min=1,
                post_direction_gap_min=0.0,
                post_raw_edge_min=0.5,
                allowed_session="RTH_CLOSE",
                allowed_direction="short",
            )
        }
    }
    engine = DirectionSignalEngine(
        max_spread_es=1.0,
        max_spread_nq=2.0,
        thresholds_by_market_horizon=thresholds,
    )
    feature_row = {
        "timestamp_utc": "2026-05-29T14:30:00.000+00:00",  # 10:30 ET (RTH_OPEN)
        "market": "ES",
        "target_stale": False,
        "target_spread": 0.5,
        "target_ofi_15s_z": 2.0,
        "target_ofi_60s_z": 1.6,
        "target_ofi_300s_z": 0.6,
        "cross_index_confirmation": 2,
        "cross_index_contradiction": 0,
        "macro_regime_score": 1,
        "macro_ready": 2,
    }
    signal = engine.evaluate(feature_row, horizon_minutes=5)
    assert signal.status == "watch"
    assert "session_filter_mismatch" in signal.risk_flags
    assert "direction_filter_mismatch" in signal.risk_flags
