# Interval calibration

From `bench/reports/benchmark.json`, run untagged; truth source: lidar_reference, repeatability (apt_lidar_a vs apt_lidar_b). Method in `bench/calibrate.py`.

LiDAR is fitted on the repeatability pair, not on its 24 laser-truth samples: the laser rooms' outlines have more walls than the truth, so each truth wall is paired with an outline fragment, a segmentation error these intervals do not model. Their coverage is reported in the benchmark's calibration rows.

| tier | group | samples | rooms | coverage at scale 1 | fitted scale | in-sample | leave-one-room-out |
|---|---|---|---|---|---|---|---|
| lidar | length | 18 | 7 | 0.61 | 17.776 | 1.00 | 0.94 (n 18) |
| lidar | area | 0 | 0 | | not fitted: fewer than 5 samples | | |
| video | length | 28 | 2 | 0.36 | 6.422 | 0.96 | 0.75 (n 28) |
| video | area | 2 | 2 | | not fitted: fewer than 5 samples | | |
| photo | length | 40 | 7 | 0.38 | 6.249 | 0.93 | 0.93 (n 40) |
| photo | area | 7 | 7 | 0.14 | 4.004 (max z: too few samples for the bound) | 1.00 | 0.86 (n 7) |

Reference 90 % half-widths after fitting (must widen from LiDAR to photo):

| measurement | lidar | video | photo |
|---|---|---|---|
| wall_length 3.0 m | ±0.061 | ±0.716 | ±1.724 |
| ceiling_height 2.6 m | ±0.051 | ±0.643 | ±1.519 |
| opening_width 0.9 m | ±0.077 | ±0.455 | ±0.817 |
| opening_height 2.0 m | ±0.182 | ±0.884 | ±1.603 |
| area 10.0 m2 | ±0.510 | ±4.635 | ±7.796 |

Adjustments:

- video area: too few samples, uses the tier's length scale 6.422
- refitted: video; kept: lidar, photo (their fits are shown above for information)
