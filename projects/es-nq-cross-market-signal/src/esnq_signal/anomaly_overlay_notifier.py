from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


class AnomalyOverlayNotifier:
    def __init__(
        self,
        *,
        webhook_url: str,
        cooldown_seconds: float,
        timeout_seconds: float,
    ) -> None:
        self._webhook_url = str(webhook_url or "").strip()
        self._cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._timeout_seconds = max(0.5, float(timeout_seconds))
        self._last_attempt_by_key: dict[str, float] = {}

    async def send_if_needed(self, overlay: dict[str, Any], signal_payload: dict[str, Any]) -> bool:
        if not self._webhook_url:
            return False
        dedupe_key = self._dedupe_key(overlay, signal_payload)
        now = time.monotonic()
        last_attempt = self._last_attempt_by_key.get(dedupe_key)
        if last_attempt is not None and (now - last_attempt) < self._cooldown_seconds:
            return False
        self._last_attempt_by_key[dedupe_key] = now
        payload = {
            "content": self._format_message(overlay, signal_payload),
            "allowed_mentions": {"parse": []},
        }
        import asyncio

        return await asyncio.to_thread(self._post_json, payload)

    @staticmethod
    def _dedupe_key(overlay: dict[str, Any], signal_payload: dict[str, Any]) -> str:
        return "|".join(
            [
                str(overlay.get("profile") or ""),
                str(signal_payload.get("market") or "").upper(),
                str(signal_payload.get("direction") or "").lower(),
                str(signal_payload.get("timestamp_utc") or "")[:16],
            ]
        )

    @staticmethod
    def _format_message(overlay: dict[str, Any], signal_payload: dict[str, Any]) -> str:
        market = str(signal_payload.get("market") or "").upper()
        direction = str(signal_payload.get("direction") or "neutral").upper()
        status = str(signal_payload.get("status") or "watch").upper()
        p_hit = signal_payload.get("p_hit")
        confidence = signal_payload.get("confidence")
        reason = str(overlay.get("reason") or "").strip()
        line1 = f"**ANOMALY OVERLAY | {market} 5m {direction} | {status}**"
        line2 = f"`{overlay.get('label')}`"
        line3 = f"`{reason}`"
        line4 = f"Model hit `{p_hit}` conf `{confidence}` nearest GEX `{overlay.get('nearest_gex_key')}`"
        return "\n".join(line for line in (line1, line2, line3, line4) if line)[:1900]

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
        except (urllib.error.HTTPError, urllib.error.URLError):
            return False
