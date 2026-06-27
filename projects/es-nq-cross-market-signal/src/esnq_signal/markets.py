from __future__ import annotations

from typing import Any

TARGET_MARKETS = {"ES", "NQ"}
CROSS_INDEX_MARKETS = {"YM", "RTY"}
MACRO_MARKETS = {"E6", "ZN", "ZB", "CL", "GC", "VX"}
KNOWN_MARKETS = TARGET_MARKETS | CROSS_INDEX_MARKETS | MACRO_MARKETS


def normalize_sierra_symbol(raw: Any) -> str:
    text = str(raw or "").strip().upper()
    if not text:
        return ""
    normalized = text.replace("/", ".")

    if "ENQ" in normalized or "NQ" in normalized:
        return "NQ"
    if normalized.startswith("F.US.EP") or ".EP" in normalized or " ES" in normalized:
        return "ES"
    if "ES" in normalized and "MES" not in normalized:
        return "ES"
    if "RTY" in normalized and "M2K" not in normalized:
        return "RTY"
    if "YM" in normalized and "MYM" not in normalized:
        return "YM"
    if "E6" in normalized or "6E" in normalized or "EURUSD" in normalized:
        return "E6"
    if "ZN" in normalized or "TYAU" in normalized:
        return "ZN"
    if "ZB" in normalized or "USAU" in normalized:
        return "ZB"
    if "CL" in normalized:
        return "CL"
    if "GC" in normalized:
        return "GC"
    if "VXM" in normalized or " VX" in normalized or normalized.startswith("VX"):
        return "VX"
    return ""
