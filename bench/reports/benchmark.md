# Benchmark report

Regenerate with `python bench/run_all.py`. Gates are defined in `bench/gates.yaml`.

## Captures and timing

| capture | tier | rooms | footprint m2 | room overlap m2 | wall time s | stages |
|---|---|---|---|---|---|---|
| apt_lidar_a | lidar | 9 | 55.7384 | 0.0 | 195.0 | load 0.39, drift 29.1, fuse 0.44, layout 13.83, openings 7.34, damage 141.58, export 0.11 |
| apt_lidar_b | lidar | 7 | 46.4266 | 0.0 | 138.3 | load 0.32, drift 10.29, fuse 0.24, layout 9.67, openings 2.96, damage 113.92, export 0.08 |
| room_lidar | lidar | 2 | 10.6173 | 0.0 | 45.8 | load 0.34, drift 3.3, fuse 0.06, layout 1.65, openings 0.55, damage 39.5, export 0.0 |
| apt_video_b | video | 2 | 13.8668 | 0.0 | 200.4 | load 180.23, drift 1.39, fuse 0.14, layout 2.59, openings 0.43, damage 15.04, export 0.02 |
| apt_photo_a | photo | 7 | 116.7447 | 0.0 | 239.6 | load+depth 2.06, room_fit 2.76, stitch 185.09, openings 2.34, damage 46.67 |
| arkit_42446532 | lidar | 2 | 11.5177 | 0.0 | 37.2 | load 0.08, drift 9.22, fuse 0.09, layout 1.34, openings 0.51, damage 25.6, export 0.01 |
| arkit_44358446 | lidar | 1 | 9.9893 | 0 | 38.3 | load 0.07, drift 10.65, fuse 0.05, layout 0.45, openings 0.32, damage 26.5, export 0.0 |
| arkit_47332890 | lidar | 1 | 14.4731 | 0 | 41.5 | load 0.05, drift 4.3, fuse 0.08, layout 1.22, openings 0.27, damage 35.43, export 0.0 |
| arkit_47331988 | lidar | 1 | 11.6543 | 0 | 73.3 | load 0.06, drift 8.17, fuse 0.07, layout 0.66, openings 0.4, damage 63.78, export 0.0 |
| own_lidar_1 | lidar | 4 | 60.8168 | 0.026 | 99.7 | load 0.27, drift 12.24, fuse 0.3, layout 5.21, openings 2.38, damage 78.99, export 0.01 |
| own_video_1 | video | 1 | 3.2668 | 0 | 1680.2 | load 1652.51, drift 0.0, fuse 8.15, layout 0.72, openings 0.37, damage 17.73, export 0.02 |
| own_photos_1 | photo | 6 | 80.5701 | 0.0 | 378.1 | load+depth 37.13, room_fit 3.39, stitch 303.72, openings 4.09, damage 29.31 |
| own_lidar_2 | lidar | 4 | 34.9424 | 0.0 | 72.5 | load 0.24, drift 3.55, fuse 8.73, layout 2.98, openings 1.24, damage 55.39, export 0.01 |
| own_lidar_3 | lidar | 1 | 13.6256 | 0 | 38.8 | load 1.13, drift 2.01, fuse 4.11, layout 0.67, openings 0.45, damage 30.23, export 0.0 |
| own_video_iphone | video | 1 | 8.0807 | 0 | 697.5 | load 666.85, drift 4.52, fuse 14.06, layout 1.41, openings 0.49, damage 9.41, export 0.01 |
| own_photos_iphone | photo | 5 | 108.6974 | 0.0 | 194.5 | load+depth 24.21, room_fit 2.65, stitch 148.36, openings 2.15, damage 16.72 |

## apt_video_b (tier video) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 59, pass_fraction 0.034, median_abs_err_m 0.3718, median_rel_err 0.2772, max_abs_err_m 1.1677 |
| opening_width | FAIL | n_scored 16, ok 0, off 2, missed 8, phantom 6, pass_fraction 0.0 |
| calibration | PASS | n 28, coverage_of_90pct_intervals 0.929 |
| footprint | FAIL | gt_m2 46.4266, pred_m2 13.8668, rel_err -0.7013, in_ci False |
| adjacency | FAIL | gt 4, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 7, pred 2, matched 2 |

