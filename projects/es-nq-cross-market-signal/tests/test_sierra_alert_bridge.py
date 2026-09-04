from __future__ import annotations

import asyncio
from pathlib import Path

from esnq_signal.discord_webhook import DiscordWebhookNotifier
from esnq_signal.sierra_alert_bridge import (
    emit_cross_market_pulse,
    pulse_from_cross_market_payload,
    read_alert_pulses,
)


def _payload(*, status: str = "post", confidence: float = 0.9, market: str = "ES") -> dict[str, object]:
    return {
        "market": market,
        "timestamp_utc": "2026-08-17T19:31:00.000+00:00",
        "horizon_minutes": 5,
        "direction": "long",
        "confidence": confidence,
        "status": status,
        "reason": "trade_filter_pass",
        "target_spot": 6501.25,
    }


def test_cross_market_payload_maps_to_pulse() -> None:
    pulse = pulse_from_cross_market_payload(_payload())
    assert pulse is not None
    assert pulse.market == "ES"
    assert pulse.side == 1
    assert pulse.source == "cross_market"
    assert pulse.stop_points == 0.0
    assert pulse.alert_id == "cross_market:ES:5:long:2026-08-17T19:31:00.000+00:00"


def test_emit_writes_tsv_and_preserves_other_market(tmp_path: Path) -> None:
    path = tmp_path / "external_alert_signals.tsv"
    assert emit_cross_market_pulse(path, _payload(market="ES")) is True
    assert emit_cross_market_pulse(path, {**_payload(market="NQ"), "direction": "short"}) is True
    rows = read_alert_pulses(path)
    assert rows["ES"].side == 1
    assert rows["NQ"].side == -1
    assert rows["ES"].source == "cross_market"


def test_bridge_accepts_experimental_cl_and_ub_targets(tmp_path: Path) -> None:
    path = tmp_path / "external_alert_signals.tsv"
    assert emit_cross_market_pulse(path, _payload(market="CL")) is True
    assert emit_cross_market_pulse(path, {**_payload(market="UB"), "direction": "short"}) is True
    rows = read_alert_pulses(path)
    assert rows["CL"].side == 1
    assert rows["UB"].side == -1


def test_discord_true_writes_tsv_discord_false_does_not(tmp_path: Path) -> None:
    path = tmp_path / "external_alert_signals.tsv"
    notifier = DiscordWebhookNotifier(
        webhook_url="https://example.test/webhook",
        enabled_statuses=("post", "high_priority"),
        min_confidence=0.6,
        cooldown_seconds=0.0,
        timeout_seconds=1.0,
    )
    notifier._post_json = lambda webhook_url, payload: True  # type: ignore[method-assign]

    watch = asyncio.run(notifier.send_if_needed(_payload(status="watch", confidence=0.9)))
    if watch:
        emit_cross_market_pulse(path, _payload(status="watch"))
    assert watch is False
    assert not path.exists()

    posted = asyncio.run(notifier.send_if_needed(_payload(status="post", confidence=0.9)))
    if posted:
        emit_cross_market_pulse(path, _payload(status="post"))
    assert posted is True
    rows = read_alert_pulses(path)
    assert rows["ES"].alert_id.startswith("cross_market:ES:5:long:")
