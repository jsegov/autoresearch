# ES/NQ Cross-Market Signal (Standalone)

Standalone 5-10 minute ES/NQ direction signal built from Sierra OFI streams.
This project is intentionally separate from any existing trading bot codebase.

## What It Includes

- New ACSIL exporter: `sierra_chart/CrossMarket_OFI_Export.cpp`
- Standalone Python runtime under `src/esnq_signal`
- Separate feature and signal JSONL logs
- Label builder script for 5m/10m outcomes
- Bootstrap importer from existing `trading-events-v2` labeled candidate logs
- Cross-market correlation research script
- Cross-market lead-lag correlation script (0-300s lag scan)
- Lightweight meta-model training script (`p_up`, `p_hit`)
- Threshold calibration script with win-rate and drawdown constraints
- Historical Sierra OFI replay script for fast backfill
- Large-trade replay ingestion (`sierra_large_trades.jsonl`) for signed flow features
- Research note mapping statistical literature to available Sierra markets (`docs/CROSS_MARKET_EVIDENCE.md`)
- Basic unit tests

## Sierra Setup

1. Compile and attach `CrossMarket_OFI_Export.cpp` in Sierra.
2. Use dedicated 1-second intraday charts.
3. Attach to: `ES`, `NQ`, `YM`, `RTY`, `E6`, `ZN`, `ZB`, `CL`, `GC`.
4. Keep market depth enabled.
5. Set exporter TCP port to `5562` for cross-market stream.
6. Put the exporter on dedicated **1-second** charts for every market above.

Optional:
- Run a separate core ES/NQ OFI stream on `5561` if you want a dedicated target stream.
- The Python service supports both `5561` and `5562`.

## Run

```powershell
cd C:\Users\14342\autoresearch-win-rtx\projects\es-nq-cross-market-signal
python -m pip install -e .[dev,research]
python scripts\run_live_signal.py
```

Optional Discord webhook alerts:

- Set `DISCORD_WEBHOOK_ENABLED=true`
- Set `DISCORD_WEBHOOK_URL=<your webhook>`
- Optional filters:
  - `DISCORD_WEBHOOK_STATUSES=post,high_priority`
  - `DISCORD_WEBHOOK_MIN_CONFIDENCE=0.0`
  - `DISCORD_WEBHOOK_COOLDOWN_SECONDS=45`

Optional rolling side guard (auto-throttle weak long/short side by market+horizon):

- `SIDE_GUARD_ENABLED=true`
- `SIDE_GUARD_LOOKBACK_TRADES=80`
- `SIDE_GUARD_MIN_SAMPLES=25`
- `SIDE_GUARD_MIN_WIN_RATE=0.53`
- `SIDE_GUARD_APPLY_STATUSES=post,high_priority`

## Output Files

- `data/cross_market_features.jsonl`
- `data/es_nq_direction_signals.jsonl`
- `data/labeled_signals_5m_10m.jsonl` (after label build)
- `data/cross_market_correlation_report.json` and `.md`
- `data/cross_market_lead_lag_report.json` and `.md`
- `data/signal_models.json` and `.md`
- `data/signal_calibration.json` and `.md`

## Build Labels

```powershell
python scripts\build_labels.py
```

Default label build now applies quality guards:
- excludes `blocked` signal rows
- requires future timestamp proximity (`--max-future-delay-seconds`, default `120`)
- drops extreme forward-return outliers (`--max-abs-forward-return`, default `0.02`)

## Bootstrap Historical Data

Use your existing labeled logs to seed standalone feature/signal history immediately:

```powershell
python scripts\bootstrap_from_trading_events_v2.py --reset --include-blocked
```

## Analyze Correlations

```powershell
python scripts\analyze_cross_market_correlations.py
```

This reports ES/NQ 5m and 10m correlation strength for:
- OFI stack
- cross-index (ES/NQ/YM/RTY)
- USD proxy (E6)
- bonds (ZN/ZB)
- oil (CL)
- gold (GC)
- volatility proxy (VX if available)

## Analyze Lead-Lag Correlations

```powershell
python scripts\analyze_cross_market_lead_lag.py
```

This scans lag grids (default `0,5,15,30,60,120,180,300` seconds) for ES/NQ 5m and 10m returns to identify which cross-market predictors lead target direction.

## Calibrate Live Thresholds

```powershell
python scripts\calibrate_signal_thresholds.py --min-trades 40 --min-win-rate 0.65 --max-drawdown 0.02
```

Set `CALIBRATION_FILE=data/signal_calibration.json` in `.env` so live runtime uses calibrated per-market/per-horizon thresholds.
Calibration now also searches direction-gap and raw-edge gates.
Calibration also searches optional session (`RTH_OPEN`, `RTH_MID`, `RTH_CLOSE`, `OVERNIGHT`) and direction (`long`/`short`) filters.
When calibration `summary` marks a market/horizon as not `live_ok`, the live service keeps that horizon in `watch` (research-only) mode.

## Train Meta-Models

```powershell
python scripts\train_meta_models.py
```

Set `MODEL_FILE=data/signal_models.json` in `.env` to enable model-based `p_up` and `p_hit` (with automatic heuristic fallback when models are missing).

## Run Full Research Pass

```powershell
python scripts\run_research_pipeline.py
```

## Backfill From Sierra OFI JSONL

Use this when you already have Sierra OFI history and want faster calibration/model updates:

```powershell
python scripts\replay_sierra_ofi_jsonl.py --source-jsonl C:\SierraChart\Data\ES_NQ_OFI_1s.jsonl --reset --max-rows 200000
```

To replay multiple files in timestamp order (recommended when you have separate cross-market exports):

```powershell
python scripts\replay_sierra_ofi_jsonl.py --source-jsonl C:\SierraChart\Data\ES_NQ_OFI_1s.jsonl --source-jsonl C:\SierraChart\Data\CrossMarket_OFI_1s.jsonl --reset
```

To include large-trade flow explicitly:

```powershell
python scripts\replay_sierra_ofi_jsonl.py --source-jsonl C:\SierraChart\Data\ES_NQ_OFI_1s.jsonl --source-jsonl C:\SierraChart\Data\CrossMarket_OFI_1s.jsonl --large-trade-jsonl C:\SierraChart\Data\sierra_large_trades.jsonl --reset
```
