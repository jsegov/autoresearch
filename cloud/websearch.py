"""Turn sourced web research into machine-readable experiment candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


LINKUP_SEARCH_URL = "https://api.linkup.so/v1/search"
SCHEMA_VERSION = 1
DEFAULT_RESULTS = 5
MAX_RESULTS = 10


EXPERIMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "description": "Minimal, independently testable training experiments.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "hypothesis": {
                        "type": "string",
                        "description": "A falsifiable claim about validation bits per byte.",
                    },
                    "proposed_change": {
                        "type": "string",
                        "description": "The smallest train.py-only implementation to test.",
                    },
                    "expected_effect": {"type": "string"},
                    "evidence_summary": {
                        "type": "string",
                        "description": "What the cited primary sources actually support.",
                    },
                    "compatibility_risks": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "source_urls": {
                        "type": "array",
                        "description": "Primary-source URLs returned by the search.",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "title",
                    "hypothesis",
                    "proposed_change",
                    "expected_effect",
                    "evidence_summary",
                    "compatibility_risks",
                    "source_urls",
                ],
            },
        }
    },
    "required": ["candidates"],
}


def build_query(
    objective: str, count: int, hardware: str = "one 24 GB NVIDIA GPU"
) -> str:
    """Build a retrieval-first query constrained to this repository's experiment contract."""
    return f"""
Find primary sources that support exactly {count} distinct experiments for this objective:

{objective}

The target is a small GPT trained on TinyStories on {hardware}. Every experiment receives
exactly five minutes of training, is evaluated by validation bits per byte (lower is better), may
edit only train.py, and may not add dependencies or modify evaluation. Prefer papers, official
implementation repositories, and author technical notes. Each idea must be implementable as one
minimal isolated change and must plausibly fit the specified hardware. Exclude generic advice,
unfalsifiable proposals, dataset changes, evaluation changes, and ideas without a primary source.
Return only source URLs that the search actually retrieved.
""".strip()


def _valid_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _required_text(candidate: dict[str, Any], field: str) -> str:
    value = candidate.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Candidate field {field!r} must be a non-empty string")
    return value.strip()


def _candidate_id(candidate: dict[str, Any]) -> str:
    identity = "\0".join(
        [candidate["title"], candidate["hypothesis"], candidate["proposed_change"]]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def normalize_response(
    raw: dict[str, Any],
    *,
    objective: str,
    query: str,
    count: int,
    depth: str,
    hardware: str,
) -> dict[str, Any]:
    """Validate Linkup structured output and add stable experiment identifiers."""
    data = raw.get("data", raw)
    if not isinstance(data, dict):
        raise ValueError("Linkup response did not contain a structured object")

    raw_candidates = data.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise ValueError("Linkup response did not contain experiment candidates")

    raw_sources = raw.get("sources", [])
    sources: list[dict[str, str]] = []
    if isinstance(raw_sources, list):
        for source in raw_sources:
            if not isinstance(source, dict) or not _valid_url(source.get("url")):
                continue
            snippet = source.get("snippet", source.get("content", ""))
            sources.append(
                {
                    "name": str(source.get("name", "")).strip(),
                    "url": source["url"].strip(),
                    "snippet": str(snippet).strip()[:2_000],
                }
            )
    retrieved_urls = {source["url"] for source in sources}

    candidates: list[dict[str, Any]] = []
    for raw_candidate in raw_candidates[:count]:
        if not isinstance(raw_candidate, dict):
            raise ValueError("Every experiment candidate must be an object")
        candidate = {
            "title": _required_text(raw_candidate, "title"),
            "hypothesis": _required_text(raw_candidate, "hypothesis"),
            "proposed_change": _required_text(raw_candidate, "proposed_change"),
            "expected_effect": _required_text(raw_candidate, "expected_effect"),
            "evidence_summary": _required_text(raw_candidate, "evidence_summary"),
            "compatibility_risks": [
                str(value).strip()
                for value in raw_candidate.get("compatibility_risks", [])
                if str(value).strip()
            ],
            "source_urls": [
                value.strip()
                for value in raw_candidate.get("source_urls", [])
                if _valid_url(value)
            ],
        }
        if not candidate["source_urls"]:
            raise ValueError(
                f"Candidate {candidate['title']!r} has no valid source URL"
            )
        if retrieved_urls and not retrieved_urls.intersection(candidate["source_urls"]):
            raise ValueError(
                f"Candidate {candidate['title']!r} does not cite a retrieved Linkup source"
            )
        candidate["id"] = _candidate_id(candidate)
        candidates.append(candidate)

    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": "linkup",
        "depth": depth,
        "hardware": hardware,
        "objective": objective,
        "query": query,
        "candidates": candidates,
        "sources": sources,
    }


def search_experiments(
    objective: str,
    *,
    count: int = DEFAULT_RESULTS,
    depth: str = "deep",
    hardware: str = "one 24 GB NVIDIA GPU",
    api_key: str | None = None,
    timeout_seconds: float = 120.0,
    session: Any = requests,
) -> dict[str, Any]:
    """Call Linkup and return a validated research packet."""
    if not 1 <= count <= MAX_RESULTS:
        raise ValueError(f"count must be between 1 and {MAX_RESULTS}")
    if depth not in {"standard", "deep"}:
        raise ValueError("depth must be 'standard' or 'deep'")

    token = api_key or os.environ.get("LINKUP_API_KEY")
    if not token:
        raise RuntimeError("LINKUP_API_KEY is required")

    query = build_query(objective, count, hardware)
    response = session.post(
        LINKUP_SEARCH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "q": query,
            "depth": depth,
            "outputType": "structured",
            "structuredOutputSchema": json.dumps(EXPERIMENT_SCHEMA),
            "includeSources": True,
            "maxResults": max(5, count * 2),
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    raw = response.json()
    if not isinstance(raw, dict):
        raise ValueError("Linkup returned a non-object JSON response")
    return normalize_response(
        raw,
        objective=objective,
        query=query,
        count=count,
        depth=depth,
        hardware=hardware,
    )


def write_packet(packet: dict[str, Any], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(output_path)
    return output_path


def _default_output_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("queue") / f"research-{stamp}.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Use Linkup web search to generate sourced autoresearch candidates."
    )
    parser.add_argument(
        "--objective", required=True, help="The research objective to optimize."
    )
    parser.add_argument("--count", type=int, default=DEFAULT_RESULTS)
    parser.add_argument("--depth", choices=("standard", "deep"), default="deep")
    parser.add_argument(
        "--hardware",
        default="one 24 GB NVIDIA GPU",
        help="The fixed hardware envelope used for every candidate.",
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = args.output or _default_output_path()
    try:
        packet = search_experiments(
            args.objective,
            count=args.count,
            depth=args.depth,
            hardware=args.hardware,
        )
        write_packet(packet, output)
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        print(f"websearch failed: {exc}", file=sys.stderr)
        return 1

    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
