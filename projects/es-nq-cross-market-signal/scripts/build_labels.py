from __future__ import annotations

import argparse
import bisect
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
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


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


@dataclass(frozen=True)
class SpotSeries:
    timestamps: list[datetime]
    spots: list[float]

    def point_at_or_after(self, ts: datetime) -> tuple[datetime, float] | None:
        idx = bisect.bisect_left(self.timestamps, ts)
        if idx >= len(self.spots):
            return None
        return self.timestamps[idx], self.spots[idx]


def _build_spot_index(feature_rows: list[dict[str, Any]]) -> dict[str, SpotSeries]:
    grouped: dict[str, list[tuple[datetime, float]]] = {}
    for row in feature_rows:
        market = str(row.get("market") or "").upper()
        ts = _parse_ts(row.get("timestamp_utc"))
        spot = row.get("target_spot")
        if market not in {"ES", "NQ"} or ts is None or spot is None:
            continue
        try:
            spot_value = float(spot)
        except (TypeError, ValueError):
            continue
        grouped.setdefault(market, []).append((ts, spot_value))
    out: dict[str, SpotSeries] = {}
    for market, pairs in grouped.items():
        pairs.sort(key=lambda pair: pair[0])
        out[market] = SpotSeries(
            timestamps=[ts for ts, _ in pairs],
            spots=[spot for _, spot in pairs],
        )
    return out


def _direction_sign(value: str) -> int:
    text = str(value or "").lower()
    if text == "long":
        return 1
    if text == "short":
        return -1
    return 0


def _fallback_outcome(
    row: dict[str, Any],
    *,
    horizon_minutes: int,
    direction_sign: int,
    max_abs_forward_return: float,
) -> dict[str, Any] | None:
    outcomes = row.get("outcomes")
    if not isinstance(outcomes, dict):
        return None
    block = outcomes.get(f"{horizon_minutes}m")
    if not isinstance(block, dict):
        return None
    fwd_ret = _float_or_none(block.get("fwd_ret"))
    if fwd_ret is None or abs(fwd_ret) > max_abs_forward_return:
        return None
    hit = block.get("directional_hit")
    if not isinstance(hit, bool) and direction_sign != 0:
        hit = (fwd_ret > 0 and direction_sign > 0) or (fwd_ret < 0 and direction_sign < 0)
    future_spot = _float_or_none(block.get("future_spot"))
    delay_seconds = _float_or_none(block.get("delay_seconds"))
    future_ts = str(
        block.get("future_timestamp_utc")
        or block.get("future_ts_utc")
        or block.get("future_ts")
        or ""
    ).strip()
    return {
        "future_timestamp_utc": future_ts or None,
        "future_spot": round(float(future_spot), 6) if future_spot is not None else None,
        "fwd_ret": round(float(fwd_ret), 8),
        "delay_seconds": round(float(delay_seconds), 3) if delay_seconds is not None else None,
        "directional_hit": hit if isinstance(hit, bool) else None,
    }


def _label_signal(
    row: dict[str, Any],
    spot_index: dict[str, SpotSeries],
    *,
    include_blocked: bool,
    max_future_delay_seconds: int,
    max_abs_forward_return: float,
) -> dict[str, Any] | None:
    market = str(row.get("market") or "").upper()
    if market not in {"ES", "NQ"}:
        return None
    status = str(row.get("status") or "").lower()
    if not include_blocked and status == "blocked":
        return None
    ts = _parse_ts(row.get("timestamp_utc"))
    if ts is None:
        return None
    try:
        spot_now = float(row.get("target_spot"))
    except (TypeError, ValueError):
        return None
    series = spot_index.get(market)
    if series is None:
        return None

    direction_sign = _direction_sign(str(row.get("direction") or ""))
    enriched = dict(row)
    outcomes: dict[str, Any] = {}
    invalid_horizons = 0
    for minutes in (5, 10):
        fallback = _fallback_outcome(
            row,
            horizon_minutes=minutes,
            direction_sign=direction_sign,
            max_abs_forward_return=max_abs_forward_return,
        )
        target_ts = ts + timedelta(minutes=minutes)
        point = series.point_at_or_after(target_ts)
        if point is None or spot_now <= 0:
            outcomes[f"{minutes}m"] = fallback
            if fallback is None:
                invalid_horizons += 1
            continue
        future_ts, future_spot = point
        if future_spot <= 0:
            outcomes[f"{minutes}m"] = fallback
            if fallback is None:
                invalid_horizons += 1
            continue
        delay_seconds = max(0.0, (future_ts - target_ts).total_seconds())
        if delay_seconds > float(max_future_delay_seconds):
            outcomes[f"{minutes}m"] = fallback
            if fallback is None:
                invalid_horizons += 1
            continue
        fwd_ret = (future_spot / spot_now) - 1.0
        if abs(fwd_ret) > max_abs_forward_return:
            outcomes[f"{minutes}m"] = fallback
            if fallback is None:
                invalid_horizons += 1
            continue
        hit = None
        if direction_sign != 0:
            hit = (fwd_ret > 0 and direction_sign > 0) or (fwd_ret < 0 and direction_sign < 0)
        outcomes[f"{minutes}m"] = {
            "future_timestamp_utc": future_ts.isoformat(timespec="milliseconds"),
            "future_spot": round(float(future_spot), 6),
            "fwd_ret": round(float(fwd_ret), 8),
            "delay_seconds": round(float(delay_seconds), 3),
            "directional_hit": hit,
        }
    if invalid_horizons >= 2:
        return None
    enriched["outcomes"] = outcomes
    return enriched


def main() -> None:
    parser = argparse.ArgumentParser(description="Build 5m/10m labels for standalone ES/NQ signals.")
    parser.add_argument("--feature-log", default="data/cross_market_features.jsonl")
    parser.add_argument("--signal-log", default="data/es_nq_direction_signals.jsonl")
    parser.add_argument("--output", default="data/labeled_signals_5m_10m.jsonl")
    parser.add_argument(
        "--include-blocked",
        action="store_true",
        help="Include blocked rows in labeled output (default excludes blocked).",
    )
    parser.add_argument(
        "--max-future-delay-seconds",
        type=int,
        default=120,
        help="Maximum allowed timestamp delay after target horizon.",
    )
    parser.add_argument(
        "--max-abs-forward-return",
        type=float,
        default=0.02,
        help="Maximum absolute forward return to keep (outlier guard).",
    )
    args = parser.parse_args()

    feature_rows = _load_jsonl(Path(args.feature_log))
    signal_rows = _load_jsonl(Path(args.signal_log))
    spot_index = _build_spot_index(feature_rows)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in signal_rows:
            labeled = _label_signal(
                row,
                spot_index,
                include_blocked=bool(args.include_blocked),
                max_future_delay_seconds=max(0, int(args.max_future_delay_seconds)),
                max_abs_forward_return=max(0.0001, float(args.max_abs_forward_return)),
            )
            if labeled is None:
                continue
            handle.write(json.dumps(labeled, ensure_ascii=True, default=str) + "\n")


if __name__ == "__main__":
    main()
