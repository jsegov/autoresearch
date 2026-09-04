from esnq_signal.markets import (
    MEGA_EQUITY_COLLECTION_ROOTS,
    MEGA_EQUITY_SPECS,
    normalize_sierra_symbol,
    product_profile,
)


def test_symbol_normalization_maps_supported_markets() -> None:
    assert normalize_sierra_symbol("F.US.EPM26") == "ES"
    assert normalize_sierra_symbol("F.US.ENQM26") == "NQ"
    assert normalize_sierra_symbol("F.US.YMM26") == "YM"
    assert normalize_sierra_symbol("F.US.RTYM26") == "RTY"
    assert normalize_sierra_symbol("F.US.E6M26") == "E6"
    assert normalize_sierra_symbol("F.US.ZNM26") == "ZN"
    assert normalize_sierra_symbol("F.US.ZBM26") == "ZB"
    assert normalize_sierra_symbol("F.US.CLM26") == "CL"
    assert normalize_sierra_symbol("F.US.GCM26") == "GC"
    assert normalize_sierra_symbol("F.US.UBZ26") == "UB"
    assert normalize_sierra_symbol("F.US.TNZ26") == "TN"
    assert normalize_sierra_symbol("F.US.ZFZ26") == "ZF"
    assert normalize_sierra_symbol("F.US.RBQ26") == "RB"
    assert normalize_sierra_symbol("F.US.HOQ26") == "HO"
    assert normalize_sierra_symbol("F.US.NGQ26") == "NG"


def test_product_profiles_define_rules_first_horizons_and_peers() -> None:
    cl = product_profile("CL")
    ub = product_profile("UB")

    assert cl is not None and cl.rules_first is True
    assert cl.primary_peers == ("RB", "HO")
    assert cl.horizons_minutes == (5, 10)
    assert cl.block_strong_opposing_primary is True
    assert cl.primary_alert_horizon_minutes == 5
    assert ub is not None and ub.rules_first is True
    assert ub.primary_peers == ("ZB", "TN", "ZN")
    assert ub.horizons_minutes == (10, 30)
    assert ub.block_strong_opposing_primary is True
    assert ub.primary_alert_horizon_minutes == 10


def test_mega_equity_registry_and_profiles_are_canonical() -> None:
    assert tuple(MEGA_EQUITY_SPECS) == (
        "AAPL", "GOOG", "GOOGL", "MSFT", "META", "TSLA", "SPCX", "NVDA", "AMZN"
    )
    assert MEGA_EQUITY_SPECS["AAPL"].sector_etf == "XLK"
    assert MEGA_EQUITY_SPECS["NVDA"].sector_etf == "SMH"
    assert MEGA_EQUITY_SPECS["SPCX"].sector_etf == "ITA"
    assert MEGA_EQUITY_SPECS["GOOG"].issuer_id == "ALPHABET"
    assert MEGA_EQUITY_SPECS["GOOGL"].issuer_id == "ALPHABET"
    assert {"QQQ", "XLK", "SMH", "XLC", "XLY", "ITA"}.issubset(
        set(MEGA_EQUITY_COLLECTION_ROOTS)
    )
    for symbol, spec in MEGA_EQUITY_SPECS.items():
        profile = product_profile(symbol)
        assert profile is not None
        assert profile.asset_class == "equity"
        assert profile.horizons_minutes == (5, 15)
        assert profile.primary_alert_horizon_minutes == 5
        assert profile.benchmark_market == "QQQ"
        assert profile.sector_market == spec.sector_etf
        assert profile.issuer_id == spec.issuer_id


def test_equity_symbol_normalization_is_anchored_and_qualifier_aware() -> None:
    assert normalize_sierra_symbol("AAPL") == "AAPL"
    assert normalize_sierra_symbol("AAPL-NQTV") == "AAPL"
    assert normalize_sierra_symbol("NASDAQ:GOOGL") == "GOOGL"
    assert normalize_sierra_symbol("MSFT_STK_SMART") == "MSFT"
    assert normalize_sierra_symbol("F.US.QQQ") == "QQQ"
    assert normalize_sierra_symbol("AAPL-BATS") == "AAPL"
    assert normalize_sierra_symbol("MBO:MSFT") == "MSFT"
    assert normalize_sierra_symbol("NVDA-NMS") == "NVDA"
    assert normalize_sierra_symbol("Q:AMZN") == "AMZN"
    assert normalize_sierra_symbol("TSLA-NQTV.scid") == "TSLA"
    assert normalize_sierra_symbol("METADATA") == ""
    assert normalize_sierra_symbol("GOOGLX") == ""
    assert normalize_sierra_symbol("FAKEAAPL") == ""
    assert normalize_sierra_symbol("AAPL-GOOG") == ""
    assert normalize_sierra_symbol("AAPL-UNKNOWNFEED") == ""
