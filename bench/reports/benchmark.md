# Benchmark report

Regenerate with `python bench/run_all.py`. Gates are defined in `bench/gates.yaml`.

## Captures and timing

| capture | tier | rooms | footprint m2 | room overlap m2 | wall time s | stages |
|---|---|---|---|---|---|---|
| apt_lidar_a | lidar | 9 | 55.7384 | 0.0 | 188.2 | load 0.47, drift 24.32, fuse 0.37, layout 11.19, openings 5.37, damage 141.23, export 0.14 |
| apt_lidar_b | lidar | 7 | 46.4266 | 0.0 | 127.4 | load 0.38, drift 8.79, fuse 0.2, layout 7.86, openings 2.71, damage 105.91, export 0.11 |
| room_lidar | lidar | 2 | 10.6173 | 0.0 | 47.5 | load 0.48, drift 3.8, fuse 0.09, layout 1.9, openings 0.64, damage 39.9, export 0.01 |
| apt_video_b | video | 2 | 13.8668 | 0.0 | 136.8 | load 119.87, drift 1.21, fuse 0.12, layout 2.15, openings 0.31, damage 12.62, export 0.04 |
| apt_photo_a | photo | 7 | 116.7447 | 0.0 | 205.1 | load+depth 2.26, room_fit 2.48, stitch 155.78, openings 1.86, damage 42.22 |
| arkit_42446532 | lidar | 1 | 11.5762 | 0 | 29.9 | load 0.13, drift 7.8, fuse 0.09, layout 0.98, openings 0.36, damage 20.17, export 0.0 |
| arkit_44358446 | lidar | 1 | 9.9893 | 0 | 35.8 | load 0.11, drift 10.44, fuse 0.05, layout 0.69, openings 0.41, damage 23.87, export 0.0 |
| arkit_47332890 | lidar | 1 | 14.4731 | 0 | 41.2 | load 0.06, drift 4.41, fuse 0.09, layout 0.98, openings 0.28, damage 34.92, export 0.0 |
| arkit_47331988 | lidar | 1 | 11.6543 | 0 | 88.8 | load 2.08, drift 11.24, fuse 0.08, layout 0.87, openings 0.41, damage 73.67, export 0.02 |
| own_lidar_1 | lidar | 4 | 60.8168 | 0.026 | 122.8 | load 0.58, drift 17.7, fuse 0.44, layout 11.12, openings 3.64, damage 88.65, export 0.04 |
| own_video_1 | video | 1 | 3.2668 | 0 | 171.0 | load 152.19, drift 0.0, fuse 0.22, layout 1.04, openings 0.39, damage 16.39, export 0.03 |
| own_photos_1 | photo | 6 | 80.5701 | 0.0 | 625.4 | load+depth 6.11, room_fit 4.1, stitch 580.09, openings 5.49, damage 28.87 |
| own_lidar_2 | lidar | 4 | 34.9424 | 0.0 | 76.2 | load 0.46, drift 6.93, fuse 0.31, layout 6.75, openings 2.28, damage 58.49, export 0.03 |
| own_lidar_3 | lidar | 1 | 13.6256 | 0 | 47.6 | load 0.33, drift 4.08, fuse 0.12, layout 10.18, openings 0.68, damage 31.82, export 0.0 |
| own_video_iphone | video | 1 | 8.0807 | 0 | 109.3 | load 83.87, drift 7.96, fuse 0.65, layout 2.72, openings 0.9, damage 11.72, export 0.02 |
| own_photos_iphone | photo | 5 | 108.6974 | 0.0 | 180.4 | load+depth 2.5, room_fit 5.06, stitch 151.35, openings 3.23, damage 17.65 |

## apt_video_b (tier video) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 59, pass_fraction 0.034, median_abs_err_m 0.3718, median_rel_err 0.2772, max_abs_err_m 1.1677 |
| opening_width | FAIL | n_scored 16, ok 0, off 2, missed 7, phantom 7, pass_fraction 0.0 |
| calibration | FAIL | n 28, coverage_of_90pct_intervals 1.0 |
| footprint | FAIL | gt_m2 46.4266, pred_m2 13.8668, rel_err -0.7013, in_ci False |
| adjacency | FAIL | gt 4, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 7, pred 2, matched 2 |

## apt_photo_a (tier photo) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 68, pass_fraction 0.0, median_abs_err_m 1.4072, median_rel_err 0.5947, max_abs_err_m 4.6259 |
| ceiling_height | FAIL | n 9, n_ok 1, max_abs_err_m 0.803, gate_m 0.015 |
| opening_width | FAIL | n_scored 28, ok 0, off 5, missed 12, phantom 11, pass_fraction 0.0 |
| calibration | PASS | n 40, coverage_of_90pct_intervals 0.9 |
| footprint | FAIL | gt_m2 55.7384, pred_m2 116.7447, rel_err 1.0945, in_ci True |
| adjacency | FAIL | gt 5, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 9, pred 7, matched 7 |

## arkit_42446532 (tier lidar) vs laser_scanner (ARKitScenes visit 422386, Faro scans 173436, 173438, 173439, 173440)

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 4, pass_fraction 0.0, median_abs_err_m 0.0474, median_rel_err 0.0131, max_abs_err_m 2.2773 |
| ceiling_height | FAIL | n 1, n_ok 0, max_abs_err_m 0.0293, gate_m 0.015 |
| calibration | PASS | n 5, coverage_of_90pct_intervals 0.8 |
| footprint | FAIL | gt_m2 12.466, pred_m2 11.5762, rel_err -0.0714, in_ci False |
| no_room_overlap | PASS |  |
| rooms_found | PASS | gt 1, pred 1, matched 1 |

