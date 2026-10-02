# Interval calibration

From `bench/reports/benchmark.json`, run (commit 80bce56); truth source: lidar_reference, repeatability (apt_lidar_a vs apt_lidar_b). Method in `bench/calibrate.py`.

| tier | group | samples | rooms | coverage at scale 1 | fitted scale | in-sample | leave-one-room-out |
|---|---|---|---|---|---|---|---|
| lidar | length | 19 | 8 | 0.58 | 2.611 | 0.95 | 0.95 (n 19) |
| lidar | area | 0 | 0 | | not fitted: fewer than 5 samples | | |
| video | length | 32 | 2 | 0.38 | 2.577 | 0.94 | 0.91 (n 32) |
| video | area | 2 | 2 | | not fitted: fewer than 5 samples | | |
| photo | length | 40 | 7 | 0.28 | 6.615 | 0.95 | 0.90 (n 40) |
| photo | area | 7 | 7 | 0.00 | 4.519 (max z: too few samples for the bound) | 1.00 | 0.86 (n 7) |

Reference 90 % half-widths after fitting (must widen from LiDAR to photo):

| measurement | lidar | video | photo |
|---|---|---|---|
| wall_length 3.0 m | ±0.061 | ±0.287 | ±1.724 |
| ceiling_height 2.6 m | ±0.051 | ±0.258 | ±1.519 |
| opening_width 0.9 m | ±0.077 | ±0.183 | ±0.817 |
| opening_height 2.0 m | ±0.182 | ±0.355 | ±1.603 |
| area 10.0 m2 | ±0.510 | ±1.860 | ±7.796 |

Adjustments:

- lidar area: too few samples, uses the tier's length scale 2.611
- video area: too few samples, uses the tier's length scale 2.577
