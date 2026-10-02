# Benchmark report (commit b8a95ff)

Regenerate with `python bench/run_all.py`. Gates are defined in `bench/gates.yaml`.

## Captures and timing

| capture | tier | rooms | footprint m2 | room overlap m2 | wall time s | stages |
|---|---|---|---|---|---|---|
| apt_lidar_a | lidar | 9 | 49.2465 | 0.0 | 212.2 | load 0.24, drift 18.9, fuse 0.33, layout 6.0, openings 60.88, damage 124.27, export 0.09 |
| apt_lidar_b | lidar | 7 | 44.1069 | 0.0 | 123.2 | load 0.18, drift 6.01, fuse 0.16, layout 4.12, openings 25.03, damage 87.25, export 0.06 |
| room_lidar | lidar | 2 | 10.6381 | 0.0 | 39.2 | load 0.12, drift 1.65, fuse 0.05, layout 0.73, openings 4.16, damage 32.33, export 0.0 |
| apt_video_b | video | 2 | 13.2332 | 0.0 | 119.5 | load 105.47, drift 0.88, fuse 0.1, layout 1.12, openings 1.96, damage 9.68, export 0.02 |
| apt_photo_a | photo | 7 | 116.7447 | 0.0 | 174.4 | load+depth 1.27, room_fit 1.64, stitch 128.33, openings 8.51, damage 34.0 |

## apt_video_b (tier video) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 71, pass_fraction 0.056, median_abs_err_m 0.2984, median_rel_err 0.3657, max_abs_err_m 2.3102 |
| opening_width | FAIL | n_scored 13, ok 0, off 0, missed 9, phantom 4, pass_fraction 0.0 |
| calibration | PASS | n 32, coverage_of_90pct_intervals 0.938 |
| footprint | FAIL | gt_m2 44.1069, pred_m2 13.2332, rel_err -0.7, in_ci False |
| adjacency | FAIL | gt 4, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 7, pred 2, matched 2 |

## apt_photo_a (tier photo) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 86, pass_fraction 0.0, median_abs_err_m 2.085, median_rel_err 1.0856, max_abs_err_m 6.0056 |
| ceiling_height | FAIL | n 9, n_ok 0, max_abs_err_m 0.6707, gate_m 0.015 |
| opening_width | FAIL | n_scored 36, ok 0, off 5, missed 19, phantom 12, pass_fraction 0.0 |
| calibration | PASS | n 40, coverage_of_90pct_intervals 0.95 |
| footprint | FAIL | gt_m2 49.2465, pred_m2 116.7447, rel_err 1.3706, in_ci False |
| adjacency | FAIL | gt 7, found 1 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 9, pred 7, matched 7 |

## Repeatability (same space, same tier, two captures)

**c7d28f72c6 vs 1a8384c3f6** (tier lidar): FAIL; rooms 9 vs 7, paired 0; walls_compared 0, walls_within_gate 0, median_abs_diff_m None, p90_abs_diff_m None, max_abs_diff_m None, ceiling_max_spread_m None, footprint_a_m2 49.2465, footprint_b_m2 44.1069

| room a | room b | a (m) | b (m) | diff (m) | within gate |
|---|---|---|---|---|---|

## Drift ablation (stitched footprint with correction off / on)

| capture | drift | rooms | footprint m2 | bbox diagonal m | wall crispness | loop edges | loop residual before -> after (m) |
|---|---|---|---|---|---|---|---|
| apt_lidar_a | off | 9 | 46.9492 | 13.9273 | 8.429 | None | None -> None |
| apt_lidar_a | loop | 9 | 49.2465 | 14.2437 | 8.836 | 30 | 0.0825 -> 0.0242 |
| apt_lidar_b | off | 7 | 42.0751 | 13.8715 | 10.019 | None | None -> None |
| apt_lidar_b | loop | 7 | 44.1069 | 14.0886 | 12.261 | 7 | 0.1691 -> 0.0258 |
