from __future__ import annotations

import asyncio
import json
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .anomaly_overlay import evaluate_cross_market_anomaly_overlay, load_gexbot_market_context
from .anomaly_overlay_notifier import AnomalyOverlayNotifier
from .calibration import load_live_ok_horizons, load_thresholds_config
from .config import Settings
from .discord_webhook import DiscordWebhookNotifier
from .features import CrossMarketFeatureStore
from .markets import TARGET_MARKETS
from .model import load_models
from .ofi_stream import OfiSample, OfiTcpStream
from .live_cluster_gate import LiveClusterGate, LiveClusterGateConfig, apply_live_cluster_gate
from .shadow_quality_gate import ShadowQualityGateConfig, apply_shadow_quality_gate
from .side_guard import RollingSideGuard
from .signal import DirectionSignalEngine


ET_ZONE = ZoneInfo("America/New_York")


def _parse_session_time(raw: str, default: time) -> time:
    try:
        hour_raw, minute_raw = raw.strip().split(":", maxsplit=1)
        return time(hour=int(hour_raw), minute=int(minute_raw))
    except (AttributeError, TypeError, ValueError):
        return default


def _is_within_session(now_utc: datetime, start_et: time, end_et: time) -> bool:
    now_et = now_utc.astimezone(ET_ZONE).time()
    if start_et <= end_et:
        return start_et <= now_et < end_et
    return now_et >= start_et or now_et < end_et


