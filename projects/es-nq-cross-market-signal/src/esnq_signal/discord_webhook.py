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


def _fmt_int(value: Any, *, default: str = "na", signed: bool = False) -> str:
    if value is None:
        return default
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return default
    prefix = "+" if signed and parsed > 0 else ""
    return f"{prefix}{parsed}"


def _fmt_map(raw: Any, *, decimals: int = 2, signed: bool = True, max_items: int = 4) -> str:
    if not isinstance(raw, dict) or not raw:
        return "na"
    parts: list[str] = []
    for key in sorted(raw.keys())[:max_items]:
        parts.append(f"{str(key).upper()}:{_fmt_num(raw.get(key), decimals=decimals, signed=signed)}")
    return " ".join(parts) if parts else "na"


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
        status = str(signal_payload.get("status") or "watch").upper()
        market = str(signal_payload.get("market") or "").upper()
        horizon = int(signal_payload.get("horizon_minutes") or 0)
        direction = str(signal_payload.get("direction") or "neutral").upper()
        p_hit = _num(signal_payload.get("p_hit"))
        confidence = _num(signal_payload.get("confidence"))
        p_up = _num(signal_payload.get("p_up"))
        reason = str(signal_payload.get("reason") or "").strip()
        ts = str(signal_payload.get("timestamp_utc") or "").strip()
        price_text = _fmt_num(signal_payload.get("target_spot"), decimals=2)
        spread_text = _fmt_num(signal_payload.get("target_spread"), decimals=3)
        age_text = _fmt_num(signal_payload.get("target_age_s"), decimals=2)
        layers = signal_payload.get("layers") if isinstance(signal_payload.get("layers"), dict) else {}
        model = layers.get("model") if isinstance(layers.get("model"), dict) else {}
        raw_score = layers.get("direction_core_raw")
        raw_edge = model.get("raw_edge")
        direction_gap = model.get("direction_gap")
        large_trades = layers.get("large_trades") if isinstance(layers.get("large_trades"), dict) else {}
        risk_flags = signal_payload.get("risk_flags")
        risk_text = ""
        if isinstance(risk_flags, list) and risk_flags:
            risk_text = ",".join(str(flag) for flag in risk_flags[:5])
        direction_icon = "LONG" if direction == "LONG" else ("SHORT" if direction == "SHORT" else "NEUTRAL")
        line1 = f"**{status} | {market} {horizon}m {direction_icon} | {price_text}**"
        line2 = f"`{reason}`  `{ts}`"
        line3 = (
            "**Model**  "
            f"Hit `{p_hit:.3f}`  Conf `{confidence:.3f}`  Up `{p_up:.3f}`  "
            f"Raw `{_fmt_num(raw_score, decimals=2, signed=True)}`  "
            f"Edge `{_fmt_num(raw_edge, decimals=2)}`  Gap `{_fmt_num(direction_gap, decimals=3)}`"
        )
        line4 = (
            "**Target OFI**  "
            f"5s `{_fmt_num(signal_payload.get('target_ofi_5s'), decimals=0, signed=True)}`  "
            f"15s `{_fmt_num(signal_payload.get('target_ofi_15s'), decimals=0, signed=True)}`  "
            f"60s `{_fmt_num(signal_payload.get('target_ofi_60s'), decimals=0, signed=True)}`  "
            f"300s `{_fmt_num(signal_payload.get('target_ofi_300s'), decimals=0, signed=True)}`  "
            f"Z15 `{_fmt_num(signal_payload.get('target_ofi_15s_z'), decimals=2, signed=True)}`"
        )
        line5 = f"**Market Quality**  Spread `{spread_text}`  Age `{age_text}s`"
        line6 = (
            "**Cross Index**  "
            f"Ready `{_fmt_int(signal_payload.get('cross_index_ready'))}`  "
            f"Confirm `{_fmt_int(signal_payload.get('cross_index_confirmation'))}`  "
            f"Against `{_fmt_int(signal_payload.get('cross_index_contradiction'))}`  "
            f"Lead `{_fmt_int(signal_payload.get('cross_index_lead_balance'), signed=True)}`"
        )
        line7 = f"Peers: `{_fmt_map(signal_payload.get('cross_index_values'), decimals=2)}`"
        line8 = (
            "**Macro**  "
            f"Ready `{_fmt_int(signal_payload.get('macro_ready'))}`  "
            f"Regime `{_fmt_int(signal_payload.get('macro_regime_score'), signed=True)}`  "
            f"Lead `{_fmt_int(signal_payload.get('macro_lead_score'), signed=True)}`  "
            f"Votes `{_fmt_map(signal_payload.get('macro_votes'), decimals=0)}`"
        )
        line9 = (
            "**Large Trades**  "
            f"60s `{_fmt_num(large_trades.get('signed_60s'), decimals=0, signed=True)}`  "
            f"300s `{_fmt_num(large_trades.get('signed_300s'), decimals=0, signed=True)}`  "
            f"Count `{_fmt_int(large_trades.get('count_300s'))}`"
        )
        line10 = f"**Risk**  `{risk_text}`" if risk_text else ""
        message = "\n".join(
            line for line in (line1, line2, line3, line4, line5, line6, line7, line8, line9, line10) if line
        )
        return message[:1900]

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
