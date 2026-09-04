from esnq_signal.signal import (
    DirectionSignalEngine,
    SignalThresholds,
    _has_explicit_contract_month_year,
)


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


def _rules_feature_row(market: str = "CL") -> dict:
    required = 1 if market == "CL" else 2
    return {
        "timestamp_utc": "2026-08-20T14:30:00.000+00:00",
        "market": market,
        "contract_id": f"F.US.{market}Z26",
        "target_stale": False,
        "target_spread": 0.01 if market == "CL" else 0.03125,
        "target_ofi_norm_15s": 0.8,
        "roll_warmup_active": False,
        "rule_score": 0.85,
        "peer_confirmation": {
            "ready": required,
            "aligned": required,
            "opposing": 0,
            "required_aligned": required,
            "aligned_primary_count": required,
            "opposing_primary_count": 0,
            "strong_opposing_primary_count": 0,
            "strong_opposing_peers": [],
            "confirmed": True,
            "ok": True,
        },
    }


def test_rules_first_signal_has_null_probabilities_and_posts_fail_closed() -> None:
    engine = DirectionSignalEngine(max_spread_es=1.0, max_spread_nq=2.0)
    signal = engine.evaluate(_rules_feature_row("CL"), horizon_minutes=5)
    payload = signal.to_dict()

    assert signal.status == "watch"
    assert signal.reason == "experimental_posting_disabled"
    assert payload["signal_kind"] == "rules_first"
    assert payload["validation_status"] == "experimental_unvalidated"
    assert payload["p_up"] is None
    assert payload["p_hit"] is None
    assert payload["confidence"] is None


def test_rules_first_requires_smoke_validation_separately_from_posting_switch() -> None:
    engine = DirectionSignalEngine(
        max_spread_es=1.0,
        max_spread_nq=2.0,
        experimental_posting_enabled={"CL"},
    )
    signal = engine.evaluate(_rules_feature_row("CL"), horizon_minutes=5)
    assert signal.status == "watch"
    assert signal.reason == "smoke_validation_required"


def test_rules_first_can_post_after_explicit_smoke_and_product_switch() -> None:
    engine = DirectionSignalEngine(
        max_spread_es=1.0,
        max_spread_nq=2.0,
        experimental_posting_enabled={"UB"},
        experimental_smoke_validated={"UB"},
    )
    signal = engine.evaluate(_rules_feature_row("UB"), horizon_minutes=30)
    assert signal.status == "post"
    assert signal.validation_status == "smoke_passed"
    assert signal.horizon_minutes == 30


def test_rules_first_blocks_during_authoritative_contract_roll_warmup() -> None:
    engine = DirectionSignalEngine(
        max_spread_es=1.0,
        max_spread_nq=2.0,
        experimental_posting_enabled={"CL"},
        experimental_smoke_validated={"CL"},
    )
    row = _rules_feature_row("CL")
    row["roll_warmup_active"] = True
    signal = engine.evaluate(row, horizon_minutes=5)
    assert signal.status == "blocked"
    assert "contract_roll_warmup" in signal.risk_flags


def test_ub_rules_block_strong_opposing_primary_even_with_two_aligned() -> None:
    engine = DirectionSignalEngine(
        max_spread_es=1.0,
        max_spread_nq=2.0,
        experimental_posting_enabled={"UB"},
        experimental_smoke_validated={"UB"},
    )
    row = _rules_feature_row("UB")
    row["peer_confirmation"]["strong_opposing_primary_count"] = 1
    row["peer_confirmation"]["strong_opposing_peers"] = ["ZN"]
    signal = engine.evaluate(row, horizon_minutes=10)
    assert signal.status == "watch"
    assert signal.reason == "rules_first_alignment_not_met"
    assert "strong_opposing_primary_peer" in signal.risk_flags


def test_rules_first_root_only_contract_is_never_postable() -> None:
    engine = DirectionSignalEngine(
        max_spread_es=1.0,
        max_spread_nq=2.0,
        experimental_posting_enabled={"CL"},
        experimental_smoke_validated={"CL"},
    )
    row = _rules_feature_row("CL")
    row["contract_id"] = "CL"
    signal = engine.evaluate(row, horizon_minutes=5)
    assert signal.status == "blocked"
    assert signal.reason == "experimental_data_quality_gate"
    assert "contract_month_year_missing" in signal.risk_flags


def test_rules_first_outside_session_is_logged_but_non_postable() -> None:
    engine = DirectionSignalEngine(
        max_spread_es=1.0,
        max_spread_nq=2.0,
        experimental_posting_enabled={"CL"},
        experimental_smoke_validated={"CL"},
    )
    row = _rules_feature_row("CL")
    row["alert_session_open"] = False
    signal = engine.evaluate(row, horizon_minutes=5)
    assert signal.status == "watch"
    assert signal.reason == "outside_alert_session"
    assert "outside_alert_session" in signal.risk_flags


