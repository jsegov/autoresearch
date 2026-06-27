from __future__ import annotations

import argparse
import bisect
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
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


class SpotSeries:
    def __init__(self, timestamps: list[datetime], spots: list[float]) -> None:
        self.timestamps = timestamps
        self.spots = spots

    def spot_at_or_after(self, ts: datetime) -> float | None:
        idx = bisect.bisect_left(self.timestamps, ts)
        if idx >= len(self.spots):
            return None
        return self.spots[idx]


def _build_spot_index(feature_rows: list[dict[str, Any]]) -> dict[str, SpotSeries]:
    grouped: dict[str, list[tuple[datetime, float]]] = {}
    for row in feature_rows:
        market = str(row.get("market") or "").upper()
        if market not in {"ES", "NQ"}:
            continue
        ts = _parse_ts(row.get("timestamp_utc"))
        spot = _num(row.get("target_spot"))
        if ts is None or spot is None:
            continue
        grouped.setdefault(market, []).append((ts, spot))

    out: dict[str, SpotSeries] = {}
    for market, pairs in grouped.items():
        pairs.sort(key=lambda pair: pair[0])
        out[market] = SpotSeries(
            timestamps=[ts for ts, _ in pairs],
            spots=[spot for _, spot in pairs],
        )
    return out


def _extract_predictors(row: dict[str, Any]) -> dict[str, float | None]:
    cross_vals = row.get("cross_index_values")
    if not isinstance(cross_vals, dict):
        cross_vals = {}
    cross_lag30 = row.get("cross_index_lag30_values")
    if not isinstance(cross_lag30, dict):
        cross_lag30 = {}
    macro_vals = row.get("macro_ofi_values")
    if not isinstance(macro_vals, dict):
        macro_vals = {}
    macro_lag30 = row.get("macro_lag30_ofi_values")
    if not isinstance(macro_lag30, dict):
        macro_lag30 = {}
    return {
        "target_ofi_15s_z": _num(row.get("target_ofi_15s_z")),
        "target_ofi_60s_z": _num(row.get("target_ofi_60s_z")),
        "target_ofi_300s_z": _num(row.get("target_ofi_300s_z")),
        "cross_es_nq": _num(cross_vals.get("ES")) if _num(cross_vals.get("ES")) is not None else _num(cross_vals.get("NQ")),
        "cross_ym": _num(cross_vals.get("YM")),
        "cross_rty": _num(cross_vals.get("RTY")),
        "cross_es_nq_lag30": _num(cross_lag30.get("ES")) if _num(cross_lag30.get("ES")) is not None else _num(cross_lag30.get("NQ")),
        "cross_ym_lag30": _num(cross_lag30.get("YM")),
        "cross_rty_lag30": _num(cross_lag30.get("RTY")),
        "cross_index_balance": _num(row.get("cross_index_balance")),
        "cross_index_lead_balance": _num(row.get("cross_index_lead_balance")),
        "macro_regime_score": _num(row.get("macro_regime_score")),
        "macro_lead_score": _num(row.get("macro_lead_score")),
        "usd_e6_ofi": _num(macro_vals.get("E6")),
        "usd_e6_ofi_lag30": _num(macro_lag30.get("E6")),
        "bonds_zn_ofi": _num(macro_vals.get("ZN")),
        "bonds_zn_ofi_lag30": _num(macro_lag30.get("ZN")),
        "bonds_zb_ofi": _num(macro_vals.get("ZB")),
        "bonds_zb_ofi_lag30": _num(macro_lag30.get("ZB")),
        "oil_cl_ofi": _num(macro_vals.get("CL")),
        "oil_cl_ofi_lag30": _num(macro_lag30.get("CL")),
        "gold_gc_ofi": _num(macro_vals.get("GC")),
        "gold_gc_ofi_lag30": _num(macro_lag30.get("GC")),
        "vol_vx_ofi": _num(macro_vals.get("VX")),
        "vol_vx_ofi_lag30": _num(macro_lag30.get("VX")),
    }


def _build_dataset(
    feature_rows: list[dict[str, Any]],
    spot_index: dict[str, SpotSeries],
    *,
    market: str,
    horizon_minutes: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    series = spot_index.get(market)
    if series is None:
        return out
    for row in feature_rows:
        if str(row.get("market") or "").upper() != market:
            continue
        ts = _parse_ts(row.get("timestamp_utc"))
        spot_now = _num(row.get("target_spot"))
        if ts is None or spot_now is None or spot_now <= 0:
            continue
        future = series.spot_at_or_after(ts + timedelta(minutes=horizon_minutes))
        if future is None:
            continue
        fwd_ret = (future / spot_now) - 1.0
        rec: dict[str, Any] = {
            "_ts": ts,
            "timestamp_utc": ts.isoformat(timespec="milliseconds"),
            "market": market,
            "horizon_minutes": horizon_minutes,
            "fwd_ret": float(fwd_ret),
        }
        rec.update(_extract_predictors(row))
        out.append(rec)
    out.sort(key=lambda item: item["_ts"])
    return out


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    x_mean = mean(xs)
    y_mean = mean(ys)
    cov = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True))
    var_x = sum((x - x_mean) ** 2 for x in xs)
    var_y = sum((y - y_mean) ** 2 for y in ys)
    if var_x <= 1e-12 or var_y <= 1e-12:
        return None
    return cov / math.sqrt(var_x * var_y)


