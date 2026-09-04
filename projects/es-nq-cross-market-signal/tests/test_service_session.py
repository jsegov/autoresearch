from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, time, timezone

from esnq_signal.config import Settings
from esnq_signal.markets import product_profile
from esnq_signal.service import (
    LiveSignalService,
    _experimental_notification_eligible,
    _is_within_session,
    _parse_session_time,
    _should_evaluate_target,
    _is_equity_alert_session,
)


def test_parse_session_time_falls_back_on_invalid_value() -> None:
    fallback = time(9, 30)

    assert _parse_session_time("10:15", fallback) == time(10, 15)
    assert _parse_session_time("bad", fallback) == fallback


def test_rth_session_window_is_inclusive_start_exclusive_end() -> None:
    start = time(9, 30)
    end = time(16, 0)

    assert not _is_within_session(datetime(2026, 6, 4, 13, 29, tzinfo=timezone.utc), start, end)
    assert _is_within_session(datetime(2026, 6, 4, 13, 30, tzinfo=timezone.utc), start, end)
    assert _is_within_session(datetime(2026, 6, 4, 19, 59, tzinfo=timezone.utc), start, end)
    assert not _is_within_session(datetime(2026, 6, 4, 20, 0, tzinfo=timezone.utc), start, end)


def test_experimental_targets_evaluate_outside_session_but_notify_primary_only() -> None:
    cl = product_profile("CL")
    ub = product_profile("UB")
    assert cl is not None and ub is not None

    assert _should_evaluate_target(cl, within_alert_session=False, rth_only_enabled=True) is True
    assert _experimental_notification_eligible(
        cl, horizon_minutes=5, within_alert_session=True
    ) is True
    assert _experimental_notification_eligible(
        cl, horizon_minutes=10, within_alert_session=True
    ) is False
    assert _experimental_notification_eligible(
        ub, horizon_minutes=10, within_alert_session=True
    ) is True
    assert _experimental_notification_eligible(
        ub, horizon_minutes=10, within_alert_session=False
    ) is False


def test_equity_alert_session_uses_delayed_open_and_early_close_buffer() -> None:
    # Regular day: 09:35 inclusive, 15:55 exclusive.
    assert not _is_equity_alert_session(
        datetime(2026, 8, 28, 13, 34, tzinfo=timezone.utc)
    )
    assert _is_equity_alert_session(
        datetime(2026, 8, 28, 13, 35, tzinfo=timezone.utc)
    )
    assert not _is_equity_alert_session(
        datetime(2026, 8, 28, 19, 55, tzinfo=timezone.utc)
    )
    # Friday after Thanksgiving: 12:55 ET exclusive.
    assert _is_equity_alert_session(
        datetime(2026, 11, 27, 17, 54, tzinfo=timezone.utc)
    )
    assert not _is_equity_alert_session(
        datetime(2026, 11, 27, 17, 55, tzinfo=timezone.utc)
    )
    # Operator override covers one-off exchange calendar changes.
    assert not _is_equity_alert_session(
        datetime(2026, 8, 28, 17, 0, tzinfo=timezone.utc),
        extra_early_close_dates={"2026-08-28"},
    )


def test_equity_alert_session_uses_exchange_holidays_not_shifted_half_days() -> None:
    # July 3, 2026 is the observed full-day Independence Day closure. NYSE
    # does not shift a 13:00 close to July 2; July 2 remains a regular session.
    assert _is_equity_alert_session(
        datetime(2026, 7, 2, 17, 0, tzinfo=timezone.utc)
    )
    assert not _is_equity_alert_session(
        datetime(2026, 7, 3, 14, 0, tzinfo=timezone.utc)
    )

    # In 2028 July 3 itself is a trading Monday and closes at 13:00 ET.
    assert _is_equity_alert_session(
        datetime(2028, 7, 3, 16, 54, tzinfo=timezone.utc)
    )
    assert not _is_equity_alert_session(
        datetime(2028, 7, 3, 16, 55, tzinfo=timezone.utc)
    )


def test_equity_alert_session_closes_on_observed_christmas_holiday() -> None:
    # Christmas 2027 falls on Saturday, so Friday Dec. 24 is a full closure,
    # not a half-day session.
    assert not _is_equity_alert_session(
        datetime(2027, 12, 24, 15, 0, tzinfo=timezone.utc)
    )