def test_contract_gate_accepts_delimited_sierra_suffixes_only() -> None:
    assert _has_explicit_contract_month_year("F.US.CLZ26", "CL") is True
    assert _has_explicit_contract_month_year("CLZ26-NYMEX", "CL") is True
    assert _has_explicit_contract_month_year("CLZ26_FUT_CME", "CL") is True
    assert _has_explicit_contract_month_year("UBH2027-CBOT", "UB") is True
    assert _has_explicit_contract_month_year("CL", "CL") is False
    assert _has_explicit_contract_month_year("CLZ26TAIL", "CL") is False
    assert _has_explicit_contract_month_year("XCLZ26", "CL") is False
    assert _has_explicit_contract_month_year("CLZ26-NYMEX@", "CL") is False


def _equity_rules_feature_row(market: str = "AAPL") -> dict:
    return {
        "timestamp_utc": "2026-08-28T14:30:00.000+00:00",
        "market": market,
        "ticker": market,
        "contract_id": f"{market}-NQTV",
        "ticker_identity_ok": True,
        "asset_class": "equity",
        "target_stale": False,
        "target_age_s": 0.4,
        "target_spot": 225.0,
        "target_spread": 0.02,
        "input_mode": "l2_ofi",
        "input_value_15s": 0.4,
        "target_ofi_norm_15s": 0.4,
        "target_trade_imbalance_norm_15s": None,
        "alert_session_open": True,
        "rule_score": 0.75,
        "peer_confirmation": {
            "source": "theta_equity_breadth_v1",
            "schema_version": "equity_breadth_snapshot_v1",
            "snapshot_health": True,
            "snapshot_age_s": 0.4,
            "ready_groups": 3,
            "aligned_group_count": 2,
            "opposing_group_count": 1,
            "required_aligned": 2,
            "qqq_strong_opposition": False,
            "confirmed": True,
        },
    }


def test_equity_rules_first_is_shadow_candidate_with_null_probabilities() -> None:
    engine = DirectionSignalEngine(max_spread_es=1.0, max_spread_nq=2.0)
    signal = engine.evaluate(_equity_rules_feature_row(), horizon_minutes=5)
    payload = signal.to_dict()

    assert signal.status == "watch"
    assert signal.reason == "experimental_posting_disabled"
    assert signal.candidate_eligible is True
    assert payload["asset_class"] == "equity"
    assert payload["input_mode"] == "l2_ofi"
    assert payload["p_up"] is None
    assert payload["p_hit"] is None
    assert payload["confidence"] is None


def test_equity_rules_first_can_post_only_with_symbol_switch_and_smoke() -> None:
    engine = DirectionSignalEngine(
        max_spread_es=1.0,
        max_spread_nq=2.0,
        experimental_posting_enabled={"AAPL"},
        experimental_smoke_validated={"AAPL"},
    )
    signal = engine.evaluate(_equity_rules_feature_row(), horizon_minutes=5)
    assert signal.status == "post"
    assert signal.candidate_eligible is True
    assert signal.validation_status == "smoke_passed"


def test_equity_fifteen_minute_row_is_research_only() -> None:
    engine = DirectionSignalEngine(
        max_spread_es=1.0,
        max_spread_nq=2.0,
        experimental_posting_enabled={"AAPL"},
        experimental_smoke_validated={"AAPL"},
    )
    signal = engine.evaluate(_equity_rules_feature_row(), horizon_minutes=15)
    assert signal.status == "watch"
    assert signal.reason == "research_only_horizon"
    assert signal.candidate_eligible is False


def test_equity_rules_first_requires_exact_ticker_and_theta_breadth() -> None:
    engine = DirectionSignalEngine(max_spread_es=1.0, max_spread_nq=2.0)
    row = _equity_rules_feature_row()
    row["contract_id"] = "FAKEAAPL"
    row["ticker_identity_ok"] = False
    row["peer_confirmation"]["source"] = "missing"
    signal = engine.evaluate(row, horizon_minutes=5)
    assert signal.status == "blocked"
    assert signal.candidate_eligible is False
    assert "ticker_identity_mismatch" in signal.risk_flags
    assert "theta_equity_breadth_required" in signal.risk_flags


def test_equity_trade_imbalance_provenance_stays_distinct() -> None:
    engine = DirectionSignalEngine(max_spread_es=1.0, max_spread_nq=2.0)
    row = _equity_rules_feature_row()
    row["input_mode"] = "trade_imbalance"
    row["input_value_15s"] = -0.35
    row["target_ofi_norm_15s"] = None
    row["target_trade_imbalance_norm_15s"] = -0.35
    row["peer_confirmation"]["aligned_group_count"] = 2
    signal = engine.evaluate(row, horizon_minutes=5)
    assert signal.direction == "short"
    assert signal.input_mode == "trade_imbalance"
    assert signal.candidate_eligible is True


