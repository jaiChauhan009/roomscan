# Benchmark report

Regenerate with `python bench/run_all.py`. Gates are defined in `bench/gates.yaml`.

## Captures and timing

| capture | tier | rooms | footprint m2 | room overlap m2 | wall time s | stages |
|---|---|---|---|---|---|---|
| apt_lidar_a | lidar | 9 | 55.3354 | 0.0 | 183.6 | load 0.45, drift 28.98, fuse 0.58, layout 13.42, openings 6.99, damage 133.13, export 0.08 |
| apt_lidar_b | lidar | 7 | 45.9569 | 0.0 | 135.0 | load 0.31, drift 10.97, fuse 0.32, layout 9.13, openings 3.59, damage 110.6, export 0.08 |
| room_lidar | lidar | 2 | 10.6225 | 0.0 | 45.5 | load 0.22, drift 3.34, fuse 0.1, layout 1.64, openings 0.78, damage 39.4, export 0.0 |
| apt_video_b | video | 2 | 13.2791 | 0.0 | 186.6 | load 161.09, drift 1.33, fuse 0.15, layout 2.73, openings 0.29, damage 20.98, export 0.06 |
| apt_photo_a | photo | 7 | 116.7447 | 0.0 | 245.8 | load+depth 2.44, room_fit 3.4, stitch 187.51, openings 2.92, damage 49.54 |
| arkit_42446532 | lidar | 2 | 11.5187 | 0.0 | 36.4 | load 0.07, drift 8.4, fuse 0.14, layout 1.21, openings 0.53, damage 26.02, export 0.0 |
| arkit_44358446 | lidar | 1 | 9.6619 | 0 | 43.1 | load 0.08, drift 11.83, fuse 0.11, layout 1.0, openings 0.64, damage 29.45, export 0.0 |
| arkit_47332890 | lidar | 1 | 14.422 | 0 | 46.6 | load 0.05, drift 5.48, fuse 0.14, layout 1.43, openings 0.6, damage 38.86, export 0.0 |
| arkit_47331988 | lidar | 1 | 11.6543 | 0 | 86.3 | load 0.08, drift 9.73, fuse 0.11, layout 1.26, openings 0.73, damage 74.43, export 0.0 |

## apt_video_b (tier video) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 66, pass_fraction 0.015, median_abs_err_m 0.3269, median_rel_err 0.421, max_abs_err_m 2.612 |
| opening_width | FAIL | n_scored 14, ok 0, off 0, missed 10, phantom 4, pass_fraction 0.0 |
| calibration | PASS | n 28, coverage_of_90pct_intervals 0.929 |
| footprint | FAIL | gt_m2 45.9569, pred_m2 13.2791, rel_err -0.7111, in_ci False |
| adjacency | FAIL | gt 4, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 7, pred 2, matched 2 |

## apt_photo_a (tier photo) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 71, pass_fraction 0.0, median_abs_err_m 1.6488, median_rel_err 0.7361, max_abs_err_m 4.6259 |
| ceiling_height | FAIL | n 9, n_ok 1, max_abs_err_m 1.1225, gate_m 0.015 |
| opening_width | FAIL | n_scored 29, ok 0, off 5, missed 14, phantom 10, pass_fraction 0.0 |
| calibration | PASS | n 40, coverage_of_90pct_intervals 0.95 |
| footprint | FAIL | gt_m2 55.3354, pred_m2 116.7447, rel_err 1.1098, in_ci True |
| adjacency | FAIL | gt 6, found 1 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 9, pred 7, matched 7 |

## arkit_42446532 (tier lidar) vs laser_scanner (ARKitScenes visit 422386, Faro scans 173436, 173438, 173439, 173440)

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 4, pass_fraction 0.0, median_abs_err_m 1.4836, median_rel_err 0.4059, max_abs_err_m 2.2774 |
| ceiling_height | FAIL | n 1, n_ok 0, max_abs_err_m 0.0536, gate_m 0.015 |
| calibration | FAIL | n 5, coverage_of_90pct_intervals 0.2 |
| footprint | FAIL | gt_m2 12.466, pred_m2 11.5187, rel_err -0.076, in_ci False |
| no_room_overlap | PASS |  |
| rooms_found | PASS | gt 1, pred 2, matched 1 |

## arkit_44358446 (tier lidar) vs laser_scanner (ARKitScenes visit 460419, Faro scans 176333, 176336)

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 4, pass_fraction 0.25, median_abs_err_m 0.1029, median_rel_err 0.0314, max_abs_err_m 1.7991 |
| ceiling_height | FAIL | n 1, n_ok 0, max_abs_err_m 0.0171, gate_m 0.015 |
| calibration | FAIL | n 5, coverage_of_90pct_intervals 0.6 |
| footprint | FAIL | gt_m2 10.244, pred_m2 9.6619, rel_err -0.0568, in_ci True |
| no_room_overlap | PASS |  |
| rooms_found | PASS | gt 1, pred 1, matched 1 |

## arkit_47332890 (tier lidar) vs laser_scanner (ARKitScenes visit 469272, Faro scans 189578, 189581, 189582)

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 4, pass_fraction 0.0, median_abs_err_m 1.327, median_rel_err 0.3569, max_abs_err_m 1.434 |
| ceiling_height | FAIL | n 1, n_ok 0, max_abs_err_m 0.022, gate_m 0.015 |
| calibration | FAIL | n 5, coverage_of_90pct_intervals 0.2 |
| footprint | PASS | gt_m2 14.392, pred_m2 14.422, rel_err 0.0021, in_ci True |
| no_room_overlap | PASS |  |
| rooms_found | PASS | gt 1, pred 1, matched 1 |

## arkit_47331988 (tier lidar) vs laser_scanner (ARKitScenes visit 470811, Faro scans 203839, 203840, 203843, 203844)

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 4, pass_fraction 0.5, median_abs_err_m 0.0327, median_rel_err 0.01, max_abs_err_m 0.0483 |
| ceiling_height | PASS | n 1, n_ok 1, max_abs_err_m 0.0102, gate_m 0.015 |
| calibration | FAIL | n 5, coverage_of_90pct_intervals 1.0 |
| footprint | FAIL | gt_m2 11.891, pred_m2 11.6543, rel_err -0.0199, in_ci True |
| no_room_overlap | PASS |  |
| rooms_found | PASS | gt 1, pred 1, matched 1 |

## Repeatability (same space, same tier, two captures)

**c7d28f72c6 vs 1a8384c3f6** (tier lidar): FAIL; rooms 9 vs 7, paired 1; walls_compared 7, walls_within_gate 0, median_abs_diff_m 0.2692, p90_abs_diff_m 0.4762, max_abs_diff_m 0.5237, ceiling_max_spread_m None, footprint_a_m2 55.3354, footprint_b_m2 45.9569

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
| apt_lidar_a | off | 9 | 52.8071 | 14.0849 | 8.429 | None | None -> None |
| apt_lidar_a | loop | 9 | 55.3354 | 14.2547 | 8.836 | 30 | 0.0825 -> 0.0242 |
| apt_lidar_b | off | 7 | 43.744 | 13.931 | 10.019 | None | None -> None |
| apt_lidar_b | loop | 7 | 45.9569 | 14.1517 | 12.261 | 7 | 0.1691 -> 0.0258 |