def test_mega_equity_collection_and_posting_defaults_fail_closed(monkeypatch) -> None:
    for name in (
        "MEGA_EQUITIES_ENABLED",
        "MEGA_EQUITY_POST_MARKETS",
        "MEGA_EQUITY_SMOKE_VALIDATED_MARKETS",
        "EQUITY_BREADTH_PROXY_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()
    assert settings.mega_equities_enabled is False
    assert settings.mega_equity_post_markets == ()
    assert settings.mega_equity_smoke_validated_markets == ()
    assert settings.equity_breadth_proxy_secret == ""


def test_equity_breadth_proxy_secret_is_trimmed_and_wired_to_poller(monkeypatch) -> None:
    monkeypatch.setenv("EQUITY_BREADTH_PROXY_SECRET", "  shared-secret  ")
    settings = replace(
        Settings.from_env(),
        enabled_target_markets=("AAPL",),
        mega_equities_enabled=True,
        mega_equity_markets=("AAPL",),
        live_cluster_models_file=None,
        side_guard_enabled=False,
    )

    assert settings.equity_breadth_proxy_secret == "shared-secret"
    service = LiveSignalService(settings)
    assert service._equity_breadth_poller is not None
    assert service._equity_breadth_poller._proxy_secret == "shared-secret"


def test_producer_never_routes_equities_to_legacy_or_experimental_webhooks(
    monkeypatch,
) -> None:
    for name in (
        "MEGA_EQUITIES_ENABLED",
        "MEGA_EQUITY_POST_MARKETS",
        "MEGA_EQUITY_SMOKE_VALIDATED_MARKETS",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = replace(
        Settings.from_env(),
        enabled_target_markets=("AAPL",),
        mega_equities_enabled=True,
        mega_equity_markets=("AAPL",),
        mega_equity_post_markets=("AAPL",),
        mega_equity_smoke_validated_markets=("AAPL",),
        discord_webhook_enabled=True,
        discord_webhook_url="https://example.invalid/global",
        experimental_cl_posting_enabled=False,
        experimental_ub_posting_enabled=False,
        live_cluster_models_file=None,
        side_guard_enabled=False,
    )

    service = LiveSignalService(settings)
    assert service._target_markets == ("AAPL",)
    assert service._discord_notifier is not None
    assert "AAPL" not in service._experimental_notifiers

    now = datetime(2026, 8, 28, 14, 30, tzinfo=timezone.utc)
    feature_row = {
        "timestamp_utc": now.isoformat(timespec="milliseconds"),
        "market": "AAPL",
        "ticker": "AAPL",
        "contract_id": "AAPL-NQTV",
        "ticker_identity_ok": True,
        "asset_class": "equity",
        "status": "ok",
        "target_age_s": 0.2,
        "target_stale": False,
        "target_spot": 225.0,
        "target_spread": 0.02,
        "input_mode": "l2_ofi",
        "input_provenance": {"source": "sierra_l2_ofi", "window_seconds": 15},
        "input_value_15s": 0.4,
        "target_ofi_norm_15s": 0.4,
        "target_trade_imbalance_norm_15s": None,
        "rule_score": 0.75,
        "peer_confirmation": {
            "source": "theta_equity_breadth_v1",
            "schema_version": "equity_breadth_snapshot_v1",
            "snapshot_health": True,
            "snapshot_age_s": 0.2,
            "ready_groups": 2,
            "aligned_group_count": 2,
            "required_aligned": 2,
            "qqq_strong_opposition": False,
            "confirmed": True,
        },
    }

    class _FeatureStore:
        def build_target_row(self, *args, **kwargs):
            return dict(feature_row)

    class _Recorder:
        def __init__(self) -> None:
            self.calls = 0

        async def send_if_needed(self, payload):
            self.calls += 1
            return True

    written: list[dict] = []

    async def _capture_jsonl(path, payload) -> None:
        written.append(dict(payload))

    recorder = _Recorder()
    service._feature_store = _FeatureStore()
    service._discord_notifier = recorder
    service._append_jsonl = _capture_jsonl
    asyncio.run(service._emit_signals_for_target("AAPL", now))
    assert recorder.calls == 0

    signals = [row for row in written if "horizon_minutes" in row]
    assert {row["horizon_minutes"] for row in signals} == {5, 15}
    primary = next(row for row in signals if row["horizon_minutes"] == 5)
    research = next(row for row in signals if row["horizon_minutes"] == 15)
    assert primary["candidate_eligible"] is True
    assert primary["signal_kind"] == "rules_first"
    assert primary["asset_class"] == "equity"
    assert primary["input_provenance"]["source"] == "sierra_l2_ofi"
    assert primary["p_up"] is primary["p_hit"] is primary["confidence"] is None
    assert research["candidate_eligible"] is False
    assert research["reason"] == "research_only_horizon"
