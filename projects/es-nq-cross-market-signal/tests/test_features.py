from datetime import datetime, timedelta, timezone

from esnq_signal.features import CrossMarketFeatureStore
from esnq_signal.ofi_stream import OfiSample


def _sample(
    market: str,
    ts: datetime,
    ofi: float,
    spot: float,
    spread: float = 0.5,
    *,
    ofi_norm: float | None = None,
    contract_id: str | None = None,
    trade_imbalance_norm: float | None = None,
    l2_available: bool | None = None,
    input_mode: str | None = None,
) -> OfiSample:
    return OfiSample(
        market=market,
        symbol_raw=market,
        timestamp_utc=ts,
        ofi_1s=ofi,
        spread=spread,
        bid_depth=100.0,
        ask_depth=95.0,
        spot=spot,
        bar_delta=ofi,
        source="test",
        ofi_norm=ofi_norm,
        contract_id=contract_id,
        trade_imbalance_norm=trade_imbalance_norm,
        l2_available=l2_available,
        input_mode=input_mode,
    )


def test_feature_row_has_cross_index_and_macro_context() -> None:
    store = CrossMarketFeatureStore()
    base = datetime(2026, 5, 29, 14, 30, tzinfo=timezone.utc)
    for i in range(120):
        ts = base + timedelta(seconds=i)
        store.ingest(_sample("ES", ts, 5.0, 6000.0 + (i * 0.01), spread=0.5))
        store.ingest(_sample("NQ", ts, 4.0, 21000.0 + (i * 0.05), spread=1.0))
        store.ingest(_sample("YM", ts, 2.0, 43000.0 + (i * 0.03), spread=1.0))
        store.ingest(_sample("RTY", ts, 1.5, 2400.0 + (i * 0.01), spread=0.5))
        store.ingest(_sample("E6", ts, 1.0, 1.08 + (i * 0.00001), spread=0.0001))
        store.ingest(_sample("ZN", ts, -0.8, 109.0 + (i * 0.0001), spread=0.01))

    row = store.build_target_row("ES", now_utc=base + timedelta(seconds=119), stale_seconds=2.5)
    assert row["status"] == "ok"
    assert row["target_stale"] is False
    assert row["cross_index_ready"] >= 1
    assert "macro_regime_score" in row
    assert "cross_index_lead_balance" in row
    assert "macro_lead_score" in row


def test_feature_row_tracks_large_trade_aggregates() -> None:
    store = CrossMarketFeatureStore()
    base = datetime(2026, 5, 29, 15, 0, tzinfo=timezone.utc)
    for i in range(80):
        ts = base + timedelta(seconds=i)
        store.ingest(_sample("ES", ts, 1.5, 6000.0 + (i * 0.02), spread=0.5))

    now = base + timedelta(seconds=79)
    store.ingest_large_trade("ES", ts_utc=now - timedelta(seconds=50), side="buy", volume=20)
    store.ingest_large_trade("ES", ts_utc=now - timedelta(seconds=10), side="sell", volume=5)
    store.ingest_large_trade("ES", ts_utc=now - timedelta(seconds=5), side="buy", volume=15)
    store.ingest_large_trade("ES", ts_utc=now - timedelta(seconds=5), side="unknown", volume=99)

    row = store.build_target_row("ES", now_utc=now, stale_seconds=2.5)
    assert row["target_large_trade_signed_60s"] == 30.0
    assert row["target_large_trade_signed_300s"] == 30.0
    assert row["target_large_trade_count_60s"] == 3
    assert row["target_large_trade_count_300s"] == 3
    assert abs(float(row["target_large_trade_buy_ratio_300s"]) - (2.0 / 3.0)) < 1e-9