def _t_stat_from_r(r: float, n: int) -> float | None:
    if n < 3:
        return None
    if abs(r) >= 0.999999:
        return None
    return r * math.sqrt((n - 2) / max(1e-12, 1.0 - (r * r)))


def _lag_correlation(dataset: list[dict[str, Any]], predictor: str, lag_seconds: int) -> dict[str, Any] | None:
    xs: list[float] = []
    ys: list[float] = []

    pred_ts: list[datetime] = []
    pred_vals: list[float] = []
    for row in dataset:
        x = _num(row.get(predictor))
        if x is None:
            continue
        pred_ts.append(row["_ts"])
        pred_vals.append(x)
    if len(pred_ts) < 10:
        return None

    for row in dataset:
        y = _num(row.get("fwd_ret"))
        if y is None:
            continue
        target_ts = row["_ts"] - timedelta(seconds=max(0, int(lag_seconds)))
        idx = bisect.bisect_right(pred_ts, target_ts) - 1
        if idx < 0:
            continue
        xs.append(pred_vals[idx])
        ys.append(y)

    r = _pearson(xs, ys)
    if r is None:
        return None
    return {
        "predictor": predictor,
        "lag_seconds": int(lag_seconds),
        "n": len(xs),
        "pearson_r": float(r),
        "abs_r": abs(float(r)),
        "t_stat": _t_stat_from_r(float(r), len(xs)),
    }


def _analyze_dataset(dataset: list[dict[str, Any]], *, lags_seconds: list[int]) -> dict[str, Any]:
    if not dataset:
        return {"rows": 0, "lags_seconds": lags_seconds, "top_overall": [], "best_by_predictor": []}
    predictors = [
        key
        for key in dataset[0].keys()
        if key not in {"_ts", "timestamp_utc", "market", "horizon_minutes", "fwd_ret"}
    ]
    all_results: list[dict[str, Any]] = []
    best_by_predictor: list[dict[str, Any]] = []
    for predictor in predictors:
        per_lag: list[dict[str, Any]] = []
        for lag_seconds in lags_seconds:
            stats = _lag_correlation(dataset, predictor, lag_seconds)
            if stats is not None and int(stats["n"]) >= 30:
                per_lag.append(stats)
                all_results.append(stats)
        if per_lag:
            best = max(per_lag, key=lambda item: item.get("abs_r", 0.0))
            best_by_predictor.append(best)
    all_results.sort(key=lambda item: item.get("abs_r", 0.0), reverse=True)
    best_by_predictor.sort(key=lambda item: item.get("abs_r", 0.0), reverse=True)
    return {
        "rows": len(dataset),
        "lags_seconds": lags_seconds,
        "top_overall": all_results[:30],
        "best_by_predictor": best_by_predictor,
    }


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# Cross-Market Lead-Lag Report")
    lines.append("")
    lines.append(f"- generated_at_utc: `{payload.get('generated_at_utc')}`")
    lines.append(f"- feature_log: `{payload.get('feature_log')}`")
    lines.append(f"- lag_grid_seconds: `{payload.get('lag_grid_seconds')}`")
    lines.append("")
    for key in sorted(payload.get("results", {}).keys()):
        item = payload["results"][key]
        lines.append(f"## {key}")
        lines.append("")
        lines.append(f"- rows: `{item.get('rows', 0)}`")
        lines.append("")
        lines.append("| Predictor | Best Lag (s) | N | Pearson r | t-stat |")
        lines.append("|---|---:|---:|---:|---:|")
        for row in item.get("best_by_predictor", [])[:15]:
            lines.append(
                f"| {row.get('predictor')} | {int(row.get('lag_seconds') or 0)} | {int(row.get('n') or 0)} | "
                f"{float(row.get('pearson_r') or 0.0):.4f} | {float(row.get('t_stat') or 0.0):.3f} |"
            )
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _parse_lags(raw: str) -> list[int]:
    out: list[int] = []
    for piece in str(raw or "").split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            lag = int(piece)
        except ValueError:
            continue
        if lag >= 0:
            out.append(lag)
    if not out:
        return [0, 5, 15, 30, 60, 120, 180, 300]
    out = sorted(list(set(out)))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze cross-market lead-lag predictor correlations vs 5m/10m ES/NQ returns."
    )
    parser.add_argument("--feature-log", default="data/cross_market_features.jsonl")
    parser.add_argument("--output-json", default="data/cross_market_lead_lag_report.json")
    parser.add_argument("--output-md", default="data/cross_market_lead_lag_report.md")
    parser.add_argument("--lags", default="0,5,15,30,60,120,180,300")
    args = parser.parse_args()

    lag_grid = _parse_lags(args.lags)
    feature_path = Path(args.feature_log)
    rows = _load_jsonl(feature_path)
    spot_index = _build_spot_index(rows)

    payload: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "feature_log": str(feature_path),
        "lag_grid_seconds": lag_grid,
        "results": {},
    }
    for market in ("ES", "NQ"):
        for horizon in (5, 10):
            key = f"{market}_{horizon}m"
            dataset = _build_dataset(rows, spot_index, market=market, horizon_minutes=horizon)
            payload["results"][key] = _analyze_dataset(dataset, lags_seconds=lag_grid)

    out_json = Path(args.output_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
    _write_markdown(Path(args.output_md), payload)
    print(f"wrote: {out_json}")
    print(f"wrote: {args.output_md}")


if __name__ == "__main__":
    main()
