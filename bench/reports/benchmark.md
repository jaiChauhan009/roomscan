# Benchmark report (a88f7ef outputs re-scored for calibration)

Regenerate with `python bench/run_all.py`. Gates are defined in `bench/gates.yaml`.

## Captures and timing

| capture | tier | rooms | footprint m2 | room overlap m2 | wall time s | stages |
|---|---|---|---|---|---|---|
| apt_lidar_a | lidar | 9 | 49.2465 | 0.0 | 0.0 | load 0.23, drift 18.58, fuse 0.32, layout 5.82, openings 57.66, damage 83.04, export 0.07 |
| apt_lidar_b | lidar | 7 | 44.1069 | 0.0 | 0.0 | load 0.13, drift 6.26, fuse 0.15, layout 4.07, openings 25.56, damage 67.66, export 0.05 |
| room_lidar | lidar | 2 | 10.6381 | 0.0 | 0.0 | load 0.04, drift 1.87, fuse 0.05, layout 0.74, openings 4.33, damage 26.23, export 0.0 |
| apt_video_b | video | 1 | 3.8264 | 0 | 0.0 | load 92.81, drift 0.36, fuse 0.05, layout 0.53, openings 0.45, damage 1.65, export 0.0 |
| apt_photo_a | photo | 7 | 116.7447 | 0.0 | 0.0 | load+depth 1.31, room_fit 1.68, stitch 121.4, openings 8.6, damage 31.54 |

## apt_video_b (tier video) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 71, pass_fraction 0.0, median_abs_err_m 0.1457, median_rel_err 0.4003, max_abs_err_m 2.0195 |
| opening_width | FAIL | n_scored 13, ok 0, off 0, missed 9, phantom 4, pass_fraction 0.0 |
| calibration | FAIL | n 17, coverage_of_90pct_intervals 0.765 |
| footprint | FAIL | gt_m2 44.1069, pred_m2 3.8264, rel_err -0.9132, in_ci False |
| adjacency | FAIL | gt 4, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 7, pred 1, matched 1 |

## apt_photo_a (tier photo) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 86, pass_fraction 0.0, median_abs_err_m 2.085, median_rel_err 1.0856, max_abs_err_m 6.0056 |
| ceiling_height | FAIL | n 9, n_ok 0, max_abs_err_m 0.6707, gate_m 0.015 |
| opening_width | FAIL | n_scored 36, ok 0, off 5, missed 19, phantom 12, pass_fraction 0.0 |
| calibration | FAIL | n 40, coverage_of_90pct_intervals 0.275 |
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