def test_effective_ofi_prefers_cks_falls_back_legacy() -> None:
    """CKS-corrected OFI (Cont/Kukanov/Stoikov 2014 sec 2.2) is preferred when
    the exporter provides it; legacy depth-delta only as fallback."""
    from esnq_signal.ofi_stream import effective_ofi, parse_ofi_payload

    new_payload = {
        "ts": "2026-07-01T14:30:00.000Z", "symbol": "F.US.EPM26",
        "ofi": -30.0, "ofi_cks": 90.0, "ofi_deep": 120.0, "ofi_norm": 0.24,
        "spread": 0.25, "bid_depth": 500, "ask_depth": 480, "spot": 7420.25,
    }
    sample = parse_ofi_payload(new_payload, source="test")
    # canonical wrong-sign case: legacy negative, CKS positive -> CKS wins
    assert effective_ofi(sample) == 90.0
    assert sample.ofi_norm == 0.24
    assert sample.contract_id == "F.US.EPM26"

    legacy_payload = {
        "ts": "2026-07-01T14:30:00.000Z", "symbol": "F.US.ENQM26",
        "ofi": -4.0, "spread": 0.25, "bid_depth": 100, "ask_depth": 90,
    }
    old = parse_ofi_payload(legacy_payload, source="test")
    assert old.ofi_cks is None
    assert effective_ofi(old) == -4.0

    explicit_contract = parse_ofi_payload(
        {
            "ts": "2026-07-01T14:30:00.000Z",
            "symbol": "CL",
            "contract_id": "F.US.CLU26",
            "ofi_norm": 0.1,
        },
        source="test",
    )
    assert explicit_contract.contract_id == "F.US.CLU26"

    equity_fallback = parse_ofi_payload(
        {
            "ts": "2026-08-28T14:30:00.000Z",
            "symbol": "AAPL-NQTV",
            "contract_id": "AAPL-NQTV",
            "l2_available": False,
            "ofi_norm": None,
            "trade_imbalance_15s": 420.0,
            "trade_imbalance_norm": 0.35,
            "trade_volume_15s": 1200.0,
            "input_mode": "trade_imbalance",
            "spot": 225.0,
        },
        source="test",
    )
    assert equity_fallback is not None
    assert equity_fallback.market == "AAPL"
    assert equity_fallback.ofi_norm is None
    assert equity_fallback.trade_imbalance_norm == 0.35
    assert equity_fallback.l2_available is False
    assert equity_fallback.input_mode == "trade_imbalance"


def test_feature_store_ingests_cks_ofi() -> None:
    from esnq_signal.features import CrossMarketFeatureStore
    from esnq_signal.ofi_stream import OfiSample

    store = CrossMarketFeatureStore()
    ts = datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc)
    sample = OfiSample(
        market="ES", symbol_raw="ES", timestamp_utc=ts, ofi_1s=-30.0,
        spread=0.25, bid_depth=500.0, ask_depth=480.0, spot=7420.0,
        bar_delta=0.0, source="test", ofi_cks=90.0,
    )
    store.ingest(sample)
    state = store._states["ES"]
    assert state.ofi[-1][1] == 90.0  # corrected value, not legacy -30


def test_cl_peer_votes_require_normalized_ofi_and_emit_canonical_counts() -> None:
    store = CrossMarketFeatureStore()
    base = datetime(2026, 8, 20, 14, 30, tzinfo=timezone.utc)
    for i in range(20):
        ts = base + timedelta(seconds=i)
        store.ingest(
            _sample("CL", ts, 500.0, 72.0, 0.01, ofi_norm=0.08, contract_id="F.US.CLU26")
        )
        # Large raw OFI without ofi_norm must not vote cross-product.
        store.ingest(_sample("RB", ts, 9000.0, 2.20, 0.0001, contract_id="F.US.RBU26"))

    row = store.build_target_row("CL", now_utc=base + timedelta(seconds=19), stale_seconds=2.0)
    peers = row["peer_confirmation"]
    assert row["contract_id"] == "F.US.CLU26"
    assert row["target_ofi_norm_15s"] is not None
    assert peers["normalized_only"] is True
    assert peers["ready"] == 0
    assert peers["aligned_primary_count"] == 0
    assert peers["confirmed"] is False