## apt_photo_a (tier photo) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 68, pass_fraction 0.0, median_abs_err_m 1.4072, median_rel_err 0.5947, max_abs_err_m 4.6259 |
| ceiling_height | FAIL | n 9, n_ok 1, max_abs_err_m 0.8097, gate_m 0.015 |
| opening_width | FAIL | n_scored 28, ok 0, off 5, missed 12, phantom 11, pass_fraction 0.0 |
| calibration | PASS | n 40, coverage_of_90pct_intervals 0.925 |
| footprint | FAIL | gt_m2 55.7384, pred_m2 116.7447, rel_err 1.0945, in_ci True |
| adjacency | FAIL | gt 6, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 9, pred 7, matched 7 |

## arkit_42446532 (tier lidar) vs laser_scanner (ARKitScenes visit 422386, Faro scans 173436, 173438, 173439, 173440)

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 4, pass_fraction 0.0, median_abs_err_m 1.4837, median_rel_err 0.406, max_abs_err_m 2.2775 |
| ceiling_height | FAIL | n 1, n_ok 0, max_abs_err_m 0.0536, gate_m 0.015 |
| calibration | FAIL | n 5, coverage_of_90pct_intervals 0.2 |
| footprint | FAIL | gt_m2 12.466, pred_m2 11.5177, rel_err -0.0761, in_ci False |
| no_room_overlap | PASS |  |
| rooms_found | PASS | gt 1, pred 2, matched 1 |

## arkit_44358446 (tier lidar) vs laser_scanner (ARKitScenes visit 460419, Faro scans 176333, 176336)

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 4, pass_fraction 0.0, median_abs_err_m 0.1035, median_rel_err 0.0316, max_abs_err_m 1.9363 |
| ceiling_height | FAIL | n 1, n_ok 0, max_abs_err_m 0.0171, gate_m 0.015 |
| calibration | FAIL | n 5, coverage_of_90pct_intervals 1.0 |
| footprint | FAIL | gt_m2 10.244, pred_m2 9.9893, rel_err -0.0249, in_ci True |
| no_room_overlap | PASS |  |
| rooms_found | PASS | gt 1, pred 1, matched 1 |

## arkit_47332890 (tier lidar) vs laser_scanner (ARKitScenes visit 469272, Faro scans 189578, 189581, 189582)

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 4, pass_fraction 0.0, median_abs_err_m 0.1301, median_rel_err 0.0316, max_abs_err_m 1.3464 |
| ceiling_height | FAIL | n 1, n_ok 0, max_abs_err_m 0.022, gate_m 0.015 |
| calibration | PASS | n 5, coverage_of_90pct_intervals 0.8 |
| footprint | PASS | gt_m2 14.392, pred_m2 14.4731, rel_err 0.0056, in_ci True |
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

## own_video_1 (tier video) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 31, pass_fraction 0.0, median_abs_err_m 1.199, median_rel_err 0.408, max_abs_err_m 1.9911 |
| ceiling_height | FAIL | n 4, n_ok 0, max_abs_err_m 0.0678, gate_m 0.015 |
| opening_width | FAIL | n_scored 13, ok 0, off 1, missed 11, phantom 1, pass_fraction 0.0 |
| calibration | FAIL | n 6, coverage_of_90pct_intervals 0.667 |
| footprint | FAIL | gt_m2 60.8168, pred_m2 3.2668, rel_err -0.9463, in_ci False |
| adjacency | FAIL | gt 2, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 4, pred 1, matched 1 |

## own_photos_1 (tier photo) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 31, pass_fraction 0.129, median_abs_err_m 0.8536, median_rel_err 0.287, max_abs_err_m 2.8094 |
| ceiling_height | FAIL | n 4, n_ok 0, max_abs_err_m 0.9966, gate_m 0.015 |
| opening_width | FAIL | n_scored 19, ok 0, off 3, missed 9, phantom 7, pass_fraction 0.0 |
| calibration | PASS | n 23, coverage_of_90pct_intervals 0.957 |
| footprint | FAIL | gt_m2 60.8168, pred_m2 80.5701, rel_err 0.3248, in_ci True |
| adjacency | FAIL | gt 2, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | PASS | gt 4, pred 6, matched 4 |

## own_video_iphone (tier video) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 31, pass_fraction 0.065, median_abs_err_m 0.2084, median_rel_err 0.0716, max_abs_err_m 0.3763 |
| ceiling_height | FAIL | n 4, n_ok 0, max_abs_err_m 1.9497, gate_m 0.015 |
| opening_width | FAIL | n_scored 14, ok 0, off 1, missed 11, phantom 2, pass_fraction 0.0 |
| calibration | PASS | n 6, coverage_of_90pct_intervals 0.833 |
| footprint | FAIL | gt_m2 60.8168, pred_m2 8.0807, rel_err -0.8671, in_ci False |
| adjacency | FAIL | gt 2, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 4, pred 1, matched 1 |