def test_equity_qqq_strong_opposition_vetoes_candidate() -> None:
    engine = DirectionSignalEngine(max_spread_es=1.0, max_spread_nq=2.0)
    row = _equity_rules_feature_row()
    row["peer_confirmation"]["qqq_strong_opposition"] = True
    row["peer_confirmation"]["confirmed"] = False
    signal = engine.evaluate(row, horizon_minutes=5)
    assert signal.status == "watch"
    assert signal.reason == "rules_first_alignment_not_met"
    assert signal.candidate_eligible is False
    assert "strong_opposing_qqq" in signal.risk_flags


def test_equity_candidate_gate_rejects_blended_or_mismatched_input_sources() -> None:
    engine = DirectionSignalEngine(max_spread_es=1.0, max_spread_nq=2.0)

    blended = _equity_rules_feature_row()
    blended["target_trade_imbalance_norm_15s"] = 0.2
    signal = engine.evaluate(blended, horizon_minutes=5)
    assert signal.status == "blocked"
    assert signal.candidate_eligible is False
    assert "target_input_mode_blended" in signal.risk_flags

    mismatched = _equity_rules_feature_row()
    mismatched["input_value_15s"] = 0.3
    signal = engine.evaluate(mismatched, horizon_minutes=5)
    assert signal.status == "blocked"
    assert signal.candidate_eligible is False
    assert "target_input_value_mismatch" in signal.risk_flags


def test_equity_candidate_gate_checks_signed_target_age_independently() -> None:
    engine = DirectionSignalEngine(max_spread_es=1.0, max_spread_nq=2.0)
    for invalid_age in (2.01, -2.01, None, float("inf")):
        row = _equity_rules_feature_row()
        row["target_age_s"] = invalid_age
        signal = engine.evaluate(row, horizon_minutes=5)
        assert signal.status == "blocked"
        assert signal.candidate_eligible is False
        assert "target_input_age_invalid" in signal.risk_flags


def test_equity_candidate_gate_honors_explicit_peer_veto_alias() -> None:
    engine = DirectionSignalEngine(max_spread_es=1.0, max_spread_nq=2.0)
    row = _equity_rules_feature_row()
    row["peer_confirmation"]["veto"] = True
    signal = engine.evaluate(row, horizon_minutes=5)
    assert signal.candidate_eligible is False
    assert "strong_opposing_qqq" in signal.risk_flags


def test_equity_candidate_gate_honors_qqq_opposition_alias() -> None:
    engine = DirectionSignalEngine(max_spread_es=1.0, max_spread_nq=2.0)
    row = _equity_rules_feature_row()
    row["peer_confirmation"]["qqq_opposition"] = True
    signal = engine.evaluate(row, horizon_minutes=5)
    assert signal.candidate_eligible is False
    assert "strong_opposing_qqq" in signal.risk_flags


def test_equity_candidate_gate_enforces_two_aligned_groups_fail_closed() -> None:
    engine = DirectionSignalEngine(max_spread_es=1.0, max_spread_nq=2.0)
    row = _equity_rules_feature_row()
    row["peer_confirmation"].update(
        {
            "ready_groups": 2,
            "aligned_group_count": 1,
            "required_aligned": 1,
            "confirmed": True,
        }
    )
    signal = engine.evaluate(row, horizon_minutes=5)
    assert signal.candidate_eligible is False
    assert signal.layers["rules_first"]["required_aligned"] == 2
    assert "peer_alignment_below_rule_min" in signal.risk_flags

    malformed = _equity_rules_feature_row()
    malformed["peer_confirmation"]["ready_groups"] = "not-an-integer"
    malformed_signal = engine.evaluate(malformed, horizon_minutes=5)
    assert malformed_signal.candidate_eligible is False
    assert "equity_peer_groups_incomplete" in malformed_signal.risk_flags


def test_equity_candidate_gate_requires_typed_healthy_theta_snapshot() -> None:
    engine = DirectionSignalEngine(max_spread_es=1.0, max_spread_nq=2.0)

    unhealthy = _equity_rules_feature_row()
    unhealthy["peer_confirmation"]["snapshot_health"] = False
    unhealthy_signal = engine.evaluate(unhealthy, horizon_minutes=5)
    assert unhealthy_signal.candidate_eligible is False
    assert "theta_equity_breadth_unhealthy" in unhealthy_signal.risk_flags

    wrong_schema = _equity_rules_feature_row()
    wrong_schema["peer_confirmation"]["schema_version"] = "legacy"
    wrong_schema_signal = engine.evaluate(wrong_schema, horizon_minutes=5)
    assert wrong_schema_signal.candidate_eligible is False
    assert "theta_equity_breadth_schema_invalid" in wrong_schema_signal.risk_flags


def test_equity_candidate_gate_requires_exact_ticker_field() -> None:
    engine = DirectionSignalEngine(max_spread_es=1.0, max_spread_nq=2.0)
    row = _equity_rules_feature_row()
    row["ticker"] = "MSFT"
    signal = engine.evaluate(row, horizon_minutes=5)
    assert signal.candidate_eligible is False
    assert "ticker_identity_mismatch" in signal.risk_flags
