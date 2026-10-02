# Benchmark report after fix-loop round 2 (commit 2f4ed8b)

Regenerate with `python bench/run_all.py`. Gates are defined in `bench/gates.yaml`.

## Captures and timing

| capture | tier | rooms | footprint m2 | wall time s | stages |
|---|---|---|---|---|---|
| apt_lidar_a | lidar | 9 | 49.4622 | 226.0 | load 0.26, drift 19.61, fuse 29.12, layout 7.36, openings 69.18, damage 98.34, export 0.09 |
| apt_lidar_b | lidar | 7 | 48.9955 | 131.1 | load 0.11, drift 6.93, fuse 14.87, layout 4.71, openings 30.95, damage 73.09, export 0.05 |
| room_lidar | lidar | 2 | 11.4374 | 40.8 | load 0.15, drift 2.3, fuse 4.47, layout 0.88, openings 4.96, damage 27.85, export 0.0 |
| apt_video_b | video | 1 | 3.8264 | 115.4 | load 109.69, drift 0.42, fuse 1.69, layout 0.66, openings 0.53, damage 2.14, export 0.0 |
| apt_photo_a | photo | 7 | 116.7447 | 174.6 | load+depth 2.09, room_fit 2.15, stitch 126.44, openings 10.28, damage 33.28 |

## apt_video_b (tier video) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 72, pass_fraction 0.0, median_abs_err_m 0.1457, median_rel_err 0.4003, max_abs_err_m 2.0195 |
| opening_width | FAIL | n_scored 15, ok 0, off 0, missed 11, phantom 4, pass_fraction 0.0 |
| calibration | FAIL | n 17, coverage_of_90pct_intervals 0.765 |
| footprint | FAIL | gt_m2 48.9955, pred_m2 3.8264, rel_err -0.9219, in_ci False |
| adjacency | FAIL | gt 5, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 7, pred 1, matched 1 |

## apt_photo_a (tier photo) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 83, pass_fraction 0.012, median_abs_err_m 2.467, median_rel_err 1.0312, max_abs_err_m 6.0616 |
| ceiling_height | FAIL | n 9, n_ok 0, max_abs_err_m 0.8256, gate_m 0.015 |
| opening_width | FAIL | n_scored 37, ok 0, off 4, missed 22, phantom 11, pass_fraction 0.0 |
| calibration | FAIL | n 39, coverage_of_90pct_intervals 0.282 |
| footprint | FAIL | gt_m2 49.4622, pred_m2 116.7447, rel_err 1.3603, in_ci False |
| adjacency | FAIL | gt 8, found 2 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 9, pred 7, matched 7 |

## Repeatability (same space, same tier, two captures)

**c7d28f72c6 vs 1a8384c3f6** (tier lidar): FAIL; rooms 9 vs 7, paired 0; walls_compared 0, walls_within_gate 0, median_abs_diff_m None, p90_abs_diff_m None, max_abs_diff_m None, ceiling_max_spread_m None, footprint_a_m2 49.4622, footprint_b_m2 48.9955

| room a | room b | a (m) | b (m) | diff (m) | within gate |
|---|---|---|---|---|---|

## Drift ablation (stitched footprint with correction off / on)

| capture | drift | rooms | footprint m2 | bbox diagonal m | wall crispness | loop edges | loop residual before -> after (m) |
|---|---|---|---|---|---|---|---|
| apt_lidar_a | off | 9 | 47.1704 | 13.9273 | 8.429 | None | None -> None |
| apt_lidar_a | loop | 9 | 49.4622 | 14.2437 | 8.836 | 30 | 0.0825 -> 0.0242 |
| apt_lidar_b | off | 7 | 42.1614 | 13.8715 | 10.019 | None | None -> None |
| apt_lidar_b | loop | 7 | 48.9955 | 14.0886 | 12.261 | 7 | 0.1691 -> 0.0258 |