## own_photos_iphone (tier photo) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 31, pass_fraction 0.032, median_abs_err_m 1.5174, median_rel_err 0.4965, max_abs_err_m 2.7824 |
| ceiling_height | FAIL | n 4, n_ok 0, max_abs_err_m 0.842, gate_m 0.015 |
| opening_width | FAIL | n_scored 21, ok 0, off 0, missed 12, phantom 9, pass_fraction 0.0 |
| calibration | FAIL | n 20, coverage_of_90pct_intervals 1.0 |
| footprint | FAIL | gt_m2 60.8168, pred_m2 108.6974, rel_err 0.7873, in_ci True |
| adjacency | FAIL | gt 2, found 1 |
| no_room_overlap | PASS |  |
| rooms_found | PASS | gt 4, pred 5, matched 4 |

## Repeatability (same space, same tier, two captures)

**c7d28f72c6 vs 1a8384c3f6** (tier lidar): FAIL; rooms 9 vs 7, paired 1; walls_compared 20, walls_within_gate 2, median_abs_diff_m 0.0841, p90_abs_diff_m 0.3074, max_abs_diff_m 0.4856, ceiling_max_spread_m None, footprint_a_m2 55.7384, footprint_b_m2 46.4266

| room a | room b | a (m) | b (m) | diff (m) | within gate |
|---|---|---|---|---|---|
| room_2 | room_2 | 3.4377 | 2.9521 | 0.4856 | no |
| room_2 | room_2 | 3.0967 | 3.0041 | 0.0926 | no |
| room_4 | room_6 | 1.0376 | 1.0616 | -0.024 | no |
| room_4 | room_4 | 0.9956 | 0.9363 | 0.0593 | no |
| room_4 | room_4 | 1.1547 | 1.1151 | 0.0396 | no |
| room_4 | room_1 | 1.3673 | 1.0974 | 0.2699 | no |
| room_5 | room_6 | 1.1398 | 1.1632 | -0.0234 | no |
| room_5 | room_6 | 2.851 | 2.7754 | 0.0756 | no |
| room_5 | room_6 | 1.14 | 1.1444 | -0.0044 | yes |
| room_5 | room_6 | 1.0224 | 1.0616 | -0.0392 | no |
| room_5 | room_6 | 3.1599 | 3.062 | 0.0979 | no |
| room_6 | room_1 | 1.5411 | 1.5913 | -0.0502 | no |
| room_7 | room_7 | 2.3225 | 2.1661 | 0.1564 | no |
| room_7 | room_7 | 1.2955 | 1.2838 | 0.0117 | no |
| room_7 | room_7 | 0.6924 | 0.6937 | -0.0013 | yes |
| room_7 | room_7 | 1.6301 | 1.4526 | 0.1775 | no |
| room_8 | room_5 | 1.0154 | 0.6738 | 0.3416 | no |
| room_9 | room_5 | 2.5363 | 2.6331 | -0.0968 | no |
| room_9 | room_5 | 1.0319 | 1.2277 | -0.1958 | no |
| room_9 | room_5 | 2.2115 | 1.9079 | 0.3036 | no |
**609c9be5d1 vs 84d7fdb836** (tier lidar): FAIL; rooms 4 vs 1, paired 0; walls_compared 2, walls_within_gate 1, median_abs_diff_m 0.0171, p90_abs_diff_m 0.0274, max_abs_diff_m 0.03, ceiling_max_spread_m None, footprint_a_m2 60.8168, footprint_b_m2 13.6256

| room a | room b | a (m) | b (m) | diff (m) | within gate |
|---|---|---|---|---|---|
| room_4 | room_1 | 4.1615 | 4.1572 | 0.0043 | yes |
| room_4 | room_1 | 3.3076 | 3.2776 | 0.03 | no |

## Drift ablation (stitched footprint with correction off / on)

| capture | drift | rooms | footprint m2 | bbox diagonal m | wall crispness | loop edges | loop residual before -> after (m) |
|---|---|---|---|---|---|---|---|
| apt_lidar_a | off | 9 | 56.3595 | 14.1522 | 8.429 | None | None -> None |
| apt_lidar_a | loop | 9 | 55.7384 | 14.2545 | 8.836 | 30 | 0.0825 -> 0.0242 |
| apt_lidar_b | off | 7 | 42.9372 | 13.931 | 10.019 | None | None -> None |
| apt_lidar_b | loop | 7 | 46.4266 | 14.1518 | 12.261 | 7 | 0.1691 -> 0.0258 |
