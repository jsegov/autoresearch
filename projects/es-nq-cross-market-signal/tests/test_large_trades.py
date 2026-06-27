from __future__ import annotations

from esnq_signal.large_trades import parse_large_trade_payload


def test_parse_large_trade_payload_maps_symbol_and_side() -> None:
    payload = {
        "event_time_utc": "2026-05-25T12:23:41.500Z",
        "symbol": "F.US.ENQM26",
        "sequence": 7470,
        "volume": 10,
        "side": "ask",
    }
    sample = parse_large_trade_payload(payload, source="test")
    assert sample is not None
    assert sample.market == "NQ"
    assert sample.side == "sell"
    assert sample.volume == 10.0
    assert sample.sequence == 7470


def test_parse_large_trade_payload_rejects_unknown_side() -> None:
    payload = {
        "event_time_utc": "2026-05-25T12:23:41.500Z",
        "market": "ES",
        "sequence": 7647,
        "volume": 158,
        "side": "unknown",
    }
    sample = parse_large_trade_payload(payload, source="test")
    assert sample is None
