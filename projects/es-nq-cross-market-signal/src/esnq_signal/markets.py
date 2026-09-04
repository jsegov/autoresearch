from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class ProductProfile:
    """Static, additive runtime contract for one target future.

    CL/UB are deliberately rules-first. Their peer votes require the
    exporter's scale-free ofi_norm field and never fall back to
    contract-sized raw OFI.
    """

    profile_id: str
    market: str
    primary_peers: tuple[str, ...]
    context_markets: tuple[str, ...]
    horizons_minutes: tuple[int, ...]
    min_aligned_peers: int
    rules_first: bool = False
    roll_warmup_seconds: int = 0
    block_strong_opposing_primary: bool = False
    primary_alert_horizon_minutes: int | None = None
    asset_class: str = "futures"
    benchmark_market: str | None = None
    sector_market: str | None = None
    issuer_id: str | None = None


@dataclass(frozen=True)
class MegaEquitySpec:
    """Canonical producer metadata for one native mega-equity target."""

    symbol: str
    issuer_id: str
    sector_etf: str
    benchmark: str = "QQQ"


MEGA_EQUITY_SPECS: dict[str, MegaEquitySpec] = {
    "AAPL": MegaEquitySpec("AAPL", "APPLE", "XLK"),
    "GOOG": MegaEquitySpec("GOOG", "ALPHABET", "XLC"),
    "GOOGL": MegaEquitySpec("GOOGL", "ALPHABET", "XLC"),
    "MSFT": MegaEquitySpec("MSFT", "MICROSOFT", "XLK"),
    "META": MegaEquitySpec("META", "META", "XLC"),
    "TSLA": MegaEquitySpec("TSLA", "TESLA", "XLY"),
    "SPCX": MegaEquitySpec("SPCX", "SPACEX", "ITA"),
    "NVDA": MegaEquitySpec("NVDA", "NVIDIA", "SMH"),
    "AMZN": MegaEquitySpec("AMZN", "AMAZON", "XLY"),
}
MEGA_EQUITY_SYMBOLS = tuple(MEGA_EQUITY_SPECS)
MEGA_EQUITY_SECTOR_ETFS = tuple(
    dict.fromkeys(spec.sector_etf for spec in MEGA_EQUITY_SPECS.values())
)
MEGA_EQUITY_COLLECTION_ROOTS = tuple(
    dict.fromkeys((*MEGA_EQUITY_SYMBOLS, "QQQ", *MEGA_EQUITY_SECTOR_ETFS))
)


PRODUCT_PROFILES: dict[str, ProductProfile] = {
    "ES": ProductProfile(
        profile_id="equity_index_es_v1",
        market="ES",
        primary_peers=("NQ", "YM", "RTY"),
        context_markets=("E6", "CL", "ZN", "ZB", "GC", "VX"),
        horizons_minutes=(5, 10),
        min_aligned_peers=1,
    ),
    "NQ": ProductProfile(
        profile_id="equity_index_nq_v1",
        market="NQ",
        primary_peers=("ES", "YM", "RTY"),
        context_markets=("E6", "CL", "ZN", "ZB", "GC", "VX"),
        horizons_minutes=(5, 10),
        min_aligned_peers=1,
    ),
    "CL": ProductProfile(
        profile_id="energy_cl_rules_v1",
        market="CL",
        primary_peers=("RB", "HO"),
        context_markets=("NG", "E6", "ES", "GC"),
        horizons_minutes=(5, 10),
        min_aligned_peers=1,
        rules_first=True,
        roll_warmup_seconds=600,
        block_strong_opposing_primary=True,
        primary_alert_horizon_minutes=5,
    ),
    "UB": ProductProfile(
        profile_id="rates_ub_rules_v1",
        market="UB",
        primary_peers=("ZB", "TN", "ZN"),
        context_markets=("ZF", "ES", "GC"),
        horizons_minutes=(10, 30),
        min_aligned_peers=2,
        rules_first=True,
        roll_warmup_seconds=600,
        block_strong_opposing_primary=True,
        primary_alert_horizon_minutes=10,
    ),
    **{
        symbol: ProductProfile(
            profile_id=f"mega_equity_{symbol.lower()}_rules_v1",
            market=symbol,
            primary_peers=(spec.benchmark, spec.sector_etf),
            context_markets=tuple(
                peer
                for peer in MEGA_EQUITY_SYMBOLS
                if MEGA_EQUITY_SPECS[peer].issuer_id != spec.issuer_id
            ),
            horizons_minutes=(5, 15),
            min_aligned_peers=2,
            rules_first=True,
            block_strong_opposing_primary=True,
            primary_alert_horizon_minutes=5,
            asset_class="equity",
            benchmark_market=spec.benchmark,
            sector_market=spec.sector_etf,
            issuer_id=spec.issuer_id,
        )
        for symbol, spec in MEGA_EQUITY_SPECS.items()
    },
}

