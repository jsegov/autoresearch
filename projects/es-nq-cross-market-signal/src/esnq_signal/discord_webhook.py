from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from typing import Any


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_num(value: Any, *, decimals: int = 2, default: str = "na", signed: bool = False) -> str:
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:
        return default
    sign = "+" if signed else ""
    return f"{parsed:{sign}.{decimals}f}"


def _clean_statuses(values: tuple[str, ...]) -> tuple[str, ...]:
    allowed = {str(v or "").strip().lower() for v in values if str(v or "").strip()}
    if not allowed:
        return ("post", "high_priority")
    return tuple(sorted(allowed))


class DiscordWebhookNotifier:
    def __init__(
        self,
        *,
        webhook_url: str,
        enabled_statuses: tuple[str, ...],
        min_confidence: float,
        cooldown_seconds: float,
        timeout_seconds: float,
    ) -> None:
        self._webhook_url = str(webhook_url or "").strip()
        self._enabled_statuses = _clean_statuses(enabled_statuses)
        self._min_confidence = max(0.0, float(min_confidence))
        self._cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._timeout_seconds = max(0.5, float(timeout_seconds))
        self._last_attempt_by_key: dict[str, float] = {}

    async def send_if_needed(self, signal_payload: dict[str, Any]) -> bool:
        if not self._webhook_url:
            return False

        status = str(signal_payload.get("status") or "").strip().lower()
        if status not in self._enabled_statuses:
            return False

        confidence = _num(signal_payload.get("confidence"))
        if confidence < self._min_confidence:
            return False

        dedupe_key = self._dedupe_key(signal_payload)
        now = time.monotonic()
        last_attempt = self._last_attempt_by_key.get(dedupe_key)
        if last_attempt is not None and (now - last_attempt) < self._cooldown_seconds:
            return False
        self._last_attempt_by_key[dedupe_key] = now

        payload = {
            "content": self._format_message(signal_payload),
            "allowed_mentions": {"parse": []},
        }
        return await asyncio.to_thread(self._post_json, payload)

    @staticmethod
    def _dedupe_key(signal_payload: dict[str, Any]) -> str:
        market = str(signal_payload.get("market") or "").upper()
        horizon = int(signal_payload.get("horizon_minutes") or 0)
        direction = str(signal_payload.get("direction") or "").lower()
        status = str(signal_payload.get("status") or "").lower()
        reason = str(signal_payload.get("reason") or "").lower()
        return f"{market}:{horizon}:{direction}:{status}:{reason}"

    @staticmethod
    def _format_message(signal_payload: dict[str, Any]) -> str:
        """Discord content only — full signal detail remains in SIGNAL_LOG_FILE JSONL."""
        status = str(signal_payload.get("status") or "watch").upper()
        market = str(signal_payload.get("market") or "").upper()
        horizon = int(signal_payload.get("horizon_minutes") or 0)
        direction = str(signal_payload.get("direction") or "neutral").upper()
        price_text = _fmt_num(signal_payload.get("target_spot"), decimals=2)
        direction_label = "LONG" if direction == "LONG" else ("SHORT" if direction == "SHORT" else "NEUTRAL")
        return f"{status} | {market} {horizon}m {direction_label} | {price_text}"

    def _post_json(self, payload: dict[str, Any]) -> bool:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        request = urllib.request.Request(
            self._webhook_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "es-nq-cross-market-signal/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                status_code = int(getattr(response, "status", 0))
                return 200 <= status_code < 300
        except urllib.error.HTTPError:
            return False
        except urllib.error.URLError:
            return False
