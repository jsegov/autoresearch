# Cross-Market Correlation Report

- generated_at_utc: `2026-05-29T19:33:30+00:00`
- feature_log: `data\cross_market_features.jsonl`

## ES_10m

- rows: `2102`

| Predictor | N | Pearson r | t-stat |
|---|---:|---:|---:|
| target_ofi_300s_z | 1810 | -0.1812 | -7.836 |
| bonds_zn_ofi | 2098 | -0.1660 | -7.708 |
| bonds_zn_ofi_lag30 | 2075 | -0.1598 | -7.368 |
| bonds_zb_ofi | 2098 | 0.1422 | 6.579 |
| gold_gc_vote | 1879 | -0.1357 | -5.932 |
| bonds_zb_ofi_lag30 | 2076 | 0.1353 | 6.220 |
| cross_rty_lag30 | 2075 | 0.1282 | 5.884 |
| cross_index_score_avg | 2102 | 0.1232 | 5.689 |
| cross_rty_value | 2101 | 0.1186 | 5.474 |
| oil_cl_ofi | 2067 | -0.1038 | -4.743 |
| oil_cl_ofi_lag30 | 2042 | -0.0953 | -4.323 |
| target_ofi_60s_z | 1810 | -0.0730 | -3.112 |
| cross_es_nq_value | 2092 | 0.0691 | 3.169 |
| macro_regime_score | 2102 | -0.0515 | -2.362 |
| cross_ym_lag30 | 2038 | 0.0472 | 2.131 |

## ES_5m

- rows: `2360`

| Predictor | N | Pearson r | t-stat |
|---|---:|---:|---:|
| bonds_zn_ofi_lag30 | 2333 | -0.1574 | -7.693 |
| bonds_zb_ofi_lag30 | 2334 | 0.1334 | 6.500 |
| cross_rty_lag30 | 2333 | 0.1201 | 5.842 |
| gold_gc_vote | 2118 | -0.1049 | -4.853 |
| oil_cl_ofi_lag30 | 2298 | -0.0923 | -4.444 |
| macro_lead_score | 2360 | -0.0792 | -3.858 |
| bonds_zn_vote | 2356 | -0.0777 | -3.781 |
| macro_regime_score | 2360 | -0.0759 | -3.696 |
| cross_es_nq_lag30 | 2326 | 0.0539 | 2.603 |
| target_ofi_60s | 2137 | 0.0494 | 2.287 |
| target_ofi_300s_z | 2068 | 0.0416 | 1.892 |
| oil_cl_vote | 2323 | 0.0401 | 1.933 |
| cross_ym_lag30 | 2292 | 0.0390 | 1.869 |
| cross_index_balance | 2360 | 0.0272 | 1.321 |
| cross_es_nq_value | 2350 | 0.0210 | 1.016 |

## NQ_10m

- rows: `2103`

| Predictor | N | Pearson r | t-stat |
|---|---:|---:|---:|
| target_ofi_300s_z | 2093 | 0.2702 | 12.832 |
| bonds_zn_ofi | 2098 | -0.2261 | -10.628 |
| bonds_zn_ofi_lag30 | 2075 | -0.2009 | -9.337 |
| bonds_zb_ofi | 2098 | 0.1944 | 9.072 |
| bonds_zb_ofi_lag30 | 2076 | 0.1727 | 7.984 |
| cross_rty_lag30 | 2075 | 0.1714 | 7.923 |
| cross_rty_value | 2101 | 0.1651 | 7.670 |
| oil_cl_ofi | 2067 | -0.1554 | -7.149 |
| oil_cl_ofi_lag30 | 2042 | -0.1414 | -6.452 |
| gold_gc_vote | 1879 | -0.1198 | -5.227 |
| cross_index_score_avg | 2102 | 0.0650 | 2.985 |
| cross_es_nq_lag30 | 1802 | -0.0609 | -2.589 |
| cross_ym_lag30 | 2038 | 0.0601 | 2.716 |
| cross_ym_value | 2063 | 0.0586 | 2.665 |
| target_ofi_60s | 2103 | -0.0568 | -2.607 |

## NQ_5m

- rows: `2361`

| Predictor | N | Pearson r | t-stat |
|---|---:|---:|---:|
| bonds_zn_ofi_lag30 | 2333 | -0.2154 | -10.647 |
| bonds_zn_ofi | 2356 | -0.2112 | -10.482 |
| target_ofi_300s_z | 2351 | 0.1873 | 9.240 |
| bonds_zb_ofi_lag30 | 2334 | 0.1848 | 9.082 |
| bonds_zb_ofi | 2356 | 0.1818 | 8.972 |
| cross_rty_lag30 | 2333 | 0.1762 | 8.645 |
| oil_cl_ofi_lag30 | 2298 | -0.1562 | -7.578 |
| cross_rty_value | 2359 | 0.1487 | 7.301 |
| oil_cl_ofi | 2323 | -0.1434 | -6.980 |
| macro_lead_score | 2361 | -0.0892 | -4.350 |
| cross_index_score_avg | 2360 | 0.0876 | 4.272 |
| bonds_zn_vote | 2356 | -0.0828 | -4.030 |
| macro_regime_score | 2361 | -0.0819 | -3.993 |
| gold_gc_vote | 2118 | -0.0785 | -3.621 |
| target_ofi_60s | 2361 | -0.0705 | -3.435 |
