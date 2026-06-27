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
