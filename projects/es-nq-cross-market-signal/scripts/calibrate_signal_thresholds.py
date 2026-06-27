from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from esnq_signal.calibration import serialize_thresholds_config
from esnq_signal.signal import SignalThresholds

_ET = ZoneInfo("America/New_York")


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


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
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


@dataclass(frozen=True)
class LabeledTrade:
    timestamp_utc: str
    market: str
    horizon_minutes: int
    p_hit: float
    confidence: float
    cross_confirm: int
    direction_gap: float
    raw_edge: float
    session_bucket: str
    direction: str
    trade_return: float
    directional_hit: bool


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


def _session_bucket(value: Any) -> str:
    dt = _parse_ts(value)
    if dt is None:
        return "UNKNOWN"
    hour = dt.astimezone(_ET).hour
    if hour in {9, 10}:
        return "RTH_OPEN"
    if hour in {11, 12, 13}:
        return "RTH_MID"
    if hour in {14, 15}:
        return "RTH_CLOSE"
    return "OVERNIGHT"


def _extract_trades(
    rows: list[dict[str, Any]],
    *,
    market: str,
    horizon_minutes: int,
    allowed_statuses: set[str],
    max_abs_trade_return: float,
) -> list[LabeledTrade]:
    out: list[LabeledTrade] = []
    key = f"{horizon_minutes}m"
    for row in rows:
        if str(row.get("market") or "").upper() != market:
            continue
        if int(row.get("horizon_minutes") or 0) != horizon_minutes:
            continue
        status = str(row.get("status") or "").strip().lower()
        if allowed_statuses and status not in allowed_statuses:
            continue
        direction = str(row.get("direction") or "").lower()
        if direction not in {"long", "short"}:
            continue
        outcomes = row.get("outcomes")
        if not isinstance(outcomes, dict):
            continue
        block = outcomes.get(key)
        if not isinstance(block, dict):
            continue
        fwd_ret = _num(block.get("fwd_ret"))
        hit = block.get("directional_hit")
        p_hit = _num(row.get("p_hit"))
        confidence = _num(row.get("confidence"))
        p_up = _num(row.get("p_up"))
        layers = row.get("layers")
        cross_confirm = 0
        raw_edge = 0.0
        if isinstance(layers, dict):
            cross = layers.get("cross_index")
            if isinstance(cross, dict):
                try:
                    cross_confirm = int(
                        cross.get("support_used")
                        if cross.get("support_used") is not None
                        else max(int(cross.get("confirmation") or 0), int(cross.get("lead_confirmation") or 0))
                    )
                except (TypeError, ValueError):
                    cross_confirm = 0
            raw = _num(layers.get("direction_core_raw"))
            raw_edge = abs(float(raw)) if raw is not None else 0.0
        if fwd_ret is None or not isinstance(hit, bool) or p_hit is None or confidence is None or p_up is None:
            continue
        signed_return = fwd_ret if direction == "long" else (-fwd_ret)
        if abs(float(signed_return)) > max_abs_trade_return:
            continue
        out.append(
            LabeledTrade(
                timestamp_utc=str(row.get("timestamp_utc") or ""),
                market=market,
                horizon_minutes=horizon_minutes,
                p_hit=float(p_hit),
                confidence=float(confidence),
                cross_confirm=max(0, cross_confirm),
                direction_gap=abs(float(p_up) - 0.5),
                raw_edge=max(0.0, float(raw_edge)),
                session_bucket=_session_bucket(row.get("timestamp_utc")),
                direction=direction,
                trade_return=float(signed_return),
                directional_hit=bool(hit),
            )
        )
    out.sort(key=lambda item: item.timestamp_utc)
    return out


def _max_drawdown(returns: list[float]) -> float:
    if not returns:
        return 0.0
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for value in returns:
        equity *= max(1e-12, 1.0 + float(value))
        if equity > peak:
            peak = equity
        drawdown = 1.0 - (equity / peak)
        if drawdown > max_dd:
            max_dd = drawdown
    return float(max_dd)


def _metrics(trades: list[LabeledTrade]) -> dict[str, float]:
    if not trades:
        return {"n": 0, "win_rate": 0.0, "avg_return": 0.0, "max_drawdown": 0.0}
    returns = [trade.trade_return for trade in trades]
    wins = [1.0 if trade.directional_hit else 0.0 for trade in trades]
    return {
        "n": float(len(trades)),
        "win_rate": float(mean(wins)),
        "avg_return": float(mean(returns)),
        "max_drawdown": _max_drawdown(returns),
    }


