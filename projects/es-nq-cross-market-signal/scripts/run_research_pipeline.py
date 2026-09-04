from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str], cwd: Path) -> None:
    print("running:", " ".join(cmd))
    completed = subprocess.run(cmd, cwd=str(cwd), check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run standalone cross-market signal research pipeline.")
    parser.add_argument("--python", default=sys.executable, help="Python executable")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    py = args.python
    _run(
        [
            py,
            "scripts/build_labels.py",
            "--max-future-delay-seconds",
            "120",
            "--max-abs-forward-return",
            "0.02",
        ],
        cwd=root,
    )
    _run([py, "scripts/analyze_cross_market_correlations.py"], cwd=root)
    _run([py, "scripts/analyze_cross_market_lead_lag.py"], cwd=root)
    _run(
        [
            py,
            "scripts/train_meta_models.py",
            "--allowed-statuses",
            "watch,post,high_priority",
            "--max-abs-forward-return",
            "0.02",
        ],
        cwd=root,
    )
    _run(
        [
            py,
            "scripts/calibrate_signal_thresholds.py",
            "--min-trades",
            "40",
            "--min-win-rate",
            "0.65",
            "--max-drawdown",
            "0.02",
            "--allowed-statuses",
            "watch,post,high_priority",
            "--max-abs-trade-return",
            "0.02",
        ],
        cwd=root,
    )


if __name__ == "__main__":
    main()
