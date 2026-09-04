from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from esnq_signal.model import LinearLogitModel


FEATURE_ORDER = (
    "raw_score",
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
    "macro_regime_score",
    "macro_lead_score",
    "macro_ready",
    "target_spread",
    "direction_gap",
    "raw_edge",
    "p_up_heuristic",
    "p_hit_heuristic",
)


def _num(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(parsed) or math.isinf(parsed):
        return 0.0
    return parsed


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


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


def _extract_features(row: dict[str, Any]) -> dict[str, float]:
    layers = row.get("layers")
    if not isinstance(layers, dict):
        layers = {}
    ofi = layers.get("ofi")
    if not isinstance(ofi, dict):
        ofi = {}
    cross = layers.get("cross_index")
    if not isinstance(cross, dict):
        cross = {}
    macro = layers.get("macro")
    if not isinstance(macro, dict):
        macro = {}
    model = layers.get("model")
    if not isinstance(model, dict):
        model = {}
    large_trades = layers.get("large_trades")
    if not isinstance(large_trades, dict):
        large_trades = {}
    raw_score = _num(layers.get("direction_core_raw"))
    cross_confirm = _num(cross.get("confirmation"))
    cross_contra = _num(cross.get("contradiction"))
    return {
        "raw_score": raw_score,
        "target_ofi_15s_z": _num(ofi.get("z15")),
        "target_ofi_60s_z": _num(ofi.get("z60")),
        "target_ofi_300s_z": _num(ofi.get("z300")),
        "target_large_trade_signed_60s": _num(
            large_trades.get("signed_60s")
            if large_trades.get("signed_60s") is not None
            else row.get("target_large_trade_signed_60s")
        ),
        "target_large_trade_signed_300s": _num(
            large_trades.get("signed_300s")
            if large_trades.get("signed_300s") is not None
            else row.get("target_large_trade_signed_300s")
        ),
        "target_large_trade_count_60s": _num(
            large_trades.get("count_60s")
            if large_trades.get("count_60s") is not None
            else row.get("target_large_trade_count_60s")
        ),
        "target_large_trade_count_300s": _num(
            large_trades.get("count_300s")
            if large_trades.get("count_300s") is not None
            else row.get("target_large_trade_count_300s")
        ),
        "target_large_trade_buy_ratio_300s": _num(
            large_trades.get("buy_ratio_300s")
            if large_trades.get("buy_ratio_300s") is not None
            else row.get("target_large_trade_buy_ratio_300s")
        ),
        "cross_index_confirmation": cross_confirm,
        "cross_index_contradiction": cross_contra,
        "cross_index_balance": cross_confirm - cross_contra,
        "cross_index_lead_confirmation": _num(cross.get("lead_confirmation")),
        "cross_index_lead_contradiction": _num(cross.get("lead_contradiction")),
        "cross_index_lead_balance": _num(cross.get("lead_confirmation")) - _num(cross.get("lead_contradiction")),
        "macro_regime_score": _num(macro.get("score")),
        "macro_lead_score": _num(macro.get("lead_score")),
        "macro_ready": _num(macro.get("ready")),
        "target_spread": _num(row.get("target_spread")),
        "direction_gap": abs(_num(row.get("p_up")) - 0.5),
        "raw_edge": abs(raw_score),
        "p_up_heuristic": _num(model.get("p_up_heuristic") if model.get("p_up_heuristic") is not None else row.get("p_up")),
        "p_hit_heuristic": _num(model.get("p_hit_heuristic") if model.get("p_hit_heuristic") is not None else row.get("p_hit")),
    }


def _extract_dataset(
    rows: list[dict[str, Any]],
    *,
    market: str,
    horizon_minutes: int,
    allowed_statuses: set[str],
    max_abs_forward_return: float,
) -> tuple[list[list[float]], list[int], list[int]]:
    x: list[list[float]] = []
    y_up: list[int] = []
    y_hit: list[int] = []
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
        fwd_ret = block.get("fwd_ret")
        hit = block.get("directional_hit")
        if not isinstance(hit, bool):
            continue
        y_ret = _num(fwd_ret)
        if abs(y_ret) > max_abs_forward_return:
            continue
        y_up_label = 1 if y_ret > 0 else 0
        feats = _extract_features(row)
        x.append([feats[name] for name in FEATURE_ORDER])
        y_up.append(y_up_label)
        y_hit.append(1 if hit else 0)
    return x, y_up, y_hit


def _standardize_fit(x: list[list[float]]) -> tuple[list[float], list[float]]:
    if not x:
        return [], []
    cols = len(x[0])
    means: list[float] = []
    stds: list[float] = []
    for j in range(cols):
        col = [row[j] for row in x]
        mu = mean(col)
        sigma = pstdev(col) if len(col) > 1 else 1.0
        if sigma <= 1e-9:
            sigma = 1.0
        means.append(mu)
        stds.append(sigma)
    return means, stds


def _standardize_apply(x: list[list[float]], means: list[float], stds: list[float]) -> list[list[float]]:
    out: list[list[float]] = []
    for row in x:
        out.append([(row[j] - means[j]) / stds[j] for j in range(len(row))])
    return out


def _train_logit(
    x: list[list[float]],
    y: list[int],
    *,
    learning_rate: float = 0.05,
    l2: float = 1e-3,
    epochs: int = 600,
) -> tuple[list[float], float]:
    if not x:
        return [], 0.0
    d = len(x[0])
    weights = [0.0 for _ in range(d)]
    bias = 0.0
    n = float(len(x))
    for _ in range(epochs):
        grad_w = [0.0 for _ in range(d)]
        grad_b = 0.0
        for i, row in enumerate(x):
            z = bias
            for j in range(d):
                z += weights[j] * row[j]
            p = _sigmoid(z)
            err = p - float(y[i])
            grad_b += err
            for j in range(d):
                grad_w[j] += err * row[j]
        for j in range(d):
            grad_w[j] = (grad_w[j] / n) + (l2 * weights[j])
            weights[j] -= learning_rate * grad_w[j]
        bias -= learning_rate * (grad_b / n)
    return weights, bias


def _predict_probs(x: list[list[float]], weights: list[float], bias: float) -> list[float]:
    probs: list[float] = []
    for row in x:
        z = bias
        for j in range(len(weights)):
            z += weights[j] * row[j]
        probs.append(_sigmoid(z))
    return probs


def _metrics(y_true: list[int], probs: list[float]) -> dict[str, float]:
    if not y_true:
        return {"n": 0.0, "accuracy": 0.0, "brier": 1.0}
    preds = [1 if p >= 0.5 else 0 for p in probs]
    correct = sum(1 for t, p in zip(y_true, preds, strict=True) if t == p)
    brier = sum((float(t) - p) ** 2 for t, p in zip(y_true, probs, strict=True)) / float(len(y_true))
    return {
        "n": float(len(y_true)),
        "accuracy": float(correct) / float(len(y_true)),
        "brier": float(brier),
    }


def _fit_model(x: list[list[float]], y: list[int]) -> tuple[LinearLogitModel | None, dict[str, float]]:
    if len(x) < 50 or len(y) != len(x):
        return None, {"n": float(len(x)), "accuracy": 0.0, "brier": 1.0}
    split = max(30, int(len(x) * 0.8))
    x_train = x[:split]
    y_train = y[:split]
    x_test = x[split:]
    y_test = y[split:]
    if len(x_test) < 10:
        x_train = x
        y_train = y
        x_test = x
        y_test = y
    means, stds = _standardize_fit(x_train)
    x_train_std = _standardize_apply(x_train, means, stds)
    x_test_std = _standardize_apply(x_test, means, stds)
    weights, bias = _train_logit(x_train_std, y_train)
    probs = _predict_probs(x_test_std, weights, bias)
    metrics = _metrics(y_test, probs)
    model = LinearLogitModel(
        feature_order=tuple(FEATURE_ORDER),
        means=tuple(means),
        stds=tuple(stds),
        weights=tuple(weights),
        bias=float(bias),
    )
    return model, metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Train lightweight per-market/per-horizon p_up and p_hit meta-models.")
    parser.add_argument("--labeled-input", default="data/labeled_signals_5m_10m.jsonl")
    parser.add_argument("--output-json", default="data/signal_models.json")
    parser.add_argument("--output-md", default="data/signal_models.md")
    parser.add_argument(
        "--allowed-statuses",
        default="watch,post,high_priority",
        help="Comma-separated statuses to include for model training.",
    )
    parser.add_argument(
        "--max-abs-forward-return",
        type=float,
        default=0.02,
        help="Drop outlier labels above this absolute forward-return threshold.",
    )
    args = parser.parse_args()

    rows = _load_jsonl(Path(args.labeled_input))
    allowed_statuses = {
        item.strip().lower()
        for item in str(args.allowed_statuses).split(",")
        if item.strip()
    }
    max_abs_forward_return = max(0.0001, float(args.max_abs_forward_return))
    payload: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "labeled_input": str(args.labeled_input),
        "allowed_statuses": sorted(list(allowed_statuses)),
        "max_abs_forward_return": max_abs_forward_return,
        "models": {},
        "metrics": {},
    }
    lines: list[str] = [
        "# Signal Models",
        "",
        f"- generated_at_utc: `{payload['generated_at_utc']}`",
        f"- labeled_input: `{payload['labeled_input']}`",
        f"- allowed_statuses: `{payload['allowed_statuses']}`",
        f"- max_abs_forward_return: `{payload['max_abs_forward_return']}`",
        "",
    ]
    for market in ("ES", "NQ"):
        payload["models"][market] = {}
        payload["metrics"][market] = {}
        for horizon in (5, 10):
            x, y_up, y_hit = _extract_dataset(
                rows,
                market=market,
                horizon_minutes=horizon,
                allowed_statuses=allowed_statuses,
                max_abs_forward_return=max_abs_forward_return,
            )
            model_up, metric_up = _fit_model(x, y_up)
            model_hit, metric_hit = _fit_model(x, y_hit)
            model_block: dict[str, Any] = {}
            if model_up is not None:
                model_block["p_up"] = model_up.to_dict()
            if model_hit is not None:
                model_block["p_hit"] = model_hit.to_dict()
            payload["models"][market][str(horizon)] = model_block
            payload["metrics"][market][str(horizon)] = {"p_up": metric_up, "p_hit": metric_hit, "rows": len(x)}
            lines.append(f"## {market} {horizon}m")
            lines.append("")
            lines.append(f"- rows: `{len(x)}`")
            lines.append(f"- p_up accuracy: `{metric_up['accuracy']:.3f}` brier: `{metric_up['brier']:.4f}`")
            lines.append(f"- p_hit accuracy: `{metric_hit['accuracy']:.3f}` brier: `{metric_hit['brier']:.4f}`")
            lines.append(f"- models_trained: `{bool(model_block)}`")
            lines.append("")

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    output_md = Path(args.output_md)
    output_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote: {output_json}")
    print(f"wrote: {output_md}")


if __name__ == "__main__":
    main()