def _search_thresholds(
    trades: list[LabeledTrade],
    *,
    min_trades: int,
    min_win_rate: float,
    max_drawdown: float,
    max_direction_share: float,
    min_one_sided_trades: int,
    allow_direction_locked: bool,
) -> tuple[SignalThresholds, dict[str, float], str]:
    if not trades:
        return SignalThresholds(), {"n": 0.0, "win_rate": 0.0, "avg_return": 0.0, "max_drawdown": 0.0}, "no_data"

    p_hit_grid = [0.45, 0.55, 0.65, 0.75, 0.85, 0.90]
    confidence_grid = [0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]
    cross_grid = [0, 1, 2, 3]
    direction_gap_grid = [0.00, 0.04, 0.08, 0.12, 0.16, 0.20]
    raw_edge_grid = [0.00, 1.00, 2.00, 2.50, 3.00, 3.50, 4.00]
    session_grid = ["any", "RTH_OPEN", "RTH_MID", "RTH_CLOSE", "OVERNIGHT"]
    direction_grid = ["any", "long", "short"] if allow_direction_locked else ["any"]

    best: tuple[SignalThresholds, dict[str, float], float] | None = None
    fallback: tuple[SignalThresholds, dict[str, float], float] | None = None

    for session_filter in session_grid:
        for direction_filter in direction_grid:
            scope = [
                trade
                for trade in trades
                if (session_filter == "any" or trade.session_bucket == session_filter)
                and (direction_filter == "any" or trade.direction == direction_filter)
            ]
            if not scope:
                continue
            for p_hit_min in p_hit_grid:
                for confidence_min in confidence_grid:
                    for cross_confirm_min in cross_grid:
                        for direction_gap_min in direction_gap_grid:
                            for raw_edge_min in raw_edge_grid:
                                selected = [
                                    trade
                                    for trade in scope
                                    if trade.p_hit >= p_hit_min
                                    and trade.confidence >= confidence_min
                                    and trade.cross_confirm >= cross_confirm_min
                                    and trade.direction_gap >= direction_gap_min
                                    and trade.raw_edge >= raw_edge_min
                                ]
                                metrics = _metrics(selected)
                                n = int(metrics["n"])
                                if n == 0:
                                    continue
                                long_n = sum(1 for trade in selected if trade.direction == "long")
                                short_n = sum(1 for trade in selected if trade.direction == "short")
                                dominant_share = float(max(long_n, short_n)) / float(n)
                                # Prefer two-sided trade sets when filter is open; this reduces persistent one-way bias.
                                if direction_filter == "any" and dominant_share > max_direction_share:
                                    continue
                                # Direction-locked gates are allowed only with larger evidence size.
                                if direction_filter != "any" and n < max(min_one_sided_trades, (2 * min_trades)):
                                    continue
                                score = (
                                    (metrics["win_rate"] * 100.0)
                                    + (metrics["avg_return"] * 20000.0)
                                    - (metrics["max_drawdown"] * 3000.0)
                                    + (min(0.5, n / 5000.0))
                                )
                                if direction_filter != "any":
                                    score -= 2.0
                                thresholds = SignalThresholds(
                                    post_p_hit_min=p_hit_min,
                                    post_confidence_min=confidence_min,
                                    post_cross_confirm_min=cross_confirm_min,
                                    post_direction_gap_min=direction_gap_min,
                                    post_raw_edge_min=raw_edge_min,
                                    allowed_session=session_filter,
                                    allowed_direction=direction_filter,
                                    high_p_hit_min=min(0.95, p_hit_min + 0.08),
                                    high_confidence_min=min(0.95, confidence_min + 0.05),
                                    high_cross_confirm_min=min(3, cross_confirm_min + 1),
                                    high_direction_gap_min=min(0.45, direction_gap_min + 0.03),
                                    high_raw_edge_min=min(6.0, raw_edge_min + 0.35),
                                )
                                if n >= min_trades:
                                    if fallback is None or score > fallback[2]:
                                        fallback = (thresholds, metrics, score)
                                if n < min_trades or metrics["win_rate"] < min_win_rate or metrics["max_drawdown"] > max_drawdown:
                                    continue
                                if best is None or score > best[2]:
                                    best = (thresholds, metrics, score)

    if best is not None:
        return best[0], best[1], "live_ok"
    if fallback is not None:
        return fallback[0], fallback[1], "research_only"
    return SignalThresholds(), {"n": 0.0, "win_rate": 0.0, "avg_return": 0.0, "max_drawdown": 0.0}, "no_fit"


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# Signal Threshold Calibration")
    lines.append("")
    lines.append(f"- generated_at_utc: `{payload.get('generated_at_utc')}`")
    lines.append(f"- labeled_input: `{payload.get('labeled_input')}`")
    lines.append(f"- global_status: `{payload.get('global_status')}`")
    lines.append("")
    lines.append(
        "| Market | Horizon | Status | N | Win Rate | Avg Return | Max Drawdown | p_hit >= | conf >= | cross >= | gap >= | edge >= | Session | Direction |"
    )
    lines.append("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|")
    for row in payload.get("summary", []):
        lines.append(
            f"| {row['market']} | {row['horizon_minutes']} | {row['status']} | {int(row['n'])} | "
            f"{row['win_rate']:.3f} | {row['avg_return']:.6f} | {row['max_drawdown']:.6f} | "
            f"{row['post_p_hit_min']:.2f} | {row['post_confidence_min']:.2f} | {int(row['post_cross_confirm_min'])} | "
            f"{row['post_direction_gap_min']:.2f} | {row['post_raw_edge_min']:.2f} | {row['allowed_session']} | {row['allowed_direction']} |"
        )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate live threshold gates from labeled 5m/10m signal outcomes.")
    parser.add_argument("--labeled-input", default="data/labeled_signals_5m_10m.jsonl")
    parser.add_argument("--output-json", default="data/signal_calibration.json")
    parser.add_argument("--output-md", default="data/signal_calibration.md")
    parser.add_argument("--min-trades", type=int, default=40)
    parser.add_argument("--min-win-rate", type=float, default=0.65)
    parser.add_argument("--max-drawdown", type=float, default=0.02)
    parser.add_argument(
        "--allowed-statuses",
        default="watch,post,high_priority",
        help="Comma-separated list of statuses to include for calibration.",
    )
    parser.add_argument(
        "--max-abs-trade-return",
        type=float,
        default=0.02,
        help="Drop outlier labeled returns above this absolute threshold.",
    )
    parser.add_argument(
        "--max-direction-share",
        type=float,
        default=0.90,
        help="When allowed_direction is any, reject candidate gates where one side exceeds this share.",
    )
    parser.add_argument(
        "--min-one-sided-trades",
        type=int,
        default=300,
        help="Minimum selected trades required before allowing direction-locked gates (long-only/short-only).",
    )
    parser.add_argument(
        "--allow-direction-locked",
        action="store_true",
        help="Allow long-only or short-only calibrated gates. Default keeps direction filter at any.",
    )
    args = parser.parse_args()

    rows = _load_jsonl(Path(args.labeled_input))
    thresholds: dict[str, dict[int, SignalThresholds]] = {}
    summary: list[dict[str, Any]] = []
    global_status = "live_ok"
    allowed_statuses = {
        item.strip().lower()
        for item in str(args.allowed_statuses).split(",")
        if item.strip()
    }
    for market in ("ES", "NQ"):
        thresholds[market] = {}
        for horizon in (5, 10):
            trades = _extract_trades(
                rows,
                market=market,
                horizon_minutes=horizon,
                allowed_statuses=allowed_statuses,
                max_abs_trade_return=max(0.0001, float(args.max_abs_trade_return)),
            )
            selected, metrics, status = _search_thresholds(
                trades,
                min_trades=max(1, int(args.min_trades)),
                min_win_rate=float(args.min_win_rate),
                max_drawdown=max(0.0, float(args.max_drawdown)),
                max_direction_share=min(0.99, max(0.50, float(args.max_direction_share))),
                min_one_sided_trades=max(1, int(args.min_one_sided_trades)),
                allow_direction_locked=bool(args.allow_direction_locked),
            )
            thresholds[market][horizon] = selected
            if status != "live_ok":
                global_status = "research_only"
            summary.append(
                {
                    "market": market,
                    "horizon_minutes": horizon,
                    "status": status,
                    "n": metrics["n"],
                    "win_rate": metrics["win_rate"],
                    "avg_return": metrics["avg_return"],
                    "max_drawdown": metrics["max_drawdown"],
                    "post_p_hit_min": selected.post_p_hit_min,
                    "post_confidence_min": selected.post_confidence_min,
                    "post_cross_confirm_min": selected.post_cross_confirm_min,
                    "post_direction_gap_min": selected.post_direction_gap_min,
                    "post_raw_edge_min": selected.post_raw_edge_min,
                    "allowed_session": selected.allowed_session,
                    "allowed_direction": selected.allowed_direction,
                    "high_p_hit_min": selected.high_p_hit_min,
                    "high_confidence_min": selected.high_confidence_min,
                    "high_cross_confirm_min": selected.high_cross_confirm_min,
                    "high_direction_gap_min": selected.high_direction_gap_min,
                    "high_raw_edge_min": selected.high_raw_edge_min,
                }
            )

    payload = serialize_thresholds_config(thresholds)
    payload["generated_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload["labeled_input"] = str(args.labeled_input)
    payload["global_status"] = global_status
    payload["constraints"] = {
        "min_trades": int(args.min_trades),
        "min_win_rate": float(args.min_win_rate),
        "max_drawdown": float(args.max_drawdown),
        "allowed_statuses": sorted(list(allowed_statuses)),
        "max_abs_trade_return": float(args.max_abs_trade_return),
        "max_direction_share": float(args.max_direction_share),
        "min_one_sided_trades": int(args.min_one_sided_trades),
        "allow_direction_locked": bool(args.allow_direction_locked),
    }
    payload["summary"] = summary

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    _write_markdown(Path(args.output_md), payload)
    print(f"global_status={global_status}")
    print(f"wrote: {output_json}")
    print(f"wrote: {args.output_md}")


if __name__ == "__main__":
    main()
