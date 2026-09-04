from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .markets import normalize_sierra_symbol
from .ofi_stream import parse_ts_utc


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


@dataclass(frozen=True)
class LargeTradeSample:
    market: str
    symbol_raw: str
    timestamp_utc: datetime
    side: str
    volume: float
    sequence: int | None
    source: str


def parse_large_trade_payload(payload: dict[str, Any], source: str) -> LargeTradeSample | None:
    symbol_raw = str(payload.get("symbol") or payload.get("market") or "").strip()
    market = normalize_sierra_symbol(symbol_raw) or str(payload.get("market") or "").strip().upper()
    if not market:
        return None

    ts = parse_ts_utc(
        payload.get("event_time_utc")
        or payload.get("ts")
        or payload.get("timestamp_utc")
        or payload.get("timestamp")
    )
    if ts is None:
        return None

    side_raw = str(payload.get("side") or "").strip().lower()
    if side_raw in {"buy", "bid", "b"}:
        side = "buy"
    elif side_raw in {"sell", "ask", "s"}:
        side = "sell"
    else:
        return None

    volume = _num(payload.get("volume"))
    if volume is None or volume <= 0:
        return None

    sequence_raw = payload.get("sequence")
    sequence: int | None = None
    if sequence_raw is not None:
        try:
            sequence = int(sequence_raw)
        except (TypeError, ValueError):
            sequence = None

    return LargeTradeSample(
        market=market,
        symbol_raw=symbol_raw,
        timestamp_utc=ts,
        side=side,
        volume=float(volume),
        sequence=sequence,
        source=source,
    )
