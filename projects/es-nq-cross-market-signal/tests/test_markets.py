from esnq_signal.markets import normalize_sierra_symbol


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