def test_ub_rules_require_two_aligned_normalized_primary_peers() -> None:
    store = CrossMarketFeatureStore()
    base = datetime(2026, 8, 20, 14, 30, tzinfo=timezone.utc)
    for i in range(20):
        ts = base + timedelta(seconds=i)
        store.ingest(
            _sample("UB", ts, 10.0, 132.0, 0.03125, ofi_norm=-0.06, contract_id="F.US.UBZ26")
        )
        store.ingest(
            _sample("ZB", ts, -8.0, 118.0, 0.03125, ofi_norm=-0.05, contract_id="F.US.ZBZ26")
        )
        store.ingest(
            _sample("TN", ts, -7.0, 121.0, 0.015625, ofi_norm=-0.04, contract_id="F.US.TNZ26")
        )

    row = store.build_target_row("UB", now_utc=base + timedelta(seconds=19), stale_seconds=2.0)
    peers = row["peer_confirmation"]
    assert peers["required_aligned"] == 2
    assert peers["aligned_primary_count"] == 2
    assert peers["confirmed"] is True
    assert row["rule_score"] is not None


def test_authoritative_target_contract_change_resets_state_and_starts_warmup() -> None:
    store = CrossMarketFeatureStore()
    base = datetime(2026, 8, 20, 14, 30, tzinfo=timezone.utc)
    store.ingest(
        _sample("CL", base, 5.0, 72.0, 0.01, ofi_norm=0.2, contract_id="F.US.CLQ26")
    )
    store.ingest(
        _sample("RB", base, 5.0, 2.2, 0.0001, ofi_norm=0.2, contract_id="F.US.RBQ26")
    )
    roll_ts = base + timedelta(seconds=30)
    store.ingest(
        _sample("CL", roll_ts, -5.0, 71.8, 0.01, ofi_norm=-0.2, contract_id="F.US.CLU26")
    )

    row = store.build_target_row("CL", now_utc=roll_ts, stale_seconds=2.0)
    assert row["contract_id"] == "F.US.CLU26"
    assert row["roll_warmup_active"] is True
    assert row["peer_confirmation"]["ready"] == 0
    assert len(store._states["CL"].ofi) == 1


def test_feature_row_smoke_status_requires_explicit_product_allowlist() -> None:
    ts = datetime(2026, 8, 20, 14, 30, tzinfo=timezone.utc)
    store = CrossMarketFeatureStore(smoke_validated_markets={"CL"})
    store.ingest(
        _sample("CL", ts, 5.0, 72.0, 0.01, ofi_norm=0.2, contract_id="F.US.CLU26")
    )
    row = store.build_target_row("CL", now_utc=ts, stale_seconds=2.0)
    assert row["validation_status"] == "smoke_passed"


def test_ub_strong_opposing_primary_blocks_confirmation() -> None:
    store = CrossMarketFeatureStore()
    base = datetime(2026, 8, 20, 14, 30, tzinfo=timezone.utc)
    for i in range(20):
        ts = base + timedelta(seconds=i)
        store.ingest(
            _sample("UB", ts, 5.0, 132.0, 0.03125, ofi_norm=0.06, contract_id="F.US.UBZ26")
        )
        store.ingest(
            _sample("ZB", ts, 4.0, 118.0, 0.03125, ofi_norm=0.05, contract_id="F.US.ZBZ26")
        )
        store.ingest(
            _sample("TN", ts, 4.0, 121.0, 0.015625, ofi_norm=0.05, contract_id="F.US.TNZ26")
        )
        store.ingest(
            _sample("ZN", ts, -5.0, 110.0, 0.015625, ofi_norm=-0.05, contract_id="F.US.ZNZ26")
        )

    row = store.build_target_row("UB", now_utc=base + timedelta(seconds=19), stale_seconds=2.0)
    peers = row["peer_confirmation"]
    assert peers["aligned_primary_count"] == 2
    assert peers["strong_opposing_primary_count"] == 1
    assert peers["confirmed"] is False


