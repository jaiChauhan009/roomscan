# Benchmark report after fix (commit 1e8c490)

Regenerate with `python bench/run_all.py`. Gates are defined in `bench/gates.yaml`.

## Captures and timing

| capture | tier | rooms | footprint m2 | wall time s | stages |
|---|---|---|---|---|---|
| apt_lidar_a | lidar | 10 | 50.1641 | 225.3 | load 0.28, drift 20.35, fuse 0.36, layout 8.43, openings 83.73, damage 109.45, export 0.19 |
| apt_lidar_b | lidar | 7 | 41.1791 | 150.7 | load 0.21, drift 10.53, fuse 0.23, layout 7.16, openings 46.85, damage 85.26, export 0.03 |
| room_lidar | lidar | 2 | 7.2642 | 27.8 | load 0.06, drift 2.29, fuse 0.07, layout 0.85, openings 5.35, damage 18.97, export 0.0 |
| apt_video_b | video | 1 | 3.8264 | 122.6 | load 117.9, drift 0.51, fuse 0.07, layout 0.74, openings 0.58, damage 2.46, export 0.01 |
| apt_photo_a | photo | 7 | 116.7447 | 201.9 | load+depth 1.96, room_fit 2.1, stitch 148.98, openings 10.45, damage 37.94 |

## apt_video_b (tier video) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 49, pass_fraction 0.041, median_abs_err_m 0.324, median_rel_err 0.4758, max_abs_err_m 0.475 |
| opening_width | FAIL | n_scored 22, ok 0, off 1, missed 18, phantom 3, pass_fraction 0.0 |
| calibration | FAIL | n 10, coverage_of_90pct_intervals 0.5 |
| footprint | FAIL | gt_m2 41.1791, pred_m2 3.8264, rel_err -0.9071, in_ci False |
| adjacency | FAIL | gt 3, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 7, pred 1, matched 1 |

## apt_photo_a (tier photo) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 103, pass_fraction 0.0, median_abs_err_m 2.2698, median_rel_err 1.065, max_abs_err_m 4.4346 |
| ceiling_height | FAIL | n 10, n_ok 0, max_abs_err_m 0.8168, gate_m 0.015 |
| opening_width | FAIL | n_scored 45, ok 0, off 6, missed 30, phantom 9, pass_fraction 0.0 |
| calibration | FAIL | n 41, coverage_of_90pct_intervals 0.268 |
| footprint | FAIL | gt_m2 50.1641, pred_m2 116.7447, rel_err 1.3273, in_ci False |
| adjacency | FAIL | gt 10, found 1 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 10, pred 7, matched 7 |

## Repeatability (same space, same tier, two captures)

**c7d28f72c6 vs 1a8384c3f6** (tier lidar): FAIL; rooms 10 vs 7, paired 1; walls_compared 4, walls_within_gate 0, median_abs_diff_m 0.2372, p90_abs_diff_m 0.4389, max_abs_diff_m 0.5044, ceiling_max_spread_m None, footprint_a_m2 50.1641, footprint_b_m2 41.1791

| room a | room b | a (m) | b (m) | diff (m) | within gate |
|---|---|---|---|---|---|
| room_7 | room_6 | 1.434 | 1.5884 | -0.1544 | no |
| room_7 | room_6 | 0.6826 | 1.187 | -0.5044 | no |
| room_7 | room_6 | 1.4001 | 1.5884 | -0.1883 | no |
| room_7 | room_6 | 0.9009 | 1.187 | -0.2861 | no |

## Drift ablation (stitched footprint with correction off / on)

| capture | drift | rooms | footprint m2 | bbox diagonal m | wall crispness | loop edges | loop residual before -> after (m) |
|---|---|---|---|---|---|---|---|
| apt_lidar_a | off | 9 | 47.1284 | 13.9273 | 8.429 | None | None -> None |
| apt_lidar_a | loop | 10 | 50.1641 | 14.1088 | 8.359 | 30 | 0.0825 -> 0.0265 |
| apt_lidar_b | off | 7 | 40.8996 | 13.2048 | 10.019 | None | None -> None |
| apt_lidar_b | loop | 7 | 41.1791 | 13.3664 | 9.583 | 7 | 0.1691 -> 0.025 |
