from __future__ import annotations

import argparse
import bisect
import json
import math
from dataclasses import dataclass
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


@dataclass(frozen=True)
class SpotSeries:
    timestamps: list[datetime]
    spots: list[float]

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
    return r * math.sqrt((n - 2) / max(1e-12, (1.0 - (r * r))))


def _feature_vector(row: dict[str, Any]) -> dict[str, float | None]:
    macro_votes = row.get("macro_votes")
    if not isinstance(macro_votes, dict):
        macro_votes = {}
    macro_ofi = row.get("macro_ofi_values")
    if not isinstance(macro_ofi, dict):
        macro_ofi = {}
    macro_lag30_ofi = row.get("macro_lag30_ofi_values")
    if not isinstance(macro_lag30_ofi, dict):
        macro_lag30_ofi = {}
    cross_vals = row.get("cross_index_values")
    if not isinstance(cross_vals, dict):
        cross_vals = {}
    cross_lag30 = row.get("cross_index_lag30_values")
    if not isinstance(cross_lag30, dict):
        cross_lag30 = {}
    return {
        "target_ofi_15s_z": _num(row.get("target_ofi_15s_z")),
        "target_ofi_60s_z": _num(row.get("target_ofi_60s_z")),
        "target_ofi_300s_z": _num(row.get("target_ofi_300s_z")),
        "target_ofi_15s": _num(row.get("target_ofi_15s")),
        "target_ofi_60s": _num(row.get("target_ofi_60s")),
        "cross_index_balance": _num(row.get("cross_index_balance")),
        "cross_index_lead_balance": _num(row.get("cross_index_lead_balance")),
        "cross_index_score_avg": _num(row.get("cross_index_score_avg")),
        "cross_es_nq_value": _num(cross_vals.get("ES")) if _num(cross_vals.get("ES")) is not None else _num(cross_vals.get("NQ")),
        "cross_ym_value": _num(cross_vals.get("YM")),
        "cross_rty_value": _num(cross_vals.get("RTY")),
        "cross_es_nq_lag30": _num(cross_lag30.get("ES")) if _num(cross_lag30.get("ES")) is not None else _num(cross_lag30.get("NQ")),
        "cross_ym_lag30": _num(cross_lag30.get("YM")),
        "cross_rty_lag30": _num(cross_lag30.get("RTY")),
        "macro_regime_score": _num(row.get("macro_regime_score")),
        "macro_lead_score": _num(row.get("macro_lead_score")),
        "usd_proxy_e6_vote": _num(macro_votes.get("E6")),
        "usd_proxy_e6_ofi": _num(macro_ofi.get("E6")),
        "usd_proxy_e6_ofi_lag30": _num(macro_lag30_ofi.get("E6")),
        "bonds_zn_vote": _num(macro_votes.get("ZN")),
        "bonds_zb_vote": _num(macro_votes.get("ZB")),
        "bonds_zn_ofi": _num(macro_ofi.get("ZN")),
        "bonds_zb_ofi": _num(macro_ofi.get("ZB")),
        "bonds_zn_ofi_lag30": _num(macro_lag30_ofi.get("ZN")),
        "bonds_zb_ofi_lag30": _num(macro_lag30_ofi.get("ZB")),
        "oil_cl_vote": _num(macro_votes.get("CL")),
        "oil_cl_ofi": _num(macro_ofi.get("CL")),
        "oil_cl_ofi_lag30": _num(macro_lag30_ofi.get("CL")),
        "gold_gc_vote": _num(macro_votes.get("GC")),
        "gold_gc_ofi": _num(macro_ofi.get("GC")),
        "gold_gc_ofi_lag30": _num(macro_lag30_ofi.get("GC")),
        "vol_vx_vote": _num(macro_votes.get("VX")),
        "vol_vx_ofi": _num(macro_ofi.get("VX")),
        "vol_vx_ofi_lag30": _num(macro_lag30_ofi.get("VX")),
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
        rec = {
            "timestamp_utc": ts.isoformat(timespec="milliseconds"),
            "market": market,
            "horizon_minutes": horizon_minutes,
            "fwd_ret": fwd_ret,
        }
        rec.update(_feature_vector(row))
        out.append(rec)
    return out


def _correlation_table(dataset: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not dataset:
        return []
    predictors = [key for key in dataset[0].keys() if key not in {"timestamp_utc", "market", "horizon_minutes", "fwd_ret"}]
    results: list[dict[str, Any]] = []
    for predictor in predictors:
        xs: list[float] = []
        ys: list[float] = []
        for row in dataset:
            x = _num(row.get(predictor))
            y = _num(row.get("fwd_ret"))
            if x is None or y is None:
                continue
            xs.append(x)
            ys.append(y)
        r = _pearson(xs, ys)
        if r is None:
            continue
        t_stat = _t_stat_from_r(r, len(xs))
        results.append(
            {
                "predictor": predictor,
                "n": len(xs),
                "pearson_r": r,
                "abs_r": abs(r),
                "t_stat": t_stat,
            }
        )
    results.sort(key=lambda item: (item.get("abs_r") or 0.0), reverse=True)
    return results


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# Cross-Market Correlation Report")
    lines.append("")
    lines.append(f"- generated_at_utc: `{payload.get('generated_at_utc')}`")
    lines.append(f"- feature_log: `{payload.get('feature_log')}`")
    lines.append("")
    for key in sorted(payload.get("results", {}).keys()):
        item = payload["results"][key]
        lines.append(f"## {key}")
        lines.append("")
        lines.append(f"- rows: `{item.get('rows', 0)}`")
        lines.append("")
        lines.append("| Predictor | N | Pearson r | t-stat |")
        lines.append("|---|---:|---:|---:|")
        for row in item.get("top", []):
            lines.append(
                f"| {row.get('predictor')} | {row.get('n')} | {float(row.get('pearson_r') or 0.0):.4f} | "
                f"{float(row.get('t_stat') or 0.0):.3f} |"
            )
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze cross-market predictor correlations vs 5m/10m ES/NQ returns.")
    parser.add_argument("--feature-log", default="data/cross_market_features.jsonl")
    parser.add_argument("--output-json", default="data/cross_market_correlation_report.json")
    parser.add_argument("--output-md", default="data/cross_market_correlation_report.md")
    args = parser.parse_args()

    feature_path = Path(args.feature_log)
    rows = _load_jsonl(feature_path)
    spot_index = _build_spot_index(rows)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    payload: dict[str, Any] = {
        "generated_at_utc": generated_at,
        "feature_log": str(feature_path),
        "results": {},
    }
    for market in ("ES", "NQ"):
        for horizon in (5, 10):
            key = f"{market}_{horizon}m"
            dataset = _build_dataset(rows, spot_index, market=market, horizon_minutes=horizon)
            table = _correlation_table(dataset)
            payload["results"][key] = {
                "rows": len(dataset),
                "top": table[:15],
            }

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
    _write_markdown(Path(args.output_md), payload)
    print(f"wrote: {output_json}")
    print(f"wrote: {args.output_md}")


if __name__ == "__main__":
    main()