def test_primary_peer_is_excluded_for_600_seconds_after_its_own_roll() -> None:
    store = CrossMarketFeatureStore()
    base = datetime(2026, 8, 20, 14, 30, tzinfo=timezone.utc)
    store.ingest(
        _sample("CL", base, 5.0, 72.0, 0.01, ofi_norm=0.2, contract_id="F.US.CLU26")
    )
    store.ingest(
        _sample("RB", base, 5.0, 2.2, 0.0001, ofi_norm=0.2, contract_id="F.US.RBU26")
    )
    peer_roll = base + timedelta(seconds=30)
    store.ingest(
        _sample("RB", peer_roll, 5.0, 2.2, 0.0001, ofi_norm=0.2, contract_id="F.US.RBV26")
    )

    before_ready = peer_roll + timedelta(seconds=599)
    store.ingest(
        _sample("CL", before_ready, 5.0, 72.0, 0.01, ofi_norm=0.2, contract_id="F.US.CLU26")
    )
    store.ingest(
        _sample("RB", before_ready, 5.0, 2.2, 0.0001, ofi_norm=0.2, contract_id="F.US.RBV26")
    )
    warm = store.build_target_row("CL", now_utc=before_ready, stale_seconds=2.0)
    assert warm["peer_confirmation"]["ready"] == 0
    assert warm["peer_confirmation"]["peer_roll_warmup_markets"] == ["RB"]

    ready_at = peer_roll + timedelta(seconds=600)
    store.ingest(
        _sample("CL", ready_at, 5.0, 72.0, 0.01, ofi_norm=0.2, contract_id="F.US.CLU26")
    )
    store.ingest(
        _sample("RB", ready_at, 5.0, 2.2, 0.0001, ofi_norm=0.2, contract_id="F.US.RBV26")
    )
    ready = store.build_target_row("CL", now_utc=ready_at, stale_seconds=2.0)
    assert ready["peer_confirmation"]["ready"] == 1
    assert ready["peer_confirmation"]["peer_roll_warmup_markets"] == []


def _equity_breadth_payload(ts: datetime, *, qqq_score: float = 1.4) -> dict:
    def obs(root: str, score: float, issuer_id: str | None = None) -> dict:
        return {
            "root": root,
            "observed_at": ts.isoformat(),
            "price": 100.0,
            "return_15s": 0.001,
            "signed_volume_imbalance_15s": 0.4,
            "return_z": score,
            "imbalance_z": score * 0.8,
            "composite_score": score,
            "vote": "bullish" if score > 0 else "bearish",
            "issuer_id": issuer_id,
            "roles": ["target"],
            "health": {"healthy": True},
            "freshness": {"age_s": 0.0},
            "provenance": {"source": "thetadata"},
        }

    return {
        "schema_version": "equity_breadth_snapshot_v1",
        "calculated_at": ts.isoformat(),
        "observations": {
            "QQQ": obs("QQQ", qqq_score),
            "XLK": obs("XLK", 1.2),
            "XLC": obs("XLC", 1.1),
            "MSFT": obs("MSFT", 1.0, "MICROSOFT"),
            "NVDA": obs("NVDA", 0.9, "NVIDIA"),
            "TSLA": obs("TSLA", 0.8, "TESLA"),
            "GOOG": obs("GOOG", 0.7, "ALPHABET"),
            "GOOGL": obs("GOOGL", 0.5, "ALPHABET"),
        },
        "health": {"healthy": True},
        "provenance": {"source": "thetadata_native_stock_trades"},
    }


def test_equity_feature_uses_theta_three_group_votes_and_collapses_alphabet() -> None:
    from esnq_signal.equity_breadth import parse_equity_breadth_snapshot

    store = CrossMarketFeatureStore()
    now = datetime(2026, 8, 28, 14, 30, tzinfo=timezone.utc)
    snapshot = parse_equity_breadth_snapshot(_equity_breadth_payload(now))
    assert snapshot is not None
    store.ingest_equity_breadth(snapshot)
    store.ingest(
        _sample(
            "AAPL", now, 10.0, 225.0, 0.02,
            ofi_norm=0.4, contract_id="AAPL-NQTV", l2_available=True,
        )
    )

    row = store.build_target_row("AAPL", now_utc=now, stale_seconds=2.0)
    peers = row["peer_confirmation"]
    assert row["input_mode"] == "l2_ofi"
    assert row["input_provenance"]["source"] == "sierra_l2_ofi"
    assert row["target_ofi_norm_15s"] == 0.4
    assert row["ticker_identity_ok"] is True
    assert peers["source"] == "theta_equity_breadth_v1"
    assert peers["ready_groups"] == 3
    assert peers["aligned_group_count"] == 3
    assert peers["confirmed"] is True
    assert peers["issuer_breadth"]["contributing_issuer_count"] == 4
    assert peers["issuer_breadth"]["issuer_members"]["ALPHABET"] == ["GOOG", "GOOGL"]


