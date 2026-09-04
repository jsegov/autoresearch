# Cross-Market Alert Optimization Suite

- generated_at_utc: `2026-06-14T18:49:07+00:00`
- labeled_input: `data\labeled_alert_signals_5m_10m_2026-06-14.jsonl`
- feature_log: `data\cross_market_features.jsonl`
- et_window: `09:30-16:00 ET`
- input_rows: `30809`
- analyzed_records: `16162`

## ES_5m

- n=3477 hit_rate=0.579 avg_ret=0.000187 avg_mfe=8.575424216278401 avg_mae=8.104508196721312 ratio=8.79950080410511

Top K-means clusters:

- cluster=4 n=233 hit=0.609 lift=+0.031 ratio=14.802415646666859
- cluster=0 n=814 hit=0.568 lift=-0.011 ratio=8.968975188300933
- cluster=3 n=933 hit=0.592 lift=+0.013 ratio=8.835065611198086
- cluster=2 n=871 hit=0.559 lift=-0.020 ratio=7.9494057420788495
- cluster=1 n=618 hit=0.591 lift=+0.012 ratio=7.557545994679189

Top RF features:

- target_spot: +0.00746
- direction_gap: +0.00564
- cross_index_max_age_s: +0.00534
- p_up: +0.00354
- target_ofi_300s_z: +0.00156
- target_ofi_5s: +0.00099
- target_age_s: +0.00066
- macro_lead_score: +0.00063

## ES_10m

- n=665 hit_rate=0.552 avg_ret=0.000271 avg_mfe=12.102067669172932 avg_mae=10.826503759398497 ratio=7.94803001259155

Top K-means clusters:

- cluster=3 n=43 hit=0.419 lift=-0.133 ratio=10.52763267147764
- cluster=4 n=181 hit=0.613 lift=+0.061 ratio=10.112431771964316
- cluster=2 n=143 hit=0.566 lift=+0.015 ratio=8.373529645307228
- cluster=5 n=56 hit=0.536 lift=-0.016 ratio=7.360233916673571
- cluster=1 n=227 hit=0.537 lift=-0.014 ratio=6.103889022269372

Top RF features:

- target_ofi_60s: +0.00768
- raw_edge: +0.00580
- macro_ready: +0.00188
- target_spot: +0.00172
- target_ofi_60s_z: +0.00110
- cross_index_confirmation: +0.00016
- cat:reason_strong_multilayer_alignment: +0.00016
- target_large_trade_signed_60s: +0.00000

## NQ_5m

- n=2339 hit_rate=0.513 avg_ret=0.000043 avg_mfe=25.23380718255665 avg_mae=22.52089568191535 ratio=14.638954530244252

Top K-means clusters:

- cluster=3 n=943 hit=0.532 lift=+0.020 ratio=16.569459134303656
- cluster=2 n=185 hit=0.492 lift=-0.021 ratio=15.540912827453711
- cluster=0 n=323 hit=0.480 lift=-0.033 ratio=15.458297137836997
- cluster=4 n=375 hit=0.549 lift=+0.037 ratio=15.0369356791892
- cluster=1 n=499 hit=0.477 lift=-0.036 ratio=10.17360432577639

Top RF features:

- target_spot: +0.02057
- direction_gap: +0.00223
- cross_index_lead_balance: +0.00174
- target_ofi_300s_z: +0.00165
- macro_lead_score: +0.00143
- cross_index_balance: +0.00129
- cat:session_bucket_RTH_OPEN: +0.00125
- target_ofi_300s: +0.00120

## NQ_10m

- n=9681 hit_rate=0.505 avg_ret=0.000072 avg_mfe=32.81209327548807 avg_mae=27.47349189133354 ratio=14.868956105633739

Top K-means clusters:

- cluster=0 n=274 hit=0.314 lift=-0.191 ratio=18.048695058398327
- cluster=4 n=1574 hit=0.536 lift=+0.030 ratio=17.27832010817688
- cluster=3 n=2170 hit=0.525 lift=+0.020 ratio=16.130545528400003
- cluster=2 n=1912 hit=0.503 lift=-0.003 ratio=15.832495088661037
- cluster=1 n=2309 hit=0.498 lift=-0.007 ratio=13.561996540697388

Top RF features:

- target_spot: +0.04712
- cat:session_bucket_RTH_CLOSE: +0.00839
- cat:session_bucket_RTH_OPEN: +0.00722
- target_ofi_60s_z: +0.00146
- cross_index_score_avg: +0.00071
- macro_ready: +0.00048
- target_ofi_5s: +0.00040
- cat:status_post: +0.00026
