from __future__ import annotations

import asyncio
import json
import math
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
    return parsed if math.isfinite(parsed) else None


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
    # CKS/CCZ fields (CrossMarket_OFI_Export.cpp >= 2026-07-01):
    #   ofi_cks  - best-level OFI, Cont/Kukanov/Stoikov 2014 sec 2.2 eq (2)-(3)
    #   ofi_deep - per-level OFI summed over tracked levels, CCZ 2023 sec 2.1
    #   ofi_norm - ofi_deep / EMA(avg depth), CCZ normalization (scale-free)
    # None (default) when the exporter DLL predates the fix.
    ofi_cks: float | None = None
    ofi_deep: float | None = None
    ofi_norm: float | None = None
    # Authoritative contract identifier supplied by Sierra.  Never derive
    # rolls from a calendar; a changed non-empty value is the roll event.
    contract_id: str | None = None
    # Native-equity Time & Sales fallback.  The exporter reports a bounded
    # 15-second bid/ask-attributed imbalance and never aliases it to L2 OFI.
    trade_imbalance_15s: float | None = None
    trade_imbalance_norm: float | None = None
    trade_volume_15s: float | None = None
    l2_available: bool | None = None
    input_mode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "symbol_raw": self.symbol_raw,
            "contract_id": self.contract_id,
            "timestamp_utc": self.timestamp_utc.isoformat(timespec="milliseconds"),
            "ofi_1s": self.ofi_1s,
            "ofi_cks": self.ofi_cks,
            "ofi_deep": self.ofi_deep,
            "ofi_norm": self.ofi_norm,
            "trade_imbalance_15s": self.trade_imbalance_15s,
            "trade_imbalance_norm": self.trade_imbalance_norm,
            "trade_volume_15s": self.trade_volume_15s,
            "l2_available": self.l2_available,
            "input_mode": self.input_mode,
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
    ofi_norm = _num(payload.get("ofi_norm"))
    l2_raw = payload.get("l2_available")
    l2_available = bool(l2_raw) if isinstance(l2_raw, bool) else (ofi_norm is not None)
    mode_raw = str(payload.get("input_mode") or "").strip().lower()
    input_mode = mode_raw if mode_raw in {"l2_ofi", "trade_imbalance"} else None
    return OfiSample(
        market=market,
        symbol_raw=symbol_raw,
        timestamp_utc=ts,
        ofi_1s=_num(payload.get("ofi") if payload.get("ofi") is not None else payload.get("ofi_1s")),
        ofi_cks=_num(payload.get("ofi_cks")),
        ofi_deep=_num(payload.get("ofi_deep")),
        ofi_norm=ofi_norm,
        contract_id=str(payload.get("contract_id") or symbol_raw).strip() or None,
        spread=_num(payload.get("spread")),
        bid_depth=_num(payload.get("bid_depth")),
        ask_depth=_num(payload.get("ask_depth")),
        spot=_num(payload.get("spot")),
        bar_delta=_num(payload.get("bar_delta")),
        source=source,
        trade_imbalance_15s=_num(payload.get("trade_imbalance_15s")),
        trade_imbalance_norm=_num(payload.get("trade_imbalance_norm")),
        trade_volume_15s=_num(payload.get("trade_volume_15s")),
        l2_available=l2_available,
        input_mode=input_mode,
    )


def effective_ofi(sample: "OfiSample") -> float | None:
    """Corrected OFI with legacy fallback.

    Prefers ofi_cks (Cont/Kukanov/Stoikov 2014 - same contract units as the
    legacy field, so calibrated magnitudes stay comparable); falls back to the
    legacy depth-delta ofi_1s when the exporter has not been rebuilt yet.
    Per CROSS_MARKET_EVIDENCE.md, thresholds are re-estimated from logs before
    promotion - re-run calibration after the exporter upgrade."""
    if sample.ofi_cks is not None:
        return sample.ofi_cks
    return sample.ofi_1s


def normalized_ofi(sample: "OfiSample") -> float | None:
    """Scale-free OFI for cross-product votes, with no raw fallback."""
    return sample.ofi_norm


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
