from __future__ import annotations

import asyncio

from esnq_signal.discord_webhook import DiscordWebhookNotifier


def _payload(*, status: str = "post", confidence: float = 0.7) -> dict[str, object]:
    return {
        "market": "ES",
        "timestamp_utc": "2026-05-29T19:24:57.062+00:00",
        "horizon_minutes": 5,
        "direction": "long",
        "p_up": 0.63,
        "p_hit": 0.71,
        "confidence": confidence,
        "status": status,
        "reason": "trade_filter_pass",
        "risk_flags": [],
        "layers": {
            "direction_core_raw": 2.4,
            "model": {"raw_edge": 2.4, "direction_gap": 0.13},
            "large_trades": {"signed_60s": 40.0, "signed_300s": 120.0, "count_300s": 3},
        },
        "target_spot": 7621.25,
        "target_spread": 0.5,
        "target_age_s": 0.35,
        "target_ofi_5s": 8.0,
        "target_ofi_15s": 12.0,
        "target_ofi_60s": 42.0,
        "target_ofi_300s": -16.0,
        "target_ofi_15s_z": 1.2,
        "cross_index_ready": 3,
        "cross_index_confirmation": 2,
        "cross_index_contradiction": 1,
        "cross_index_lead_balance": 1,
        "cross_index_values": {"NQ": 0.8, "RTY": -0.2, "YM": 0.4},
        "macro_ready": 4,
        "macro_regime_score": 2,
        "macro_lead_score": -1,
        "macro_votes": {"CL": 1, "ZN": -1, "ZB": 1, "GC": 1},
    }


def test_discord_notifier_filters_status_and_confidence() -> None:
    notifier = DiscordWebhookNotifier(
        webhook_url="https://example.test/webhook",
        enabled_statuses=("post", "high_priority"),
        min_confidence=0.6,
        cooldown_seconds=0.0,
        timeout_seconds=1.0,
    )
    calls: list[dict[str, object]] = []

    def fake_post(payload: dict[str, object]) -> bool:
        calls.append(payload)
        return True

    notifier._post_json = fake_post  # type: ignore[method-assign]

    sent_watch = asyncio.run(notifier.send_if_needed(_payload(status="watch", confidence=0.9)))
    sent_low_conf = asyncio.run(notifier.send_if_needed(_payload(status="post", confidence=0.2)))
    sent_ok = asyncio.run(notifier.send_if_needed(_payload(status="post", confidence=0.9)))

    assert sent_watch is False
    assert sent_low_conf is False
    assert sent_ok is True
    assert len(calls) == 1
    content = str(calls[0]["content"])
    assert content == "POST | ES 5m LONG | 7621.25"
    assert "Target OFI" not in content
    assert "Cross Index" not in content
    assert "Macro" not in content
    assert "Large Trades" not in content


def test_discord_format_message_high_priority_slim() -> None:
    content = DiscordWebhookNotifier._format_message(
        _payload(status="high_priority", confidence=0.9) | {"target_spot": 7521.62}
    )
    assert content == "HIGH_PRIORITY | ES 5m LONG | 7521.62"


def test_discord_notifier_applies_cooldown() -> None:
    notifier = DiscordWebhookNotifier(
        webhook_url="https://example.test/webhook",
        enabled_statuses=("post",),
        min_confidence=0.0,
        cooldown_seconds=999.0,
        timeout_seconds=1.0,
    )
    calls: list[dict[str, object]] = []

    def fake_post(payload: dict[str, object]) -> bool:
        calls.append(payload)
        return True

    notifier._post_json = fake_post  # type: ignore[method-assign]
    payload = _payload(status="post", confidence=0.9)
    first = asyncio.run(notifier.send_if_needed(payload))
    second = asyncio.run(notifier.send_if_needed(payload))
    assert first is True
    assert second is False
    assert len(calls) == 1
