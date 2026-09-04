# Cross-Market Evidence Map (ES/NQ, 5-10m)

This project uses cross-market signals as probabilistic context, not deterministic direction rules.

## Research-backed relationships used in this stack

| Predictor family | Markets in this stack | Why it is included |
|---|---|---|
| Cross-index OFI lead-lag | `ES`, `NQ`, `YM`, `RTY` | Cross-impact literature finds lagged order-flow information can improve short-horizon return forecasts in related contracts. |
| Volatility risk regime | `VX` (or `VXM`) | Equity index moves often condition on volatility regime and liquidity stress; vol futures OFI is used as a risk-off vote layer. |
| USD proxy | `E6` | Dollar moves can influence short-horizon equity risk appetite; `E6` (EURUSD futures) is used as a tradable USD proxy when DXY is unavailable. |
| Rates / duration | `ZN`, `ZB` | Treasury flow and risk sentiment often co-move with equity index futures at intraday horizons; bond OFI is treated as a risk-off control. |
| Commodities risk proxies | `CL`, `GC` | Oil and gold flows are used as complementary macro regime signals (risk-on/risk-off context), not standalone entry triggers. |

## Primary references

- Cont, Kukanov, Stoikov (2014), *The Price Impact of Order Book Events*: [arXiv:1011.6402](https://arxiv.org/abs/1011.6402)
- Cont, Cucuringu, Zhang (2023), *Cross-Impact of Order Flow Imbalance in Equity Markets*: [arXiv:2112.13213](https://arxiv.org/pdf/2112.13213)
- Takahashi (2025), *Returns and Order Flow Imbalances* (ES E-mini): [arXiv:2508.06788](https://arxiv.org/abs/2508.06788)
- Federal Reserve FEDS Note (2025), *Order Flow Imbalances and Amplification of Price Movements* (Treasuries): [federalreserve.gov](https://www.federalreserve.gov/econres/notes/feds-notes/order-flow-imbalances-and-amplification-of-price-movements-evidence-from-u-s-treasury-markets-20251103.html)

## How this maps to the code

- Live feature engine: `src/esnq_signal/features.py`
  - current and lagged cross-index context (`cross_index_values`, `cross_index_lag30_values`)
  - current and lagged macro context (`macro_ofi_values`, `macro_lag30_ofi_values`)
- Signal engine: `src/esnq_signal/signal.py`
  - multi-layer fusion of target OFI, cross-index confirmation, and macro regime/lead scores
- Research scripts:
  - `scripts/analyze_cross_market_correlations.py` (contemporaneous correlations)
  - `scripts/analyze_cross_market_lead_lag.py` (lag scan over 0-300 seconds)

## Practical note

All relationships are re-estimated from your own logs before live thresholds are promoted. Keep using calibration gates (`min_trades`, `min_win_rate`, `max_drawdown`) to prevent overfitting.

## Fidelity fix log

### 2026-07-01 — OFI computation corrected to CKS/CCZ definitions
The ACSIL exporter (`sierra_chart/CrossMarket_OFI_Export.cpp`) previously computed
`ofi = delta(aggregated bid depth) - delta(aggregated ask depth)`. That is a depth-delta
proxy: it equals Cont/Kukanov/Stoikov (2014, sec 2.2 eq 2-3) OFI only when best bid/ask
prices are unchanged between samples, and can take the WRONG SIGN on price transitions
(e.g. ask lifts bullishly while deeper liquidity scrolls into the window).

Changes:
- Exporter now also emits `ofi_cks` (best-level snapshot OFI per CKS 2014),
  `ofi_deep` (per-level OFI summed over tracked levels, per Cont/Cucuringu/Zhang 2023
  sec 2.1), and `ofi_norm` (`ofi_deep` / ~50-sample EMA of avg book depth, per CCZ
  normalization; scale-free across the ES/NQ/YM/RTY/VX/E6/ZN/ZB/CL/GC universe).
  Legacy `ofi` field kept for continuity. REQUIRES Sierra Chart DLL rebuild.
- `src/esnq_signal/ofi_stream.py`: `OfiSample` carries the new fields (None on legacy
  payloads); `effective_ofi()` prefers `ofi_cks`, falls back to legacy.
- `src/esnq_signal/features.py`: feature windows/z-scores now ingest `effective_ofi()`.
- Tests: `test_effective_ofi_prefers_cks_falls_back_legacy`,
  `test_feature_store_ingests_cks_ofi` (43 passing).

IMPORTANT: after rebuilding the DLL, re-run calibration (per "Practical note" above) —
OFI magnitudes change on price-transition samples, so promoted thresholds
(`min_trades`, `min_win_rate`, `max_drawdown` gates) must be re-estimated. Prefer
`ofi_norm` for any new absolute thresholds (comparable across markets/regimes).
