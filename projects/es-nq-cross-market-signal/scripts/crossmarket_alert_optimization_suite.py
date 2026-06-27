from __future__ import annotations

import argparse
import bisect
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from statistics import mean, median
from typing import Any

import numpy as np
from sklearn.cluster import KMeans
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import OneHotEncoder, StandardScaler


NUMERIC_FEATURES = (
    "p_hit",
    "p_up",
    "confidence",
    "target_spot",
    "target_spread",
    "target_ofi_5s",
    "target_ofi_15s",
    "target_ofi_60s",
    "target_ofi_300s",
    "target_ofi_15s_z",
    "target_ofi_60s_z",
    "target_ofi_300s_z",
    "target_large_trade_signed_60s",
    "target_large_trade_signed_300s",
    "target_large_trade_count_60s",
    "target_large_trade_count_300s",
    "target_large_trade_buy_ratio_300s",
    "cross_index_confirmation",
    "cross_index_contradiction",
    "cross_index_balance",
    "cross_index_lead_confirmation",
    "cross_index_lead_contradiction",
    "cross_index_lead_balance",
    "cross_index_score_avg",
    "cross_index_max_age_s",
    "macro_regime_score",
    "macro_lead_score",
    "macro_ready",
    "target_age_s",
)

CATEGORICAL_FEATURES = (
    "market",
    "direction",
    "status",
    "reason",
    "session_bucket",
)

_ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SpotSeries:
    timestamps: list[datetime]
    spots: list[float]


def _parse_hm(text: str) -> time:
    hour, minute = text.strip().split(":", 1)
    return time(int(hour), int(minute))


def _in_et_window(ts: datetime | None, start: time, end: time) -> bool:
    if ts is None:
        return False
    local = ts.astimezone(_ET).time()
    return start <= local < end


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
    return parsed if math.isfinite(parsed) else None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
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


def _spot_index(feature_rows: list[dict[str, Any]]) -> dict[str, SpotSeries]:
    grouped: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for row in feature_rows:
        market = str(row.get("market") or "").upper()
        ts = _parse_ts(row.get("timestamp_utc"))
        spot = _num(row.get("target_spot"))
        if market in {"ES", "NQ"} and ts is not None and spot is not None:
            grouped[market].append((ts, spot))
    out: dict[str, SpotSeries] = {}
    for market, pairs in grouped.items():
        pairs.sort(key=lambda item: item[0])
        out[market] = SpotSeries([item[0] for item in pairs], [item[1] for item in pairs])
    return out


def _layer_value(row: dict[str, Any], *path: str) -> Any:
    cur: Any = row
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _direction_sign(row: dict[str, Any]) -> int:
    direction = str(row.get("direction") or "").lower()
    if direction == "long":
        return 1
    if direction == "short":
        return -1
    return 0


def _sample_mfe_mae(row: dict[str, Any], spots: dict[str, SpotSeries]) -> dict[str, float | None]:
    market = str(row.get("market") or "").upper()
    series = spots.get(market)
    ts = _parse_ts(row.get("timestamp_utc"))
    horizon = int(row.get("horizon_minutes") or 0)
    sign = _direction_sign(row)
    entry = _num(row.get("target_spot"))
    if series is None or ts is None or horizon <= 0 or sign == 0:
        return {"mfe": None, "mae": None, "ratio": None}
    start = bisect.bisect_left(series.timestamps, ts)
    end = bisect.bisect_right(series.timestamps, ts + timedelta(minutes=horizon))
    if start >= len(series.spots) or end <= start:
        return {"mfe": None, "mae": None, "ratio": None}
    if entry is None:
        entry = series.spots[start]
    path = series.spots[start:end]
    if sign > 0:
        mfe = max(path) - entry
        mae = entry - min(path)
    else:
        mfe = entry - min(path)
        mae = max(path) - entry
    mfe = max(0.0, float(mfe))
    mae = max(0.0, float(mae))
    return {"mfe": mfe, "mae": mae, "ratio": mfe / max(mae, 0.25)}


