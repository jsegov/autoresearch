from __future__ import annotations

import argparse
import heapq
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, TextIO

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from esnq_signal.calibration import load_thresholds_config
from esnq_signal.features import CrossMarketFeatureStore
from esnq_signal.large_trades import LargeTradeSample, parse_large_trade_payload
from esnq_signal.model import load_models
from esnq_signal.ofi_stream import OfiSample, parse_ofi_payload, parse_ts_utc
from esnq_signal.signal import DirectionSignalEngine


def _write_jsonl_handle(handle: TextIO, payload: dict[str, Any]) -> None:
    handle.write(json.dumps(payload, ensure_ascii=True, default=str) + "\n")


def _parse_bound(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    return parse_ts_utc(text)


def _resolve_sources(source_args: list[str], glob_args: list[str]) -> list[Path]:
    sources: list[Path] = []
    for raw in source_args:
        text = str(raw or "").strip()
        if not text:
            continue
        sources.append(Path(text))
    for pattern in glob_args:
        pat = str(pattern or "").strip()
        if not pat:
            continue
        sources.extend(sorted(Path().glob(pat)))
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in sources:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


@dataclass(frozen=True)
class ReplayEvent:
    timestamp_utc: datetime
    kind: str
    payload: OfiSample | LargeTradeSample


def _iter_ofi_events(path: Path) -> Iterator[ReplayEvent]:
    source_tag = f"replay_jsonl:{path.name}"
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            sample = parse_ofi_payload(payload, source=source_tag)
            if sample is None:
                continue
            yield ReplayEvent(timestamp_utc=sample.timestamp_utc, kind="ofi", payload=sample)


def _iter_large_trade_events(path: Path) -> Iterator[ReplayEvent]:
    source_tag = f"replay_large_trades:{path.name}"
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            sample = parse_large_trade_payload(payload, source=source_tag)
            if sample is None:
                continue
            yield ReplayEvent(timestamp_utc=sample.timestamp_utc, kind="large_trade", payload=sample)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay Sierra OFI JSONL into standalone cross-market feature and signal logs."
    )
    parser.add_argument(
        "--source-jsonl",
        action="append",
        default=[],
        help="Source JSONL file path. Repeat for multiple files. Defaults to ES_NQ_OFI_1s.jsonl when omitted.",
    )
    parser.add_argument(
        "--source-glob",
        action="append",
        default=[],
        help="Optional glob pattern(s) to include additional source files (e.g. C:/SierraChart/Data/CrossMarket*.jsonl).",
    )
    parser.add_argument(
        "--large-trade-jsonl",
        action="append",
        default=[],
        help="Optional large-trade JSONL path. Repeat for multiple files. Defaults to sierra_large_trades.jsonl when present.",
    )
    parser.add_argument(
        "--large-trade-glob",
        action="append",
        default=[],
        help="Optional glob pattern(s) for large-trade source files.",
    )
    parser.add_argument("--feature-log", default="data/cross_market_features.jsonl")
    parser.add_argument("--signal-log", default="data/es_nq_direction_signals.jsonl")
    parser.add_argument("--reset", action="store_true", help="Truncate output logs before replay")
    parser.add_argument("--max-rows", type=int, default=0, help="0 means no limit")
    parser.add_argument("--from-ts", default="", help="Inclusive UTC ISO8601 lower bound")
    parser.add_argument("--to-ts", default="", help="Inclusive UTC ISO8601 upper bound")
    parser.add_argument("--emit-interval-seconds", type=float, default=1.0)
    parser.add_argument("--stale-seconds", type=float, default=2.0)
    parser.add_argument("--max-spread-es", type=float, default=1.0)
    parser.add_argument("--max-spread-nq", type=float, default=2.0)
    parser.add_argument("--calibration-file", default="data/signal_calibration.json")
    parser.add_argument("--model-file", default="data/signal_models.json")
    args = parser.parse_args()

    source_args: list[str] = list(args.source_jsonl or [])
    if not source_args and not args.source_glob:
        source_args = ["C:/SierraChart/Data/ES_NQ_OFI_1s.jsonl"]
    ofi_sources = _resolve_sources(source_args, list(args.source_glob or []))
    if not ofi_sources:
        raise SystemExit("no source files resolved")
    for source in ofi_sources:
        if not source.exists():
            raise SystemExit(f"source file not found: {source}")

    large_trade_args: list[str] = list(args.large_trade_jsonl or [])
    if not large_trade_args and not args.large_trade_glob:
        default_large_trades = Path("C:/SierraChart/Data/sierra_large_trades.jsonl")
        if default_large_trades.exists():
            large_trade_args = [str(default_large_trades)]
    large_trade_sources = _resolve_sources(large_trade_args, list(args.large_trade_glob or []))
    for source in large_trade_sources:
        if not source.exists():
            raise SystemExit(f"large-trade source file not found: {source}")

    feature_log = Path(args.feature_log)
    signal_log = Path(args.signal_log)
    if args.reset:
        feature_log.parent.mkdir(parents=True, exist_ok=True)
        signal_log.parent.mkdir(parents=True, exist_ok=True)
        feature_log.write_text("", encoding="utf-8")
        signal_log.write_text("", encoding="utf-8")
    feature_log.parent.mkdir(parents=True, exist_ok=True)
    signal_log.parent.mkdir(parents=True, exist_ok=True)

    calibration_path = Path(args.calibration_file) if str(args.calibration_file).strip() else None
    if calibration_path is not None and not calibration_path.exists():
        calibration_path = None
    model_path = Path(args.model_file) if str(args.model_file).strip() else None
    if model_path is not None and not model_path.exists():
        model_path = None

    store = CrossMarketFeatureStore()
    thresholds = load_thresholds_config(calibration_path)
    models = load_models(model_path)
    engine = DirectionSignalEngine(
        max_spread_es=max(0.01, float(args.max_spread_es)),
        max_spread_nq=max(0.01, float(args.max_spread_nq)),
        thresholds_by_market_horizon=thresholds,
        models_by_market_horizon=models,
    )

    lower_ts = _parse_bound(args.from_ts)
    upper_ts = _parse_bound(args.to_ts)
    emit_interval_seconds = max(0.2, float(args.emit_interval_seconds))
    stale_seconds = max(0.1, float(args.stale_seconds))
    last_emit: dict[str, datetime] = {}

    processed = 0
    samples_kept = 0
    ofi_samples_kept = 0
    large_trade_samples_kept = 0
    feature_rows = 0
    signal_rows = 0

    iterators: list[Iterator[ReplayEvent]] = [_iter_ofi_events(path) for path in ofi_sources]
    iterators.extend(_iter_large_trade_events(path) for path in large_trade_sources)
    heap: list[tuple[datetime, int, ReplayEvent]] = []
    for idx, it in enumerate(iterators):
        event = next(it, None)
        if event is None:
            continue
        heapq.heappush(heap, (event.timestamp_utc, idx, event))

    with (
        feature_log.open("a", encoding="utf-8") as feature_out,
        signal_log.open("a", encoding="utf-8") as signal_out,
    ):
        while heap:
            _, idx, event = heapq.heappop(heap)
            next_event = next(iterators[idx], None)
            if next_event is not None:
                heapq.heappush(heap, (next_event.timestamp_utc, idx, next_event))

            processed += 1
            if args.max_rows > 0 and processed > int(args.max_rows):
                break
            if lower_ts is not None and event.timestamp_utc < lower_ts:
                continue
            if upper_ts is not None and event.timestamp_utc > upper_ts:
                continue

            samples_kept += 1
            now_utc = event.timestamp_utc.astimezone(timezone.utc)
            if event.kind == "ofi":
                payload = event.payload
                if not isinstance(payload, OfiSample):
                    continue
                store.ingest(payload)
                ofi_samples_kept += 1
            elif event.kind == "large_trade":
                payload = event.payload
                if not isinstance(payload, LargeTradeSample):
                    continue
                store.ingest_large_trade(
                    payload.market,
                    ts_utc=payload.timestamp_utc,
                    side=payload.side,
                    volume=payload.volume,
                )
                large_trade_samples_kept += 1
            else:
                continue

            for target in ("ES", "NQ"):
                prev = last_emit.get(target)
                if prev is not None:
                    elapsed = (now_utc - prev).total_seconds()
                    if elapsed < emit_interval_seconds:
                        continue
                row = store.build_target_row(target, now_utc=now_utc, stale_seconds=stale_seconds)
                if row.get("status") != "ok":
                    continue
                _write_jsonl_handle(feature_out, row)
                feature_rows += 1
                for horizon in (5, 10):
                    signal = engine.evaluate(row, horizon_minutes=horizon)
                    signal_payload = signal.to_dict()
                    signal_payload["feature_row_timestamp_utc"] = row.get("timestamp_utc")
                    signal_payload["target_spot"] = row.get("target_spot")
                    signal_payload["target_spread"] = row.get("target_spread")
                    _write_jsonl_handle(signal_out, signal_payload)
                    signal_rows += 1
                last_emit[target] = now_utc

    print("sources=" + ";".join(str(path) for path in ofi_sources))
    print("large_trade_sources=" + ";".join(str(path) for path in large_trade_sources))
    print(f"processed_rows={processed}")
    print(f"samples_kept={samples_kept}")
    print(f"ofi_samples_kept={ofi_samples_kept}")
    print(f"large_trade_samples_kept={large_trade_samples_kept}")
    print(f"feature_rows={feature_rows}")
    print(f"signal_rows={signal_rows}")
    print(f"feature_log={feature_log}")
    print(f"signal_log={signal_log}")


if __name__ == "__main__":
    main()