## arkit_44358446 (tier lidar) vs laser_scanner (ARKitScenes visit 460419, Faro scans 176333, 176336)

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 4, pass_fraction 0.0, median_abs_err_m 0.1035, median_rel_err 0.0316, max_abs_err_m 1.9363 |
| ceiling_height | PASS | n 1, n_ok 1, max_abs_err_m 0.0081, gate_m 0.015 |
| calibration | FAIL | n 5, coverage_of_90pct_intervals 1.0 |
| footprint | FAIL | gt_m2 10.244, pred_m2 9.9893, rel_err -0.0249, in_ci True |
| no_room_overlap | PASS |  |
| rooms_found | PASS | gt 1, pred 1, matched 1 |

## arkit_47332890 (tier lidar) vs laser_scanner (ARKitScenes visit 469272, Faro scans 189578, 189581, 189582)

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 4, pass_fraction 0.0, median_abs_err_m 0.1301, median_rel_err 0.0316, max_abs_err_m 1.3464 |
| ceiling_height | FAIL | n 1, n_ok 0, max_abs_err_m 0.0166, gate_m 0.015 |
| calibration | FAIL | n 5, coverage_of_90pct_intervals 1.0 |
| footprint | PASS | gt_m2 14.392, pred_m2 14.4731, rel_err 0.0056, in_ci True |
| no_room_overlap | PASS |  |
| rooms_found | PASS | gt 1, pred 1, matched 1 |

## arkit_47331988 (tier lidar) vs laser_scanner (ARKitScenes visit 470811, Faro scans 203839, 203840, 203843, 203844)

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 4, pass_fraction 0.5, median_abs_err_m 0.0327, median_rel_err 0.01, max_abs_err_m 0.0483 |
| ceiling_height | PASS | n 1, n_ok 1, max_abs_err_m 0.0092, gate_m 0.015 |
| calibration | FAIL | n 5, coverage_of_90pct_intervals 1.0 |
| footprint | FAIL | gt_m2 11.891, pred_m2 11.6543, rel_err -0.0199, in_ci True |
| no_room_overlap | PASS |  |
| rooms_found | PASS | gt 1, pred 1, matched 1 |

## own_video_1 (tier video) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 31, pass_fraction 0.0, median_abs_err_m 1.199, median_rel_err 0.408, max_abs_err_m 1.9911 |
| ceiling_height | FAIL | n 4, n_ok 0, max_abs_err_m 0.0765, gate_m 0.015 |
| opening_width | FAIL | n_scored 12, ok 0, off 1, missed 10, phantom 1, pass_fraction 0.0 |
| calibration | FAIL | n 6, coverage_of_90pct_intervals 0.667 |
| footprint | FAIL | gt_m2 60.8168, pred_m2 3.2668, rel_err -0.9463, in_ci False |
| adjacency | FAIL | gt 2, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 4, pred 1, matched 1 |

## own_photos_1 (tier photo) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 31, pass_fraction 0.129, median_abs_err_m 0.8536, median_rel_err 0.287, max_abs_err_m 2.8094 |
| ceiling_height | FAIL | n 4, n_ok 0, max_abs_err_m 0.9879, gate_m 0.015 |
| opening_width | FAIL | n_scored 19, ok 0, off 1, missed 10, phantom 8, pass_fraction 0.0 |
| calibration | PASS | n 21, coverage_of_90pct_intervals 0.905 |
| footprint | FAIL | gt_m2 60.8168, pred_m2 80.5701, rel_err 0.3248, in_ci True |
| adjacency | FAIL | gt 2, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | PASS | gt 4, pred 6, matched 4 |

## own_video_iphone (tier video) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 31, pass_fraction 0.065, median_abs_err_m 0.2084, median_rel_err 0.0716, max_abs_err_m 0.3763 |
| ceiling_height | FAIL | n 4, n_ok 0, max_abs_err_m 1.941, gate_m 0.015 |
| opening_width | FAIL | n_scored 13, ok 0, off 1, missed 10, phantom 2, pass_fraction 0.0 |
| calibration | PASS | n 6, coverage_of_90pct_intervals 0.833 |
| footprint | FAIL | gt_m2 60.8168, pred_m2 8.0807, rel_err -0.8671, in_ci False |
| adjacency | FAIL | gt 2, found 0 |
| no_room_overlap | PASS |  |
| rooms_found | FAIL | gt 4, pred 1, matched 1 |

## own_photos_iphone (tier photo) vs lidar_reference

| gate | result | numbers |
|---|---|---|
| wall_length | FAIL | n 31, pass_fraction 0.032, median_abs_err_m 1.5174, median_rel_err 0.4965, max_abs_err_m 2.7824 |
| ceiling_height | FAIL | n 4, n_ok 0, max_abs_err_m 0.8359, gate_m 0.015 |
| opening_width | FAIL | n_scored 19, ok 0, off 1, missed 10, phantom 8, pass_fraction 0.0 |
| calibration | PASS | n 21, coverage_of_90pct_intervals 0.952 |
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