def test_equity_feature_falls_back_to_explicit_trade_imbalance_without_aliasing() -> None:
    from esnq_signal.equity_breadth import parse_equity_breadth_snapshot

    store = CrossMarketFeatureStore()
    now = datetime(2026, 8, 28, 14, 30, tzinfo=timezone.utc)
    snapshot = parse_equity_breadth_snapshot(_equity_breadth_payload(now))
    assert snapshot is not None
    store.ingest_equity_breadth(snapshot)
    store.ingest(
        _sample(
            "AAPL", now, 0.0, 225.0, 0.02,
            contract_id="AAPL", trade_imbalance_norm=0.35, l2_available=False,
        )
    )

    row = store.build_target_row("AAPL", now_utc=now, stale_seconds=2.0)
    assert row["input_mode"] == "trade_imbalance"
    assert row["input_provenance"]["source"] == "sierra_time_and_sales"
    assert row["input_value_15s"] == 0.35
    assert row["target_trade_imbalance_norm_15s"] == 0.35
    assert row["target_ofi_norm_15s"] is None


def test_equity_feature_qqq_strong_opposition_vetoes_two_other_aligned_groups() -> None:
    from esnq_signal.equity_breadth import parse_equity_breadth_snapshot

    store = CrossMarketFeatureStore()
    now = datetime(2026, 8, 28, 14, 30, tzinfo=timezone.utc)
    snapshot = parse_equity_breadth_snapshot(_equity_breadth_payload(now, qqq_score=-1.5))
    assert snapshot is not None
    store.ingest_equity_breadth(snapshot)
    store.ingest(
        _sample("AAPL", now, 1.0, 225.0, 0.02, ofi_norm=0.4, contract_id="AAPL")
    )

    peers = store.build_target_row("AAPL", now_utc=now, stale_seconds=2.0)["peer_confirmation"]
    assert peers["aligned_group_count"] == 2
    assert peers["qqq_strong_opposition"] is True
    assert peers["qqq_opposition"] is True
    assert peers["veto"] is True
    assert peers["veto_reason"] == "strong_opposing_qqq"
    assert peers["confirmed"] is False


def test_equity_feature_rejects_future_theta_snapshot_and_isolates_sierra_diagnostics() -> None:
    from esnq_signal.equity_breadth import parse_equity_breadth_snapshot

    store = CrossMarketFeatureStore()
    now = datetime(2026, 8, 28, 14, 30, tzinfo=timezone.utc)
    future = now + timedelta(seconds=3)
    snapshot = parse_equity_breadth_snapshot(_equity_breadth_payload(future))
    assert snapshot is not None
    store.ingest_equity_breadth(snapshot)
    store.ingest(
        _sample("AAPL", now, 1.0, 225.0, 0.02, ofi_norm=0.4, contract_id="AAPL")
    )
    store.ingest(
        _sample("QQQ", now, 1.0, 500.0, 0.01, ofi_norm=0.5, contract_id="QQQ")
    )

    peers = store.build_target_row("AAPL", now_utc=now, stale_seconds=2.0)["peer_confirmation"]
    assert peers["source"] == "theta_equity_breadth_v1_stale"
    assert peers["ready_groups"] == 0
    assert peers["confirmed"] is False
    assert peers["sierra_diagnostics"]["QQQ"]["authorizing"] is False


def test_equity_feature_rejects_unhealthy_theta_snapshot() -> None:
    from esnq_signal.equity_breadth import parse_equity_breadth_snapshot

    store = CrossMarketFeatureStore()
    now = datetime(2026, 8, 28, 14, 30, tzinfo=timezone.utc)
    payload = _equity_breadth_payload(now)
    payload["health"] = {"healthy": False}
    snapshot = parse_equity_breadth_snapshot(payload)
    assert snapshot is not None
    store.ingest_equity_breadth(snapshot)
    store.ingest(
        _sample("AAPL", now, 1.0, 225.0, 0.02, ofi_norm=0.4, contract_id="AAPL")
    )

    peers = store.build_target_row("AAPL", now_utc=now, stale_seconds=2.0)[
        "peer_confirmation"
    ]
    assert peers["source"] == "theta_equity_breadth_v1_unhealthy"
    assert peers["snapshot_health"] is False
    assert peers["ready_groups"] == 0
    assert peers["confirmed"] is False


