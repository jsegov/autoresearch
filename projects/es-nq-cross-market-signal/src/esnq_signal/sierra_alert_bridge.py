"""Atomic TSV pulse writer for Prop Alpha Signal Deployer.

Duplicates the trading-events-v2 ``external_alert_signals.tsv`` contract so
this service does not import that package. Write only after Discord actually
posts (``send_if_needed`` returned True).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

HEADER = "market\tside\tsource\tepoch_utc\tstop_points\ttarget_points\talert_id\n"
ALLOWED_MARKETS = frozenset({"ES", "NQ", "CL", "UB"})
SOURCE = "cross_market"
DEFAULT_BRIDGE_PATH = r"C:\SierraChart\Data\external_alert_signals.tsv"


@dataclass(frozen=True)
class AlertPulse:
    market: str
    side: int
    source: str
    epoch_utc: float
    stop_points: float
    target_points: float
    alert_id: str


def _side_from_direction(raw: Any) -> int:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = int(raw)
        return 1 if value > 0 else (-1 if value < 0 else 0)
    text = str(raw or "").strip().lower()
    if text in {"1", "long", "buy", "bull"}:
        return 1
    if text in {"-1", "short", "sell", "bear"}:
        return -1
    return 0


def _epoch_utc(value: Any) -> float | None:
    if isinstance(value, datetime):
        stamp = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return stamp.timestamp()
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def pulse_from_cross_market_payload(payload: dict[str, Any]) -> AlertPulse | None:
    market = str(payload.get("market") or "").upper().strip()
    if market not in ALLOWED_MARKETS:
        return None
    side = _side_from_direction(payload.get("direction"))
    if side == 0:
        return None
    epoch = _epoch_utc(payload.get("timestamp_utc") or payload.get("feature_row_timestamp_utc"))
    if epoch is None:
        return None
    horizon = payload.get("horizon_minutes")
    try:
        horizon_minutes = int(horizon) if horizon is not None else 0
    except (TypeError, ValueError):
        horizon_minutes = 0
    direction = "long" if side > 0 else "short"
    timestamp = str(payload.get("timestamp_utc") or "").strip()
    return AlertPulse(
        market=market,
        side=side,
        source=SOURCE,
        epoch_utc=epoch,
        stop_points=0.0,
        target_points=0.0,
        alert_id=f"cross_market:{market}:{horizon_minutes}:{direction}:{timestamp}",
    )


def format_pulse_row(pulse: AlertPulse) -> str:
    return (
        f"{pulse.market}\t{pulse.side}\t{pulse.source}\t"
        f"{pulse.epoch_utc:.3f}\t{pulse.stop_points:g}\t{pulse.target_points:g}\t"
        f"{pulse.alert_id}\n"
    )


def parse_pulse_row(line: str) -> AlertPulse | None:
    text = line.strip()
    if not text or text.startswith("#") or text.startswith("market\t"):
        return None
    fields = text.split("\t")
    if len(fields) < 7:
        return None
    market = fields[0].strip().upper()
    source = fields[2].strip().lower()
    if market not in ALLOWED_MARKETS:
        return None
    side = _side_from_direction(fields[1])
    if side == 0:
        return None
    epoch = _epoch_utc(fields[3])
    if epoch is None:
        return None
    alert_id = fields[6].strip()
    if not alert_id:
        return None
    try:
        stop_points = max(0.0, float(fields[4]))
        target_points = max(0.0, float(fields[5]))
    except ValueError:
        stop_points = 0.0
        target_points = 0.0
    return AlertPulse(
        market=market,
        side=side,
        source=source,
        epoch_utc=epoch,
        stop_points=stop_points,
        target_points=target_points,
        alert_id=alert_id,
    )


def read_alert_pulses(path: Path) -> dict[str, AlertPulse]:
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    pulses: dict[str, AlertPulse] = {}
    for line in text.splitlines():
        pulse = parse_pulse_row(line)
        if pulse is None:
            continue
        pulses[pulse.market] = pulse
    return pulses


def write_alert_pulse(path: Path, pulse: AlertPulse) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_alert_pulses(path)
    existing[pulse.market] = pulse
    body = HEADER + "".join(format_pulse_row(existing[market]) for market in sorted(existing))
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(body, encoding="utf-8", newline="\n")
    os.replace(tmp_path, path)


def emit_cross_market_pulse(path: Path, payload: dict[str, Any]) -> bool:
    pulse = pulse_from_cross_market_payload(payload)
    if pulse is None:
        return False
    write_alert_pulse(path, pulse)
    logger.info(
        "sierra alert bridge pulsed market=%s side=%s source=%s alert_id=%s",
        pulse.market,
        pulse.side,
        pulse.source,
        pulse.alert_id,
    )
    return True
