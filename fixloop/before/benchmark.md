# Benchmark report before fix (commit 0a9c23c)

Regenerate with `python bench/run_all.py`. Gates are defined in `bench/gates.yaml`.

## Captures and timing

| capture | tier | rooms | footprint m2 | wall time s | stages |
|---|---|---|---|---|---|
| apt_lidar_a | lidar | 9 | 50.0622 | 169.8 | load 0.16, drift 18.14, fuse 0.31, layout 6.21, openings 62.35, damage 80.78, export 0.07 |
| apt_lidar_b | lidar | 6 | 27.7491 | 70.6 | load 0.08, drift 5.86, fuse 0.16, layout 3.45, openings 22.56, damage 38.12, export 0.03 |
| room_lidar | lidar | 2 | 11.5184 | 27.2 | load 0.04, drift 1.89, fuse 0.05, layout 0.71, openings 4.48, damage 19.8, export 0.01 |
| apt_video_b | video | 1 | 4.2198 | 96.9 | load 93.97, drift 0.36, fuse 0.06, layout 0.54, openings 0.51, damage 1.28, export 0.0 |
| apt_photo_a | photo | 7 | 116.7447 | 181.0 | load+depth 1.29, room_fit 1.66, stitch 130.84, openings 8.0, damage 38.34 |

## apt_video_b (tier video) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 50, pass_fraction 0.02, median_abs_err_m 0.2188, median_rel_err 0.5094, max_abs_err_m 1.8599 |
| opening_width | FAIL | n_scored 19, ok 0, off 0, missed 16, phantom 3, pass_fraction 0.0 |
| calibration | FAIL | n 13, coverage_of_90pct_intervals 0.538 |
| footprint | FAIL | gt_m2 27.7491, pred_m2 4.2198, rel_err -0.8479, in_ci False |
| adjacency | FAIL | gt 1, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 6, pred 1, matched 1 |

## apt_photo_a (tier photo) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 89, pass_fraction 0.0, median_abs_err_m 2.1402, median_rel_err 0.966, max_abs_err_m 4.3956 |
| ceiling_height | FAIL | n 9, n_ok 0, max_abs_err_m 1.1263, gate_m 0.015 |
| opening_width | FAIL | n_scored 44, ok 0, off 5, missed 28, phantom 11, pass_fraction 0.0 |
| calibration | FAIL | n 40, coverage_of_90pct_intervals 0.3 |
| footprint | FAIL | gt_m2 50.0622, pred_m2 116.7447, rel_err 1.332, in_ci False |
| adjacency | FAIL | gt 8, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 9, pred 7, matched 7 |

## Repeatability (same space, same tier, two captures)

**c7d28f72c6 vs 1a8384c3f6** (tier lidar): FAIL; rooms 9 vs 6, paired 0; walls_compared 0, walls_within_gate 0, median_abs_diff_m None, p90_abs_diff_m None, max_abs_diff_m None, ceiling_max_spread_m None, footprint_a_m2 50.0622, footprint_b_m2 27.7491

| room a | room b | a (m) | b (m) | diff (m) | within gate |
|---|---|---|---|---|---|

## Drift ablation (stitched footprint with correction off / on)

| capture | drift | rooms | footprint m2 | bbox diagonal m | wall crispness | loop edges | loop residual before -> after (m) |
|---|---|---|---|---|---|---|---|
| apt_lidar_a | off | 9 | 48.8462 | 13.9278 | 8.429 | None | None -> None |
| apt_lidar_a | loop | 9 | 50.0622 | 14.0881 | 8.359 | 30 | 0.0825 -> 0.0265 |
| apt_lidar_b | off | 6 | 30.9542 | 13.8571 | 10.019 | None | None -> None |
| apt_lidar_b | loop | 6 | 27.7491 | 13.7555 | 9.583 | 7 | 0.1691 -> 0.025 |
