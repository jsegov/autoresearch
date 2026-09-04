from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .anomaly_overlay import evaluate_cross_market_anomaly_overlay, load_gexbot_market_context
from .anomaly_overlay_notifier import AnomalyOverlayNotifier
from .calibration import load_live_ok_horizons, load_thresholds_config
from .config import Settings
from .discord_webhook import DiscordWebhookNotifier
from .equity_breadth import EquityBreadthPoller, EquityBreadthSnapshot
from .features import CrossMarketFeatureStore
from .markets import EQUITY_TARGET_MARKETS, ProductProfile, TARGET_MARKETS, product_profile
from .model import load_models
from .ofi_stream import OfiSample, OfiTcpStream
from .live_cluster_gate import LiveClusterGate, LiveClusterGateConfig, apply_live_cluster_gate
from .shadow_quality_gate import ShadowQualityGateConfig, apply_shadow_quality_gate
from .side_guard import RollingSideGuard
from .signal import DirectionSignalEngine
from .sierra_alert_bridge import emit_cross_market_pulse


ET_ZONE = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)


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


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (7 * (occurrence - 1)))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = (
        date(year, 12, 31)
        if month == 12
        else date(year, month + 1, 1) - timedelta(days=1)
    )
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    # Anonymous Gregorian computus.
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    day_offset = (32 + (2 * e) + (2 * i) - h - k) % 7
    m = (a + (11 * h) + (22 * day_offset)) // 451
    month = (h + day_offset - (7 * m) + 114) // 31
    day = ((h + day_offset - (7 * m) + 114) % 31) + 1
    return date(year, month, day)


