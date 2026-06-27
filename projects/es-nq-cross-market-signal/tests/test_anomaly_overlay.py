from __future__ import annotations

from esnq_signal.anomaly_overlay import (
    evaluate_cross_market_anomaly_overlay,
    nearest_gex_key_name,
    prop_alpha_flags_from_context,
)


def test_cross_market_overlay_requires_5m_post_ifvg_and_zero_gamma() -> None:
    signal = {
        "market": "NQ",
        "horizon_minutes": 5,
        "status": "post",
        "direction": "short",
        "p_hit": 0.71,
        "confidence": 0.68,
        "timestamp_utc": "2026-06-14T14:31:00Z",
    }
    gexbot_context = {
        "key_levels": {"spot": 21000.0, "zero_gamma": 20995.0, "major_call": 21080.0},
        "sierra_signal_bus": {
            "active_signals": [
                {
                    "signal_name": "IFVG_BEAR_BREAK",
                    "features": {"mth_fvg_bearish_break": True},
                }
            ],
            "recent_events": [],
        },
    }
    overlay = evaluate_cross_market_anomaly_overlay(
        signal_payload=signal,
        gexbot_context=gexbot_context,
    )
    assert overlay is not None
    assert overlay["source"] == "cross_market_anomaly_overlay"
    assert overlay["nearest_gex_key"] == "zero_gamma"


def test_cross_market_overlay_rejects_non_5m() -> None:
    signal = {"market": "ES", "horizon_minutes": 10, "status": "post"}
    assert evaluate_cross_market_anomaly_overlay(signal_payload=signal, gexbot_context={}) is None


def test_nearest_gex_key_name_prefers_closest_level() -> None:
    context = {
        "key_levels": {
            "spot": 21000.0,
            "zero_gamma": 20998.0,
            "major_call": 21080.0,
            "major_put": 20850.0,
        }
    }
    assert nearest_gex_key_name(context) == "zero_gamma"


def test_prop_alpha_ifvg_monthly_flag() -> None:
    context = {
        "sierra_signal_bus": {
            "active_signals": [
                {
                    "signal_name": "IFVG_BEAR_BREAK",
                    "features": {"timeframe": "monthly"},
                }
            ],
            "recent_events": [],
        }
    }
    flags = prop_alpha_flags_from_context(context)
    assert "pa__ifvg__mth_fvg_bearish_break" in flags