TARGET_MARKETS = set(PRODUCT_PROFILES)
EQUITY_TARGET_MARKETS = set(MEGA_EQUITY_SYMBOLS)
EQUITY_PEER_MARKETS = set(MEGA_EQUITY_COLLECTION_ROOTS) - EQUITY_TARGET_MARKETS
CROSS_INDEX_MARKETS = {"YM", "RTY"}
MACRO_MARKETS = {"E6", "ZN", "ZB", "CL", "GC", "VX", "RB", "HO", "NG", "UB", "TN", "ZF"}
KNOWN_MARKETS = TARGET_MARKETS | EQUITY_PEER_MARKETS | CROSS_INDEX_MARKETS | MACRO_MARKETS

_EQUITY_FEED_QUALIFIERS = {
    "AMEX",
    "ARCA",
    "BATS",
    "DLY",
    "EQ",
    "EQUITY",
    "F",
    "MBO",
    "NASD",
    "NASDAQ",
    "NMS",
    "NQTV",
    "NYSE",
    "Q",
    "SCID",
    "SMART",
    "STK",
    "US",
}


def normalize_equity_symbol(raw: Any) -> str:
    """Normalize only an exact equity root plus explicit feed qualifiers.

    This intentionally rejects substring matches such as ``METADATA`` and
    ``GOOGLX``.  Sierra examples such as ``AAPL-NQTV``, ``NASDAQ:AAPL`` and
    ``AAPL_STK_SMART`` remain accepted.
    """

    text = str(raw or "").strip().upper().lstrip("$")
    if not text:
        return ""
    if text in MEGA_EQUITY_COLLECTION_ROOTS:
        return text
    tokens = tuple(token for token in re.split(r"[.:/_-]+", text) if token)
    roots = [token for token in tokens if token in MEGA_EQUITY_COLLECTION_ROOTS]
    if len(roots) != 1:
        return ""
    root = roots[0]
    if all(token == root or token in _EQUITY_FEED_QUALIFIERS for token in tokens):
        return root
    return ""


def normalize_sierra_symbol(raw: Any) -> str:
    text = str(raw or "").strip().upper()
    if not text:
        return ""
    normalized = text.replace("/", ".")

    equity = normalize_equity_symbol(normalized)
    if equity:
        return equity

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
    # Longer/specific rates roots must precede the generic Treasury roots.
    if "UB" in normalized or "ULTRA BOND" in normalized:
        return "UB"
    if "TN" in normalized:
        return "TN"
    if "ZN" in normalized or "TYAU" in normalized:
        return "ZN"
    if "ZB" in normalized or "USAU" in normalized:
        return "ZB"
    if "ZF" in normalized or "FVAU" in normalized:
        return "ZF"
    if "RB" in normalized:
        return "RB"
    if "HO" in normalized:
        return "HO"
    if "NG" in normalized:
        return "NG"
    if "CL" in normalized:
        return "CL"
    if "GC" in normalized:
        return "GC"
    if "VXM" in normalized or " VX" in normalized or normalized.startswith("VX"):
        return "VX"
    return ""


def product_profile(market: str) -> ProductProfile | None:
    return PRODUCT_PROFILES.get(str(market or "").upper())