def _observed_equity_holiday(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _common_equity_holidays(year: int) -> set[date]:
    holidays = {
        _observed_equity_holiday(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter_sunday(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed_equity_holiday(date(year, 6, 19)),
        _observed_equity_holiday(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed_equity_holiday(date(year, 12, 25)),
    }
    # A Saturday New Year's Day is observed on Dec. 31 of the prior year.
    if date(year + 1, 1, 1).weekday() == 5:
        holidays.add(date(year, 12, 31))
    return holidays


def _is_common_equity_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in _common_equity_holidays(day.year)


def _common_equity_early_close_date(day: date) -> bool:
    """Common NYSE/Nasdaq 13:00 ET close rules.

    Exchange calendars remain the deployment authority.  This deterministic
    fallback covers the recurring half-days without adding a runtime package.
    """

    thanksgiving = _nth_weekday(day.year, 11, 3, 4)
    candidates = {
        date(day.year, 7, 3),
        thanksgiving + timedelta(days=1),
        date(day.year, 12, 24),
    }
    return day in candidates and _is_common_equity_trading_day(day)


def _is_equity_alert_session(
    now_utc: datetime,
    *,
    extra_early_close_dates: set[str] | None = None,
) -> bool:
    now_et = now_utc.astimezone(ET_ZONE)
    if not _is_common_equity_trading_day(now_et.date()):
        return False
    day_text = now_et.date().isoformat()
    early_close = _common_equity_early_close_date(now_et.date()) or (
        day_text in (extra_early_close_dates or set())
    )
    end = time(12, 55) if early_close else time(15, 55)
    return time(9, 35) <= now_et.time() < end


def _should_evaluate_target(
    profile: ProductProfile,
    *,
    within_alert_session: bool,
    rth_only_enabled: bool,
) -> bool:
    if profile.rules_first:
        return True
    return within_alert_session or not rth_only_enabled


def _experimental_notification_eligible(
    profile: ProductProfile,
    *,
    horizon_minutes: int,
    within_alert_session: bool,
) -> bool:
    return bool(
        profile.rules_first
        and within_alert_session
        and profile.primary_alert_horizon_minutes is not None
        and int(horizon_minutes) == profile.primary_alert_horizon_minutes
    )


class LiveSignalService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        smoke_validated = set(settings.experimental_smoke_validated_markets)
        smoke_validated.update(settings.mega_equity_smoke_validated_markets)
        self._feature_store = CrossMarketFeatureStore(smoke_validated_markets=smoke_validated)
        thresholds = load_thresholds_config(settings.calibration_file)
        live_ok_horizons = load_live_ok_horizons(settings.calibration_file)
        models = load_models(settings.model_file)
        experimental_posting_enabled = {
            market
            for market, enabled in (
                ("CL", settings.experimental_cl_posting_enabled),
                ("UB", settings.experimental_ub_posting_enabled),
            )
            if enabled
        }
        if settings.mega_equities_enabled:
            experimental_posting_enabled.update(
                market
                for market in settings.mega_equity_post_markets
                if market in EQUITY_TARGET_MARKETS
            )
        self._engine = DirectionSignalEngine(
            max_spread_es=settings.max_spread_es,
            max_spread_nq=settings.max_spread_nq,
            max_spread_cl=settings.max_spread_cl,
            max_spread_ub=settings.max_spread_ub,
            experimental_posting_enabled=experimental_posting_enabled,
            experimental_smoke_validated=smoke_validated,
            thresholds_by_market_horizon=thresholds,
            models_by_market_horizon=models,
            live_ok_horizons=live_ok_horizons,
        )
        self._write_lock = asyncio.Lock()
        self._last_emit: dict[str, datetime] = {}
        self._streams: list[OfiTcpStream] = []
        requested_list = [
            market
            for market in settings.enabled_target_markets
            if market in TARGET_MARKETS
            and (market not in EQUITY_TARGET_MARKETS or settings.mega_equities_enabled)
        ]
        if settings.mega_equities_enabled:
            requested_list.extend(
                market
                for market in settings.mega_equity_markets
                if market in EQUITY_TARGET_MARKETS and market not in requested_list
            )
        requested_targets = tuple(requested_list)
        self._target_markets = requested_targets or ("ES", "NQ")
        self._discord_notifier: DiscordWebhookNotifier | None = None
        self._experimental_notifiers: dict[str, DiscordWebhookNotifier] = {}
        self._side_guard: RollingSideGuard | None = None
        self._side_guard_statuses = set(settings.side_guard_apply_statuses)
        self._rth_only_enabled = settings.rth_only_enabled
        self._rth_start_et = _parse_session_time(settings.rth_start_et, time(9, 30))
        self._rth_end_et = _parse_session_time(settings.rth_end_et, time(16, 0))
        self._mega_equity_early_close_dates = {
            value.strip() for value in settings.mega_equity_early_close_dates if value.strip()
        }
        self._equity_breadth_poller: EquityBreadthPoller | None = None
        if any(target in EQUITY_TARGET_MARKETS for target in self._target_markets):
            self._equity_breadth_poller = EquityBreadthPoller(
                url=settings.equity_breadth_url,
                on_snapshot=self._on_equity_breadth,
                proxy_secret=settings.equity_breadth_proxy_secret,
                poll_seconds=settings.equity_breadth_poll_seconds,
                timeout_seconds=settings.equity_breadth_timeout_seconds,
            )
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
        for market, enabled, webhook_url in (
            (
                "CL",
                settings.experimental_cl_posting_enabled,
                settings.experimental_cl_discord_webhook_url,
            ),
            (
                "UB",
                settings.experimental_ub_posting_enabled,
                settings.experimental_ub_discord_webhook_url,
            ),
        ):
            if enabled and webhook_url:
                self._experimental_notifiers[market] = DiscordWebhookNotifier(
                    webhook_url=webhook_url,
                    enabled_statuses=("post",),
                    min_confidence=0.0,
                    cooldown_seconds=settings.experimental_discord_cooldown_seconds,
                    timeout_seconds=settings.discord_webhook_timeout_seconds,
                    dedupe_scope="market",
                )
        self._sierra_alert_bridge_enabled = bool(settings.sierra_alert_bridge_enabled)
        self._sierra_alert_bridge_path = Path(settings.sierra_alert_bridge_path)
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
        profile = product_profile(target)
        if profile is None:
            return
        within_alert_session = (
            _is_equity_alert_session(
                now_utc,
                extra_early_close_dates=self._mega_equity_early_close_dates,
            )
            if target in EQUITY_TARGET_MARKETS
            else _is_within_session(now_utc, self._rth_start_et, self._rth_end_et)
        )
        if not _should_evaluate_target(
            profile,
            within_alert_session=within_alert_session,
            rth_only_enabled=self._rth_only_enabled,
        ):
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
        row["alert_session_open"] = within_alert_session
        target_spot = row.get("target_spot")
        if self._side_guard is not None:
            self._side_guard.observe_snapshot(market=target, now_utc=now_utc, spot=target_spot)
        await self._append_jsonl(self._settings.feature_log_file, row)
        for horizon in profile.horizons_minutes:
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
                "target_ofi_norm_15s",
                "target_ofi_norm_60s",
                "target_trade_imbalance_norm_15s",
                "input_mode",
                "input_provenance",
                "input_value_15s",
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
                "contract_id",
                "profile_id",
                "signal_kind",
                "roll_detected_at_utc",
                "roll_warmup_active",
                "roll_warmup_seconds",
                "peer_confirmation",
                "rule_score",
                "context_ofi_values",
                "context_age_seconds",
                "alert_session_open",
                "ticker",
                "ticker_identity_ok",
                "asset_class",
                "coordinate_space",
                "issuer_id",
                "benchmark_market",
                "sector_market",
                "breadth_snapshot_age_s",
                "target_future_clock_skew",
                "rule_candidate",
            ):
                row_value = row.get(key)
                # Preserve canonical values already established by the signal
                # engine (especially signal_kind/asset_class/input_mode) when
                # an incomplete feature row omits them. Missing diagnostics
                # are still emitted explicitly as null.
                if row_value is not None or key not in payload:
                    payload[key] = row_value
            is_rules_first = str(payload.get("signal_kind") or "") == "rules_first"
            if not is_rules_first:
                apply_shadow_quality_gate(payload, config=self._shadow_quality_gate)
                apply_live_cluster_gate(payload, gate=self._live_cluster_gate, config=self._live_cluster_gate_config)
            status = str(payload.get("status") or "").lower()
            if not is_rules_first and self._side_guard is not None and status in self._side_guard_statuses:
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
            if not is_rules_first and self._side_guard is not None:
                self._side_guard.register_signal(signal_payload=payload, entry_spot=target_spot, now_utc=now_utc)
            await self._append_jsonl(self._settings.signal_log_file, payload)
            posted = False
            if is_rules_first:
                notifier = self._experimental_notifiers.get(target)
                notification_eligible = _experimental_notification_eligible(
                    profile,
                    horizon_minutes=horizon,
                    within_alert_session=within_alert_session,
                )
                if notifier is not None and notification_eligible:
                    posted = await notifier.send_if_needed(payload)
            elif self._discord_notifier is not None:
                posted = await self._discord_notifier.send_if_needed(payload)
            if posted and self._sierra_alert_bridge_enabled:
                try:
                    emit_cross_market_pulse(self._sierra_alert_bridge_path, payload)
                except OSError:
                    logger.exception(
                        "sierra alert bridge write failed market=%s horizon=%s",
                        payload.get("market"),
                        payload.get("horizon_minutes"),
                    )
            if not is_rules_first:
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
        # New samples in peers/context can change any enabled target.
        for target in self._target_markets:
            await self._emit_signals_for_target(target, now_utc)

    async def _on_equity_breadth(self, snapshot: EquityBreadthSnapshot) -> None:
        self._feature_store.ingest_equity_breadth(snapshot)
        now_utc = datetime.now(timezone.utc)
        for target in self._target_markets:
            if target in EQUITY_TARGET_MARKETS:
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
        if self._equity_breadth_poller is not None:
            tasks.append(
                asyncio.create_task(
                    self._equity_breadth_poller.run(),
                    name="equity-breadth",
                )
            )
        try:
            await asyncio.gather(*tasks)
        finally:
            for stream in self._streams:
                stream.stop()
            if self._equity_breadth_poller is not None:
                self._equity_breadth_poller.stop()
            for task in tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