class LiveSignalService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._feature_store = CrossMarketFeatureStore()
        thresholds = load_thresholds_config(settings.calibration_file)
        live_ok_horizons = load_live_ok_horizons(settings.calibration_file)
        models = load_models(settings.model_file)
        self._engine = DirectionSignalEngine(
            max_spread_es=settings.max_spread_es,
            max_spread_nq=settings.max_spread_nq,
            thresholds_by_market_horizon=thresholds,
            models_by_market_horizon=models,
            live_ok_horizons=live_ok_horizons,
        )
        self._write_lock = asyncio.Lock()
        self._last_emit: dict[str, datetime] = {}
        self._streams: list[OfiTcpStream] = []
        self._discord_notifier: DiscordWebhookNotifier | None = None
        self._side_guard: RollingSideGuard | None = None
        self._side_guard_statuses = set(settings.side_guard_apply_statuses)
        self._rth_only_enabled = settings.rth_only_enabled
        self._rth_start_et = _parse_session_time(settings.rth_start_et, time(9, 30))
        self._rth_end_et = _parse_session_time(settings.rth_end_et, time(16, 0))
        self._shadow_quality_gate = ShadowQualityGateConfig(
            enabled=settings.shadow_quality_gate_enabled,
            apply_statuses=settings.shadow_quality_gate_apply_statuses,
        )
        self._live_cluster_gate = LiveClusterGate.from_file(settings.live_cluster_models_file)
        self._live_cluster_gate_config = LiveClusterGateConfig(
            enabled=settings.live_cluster_gate_enabled,
            apply_statuses=settings.live_cluster_gate_apply_statuses,
            models_file=settings.live_cluster_models_file,
        )
        self._anomaly_overlay_enabled = settings.anomaly_overlay_enabled
        self._anomaly_overlay_alert_enabled = settings.anomaly_overlay_alert_enabled
        self._anomaly_overlay_log_file = settings.anomaly_overlay_log_file
        self._gexbot_context_cache_file = settings.gexbot_context_cache_file
        self._anomaly_overlay_notifier: AnomalyOverlayNotifier | None = None
        if settings.anomaly_overlay_alert_enabled and settings.anomaly_overlay_discord_webhook_url:
            self._anomaly_overlay_notifier = AnomalyOverlayNotifier(
                webhook_url=settings.anomaly_overlay_discord_webhook_url,
                cooldown_seconds=settings.anomaly_overlay_discord_cooldown_seconds,
                timeout_seconds=settings.discord_webhook_timeout_seconds,
            )
        if settings.discord_webhook_enabled and settings.discord_webhook_url:
            self._discord_notifier = DiscordWebhookNotifier(
                webhook_url=settings.discord_webhook_url,
                enabled_statuses=settings.discord_webhook_statuses,
                min_confidence=settings.discord_webhook_min_confidence,
                cooldown_seconds=settings.discord_webhook_cooldown_seconds,
                timeout_seconds=settings.discord_webhook_timeout_seconds,
            )
        if settings.side_guard_enabled:
            self._side_guard = RollingSideGuard(
                lookback_trades=settings.side_guard_lookback_trades,
                min_samples=settings.side_guard_min_samples,
                min_win_rate=settings.side_guard_min_win_rate,
            )

    async def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=True, default=str) + "\n"
        async with self._write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(self._append_line_sync, path, line)

    @staticmethod
    def _append_line_sync(path: Path, line: str) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    async def _emit_signals_for_target(self, target: str, now_utc: datetime) -> None:
        if self._rth_only_enabled and not _is_within_session(now_utc, self._rth_start_et, self._rth_end_et):
            return
        last = self._last_emit.get(target)
        if last is not None:
            elapsed = (now_utc - last).total_seconds()
            if elapsed < self._settings.signal_emit_interval_seconds:
                return
        row = self._feature_store.build_target_row(
            target,
            now_utc=now_utc,
            stale_seconds=self._settings.signal_stale_seconds,
        )
        if row.get("status") != "ok":
            return
        target_spot = row.get("target_spot")
        if self._side_guard is not None:
            self._side_guard.observe_snapshot(market=target, now_utc=now_utc, spot=target_spot)
        await self._append_jsonl(self._settings.feature_log_file, row)
        for horizon in (5, 10):
            signal = self._engine.evaluate(row, horizon_minutes=horizon)
            payload = signal.to_dict()
            payload["feature_row_timestamp_utc"] = row.get("timestamp_utc")
            for key in (
                "target_age_s",
                "target_stale",
                "target_spot",
                "target_spread",
                "target_ofi_5s",
                "target_ofi_15s",
                "target_ofi_60s",
                "target_ofi_300s",
                "target_ofi_600s",
                "target_ofi_15s_z",
                "target_ofi_60s_z",
                "target_ofi_300s_z",
                "target_large_trade_signed_60s",
                "target_large_trade_signed_300s",
                "target_large_trade_count_60s",
                "target_large_trade_count_300s",
                "target_large_trade_buy_ratio_300s",
                "target_direction_hint",
                "cross_index_ready",
                "cross_index_confirmation",
                "cross_index_contradiction",
                "cross_index_balance",
                "cross_index_lead_confirmation",
                "cross_index_lead_contradiction",
                "cross_index_lead_balance",
                "cross_index_score_avg",
                "cross_index_values",
                "cross_index_lag30_values",
                "cross_index_max_age_s",
                "macro_ready",
                "macro_regime_score",
                "macro_lead_score",
                "macro_votes",
                "macro_ofi_values",
                "macro_lead_votes",
                "macro_lag30_ofi_values",
            ):
                payload[key] = row.get(key)
            apply_shadow_quality_gate(payload, config=self._shadow_quality_gate)
            apply_live_cluster_gate(payload, gate=self._live_cluster_gate, config=self._live_cluster_gate_config)
            status = str(payload.get("status") or "").lower()
            if self._side_guard is not None and status in self._side_guard_statuses:
                allowed, meta = self._side_guard.should_allow(signal_payload=payload)
                layers = payload.get("layers")
                if isinstance(layers, dict):
                    layers["side_guard"] = meta
                if not allowed:
                    payload["status"] = "watch"
                    payload["reason"] = "side_guard_low_win_rate"
                    flags = payload.get("risk_flags")
                    if isinstance(flags, list) and "side_guard_low_win_rate" not in flags:
                        flags.append("side_guard_low_win_rate")
            if self._side_guard is not None:
                self._side_guard.register_signal(signal_payload=payload, entry_spot=target_spot, now_utc=now_utc)
            await self._append_jsonl(self._settings.signal_log_file, payload)
            if self._discord_notifier is not None:
                await self._discord_notifier.send_if_needed(payload)
            await self._maybe_emit_anomaly_overlay(payload)
        self._last_emit[target] = now_utc

    async def _maybe_emit_anomaly_overlay(self, signal_payload: dict[str, Any]) -> None:
        if not self._anomaly_overlay_enabled:
            return
        market = str(signal_payload.get("market") or "").upper()
        gexbot_context = load_gexbot_market_context(self._gexbot_context_cache_file, market)
        overlay = evaluate_cross_market_anomaly_overlay(
            signal_payload=signal_payload,
            gexbot_context=gexbot_context,
        )
        if overlay is None:
            return
        record = {
            "timestamp_utc": signal_payload.get("timestamp_utc"),
            "signal": {
                "market": signal_payload.get("market"),
                "horizon_minutes": signal_payload.get("horizon_minutes"),
                "status": signal_payload.get("status"),
                "direction": signal_payload.get("direction"),
                "p_hit": signal_payload.get("p_hit"),
                "confidence": signal_payload.get("confidence"),
            },
            "overlay": overlay,
            "action": "post" if self._anomaly_overlay_alert_enabled else "shadow",
        }
        await self._append_jsonl(self._anomaly_overlay_log_file, record)
        if self._anomaly_overlay_notifier is not None and self._anomaly_overlay_alert_enabled:
            await self._anomaly_overlay_notifier.send_if_needed(overlay, signal_payload)

    async def _on_sample(self, sample: OfiSample) -> None:
        self._feature_store.ingest(sample)
        now_utc = datetime.now(timezone.utc)
        # Always try both targets. New sample in cross markets can confirm/contradict ES/NQ.
        for target in sorted(TARGET_MARKETS):
            await self._emit_signals_for_target(target, now_utc)

    def _build_streams(self) -> list[OfiTcpStream]:
        streams: list[OfiTcpStream] = []
        if self._settings.enable_core_stream:
            streams.append(
                OfiTcpStream(
                    name="core_ofi",
                    host=self._settings.core_ofi_host,
                    port=self._settings.core_ofi_port,
                    on_sample=self._on_sample,
                )
            )
        if self._settings.enable_cross_stream:
            streams.append(
                OfiTcpStream(
                    name="cross_market_ofi",
                    host=self._settings.cross_ofi_host,
                    port=self._settings.cross_ofi_port,
                    on_sample=self._on_sample,
                )
            )
        return streams

    async def run(self) -> None:
        self._settings.data_dir.mkdir(parents=True, exist_ok=True)
        self._streams = self._build_streams()
        if not self._streams:
            raise RuntimeError("No OFI streams enabled. Enable at least one stream in environment.")
        tasks = [asyncio.create_task(stream.run(), name=f"ofi:{idx}") for idx, stream in enumerate(self._streams)]
        try:
            await asyncio.gather(*tasks)
        finally:
            for stream in self._streams:
                stream.stop()
            for task in tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
