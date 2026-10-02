# Benchmark report

Regenerate with `python bench/run_all.py`. Gates are defined in `bench/gates.yaml`.

## Captures and timing

| capture | tier | rooms | footprint m2 | room overlap m2 | wall time s | stages |
|---|---|---|---|---|---|---|
| apt_lidar_a | lidar | 9 | 55.2923 | 0.0 | 485.0 | load 0.62, drift 46.64, fuse 0.59, layout 20.4, openings 180.74, damage 231.84, export 0.1 |
| apt_lidar_b | lidar | 7 | 45.9569 | 0.0 | 276.8 | load 0.25, drift 10.13, fuse 0.23, layout 15.42, openings 56.59, damage 193.04, export 0.11 |
| room_lidar | lidar | 2 | 10.6225 | 0.0 | 86.1 | load 0.38, drift 4.75, fuse 0.1, layout 1.87, openings 9.85, damage 68.76, export 0.0 |
| apt_video_b | video | 2 | 13.2791 | 0.0 | 262.5 | load 243.11, drift 1.35, fuse 0.15, layout 2.39, openings 2.69, damage 12.44, export 0.03 |
| apt_photo_a | photo | 7 | 116.7447 | 0.0 | 229.4 | load+depth 2.09, room_fit 2.88, stitch 167.79, openings 12.6, damage 42.76 |

## apt_video_b (tier video) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 66, pass_fraction 0.015, median_abs_err_m 0.3269, median_rel_err 0.421, max_abs_err_m 2.612 |
| opening_width | FAIL | n_scored 14, ok 0, off 0, missed 10, phantom 4, pass_fraction 0.0 |
| calibration | FAIL | n 28, coverage_of_90pct_intervals 0.571 |
| footprint | FAIL | gt_m2 45.9569, pred_m2 13.2791, rel_err -0.7111, in_ci False |
| adjacency | FAIL | gt 4, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 7, pred 2, matched 2 |

## apt_photo_a (tier photo) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 73, pass_fraction 0.0, median_abs_err_m 1.7839, median_rel_err 0.71, max_abs_err_m 4.6716 |
| ceiling_height | FAIL | n 9, n_ok 1, max_abs_err_m 0.6707, gate_m 0.015 |
| opening_width | FAIL | n_scored 30, ok 0, off 6, missed 14, phantom 10, pass_fraction 0.0 |
| calibration | PASS | n 41, coverage_of_90pct_intervals 0.927 |
| footprint | FAIL | gt_m2 55.2923, pred_m2 116.7447, rel_err 1.1114, in_ci True |
| adjacency | FAIL | gt 6, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 9, pred 7, matched 7 |

## Repeatability (same space, same tier, two captures)

**c7d28f72c6 vs 1a8384c3f6** (tier lidar): FAIL; rooms 9 vs 7, paired 1; walls_compared 7, walls_within_gate 0, median_abs_diff_m 0.2692, p90_abs_diff_m 0.4762, max_abs_diff_m 0.5237, ceiling_max_spread_m None, footprint_a_m2 55.2923, footprint_b_m2 45.9569

| room a | room b | a (m) | b (m) | diff (m) | within gate |
|---|---|---|---|---|---|
| room_7 | room_7 | 0.7601 | 1.2838 | -0.5237 | no |
| room_7 | room_7 | 1.8961 | 2.1653 | -0.2692 | no |
| room_7 | room_7 | 1.2954 | 0.8509 | 0.4445 | no |
| room_7 | room_7 | 0.6925 | 0.4386 | 0.2539 | no |
| room_7 | room_7 | 0.4081 | 0.4417 | -0.0336 | no |
| room_7 | room_7 | 1.6454 | 1.4602 | 0.1852 | no |
| room_7 | room_7 | 1.085 | 0.6937 | 0.3913 | no |

## Drift ablation (stitched footprint with correction off / on)

| capture | drift | rooms | footprint m2 | bbox diagonal m | wall crispness | loop edges | loop residual before -> after (m) |
|---|---|---|---|---|---|---|---|
| apt_lidar_a | off | 9 | 52.6672 | 14.1444 | 8.429 | None | None -> None |
| apt_lidar_a | loop | 9 | 55.2923 | 14.3119 | 8.836 | 30 | 0.0825 -> 0.0242 |
| apt_lidar_b | off | 7 | 43.744 | 13.931 | 10.019 | None | None -> None |
| apt_lidar_b | loop | 7 | 45.9569 | 14.1517 | 12.261 | 7 | 0.1691 -> 0.0258 |
