# Benchmark report baseline

Regenerate with `python bench/run_all.py`. Gates are defined in `bench/gates.yaml`.

## Captures and timing

| capture | tier | rooms | footprint m2 | wall time s | stages |
|---|---|---|---|---|---|
| apt_lidar_a | lidar | 9 | 45.9393 | 195.4 | load 0.17, drift 19.5, fuse 25.34, layout 5.58, openings 62.42, damage 80.13, export 0.07 |
| apt_lidar_b | lidar | 7 | 28.1515 | 99.6 | load 0.29, drift 6.65, fuse 0.17, layout 3.93, openings 25.83, damage 62.26, export 0.04 |
| room_lidar | lidar | 2 | 11.5184 | 31.1 | load 0.12, drift 2.04, fuse 0.05, layout 0.75, openings 4.67, damage 23.18, export 0.01 |
| apt_video_b | video | 1 | 4.2198 | 108.7 | load 104.17, drift 0.39, fuse 1.46, layout 0.56, openings 0.52, damage 1.38, export 0.0 |
| apt_photo_a | photo | 7 | 116.7447 | 192.3 | load+depth 1.32, room_fit 1.7, stitch 146.32, openings 8.67, damage 33.94 |

## apt_video_b (tier video) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 61, pass_fraction 0.016, median_abs_err_m 0.2388, median_rel_err 0.5314, max_abs_err_m 1.8599 |
| opening_width | FAIL | n_scored 20, ok 0, off 0, missed 17, phantom 3, pass_fraction 0.0 |
| calibration | FAIL | n 13, coverage_of_90pct_intervals 0.538 |
| footprint | FAIL | gt_m2 28.1515, pred_m2 4.2198, rel_err -0.8501, in_ci False |
| adjacency | FAIL | gt 1, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 7, pred 1, matched 1 |

## apt_photo_a (tier photo) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 90, pass_fraction 0.011, median_abs_err_m 2.3701, median_rel_err 1.2296, max_abs_err_m 4.7656 |
| ceiling_height | FAIL | n 7, n_ok 1, max_abs_err_m 0.5042, gate_m 0.015 |
| opening_width | FAIL | n_scored 43, ok 1, off 8, missed 26, phantom 8, pass_fraction 0.023 |
| calibration | FAIL | n 42, coverage_of_90pct_intervals 0.31 |
| footprint | FAIL | gt_m2 45.9393, pred_m2 116.7447, rel_err 1.5413, in_ci False |
| adjacency | FAIL | gt 9, found 1 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 9, pred 7, matched 7 |

## Repeatability (same space, same tier, two captures)

**c7d28f72c6 vs 1a8384c3f6** (tier lidar): FAIL; rooms 9 vs 7, paired 0; walls_compared 0, walls_within_gate 0, median_abs_diff_m None, p90_abs_diff_m None, max_abs_diff_m None, ceiling_max_spread_m None, footprint_a_m2 45.9393, footprint_b_m2 28.1515

| room a | room b | a (m) | b (m) | diff (m) | within gate |
|---|---|---|---|---|---|

## Drift ablation (stitched footprint with correction off / on)

| capture | drift | rooms | footprint m2 | bbox diagonal m | wall crispness | loop edges | loop residual before -> after (m) |
|---|---|---|---|---|---|---|---|
| apt_lidar_a | off | 10 | 59.4864 | 17.1544 | 8.429 | None | None -> None |
| apt_lidar_a | loop | 9 | 45.9393 | 14.0302 | 8.359 | 30 | 0.0825 -> 0.0265 |
| apt_lidar_b | off | 6 | 30.8706 | 13.8636 | 10.019 | None | None -> None |
| apt_lidar_b | loop | 7 | 28.1515 | 13.7699 | 9.583 | 7 | 0.1691 -> 0.025 |
