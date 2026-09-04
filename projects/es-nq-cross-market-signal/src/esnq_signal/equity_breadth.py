from __future__ import annotations

import asyncio
import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .markets import MEGA_EQUITY_COLLECTION_ROOTS
from .ofi_stream import parse_ts_utc


EQUITY_BREADTH_SCHEMA_VERSION = "equity_breadth_snapshot_v1"


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _health_ok(value: Any, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"ok", "healthy", "ready", "true"}
    if isinstance(value, dict):
        for key in ("healthy", "ok", "ready", "eligible"):
            if key in value:
                return _health_ok(value.get(key), default=default)
        status = str(value.get("status") or "").strip().lower()
        if status:
            return status in {"ok", "healthy", "ready"}
    return default


def _vote(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"bullish", "long", "buy", "positive", "1"}:
        return "bullish"
    if text in {"bearish", "short", "sell", "negative", "-1"}:
        return "bearish"
    return "neutral"


@dataclass(frozen=True)
class EquityBreadthObservation:
    root: str
    observed_at: datetime
    price: float | None
    return_15s: float | None
    signed_volume_imbalance_15s: float | None
    return_z: float | None
    imbalance_z: float | None
    composite_score: float | None
    vote: str
    issuer_id: str | None
    roles: tuple[str, ...]
    healthy: bool
    freshness: dict[str, Any]
    provenance: dict[str, Any]

    def signed_age_seconds(self, now_utc: datetime) -> float:
        return (now_utc - self.observed_at).total_seconds()


@dataclass(frozen=True)
class EquityBreadthSnapshot:
    calculated_at: datetime
    observations: dict[str, EquityBreadthObservation]
    healthy: bool
    freshness: dict[str, Any]
    provenance: dict[str, Any]
    schema_version: str

    def signed_age_seconds(self, now_utc: datetime) -> float:
        return (now_utc - self.calculated_at).total_seconds()


def parse_equity_breadth_snapshot(payload: Any) -> EquityBreadthSnapshot | None:
    """Parse the options service ``EquityBreadthSnapshotV1`` wire shape.

    The endpoint may return the snapshot directly or under ``snapshot``/``data``.
    Unknown observation roots are ignored so an entitlement failure or an
    unrelated subscription can never contaminate the configured universe.
    """

    if not isinstance(payload, dict):
        return None
    raw = payload
    for envelope_key in ("snapshot", "data"):
        nested = raw.get(envelope_key)
        if isinstance(nested, dict) and "observations" in nested:
            raw = nested
            break
    calculated_at = parse_ts_utc(raw.get("calculated_at") or raw.get("timestamp_utc"))
    observations_raw = raw.get("observations")
    schema_version = str(raw.get("schema_version") or "").strip()
    if (
        calculated_at is None
        or not isinstance(observations_raw, dict)
        or schema_version != EQUITY_BREADTH_SCHEMA_VERSION
    ):
        return None

    observations: dict[str, EquityBreadthObservation] = {}
    allowed = set(MEGA_EQUITY_COLLECTION_ROOTS)
    for key, value in observations_raw.items():
        if not isinstance(value, dict):
            continue
        root = str(value.get("root") or key or "").strip().upper()
        if root not in allowed:
            continue
        observed_at = parse_ts_utc(
            value.get("observed_at")
            or value.get("timestamp_utc")
            or value.get("timestamp")
        )
        if observed_at is None:
            continue
        roles_raw = value.get("roles")
        roles = (
            tuple(str(role).strip().lower() for role in roles_raw if str(role).strip())
            if isinstance(roles_raw, (list, tuple))
            else ()
        )
        observations[root] = EquityBreadthObservation(
            root=root,
            observed_at=observed_at,
            price=_finite(value.get("price")),
            return_15s=_finite(value.get("return_15s")),
            signed_volume_imbalance_15s=_finite(value.get("signed_volume_imbalance_15s")),
            return_z=_finite(value.get("return_z")),
            imbalance_z=_finite(value.get("imbalance_z")),
            composite_score=_finite(value.get("composite_score")),
            vote=_vote(value.get("vote")),
            issuer_id=str(value.get("issuer_id") or "").strip().upper() or None,
            roles=roles,
            healthy=_health_ok(
                value.get("health"),
                default=_health_ok(value.get("healthy"), default=False),
            ),
            freshness=(
                dict(value["freshness"])
                if isinstance(value.get("freshness"), dict)
                else {}
            ),
            provenance=(
                dict(value["provenance"])
                if isinstance(value.get("provenance"), dict)
                else {}
            ),
        )
    return EquityBreadthSnapshot(
        calculated_at=calculated_at,
        observations=observations,
        healthy=_health_ok(
            raw.get("health"),
            default=_health_ok(raw.get("healthy"), default=False),
        ),
        freshness=(
            dict(raw["freshness"]) if isinstance(raw.get("freshness"), dict) else {}
        ),
        provenance=(
            dict(raw["provenance"]) if isinstance(raw.get("provenance"), dict) else {}
        ),
        schema_version=schema_version,
    )


OnSnapshot = Callable[[EquityBreadthSnapshot], Awaitable[None]]


class EquityBreadthPoller:
    """Small dependency-free reader for the local Theta breadth endpoint."""

    def __init__(
        self,
        *,
        url: str,
        on_snapshot: OnSnapshot,
        proxy_secret: str = "",
        poll_seconds: float = 1.0,
        timeout_seconds: float = 1.0,
    ) -> None:
        self._url = str(url or "").strip()
        self._on_snapshot = on_snapshot
        self._proxy_secret = str(proxy_secret or "").strip()
        self._poll_seconds = max(0.2, float(poll_seconds))
        self._timeout_seconds = max(0.2, float(timeout_seconds))
        self._stop = asyncio.Event()

    def _fetch(self) -> EquityBreadthSnapshot | None:
        if not self._url:
            return None
        headers = {
            "Accept": "application/json",
            "User-Agent": "esnq-equity-breadth/1.0",
        }
        if self._proxy_secret:
            headers["x-options-proxy-secret"] = self._proxy_secret
        request = urllib.request.Request(self._url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
            return None
        return parse_equity_breadth_snapshot(payload)

    async def run(self) -> None:
        while not self._stop.is_set():
            snapshot = await asyncio.to_thread(self._fetch)
            if snapshot is not None:
                await self._on_snapshot(snapshot)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_seconds)
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()
