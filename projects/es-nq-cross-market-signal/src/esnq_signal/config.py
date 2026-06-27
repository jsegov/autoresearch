from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_csv(name: str, default: str) -> tuple[str, ...]:
    raw = _env(name, default)
    values = [part.strip().lower() for part in str(raw).split(",")]
    cleaned = tuple(part for part in values if part)
    return cleaned


@dataclass(frozen=True)
class Settings:
    core_ofi_host: str
    core_ofi_port: int
    cross_ofi_host: str
    cross_ofi_port: int
    enable_core_stream: bool
    enable_cross_stream: bool
    signal_emit_interval_seconds: float
    signal_stale_seconds: float
    max_spread_es: float
    max_spread_nq: float
    calibration_file: Path | None
    model_file: Path | None
    data_dir: Path
    feature_log_file: Path
    signal_log_file: Path
    discord_webhook_enabled: bool
    discord_webhook_url: str
    discord_webhook_statuses: tuple[str, ...]
    discord_webhook_min_confidence: float
    discord_webhook_cooldown_seconds: float
    discord_webhook_timeout_seconds: float
    rth_only_enabled: bool
    rth_start_et: str
    rth_end_et: str
    side_guard_enabled: bool
    side_guard_lookback_trades: int
    side_guard_min_samples: int
    side_guard_min_win_rate: float
    side_guard_apply_statuses: tuple[str, ...]
    shadow_quality_gate_enabled: bool
    shadow_quality_gate_apply_statuses: tuple[str, ...]
    live_cluster_gate_enabled: bool
    live_cluster_gate_apply_statuses: tuple[str, ...]
    live_cluster_models_file: Path | None
    anomaly_overlay_enabled: bool
    anomaly_overlay_log_file: Path
    anomaly_overlay_alert_enabled: bool
    anomaly_overlay_discord_webhook_url: str
    anomaly_overlay_discord_cooldown_seconds: float
    gexbot_context_cache_file: Path | None

    @staticmethod
    def from_env() -> "Settings":
        data_dir = Path(_env("DATA_DIR", "data"))
        feature_file = Path(_env("FEATURE_LOG_FILE", str(data_dir / "cross_market_features.jsonl")))
        signal_file = Path(_env("SIGNAL_LOG_FILE", str(data_dir / "es_nq_direction_signals.jsonl")))
        calibration_raw = os.getenv("CALIBRATION_FILE")
        calibration_file = Path(calibration_raw.strip()) if calibration_raw and calibration_raw.strip() else None
        model_raw = os.getenv("MODEL_FILE")
        model_file = Path(model_raw.strip()) if model_raw and model_raw.strip() else None
        cluster_models_raw = os.getenv("LIVE_CLUSTER_MODELS_FILE")
        live_cluster_models_file = (
            Path(cluster_models_raw.strip())
            if cluster_models_raw and cluster_models_raw.strip()
            else data_dir / "live_cluster_models_2026-06-14.json"
        )
        gexbot_cache_raw = os.getenv("GEXBOT_CONTEXT_CACHE_FILE")
        gexbot_context_cache_file = (
            Path(gexbot_cache_raw.strip()) if gexbot_cache_raw and gexbot_cache_raw.strip() else None
        )
        anomaly_log = Path(
            _env("ANOMALY_OVERLAY_LOG_FILE", str(data_dir / "cross_market_anomaly_overlay.jsonl"))
        )
        return Settings(
            core_ofi_host=_env("CORE_OFI_HOST", "127.0.0.1"),
            core_ofi_port=_env_int("CORE_OFI_PORT", 5561),
            cross_ofi_host=_env("CROSS_OFI_HOST", "127.0.0.1"),
            cross_ofi_port=_env_int("CROSS_OFI_PORT", 5562),
            enable_core_stream=_env_bool("ENABLE_CORE_STREAM", False),
            enable_cross_stream=_env_bool("ENABLE_CROSS_STREAM", True),
            signal_emit_interval_seconds=max(0.2, _env_float("SIGNAL_EMIT_INTERVAL_SECONDS", 1.0)),
            signal_stale_seconds=max(0.1, _env_float("SIGNAL_STALE_SECONDS", 2.0)),
            max_spread_es=max(0.01, _env_float("MAX_SPREAD_ES", 1.0)),
            max_spread_nq=max(0.01, _env_float("MAX_SPREAD_NQ", 2.0)),
            calibration_file=calibration_file,
            model_file=model_file,
            data_dir=data_dir,
            feature_log_file=feature_file,
            signal_log_file=signal_file,
            discord_webhook_enabled=_env_bool("DISCORD_WEBHOOK_ENABLED", False),
            discord_webhook_url=_env("DISCORD_WEBHOOK_URL", ""),
            discord_webhook_statuses=_env_csv("DISCORD_WEBHOOK_STATUSES", "post,high_priority"),
            discord_webhook_min_confidence=max(0.0, _env_float("DISCORD_WEBHOOK_MIN_CONFIDENCE", 0.0)),
            discord_webhook_cooldown_seconds=max(0.0, _env_float("DISCORD_WEBHOOK_COOLDOWN_SECONDS", 45.0)),
            discord_webhook_timeout_seconds=max(0.5, _env_float("DISCORD_WEBHOOK_TIMEOUT_SECONDS", 5.0)),
            rth_only_enabled=_env_bool("RTH_ONLY_ENABLED", False),
            rth_start_et=_env("RTH_START_ET", "09:30"),
            rth_end_et=_env("RTH_END_ET", "16:00"),
            side_guard_enabled=_env_bool("SIDE_GUARD_ENABLED", True),
            side_guard_lookback_trades=max(5, _env_int("SIDE_GUARD_LOOKBACK_TRADES", 80)),
            side_guard_min_samples=max(3, _env_int("SIDE_GUARD_MIN_SAMPLES", 25)),
            side_guard_min_win_rate=max(0.01, min(0.99, _env_float("SIDE_GUARD_MIN_WIN_RATE", 0.53))),
            side_guard_apply_statuses=_env_csv("SIDE_GUARD_APPLY_STATUSES", "post,high_priority"),
            shadow_quality_gate_enabled=_env_bool("SHADOW_QUALITY_GATE_ENABLED", True),
            shadow_quality_gate_apply_statuses=_env_csv(
                "SHADOW_QUALITY_GATE_APPLY_STATUSES", "post,high_priority"
            ),
            live_cluster_gate_enabled=_env_bool("LIVE_CLUSTER_GATE_ENABLED", True),
            live_cluster_gate_apply_statuses=_env_csv(
                "LIVE_CLUSTER_GATE_APPLY_STATUSES", "post,high_priority"
            ),
            live_cluster_models_file=live_cluster_models_file,
            anomaly_overlay_enabled=_env_bool("ANOMALY_OVERLAY_ENABLED", True),
            anomaly_overlay_log_file=anomaly_log,
            anomaly_overlay_alert_enabled=_env_bool("ANOMALY_OVERLAY_ALERT_ENABLED", False),
            anomaly_overlay_discord_webhook_url=_env("ANOMALY_OVERLAY_DISCORD_WEBHOOK_URL", ""),
            anomaly_overlay_discord_cooldown_seconds=max(
                0.0, _env_float("ANOMALY_OVERLAY_DISCORD_COOLDOWN_SECONDS", 120.0)
            ),
            gexbot_context_cache_file=gexbot_context_cache_file,
        )
