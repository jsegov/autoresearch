from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from esnq_signal.config import Settings
from esnq_signal.service import LiveSignalService


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


async def _amain() -> None:
    parser = argparse.ArgumentParser(description="Run standalone ES/NQ cross-market OFI direction service.")
    parser.add_argument("--env-file", default=".env", help="Optional environment file")
    args = parser.parse_args()

    _load_env_file(Path(args.env_file))
    settings = Settings.from_env()
    service = LiveSignalService(settings)
    await service.run()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
