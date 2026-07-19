"""Run one immutable autoresearch experiment and record a structured result."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SUMMARY_LINE = re.compile(r"^([a-z][a-z0-9_]*):\s+(.+?)\s*$")
NUMERIC_FIELDS = {
    "val_bpb",
    "training_seconds",
    "total_seconds",
    "peak_vram_mb",
    "mfu_percent",
    "total_tokens_M",
    "num_steps",
    "num_params_M",
    "depth",
    "train_batch_size",
    "eval_batch_size",
}


def validate_experiment_id(value: str) -> str:
    if not SAFE_ID.fullmatch(value):
        raise ValueError(
            "experiment id must be 1-80 characters using letters, numbers, '.', '_' or '-'"
        )
    return value


def parse_summary(output: str) -> dict[str, Any]:
    """Parse the final key/value summary emitted by train.py."""
    summary: dict[str, Any] = {}
    for line in output.splitlines():
        match = SUMMARY_LINE.match(line)
        if not match:
            continue
        key, raw_value = match.groups()
        if key in NUMERIC_FIELDS and raw_value != "n/a":
            try:
                number = float(raw_value)
                summary[key] = (
                    int(number)
                    if key
                    in {"num_steps", "depth", "train_batch_size", "eval_batch_size"}
                    else number
                )
                continue
            except ValueError:
                pass
        if raw_value in {"true", "false"}:
            summary[key] = raw_value == "true"
        else:
            summary[key] = raw_value
    return summary


def classify_result(
    *,
    return_code: int,
    timed_out: bool,
    smoke_test: bool,
    summary: dict[str, Any],
    baseline_val_bpb: float | None,
    min_improvement: float,
    hardware_matches: bool = True,
) -> str:
    if timed_out:
        return "timeout"
    if return_code != 0:
        return "crash"
    value = summary.get("val_bpb")
    if not isinstance(value, (int, float)):
        return "invalid"
    if smoke_test:
        return "smoke_passed"
    if not hardware_matches:
        return "hardware_mismatch"
    if baseline_val_bpb is None:
        return "baseline"
    if value < baseline_val_bpb - min_improvement:
        return "improved"
    return "not_improved"


def _run_text(command: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def collect_git_metadata(repo_root: Path) -> dict[str, Any]:
    diff = _run_text(["git", "diff", "HEAD", "--", "train.py"], repo_root) or ""
    return {
        "commit": _run_text(["git", "rev-parse", "HEAD"], repo_root),
        "branch": _run_text(["git", "branch", "--show-current"], repo_root),
        "train_py_dirty": bool(diff),
        "train_py_diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
    }


def collect_gpu_metadata(repo_root: Path) -> dict[str, Any] | None:
    raw = _run_text(
        [
            "nvidia-smi",
            "--query-gpu=name,uuid,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        repo_root,
    )
    if not raw:
        return None
    first = raw.splitlines()[0]
    parts = [part.strip() for part in first.split(",")]
    if len(parts) != 4:
        return {"raw": first}
    return {
        "name": parts[0],
        "uuid": parts[1],
        "memory_total_mb": parts[2],
        "driver_version": parts[3],
    }


def load_candidate(
    packet_path: Path | None, candidate_id: str | None
) -> dict[str, Any] | None:
    if packet_path is None and candidate_id is None:
        return None
    if packet_path is None or candidate_id is None:
        raise ValueError(
            "--research-packet and --candidate-id must be supplied together"
        )
    raw = json.loads(packet_path.read_text(encoding="utf-8"))
    for candidate in raw.get("candidates", []):
        if candidate.get("id") == candidate_id:
            return candidate
    raise ValueError(f"candidate {candidate_id!r} was not found in {packet_path}")


def load_baseline(path: Path | None) -> tuple[float | None, dict[str, Any] | None]:
    if path is None:
        return None, None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("smoke_test"):
        raise ValueError(f"baseline {path} is a smoke-test result")
    if raw.get("verdict") != "baseline":
        raise ValueError(f"baseline {path} does not have the 'baseline' verdict")
    value = raw.get("summary", {}).get("val_bpb")
    if not isinstance(value, (int, float)):
        raise ValueError(f"baseline {path} has no numeric val_bpb")
    gpu = raw.get("gpu")
    return float(value), gpu if isinstance(gpu, dict) else None


def same_hardware(
    baseline_gpu: dict[str, Any] | None, current_gpu: dict[str, Any] | None
) -> bool:
    if baseline_gpu is None:
        return False
    if current_gpu is None:
        return False
    identity_fields = ("name", "memory_total_mb")
    return all(
        baseline_gpu.get(field) == current_gpu.get(field) for field in identity_fields
    )


def _terminate(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
        else:
            process.kill()


def execute_training(
    *,
    uv: str,
    repo_root: Path,
    experiment_dir: Path,
    smoke_test: bool,
    timeout_seconds: int,
) -> tuple[int, bool, float, Path]:
    command = [
        uv,
        "run",
        "--frozen",
        "--project",
        str(repo_root),
        "python",
        str(repo_root / "train.py"),
    ]
    if smoke_test:
        command.append("--smoke-test")

    return execute_command(
        command=command,
        experiment_dir=experiment_dir,
        timeout_seconds=timeout_seconds,
    )


def execute_command(
    *, command: list[str], experiment_dir: Path, timeout_seconds: int
) -> tuple[int, bool, float, Path]:
    """Execute one command with a hard timeout and a context-safe log file."""
    log_path = experiment_dir / "run.log"

    start = time.monotonic()
    timed_out = False
    popen_kwargs: dict[str, Any] = {"cwd": experiment_dir}
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command, stdout=log, stderr=subprocess.STDOUT, **popen_kwargs
        )
        try:
            return_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate(process)
            return_code = 124
    return return_code, timed_out, time.monotonic() - start, log_path


def write_result(result: dict[str, Any], result_path: Path, ledger_path: Path) -> None:
    temporary = result_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(result_path)
    with ledger_path.open("a", encoding="utf-8") as ledger:
        ledger.write(json.dumps(result, sort_keys=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one cloud autoresearch experiment and save an immutable JSON result."
    )
    parser.add_argument("--id", required=True, dest="experiment_id")
    parser.add_argument("--description", required=True)
    parser.add_argument("--objective", default=None)
    parser.add_argument("--research-packet", type=Path, default=None)
    parser.add_argument("--candidate-id", default=None)
    parser.add_argument("--baseline-result", type=Path, default=None)
    parser.add_argument("--min-improvement", type=float, default=0.0)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=720)
    parser.add_argument("--results-dir", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        experiment_id = validate_experiment_id(args.experiment_id)
        if not 30 <= args.timeout_seconds <= 3_600:
            raise ValueError("timeout must be between 30 and 3600 seconds")
        if args.min_improvement < 0:
            raise ValueError("minimum improvement cannot be negative")

        repo_root = Path(__file__).resolve().parents[1]
        uv = shutil.which("uv")
        if uv is None:
            raise RuntimeError("uv is required on the cloud worker")
        candidate = load_candidate(args.research_packet, args.candidate_id)
        baseline_val_bpb, baseline_gpu = load_baseline(args.baseline_result)
        git_metadata = collect_git_metadata(repo_root)
        if git_metadata["train_py_dirty"] and not args.smoke_test:
            raise ValueError("commit train.py before running a full experiment")
        gpu_metadata = collect_gpu_metadata(repo_root)
        hardware_matches = (
            same_hardware(baseline_gpu, gpu_metadata)
            if baseline_val_bpb is not None
            else True
        )

        configured_results = os.environ.get("AUTORESEARCH_RESULTS_DIR")
        results_root = args.results_dir or (
            Path(configured_results)
            if configured_results
            else repo_root / "results" / "cloud"
        )
        experiment_dir = results_root / experiment_id
        if experiment_dir.exists():
            raise ValueError(f"experiment result already exists: {experiment_dir}")
        experiment_dir.mkdir(parents=True)

        started_at = datetime.now(timezone.utc)
        return_code, timed_out, wall_seconds, log_path = execute_training(
            uv=uv,
            repo_root=repo_root,
            experiment_dir=experiment_dir,
            smoke_test=args.smoke_test,
            timeout_seconds=args.timeout_seconds,
        )
        output = log_path.read_text(encoding="utf-8", errors="replace")
        summary = parse_summary(output)
        verdict = classify_result(
            return_code=return_code,
            timed_out=timed_out,
            smoke_test=args.smoke_test,
            summary=summary,
            baseline_val_bpb=baseline_val_bpb,
            min_improvement=args.min_improvement,
            hardware_matches=hardware_matches,
        )
        result = {
            "schema_version": SCHEMA_VERSION,
            "experiment_id": experiment_id,
            "description": args.description,
            "objective": args.objective,
            "candidate": candidate,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "wall_seconds": round(wall_seconds, 3),
            "return_code": return_code,
            "timed_out": timed_out,
            "smoke_test": args.smoke_test,
            "baseline_val_bpb": baseline_val_bpb,
            "min_improvement": args.min_improvement,
            "hardware_matches_baseline": hardware_matches,
            "verdict": verdict,
            "summary": summary,
            "git": git_metadata,
            "gpu": gpu_metadata,
            "log_path": log_path.name,
        }
        result_path = experiment_dir / "result.json"
        write_result(result, result_path, results_root / "ledger.jsonl")
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"experiment failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"result": str(result_path.resolve()), "verdict": verdict}))
    return (
        0 if verdict not in {"crash", "hardware_mismatch", "invalid", "timeout"} else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