def _flatten_features(row: dict[str, Any]) -> dict[str, Any]:
    layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}
    model = layers.get("model") if isinstance(layers.get("model"), dict) else {}
    context = layers.get("context") if isinstance(layers.get("context"), dict) else {}
    out = dict(row)
    out["session_bucket"] = context.get("session_bucket")
    out["direction_gap"] = model.get("direction_gap")
    out["raw_edge"] = model.get("raw_edge")
    out["p_up_model"] = model.get("p_up_model")
    out["p_hit_model"] = model.get("p_hit_model")
    return out


def _build_records(
    rows: list[dict[str, Any]],
    spots: dict[str, SpotSeries],
    *,
    et_start: time | None = None,
    et_end: time | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in rows:
        market = str(row.get("market") or "").upper()
        horizon = int(row.get("horizon_minutes") or 0)
        if market not in {"ES", "NQ"} or horizon not in {5, 10}:
            continue
        ts = _parse_ts(row.get("timestamp_utc"))
        if et_start is not None and et_end is not None and not _in_et_window(ts, et_start, et_end):
            continue
        outcomes = row.get("outcomes") if isinstance(row.get("outcomes"), dict) else {}
        block = outcomes.get(f"{horizon}m") if isinstance(outcomes.get(f"{horizon}m"), dict) else {}
        hit = block.get("directional_hit")
        fwd = _num(block.get("fwd_ret"))
        if hit is None or fwd is None:
            continue
        sign = _direction_sign(row)
        sampled = _sample_mfe_mae(row, spots)
        flat = _flatten_features(row)
        record = {
            "market": market,
            "horizon_minutes": horizon,
            "timestamp_utc": row.get("timestamp_utc"),
            "direction": row.get("direction"),
            "direction_sign": sign,
            "directional_hit": bool(hit),
            "directional_return": fwd * sign,
            "mfe_points": sampled["mfe"],
            "mae_points": sampled["mae"],
            "mfe_mae_ratio": sampled["ratio"],
        }
        for key in NUMERIC_FEATURES:
            record[key] = _num(flat.get(key))
        for key in CATEGORICAL_FEATURES:
            record[key] = str(flat.get(key) if flat.get(key) is not None else "__missing__")
        for key in ("direction_gap", "raw_edge", "p_up_model", "p_hit_model"):
            record[key] = _num(flat.get(key))
        records.append(record)
    records.sort(key=lambda item: str(item.get("timestamp_utc") or ""))
    return records


def _matrix(records: list[dict[str, Any]]) -> tuple[np.ndarray, list[str]]:
    numeric = [*NUMERIC_FEATURES, "direction_gap", "raw_edge", "p_up_model", "p_hit_model"]
    num_values = np.array([[record.get(key) if record.get(key) is not None else 0.0 for key in numeric] for record in records])
    num_scaled = StandardScaler().fit_transform(num_values) if len(records) else num_values
    cats = [[str(record.get(key) or "__missing__") for key in CATEGORICAL_FEATURES] for record in records]
    enc = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    cat_values = enc.fit_transform(cats) if cats else np.empty((len(records), 0))
    names = [*numeric, *[f"cat:{name}" for name in enc.get_feature_names_out(CATEGORICAL_FEATURES)]]
    return np.hstack([num_scaled, cat_values]), names


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {"n": 0}
    hits = [1 if record["directional_hit"] else 0 for record in records]
    returns = [float(record["directional_return"]) for record in records]
    mfes = [record["mfe_points"] for record in records if record.get("mfe_points") is not None]
    maes = [record["mae_points"] for record in records if record.get("mae_points") is not None]
    ratios = [record["mfe_mae_ratio"] for record in records if record.get("mfe_mae_ratio") is not None]
    return {
        "n": len(records),
        "hit_rate": mean(hits),
        "avg_directional_return": mean(returns),
        "avg_mfe_points": mean(mfes) if mfes else None,
        "avg_mae_points": mean(maes) if maes else None,
        "avg_mfe_mae_ratio": mean(ratios) if ratios else None,
        "median_mfe_mae_ratio": median(ratios) if ratios else None,
    }


def _kmeans(records: list[dict[str, Any]]) -> dict[str, Any]:
    if len(records) < 8:
        return {"status": "insufficient_rows", "baseline": _summarize(records), "clusters": []}
    x, names = _matrix(records)
    k = max(2, min(6, round(math.sqrt(len(records) / 2))))
    labels = KMeans(n_clusters=k, random_state=7, n_init=20).fit_predict(x)
    baseline = _summarize(records)
    clusters: list[dict[str, Any]] = []
    for cluster_id in sorted(set(labels)):
        idxs = [idx for idx, label in enumerate(labels) if label == cluster_id]
        cluster_records = [records[idx] for idx in idxs]
        summary = _summarize(cluster_records)
        summary["cluster"] = int(cluster_id)
        summary["hit_lift"] = (summary["hit_rate"] or 0) - (baseline["hit_rate"] or 0)
        clusters.append(summary)
    clusters.sort(key=lambda item: (item.get("avg_mfe_mae_ratio") or -999, item.get("hit_lift") or -999), reverse=True)
    return {"status": "ok", "k": k, "baseline": baseline, "clusters": clusters, "feature_count": len(names)}


def _rf(records: list[dict[str, Any]]) -> dict[str, Any]:
    ratios = [record["mfe_mae_ratio"] for record in records if record.get("mfe_mae_ratio") is not None]
    if len(records) < 24 or len(ratios) < 12:
        return {"status": "insufficient_rows"}
    cut = median(ratios)
    y = np.array([1 if (record.get("mfe_mae_ratio") or 0.0) >= cut and record["directional_hit"] else 0 for record in records])
    if len(set(y.tolist())) < 2:
        return {"status": "single_class_target", "target_cut": cut}
    x, names = _matrix(records)
    splits = min(4, max(2, len(records) // 25))
    fold_rows = []
    importances: dict[str, list[float]] = defaultdict(list)
    for train_idx, test_idx in TimeSeriesSplit(n_splits=splits).split(x):
        if len(set(y[train_idx].tolist())) < 2 or len(set(y[test_idx].tolist())) < 2:
            continue
        model = RandomForestClassifier(n_estimators=160, max_depth=4, min_samples_leaf=2, random_state=11)
        model.fit(x[train_idx], y[train_idx])
        prob = model.predict_proba(x[test_idx])[:, 1]
        auc = roc_auc_score(y[test_idx], prob)
        perm = permutation_importance(model, x[test_idx], y[test_idx], n_repeats=12, random_state=13)
        for idx, value in enumerate(perm.importances_mean):
            importances[names[idx]].append(float(value))
        fold_rows.append(
            {
                "train_n": int(len(train_idx)),
                "test_n": int(len(test_idx)),
                "test_target_rate": float(y[test_idx].mean()),
                "auc": float(auc),
            }
        )
    ranked = sorted(
        ({"feature": key, "importance": mean(values)} for key, values in importances.items() if values),
        key=lambda item: item["importance"],
        reverse=True,
    )[:15]
    return {"status": "ok", "target": f"hit and mfe_mae_ratio >= median {cut:.3f}", "folds": fold_rows, "top_importance": ranked}


def _analyze(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "summary": _summarize(records),
        "kmeans": _kmeans(records),
        "rf_walkforward": _rf(records),
    }


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Cross-Market Alert Optimization Suite",
        "",
        f"- generated_at_utc: `{payload['generated_at_utc']}`",
        f"- labeled_input: `{payload['labeled_input']}`",
        f"- feature_log: `{payload['feature_log']}`",
        f"- et_window: `{payload.get('et_window') or 'all'}`",
        f"- input_rows: `{payload.get('input_rows', 'n/a')}`",
        f"- analyzed_records: `{payload.get('records', 'n/a')}`",
        "",
    ]
    for key, block in payload["sections"].items():
        summary = block["summary"]
        lines.extend(
            [
                f"## {key}",
                "",
                (
                    f"- n={summary.get('n', 0)} hit_rate={summary.get('hit_rate', 0):.3f} "
                    f"avg_ret={summary.get('avg_directional_return', 0):.6f} "
                    f"avg_mfe={summary.get('avg_mfe_points') if summary.get('avg_mfe_points') is not None else 'n/a'} "
                    f"avg_mae={summary.get('avg_mae_points') if summary.get('avg_mae_points') is not None else 'n/a'} "
                    f"ratio={summary.get('avg_mfe_mae_ratio') if summary.get('avg_mfe_mae_ratio') is not None else 'n/a'}"
                ),
                "",
                "Top K-means clusters:",
                "",
            ]
        )
        for cluster in block["kmeans"].get("clusters", [])[:5]:
            lines.append(
                f"- cluster={cluster['cluster']} n={cluster['n']} hit={cluster['hit_rate']:.3f} "
                f"lift={cluster['hit_lift']:+.3f} ratio={cluster.get('avg_mfe_mae_ratio')}"
            )
        lines.extend(["", "Top RF features:", ""])
        for item in block["rf_walkforward"].get("top_importance", [])[:8]:
            lines.append(f"- {item['feature']}: {item['importance']:+.5f}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="K-means/RF/MFE-MAE style optimization for emitted cross-market alerts.")
    parser.add_argument("--labeled-input", default="data/labeled_alert_signals_5m_10m_2026-06-14.jsonl")
    parser.add_argument("--feature-log", default="data/cross_market_features.jsonl")
    parser.add_argument("--output-json", default="data/crossmarket_alert_optimization_suite_2026-06-14.json")
    parser.add_argument("--output-md", default="data/crossmarket_alert_optimization_suite_2026-06-14.md")
    parser.add_argument("--et-start", default=None, help="Inclusive ET session start, e.g. 09:30")
    parser.add_argument("--et-end", default=None, help="Exclusive ET session end, e.g. 16:00")
    parser.add_argument(
        "--rth-only",
        action="store_true",
        help="Evaluate alerts from 09:30 ET to 16:00 ET only.",
    )
    args = parser.parse_args()

    et_start = _parse_hm(args.et_start) if args.et_start else None
    et_end = _parse_hm(args.et_end) if args.et_end else None
    if args.rth_only:
        et_start = et_start or _parse_hm("09:30")
        et_end = et_end or _parse_hm("16:00")

    labeled_path = Path(args.labeled_input)
    feature_path = Path(args.feature_log)
    rows = _load_jsonl(labeled_path)
    spots = _spot_index(_load_jsonl(feature_path))
    records = _build_records(rows, spots, et_start=et_start, et_end=et_end)
    sections: dict[str, Any] = {}
    for market in ("ES", "NQ"):
        for horizon in (5, 10):
            key = f"{market}_{horizon}m"
            section_records = [record for record in records if record["market"] == market and int(record["horizon_minutes"]) == horizon]
            sections[key] = _analyze(section_records)
    et_window = None
    if et_start is not None and et_end is not None:
        et_window = f"{et_start.strftime('%H:%M')}-{et_end.strftime('%H:%M')} ET"
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "labeled_input": str(labeled_path),
        "feature_log": str(feature_path),
        "input_rows": len(rows),
        "records": len(records),
        "et_window": et_window,
        "sections": sections,
    }
    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    out_md.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps({"json": str(out_json), "markdown": str(out_md), "records": len(records)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
