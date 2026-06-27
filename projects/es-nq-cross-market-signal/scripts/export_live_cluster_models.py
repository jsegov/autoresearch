from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, time, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from esnq_signal.cluster_features import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    flatten_signal_features,
)

# Research suite shares the same K-means recipe (random_state=7, spot-aware rows).
import crossmarket_alert_optimization_suite as cms  # noqa: E402

MIN_BEST_CLUSTER_N = 30


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


def _build_records(
    rows: list[dict[str, Any]],
    spots: dict[str, cms.SpotSeries],
    *,
    rth_only: bool,
) -> list[dict[str, Any]]:
    et_start = et_end = None
    if rth_only:
        et_start, et_end = time(9, 30), time(16, 0)
    cms_records = cms._build_records(rows, spots, et_start=et_start, et_end=et_end)
    records: list[dict[str, Any]] = []
    for record in cms_records:
        flat = flatten_signal_features(record)
        row = {"market": record["market"], "horizon_minutes": record["horizon_minutes"], "directional_hit": record["directional_hit"]}
        for key in NUMERIC_FEATURES:
            value = flat.get(key)
            row[key] = float(value) if value is not None else None
        for key in CATEGORICAL_FEATURES:
            row[key] = str(flat.get(key) if flat.get(key) is not None else "__missing__")
        records.append(row)
    return records


def _matrix(records: list[dict[str, Any]]) -> tuple[np.ndarray, StandardScaler, OneHotEncoder]:
    numeric = np.array(
        [[record.get(key) if record.get(key) is not None else 0.0 for key in NUMERIC_FEATURES] for record in records]
    )
    scaler = StandardScaler()
    num_scaled = scaler.fit_transform(numeric) if len(records) else numeric
    cats = [[str(record.get(key) or "__missing__") for key in CATEGORICAL_FEATURES] for record in records]
    enc = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    cat_values = enc.fit_transform(cats) if cats else np.empty((len(records), 0))
    return np.hstack([num_scaled, cat_values]), scaler, enc


def _pick_best_cluster(
    records: list[dict[str, Any]],
    labels: np.ndarray,
    *,
    min_cluster_n: int = MIN_BEST_CLUSTER_N,
) -> dict[str, Any]:
    baseline_hits = [1 if record["directional_hit"] else 0 for record in records]
    baseline_rate = mean(baseline_hits) if baseline_hits else 0.0
    ranked: list[dict[str, Any]] = []
    for cluster_id in sorted(set(labels.tolist())):
        idxs = [idx for idx, label in enumerate(labels) if label == cluster_id]
        cluster_records = [records[idx] for idx in idxs]
        hits = [1 if record["directional_hit"] else 0 for record in cluster_records]
        hit_rate = mean(hits) if hits else 0.0
        ranked.append(
            {
                "cluster_id": int(cluster_id),
                "n": len(cluster_records),
                "hit_rate": hit_rate,
                "hit_lift": hit_rate - baseline_rate,
            }
        )
    min_n = max(1, int(min_cluster_n))
    eligible = [row for row in ranked if row["n"] >= min_n]
    if not eligible:
        eligible = ranked
    eligible.sort(key=lambda item: (item["hit_lift"], item["hit_rate"], item["n"]), reverse=True)
    return eligible[0]


def _export_lane(records: list[dict[str, Any]], *, min_cluster_n: int = MIN_BEST_CLUSTER_N) -> dict[str, Any] | None:
    if len(records) < 8:
        return None
    x, scaler, enc = _matrix(records)
    k = max(2, min(6, round(math.sqrt(len(records) / 2))))
    model = KMeans(n_clusters=k, random_state=7, n_init=20)
    labels = model.fit_predict(x)
    best = _pick_best_cluster(records, labels, min_cluster_n=min_cluster_n)
    return {
        "k": k,
        "best_cluster_id": best["cluster_id"],
        "best_cluster_stats": best,
        "numeric_features": list(NUMERIC_FEATURES),
        "categorical_features": list(CATEGORICAL_FEATURES),
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "categorical_categories": {
            feature: [str(value) for value in categories]
            for feature, categories in zip(CATEGORICAL_FEATURES, enc.categories_, strict=True)
        },
        "centroids": model.cluster_centers_.tolist(),
        "random_state": 7,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export live K-means cluster models for ES/NQ alerts.")
    parser.add_argument("--labeled-input", default="data/labeled_alert_signals_5m_10m_2026-06-14.jsonl")
    parser.add_argument(
        "--feature-log",
        default="data/cross_market_features.jsonl",
        help="Spot/feature log used by crossmarket_alert_optimization_suite.",
    )
    parser.add_argument("--output-json", default="data/live_cluster_models_2026-06-14.json")
    parser.add_argument(
        "--rth-only",
        action="store_true",
        help="Restrict training rows to 09:30-16:00 ET (default unless --all-sessions).",
    )
    parser.add_argument(
        "--all-sessions",
        action="store_true",
        help="Use all labeled rows (matches crossmarket_alert_optimization_suite default).",
    )
    parser.add_argument(
        "--min-best-cluster-n",
        type=int,
        default=MIN_BEST_CLUSTER_N,
        help="Minimum cluster size when selecting best_cluster_id.",
    )
    args = parser.parse_args()
    if args.rth_only and args.all_sessions:
        raise SystemExit("Use only one of --rth-only or --all-sessions.")
    rth_only = args.rth_only or not args.all_sessions

    rows = _load_jsonl(Path(args.labeled_input))
    spots = cms._spot_index(_load_jsonl(Path(args.feature_log)))
    records = _build_records(rows, spots, rth_only=rth_only)
    lanes: dict[str, Any] = {}
    min_best_n = max(1, int(args.min_best_cluster_n))

    for market in ("ES", "NQ"):
        for horizon in (5, 10):
            lane_records = [record for record in records if record["market"] == market and record["horizon_minutes"] == horizon]
            exported = _export_lane(lane_records, min_cluster_n=min_best_n)
            if exported is not None:
                lanes[f"{market}_{horizon}m"] = exported

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "labeled_input": str(args.labeled_input),
        "feature_log": str(args.feature_log),
        "et_window": "09:30-16:00 ET" if rth_only else "all",
        "min_best_cluster_n": min_best_n,
        "lanes": lanes,
    }
    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(out), "lanes": list(lanes.keys())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
