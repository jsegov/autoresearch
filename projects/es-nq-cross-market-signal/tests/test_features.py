from datetime import datetime, timedelta, timezone

from esnq_signal.features import CrossMarketFeatureStore
from esnq_signal.ofi_stream import OfiSample


def _sample(market: str, ts: datetime, ofi: float, spot: float, spread: float = 0.5) -> OfiSample:
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
