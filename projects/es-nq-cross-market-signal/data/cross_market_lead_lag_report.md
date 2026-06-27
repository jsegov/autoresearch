# Cross-Market Lead-Lag Report

- generated_at_utc: `2026-05-29T19:33:32+00:00`
- feature_log: `data\cross_market_features.jsonl`
- lag_grid_seconds: `[0, 5, 15, 30, 60, 120, 180, 300]`

## ES_10m

- rows: `2102`

| Predictor | Best Lag (s) | N | Pearson r | t-stat |
|---|---:|---:|---:|---:|
| bonds_zn_ofi | 300 | 1848 | -0.3081 | -13.913 |
| bonds_zb_ofi | 300 | 1847 | 0.2598 | 11.555 |
| bonds_zn_ofi_lag30 | 180 | 1926 | -0.2357 | -10.639 |
| oil_cl_ofi | 300 | 1850 | -0.2211 | -9.746 |
| cross_rty | 300 | 1851 | 0.2163 | 9.526 |
| bonds_zb_ofi_lag30 | 180 | 1926 | 0.1996 | 8.935 |
| cross_rty_lag30 | 180 | 1926 | 0.1935 | 8.651 |
| target_ofi_300s_z | 30 | 1785 | -0.1890 | -8.127 |
| oil_cl_ofi_lag30 | 180 | 1926 | -0.1556 | -6.910 |
| cross_es_nq | 300 | 1851 | 0.1314 | 5.700 |
| macro_lead_score | 300 | 1851 | 0.1110 | 4.803 |
| gold_gc_ofi | 300 | 1849 | -0.0977 | -4.220 |
| cross_ym | 300 | 1851 | 0.0968 | 4.183 |
| macro_regime_score | 300 | 1851 | 0.0915 | 3.949 |
| target_ofi_60s_z | 0 | 1810 | -0.0730 | -3.112 |

## ES_5m

- rows: `2360`

| Predictor | Best Lag (s) | N | Pearson r | t-stat |
|---|---:|---:|---:|---:|
| target_ofi_300s_z | 300 | 1806 | -0.2904 | -12.888 |
| bonds_zn_ofi | 300 | 2106 | -0.2886 | -13.828 |
| bonds_zn_ofi_lag30 | 180 | 2184 | -0.2508 | -12.102 |
| bonds_zb_ofi | 300 | 2105 | 0.2421 | 11.444 |
| bonds_zb_ofi_lag30 | 180 | 2184 | 0.2127 | 10.168 |
| oil_cl_ofi | 300 | 2108 | -0.2114 | -9.927 |
| cross_rty_lag30 | 180 | 2184 | 0.2069 | 9.879 |
| cross_rty | 300 | 2109 | 0.2056 | 9.646 |
| oil_cl_ofi_lag30 | 180 | 2184 | -0.1625 | -7.692 |
| cross_es_nq | 180 | 2208 | 0.1294 | 6.129 |
| target_ofi_60s_z | 300 | 1806 | -0.1075 | -4.593 |
| cross_ym | 300 | 2109 | 0.0871 | 4.015 |
| macro_lead_score | 0 | 2360 | -0.0792 | -3.858 |
| macro_regime_score | 30 | 2334 | -0.0789 | -3.823 |
| cross_ym_lag30 | 120 | 2233 | 0.0785 | 3.720 |

## NQ_10m

- rows: `2103`

| Predictor | Best Lag (s) | N | Pearson r | t-stat |
|---|---:|---:|---:|---:|
| cross_es_nq | 180 | 1950 | 0.5987 | 32.991 |
| target_ofi_300s_z | 300 | 1842 | 0.2992 | 13.452 |
| bonds_zn_ofi | 0 | 2099 | -0.2260 | -10.624 |
| bonds_zn_ofi_lag30 | 15 | 2062 | -0.2019 | -9.357 |
| bonds_zb_ofi | 0 | 2098 | 0.1944 | 9.072 |
| bonds_zb_ofi_lag30 | 0 | 2076 | 0.1727 | 7.984 |
| cross_rty_lag30 | 0 | 2076 | 0.1715 | 7.927 |
| cross_rty | 15 | 2089 | 0.1679 | 7.779 |
| oil_cl_ofi | 15 | 2088 | -0.1619 | -7.493 |
| oil_cl_ofi_lag30 | 0 | 2076 | -0.1403 | -6.452 |
| target_ofi_60s_z | 300 | 1842 | 0.1336 | 5.783 |
| macro_lead_score | 300 | 1852 | 0.1213 | 5.255 |
| macro_regime_score | 300 | 1852 | 0.1171 | 5.074 |
| cross_es_nq_lag30 | 60 | 1754 | -0.1117 | -4.706 |
| cross_ym | 30 | 2076 | 0.0832 | 3.803 |

## NQ_5m

- rows: `2361`

| Predictor | Best Lag (s) | N | Pearson r | t-stat |
|---|---:|---:|---:|---:|
| cross_es_nq | 0 | 2360 | 0.4855 | 26.968 |
| target_ofi_300s_z | 180 | 2199 | 0.2355 | 11.356 |
| bonds_zn_ofi_lag30 | 15 | 2320 | -0.2271 | -11.228 |
| bonds_zn_ofi | 15 | 2344 | -0.2192 | -10.873 |
| bonds_zb_ofi_lag30 | 15 | 2320 | 0.1917 | 9.404 |
| bonds_zb_ofi | 15 | 2343 | 0.1882 | 9.270 |
| cross_rty_lag30 | 15 | 2320 | 0.1875 | 9.192 |
| cross_rty | 15 | 2347 | 0.1652 | 8.113 |
| oil_cl_ofi_lag30 | 15 | 2320 | -0.1583 | -7.719 |
| oil_cl_ofi | 15 | 2346 | -0.1556 | -7.625 |
| macro_regime_score | 60 | 2309 | -0.0960 | -4.632 |
| macro_lead_score | 15 | 2348 | -0.0956 | -4.649 |
| cross_ym | 30 | 2334 | 0.0878 | 4.255 |
| cross_ym_lag30 | 5 | 2329 | 0.0764 | 3.696 |
| cross_es_nq_lag30 | 300 | 1796 | -0.0620 | -2.630 |