def test_equity_leave_one_out_excludes_both_alphabet_share_classes() -> None:
    from esnq_signal.equity_breadth import parse_equity_breadth_snapshot

    store = CrossMarketFeatureStore()
    now = datetime(2026, 8, 28, 14, 30, tzinfo=timezone.utc)
    snapshot = parse_equity_breadth_snapshot(_equity_breadth_payload(now))
    assert snapshot is not None
    store.ingest_equity_breadth(snapshot)
    store.ingest(
        _sample("GOOG", now, 1.0, 210.0, 0.02, ofi_norm=0.4, contract_id="GOOG")
    )

    breadth = store.build_target_row("GOOG", now_utc=now, stale_seconds=2.0)[
        "peer_confirmation"
    ]["issuer_breadth"]
    assert breadth["excluded_issuer_id"] == "ALPHABET"
    assert "ALPHABET" not in breadth["contributing_issuers"]
    assert set(breadth["excluded_issuer_members"]) == {"GOOG", "GOOGL"}
    assert breadth["excluded_issuer_members_authorizing"] is False


def test_equity_feature_never_reuses_an_older_trade_fallback_for_a_fresh_row() -> None:
    from esnq_signal.equity_breadth import parse_equity_breadth_snapshot

    store = CrossMarketFeatureStore()
    now = datetime(2026, 8, 28, 14, 30, tzinfo=timezone.utc)
    snapshot = parse_equity_breadth_snapshot(_equity_breadth_payload(now))
    assert snapshot is not None
    store.ingest_equity_breadth(snapshot)
    store.ingest(
        _sample(
            "AAPL",
            now - timedelta(seconds=1),
            0.0,
            225.0,
            0.02,
            contract_id="AAPL-NQTV",
            trade_imbalance_norm=0.4,
            l2_available=False,
            input_mode="trade_imbalance",
        )
    )
    store.ingest(
        _sample(
            "AAPL",
            now,
            0.0,
            225.0,
            0.02,
            contract_id="AAPL-NQTV",
            trade_imbalance_norm=None,
            l2_available=False,
            input_mode="trade_imbalance",
        )
    )

    row = store.build_target_row("AAPL", now_utc=now, stale_seconds=2.0)
    assert row["input_mode"] is None
    assert row["input_value_15s"] is None
    assert row["target_ofi_norm_15s"] is None
    assert row["target_trade_imbalance_norm_15s"] is None
    assert row["rule_candidate"] is False


def test_equity_feature_rejects_an_internally_inconsistent_declared_mode() -> None:
    store = CrossMarketFeatureStore()
    now = datetime(2026, 8, 28, 14, 30, tzinfo=timezone.utc)
    store.ingest(
        _sample(
            "AAPL",
            now,
            1.0,
            225.0,
            0.02,
            ofi_norm=0.4,
            contract_id="AAPL-NQTV",
            trade_imbalance_norm=0.3,
            l2_available=True,
            input_mode="trade_imbalance",
        )
    )

    row = store.build_target_row("AAPL", now_utc=now, stale_seconds=2.0)
    assert row["input_mode"] is None
    assert row["target_ofi_norm_15s"] is None
    assert row["target_trade_imbalance_norm_15s"] is None


def test_equity_feature_includes_latest_l2_sample_with_tolerated_future_clock_skew() -> None:
    store = CrossMarketFeatureStore()
    now = datetime(2026, 8, 28, 14, 30, tzinfo=timezone.utc)
    store.ingest(
        _sample(
            "AAPL",
            now + timedelta(seconds=1),
            1.0,
            225.0,
            0.02,
            ofi_norm=0.4,
            contract_id="AAPL-NQTV",
            l2_available=True,
            input_mode="l2_ofi",
        )
    )

    row = store.build_target_row("AAPL", now_utc=now, stale_seconds=2.0)
    assert row["target_age_s"] == -1.0
    assert row["input_mode"] == "l2_ofi"
    assert row["target_ofi_norm_15s"] == 0.4
