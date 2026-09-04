from __future__ import annotations

import json
import math
from datetime import datetime, time, timezone
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
START, END = time(9, 30), time(16, 0)
ROOT = Path(__file__).resolve().parents[1]


def parse_ts(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def in_rth(ts: datetime | None) -> bool:
    if ts is None:
        return False
    local = ts.astimezone(ET).time()
    return START <= local < END


def num(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def layer(row: dict, *path: str) -> object:
    cur: object = row
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            ts = parse_ts(row.get("timestamp_utc"))
            if not in_rth(ts):
                continue
            market = str(row.get("market") or "").upper()
            horizon = int(row.get("horizon_minutes") or 0)
            if market not in {"ES", "NQ"} or horizon not in {5, 10}:
                continue
            outcomes = row.get("outcomes") if isinstance(row.get("outcomes"), dict) else {}
            block = outcomes.get(f"{horizon}m") if isinstance(outcomes.get(f"{horizon}m"), dict) else {}
            hit = block.get("directional_hit")
            fwd = num(block.get("fwd_ret"))
            direction = str(row.get("direction") or "").lower()
            sign = 1 if direction == "long" else -1 if direction == "short" else 0
            if hit is None or fwd is None or sign == 0:
                continue
            rows.append(
                {
                    "market": market,
                    "horizon": horizon,
                    "hit": bool(hit),
                    "ret": fwd * sign,
                    "p_hit": num(row.get("p_hit")),
                    "conf": num(row.get("confidence")),
                    "cross": int(layer(row, "layers", "cross_index", "confirmation") or 0),
                    "contrad": int(layer(row, "layers", "cross_index", "contradiction") or 0),
                    "gap": num(layer(row, "layers", "model", "direction_gap")),
                    "edge": num(layer(row, "layers", "model", "raw_edge")),
                    "macro_ready": int(layer(row, "layers", "macro", "ready") or 0),
                    "session": str(layer(row, "layers", "context", "session_bucket") or ""),
                    "status": str(row.get("status") or ""),
                    "reason": str(row.get("reason") or ""),
                    "direction": direction,
                    "ofi60z": num(layer(row, "layers", "ofi", "z60")),
                }
            )
    return rows


def summarize(sub: list[dict]) -> dict | None:
    if not sub:
        return None
    return {
        "n": len(sub),
        "wr": mean(1 if row["hit"] else 0 for row in sub),
        "ev": mean(row["ret"] for row in sub),
    }


def main() -> int:
    rows = load_rows(ROOT / "data" / "labeled_alert_signals_5m_10m_2026-06-14.jsonl")
    print(f"RTH records: {len(rows)}")

    filters = [
        ("high_priority only", lambda r: r["status"] == "high_priority"),
        ("strong_multilayer only", lambda r: r["reason"] == "strong_multilayer_alignment"),
        ("cross>=2 & contrad=0", lambda r: r["cross"] >= 2 and r["contrad"] == 0),
        ("cross>=3", lambda r: r["cross"] >= 3),
        ("gap>=0.06", lambda r: (r["gap"] or 0) >= 0.06),
        ("gap>=0.10", lambda r: (r["gap"] or 0) >= 0.10),
        ("edge>=1.25", lambda r: abs(r["edge"] or 0) >= 1.25),
        ("edge>=2.5", lambda r: abs(r["edge"] or 0) >= 2.5),
        ("p_hit>=0.72", lambda r: (r["p_hit"] or 0) >= 0.72),
        ("conf>=0.62", lambda r: (r["conf"] or 0) >= 0.62),
        ("macro_ready>=4", lambda r: r["macro_ready"] >= 4),
        ("RTH_OPEN session", lambda r: r["session"] == "RTH_OPEN"),
        ("RTH_MID session", lambda r: r["session"] == "RTH_MID"),
        ("RTH_CLOSE session", lambda r: r["session"] == "RTH_CLOSE"),
        ("exclude RTH_CLOSE", lambda r: r["session"] != "RTH_CLOSE"),
        (
            "ofi aligned",
            lambda r: (r["direction"] == "long" and (r["ofi60z"] or 0) > 0)
            or (r["direction"] == "short" and (r["ofi60z"] or 0) < 0),
        ),
        (
            "combo ES: cross>=2 contrad=0 gap>=0.06 edge>=1.25 conf>=0.62",
            lambda r: r["cross"] >= 2
            and r["contrad"] == 0
            and (r["gap"] or 0) >= 0.06
            and abs(r["edge"] or 0) >= 1.25
            and (r["conf"] or 0) >= 0.62,
        ),
        (
            "combo NQ: cross>=3 contrad<=1 gap>=0.06 exclude close",
            lambda r: r["cross"] >= 3
            and r["contrad"] <= 1
            and (r["gap"] or 0) >= 0.06
            and r["session"] != "RTH_CLOSE",
        ),
        (
            "combo strict: high_priority cross>=2 contrad=0 gap>=0.06 edge>=1.25 p_hit>=0.72",
            lambda r: r["status"] == "high_priority"
            and r["cross"] >= 2
            and r["contrad"] == 0
            and (r["gap"] or 0) >= 0.06
            and abs(r["edge"] or 0) >= 1.25
            and (r["p_hit"] or 0) >= 0.72,
        ),
    ]

    for market in ("ES", "NQ"):
        for horizon in (5, 10):
            base = [row for row in rows if row["market"] == market and row["horizon"] == horizon]
            base_summary = summarize(base)
            if not base_summary:
                continue
            print(
                f"\n=== {market} {horizon}m RTH baseline: "
                f"n={base_summary['n']} wr={base_summary['wr']:.3f} ev={base_summary['ev']:.6f} ==="
            )
            scored = []
            for name, fn in filters:
                sub = [row for row in base if fn(row)]
                summary = summarize(sub)
                if summary and summary["n"] >= 30:
                    scored.append((summary["ev"], summary["wr"], summary["n"], name))
            scored.sort(reverse=True)
            print("Top by EV (n>=30):")
            for ev, wr, n, name in scored[:8]:
                print(f"  {name}: n={n} wr={wr:.3f} ev={ev:.6f}")
            scored_wr = sorted(
                [
                    (summary["wr"], summary["ev"], summary["n"], name)
                    for name, fn in filters
                    for summary in [summarize([row for row in base if fn(row)])]
                    if summary and summary["n"] >= 30
                ],
                reverse=True,
            )
            print("Top by win rate (n>=30):")
            for wr, ev, n, name in scored_wr[:8]:
                print(f"  {name}: n={n} wr={wr:.3f} ev={ev:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
