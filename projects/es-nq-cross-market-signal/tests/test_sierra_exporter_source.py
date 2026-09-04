from pathlib import Path


SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "sierra_chart"
    / "CrossMarket_OFI_Export.cpp"
)


def test_exporter_emits_raw_contract_id_and_resets_state_on_symbol_change() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")

    assert '<< ",\\\"contract_id\\\":\\\"" << symbol' in source
    assert "SCString& prior_contract_symbol = sc.GetPersistentSCString(6200);" in source
    assert "prior_contract_upper != symbol_upper" in source
    assert "ResetOfiPersistentState(sc);" in source
    assert "sc.GetPersistentInt(6006) = 0;" in source
    assert "sc.GetPersistentDouble(6140) = 0.0;" in source


def test_exporter_default_filter_contains_experimental_targets_and_primary_peers() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")

    for token in ("UB", "CL", "RB", "HO", "ZB", "TN", "ZN"):
        assert token in source


def test_exporter_has_anchored_equities_and_distinct_time_sales_fallback() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")

    for token in (
        "AAPL", "GOOG", "GOOGL", "MSFT", "META", "TSLA", "SPCX", "NVDA", "AMZN",
        "QQQ", "XLK", "SMH", "XLC", "XLY", "ITA",
    ):
        assert token in source
    assert "DetectEquityRootFromSymbol" in source
    assert "IsEquityFeedQualifier" in source
    for qualifier in ("BATS", "MBO", "NMS", "Q", "SCID"):
        assert f'"{qualifier}"' in source
    assert "c_SCTimeAndSalesArray time_sales" in source
    assert "SC_TS_ASK" in source and "SC_TS_BID" in source
    assert "trade_imbalance_norm" in source
    assert "l2_available" in source
    assert '(l2_available ? "l2_ofi" : "trade_imbalance")' in source
    assert "!is_native_equity || !trade_imbalance.has_classified_volume" in source


def test_trade_fallback_window_is_anchored_to_current_export_clock() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")

    assert "sc.CurrentDateTimeForReplay : sc.CurrentSystemDateTime" in source
    assert "window_end -= sc.TimeScaleAdjustment" in source
    assert "window_end_value - (15.0" in source
    assert "record_datetime > window_end_value + future_tolerance" in source
