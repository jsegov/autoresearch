from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .markets import normalize_sierra_symbol


def parse_ts_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OSError):
            return None
    text = str(value).strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


@dataclass(frozen=True)
class OfiSample:
    market: str
    symbol_raw: str
    timestamp_utc: datetime
    ofi_1s: float | None
    spread: float | None
    bid_depth: float | None
    ask_depth: float | None
    spot: float | None
    bar_delta: float | None
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "symbol_raw": self.symbol_raw,
            "timestamp_utc": self.timestamp_utc.isoformat(timespec="milliseconds"),
            "ofi_1s": self.ofi_1s,
            "spread": self.spread,
            "bid_depth": self.bid_depth,
            "ask_depth": self.ask_depth,
            "spot": self.spot,
            "bar_delta": self.bar_delta,
            "source": self.source,
        }


def parse_ofi_payload(payload: dict[str, Any], source: str) -> OfiSample | None:
    symbol_raw = str(payload.get("symbol") or payload.get("market") or "").strip()
    market = normalize_sierra_symbol(symbol_raw) or str(payload.get("market") or "").strip().upper()
    if not market:
        return None
    ts = parse_ts_utc(payload.get("ts") or payload.get("timestamp_utc") or payload.get("timestamp"))
    if ts is None:
        return None
    return OfiSample(
        market=market,
        symbol_raw=symbol_raw,
        timestamp_utc=ts,
        ofi_1s=_num(payload.get("ofi") if payload.get("ofi") is not None else payload.get("ofi_1s")),
        spread=_num(payload.get("spread")),
        bid_depth=_num(payload.get("bid_depth")),
        ask_depth=_num(payload.get("ask_depth")),
        spot=_num(payload.get("spot")),
        bar_delta=_num(payload.get("bar_delta")),
        source=source,
    )


OnSample = Callable[[OfiSample], Awaitable[None]]


class OfiTcpStream:
    def __init__(
        self,
        *,
        name: str,
        host: str,
        port: int,
        on_sample: OnSample,
        reconnect_seconds: float = 1.0,
        read_timeout_seconds: float = 1.0,
    ) -> None:
        self._name = name
        self._host = host
        self._port = port
        self._on_sample = on_sample
        self._reconnect_seconds = max(0.2, float(reconnect_seconds))
        self._read_timeout_seconds = max(0.2, float(read_timeout_seconds))
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            writer: asyncio.StreamWriter | None = None
            try:
                reader, writer = await asyncio.open_connection(self._host, self._port)
                while not self._stop.is_set():
                    try:
                        line = await asyncio.wait_for(reader.readline(), timeout=self._read_timeout_seconds)
                    except asyncio.TimeoutError:
                        continue
                    if not line:
                        raise ConnectionError(f"{self._name}: stream closed")
                    decoded = line.decode("utf-8", errors="ignore").strip()
                    if not decoded:
                        continue
                    try:
                        payload = json.loads(decoded)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    sample = parse_ofi_payload(payload, source=self._name)
                    if sample is None:
                        continue
                    await self._on_sample(sample)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                if writer is not None:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
            if not self._stop.is_set():
                await asyncio.sleep(self._reconnect_seconds)

    def stop(self) -> None:
        self._stop.set()

