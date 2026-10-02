# Interval calibration

From `bench/reports/benchmark.json`, run untagged; truth source: laser_scanner (ARKitScenes visit 422386, Faro scans 173436, 173438, 173439, 173440), laser_scanner (ARKitScenes visit 460419, Faro scans 176333, 176336), laser_scanner (ARKitScenes visit 469272, Faro scans 189578, 189581, 189582), laser_scanner (ARKitScenes visit 470811, Faro scans 203839, 203840, 203843, 203844), lidar_reference. Method in `bench/calibrate.py`.

| tier | group | samples | rooms | coverage at scale 1 | fitted scale | in-sample | leave-one-room-out |
|---|---|---|---|---|---|---|---|
| lidar | length | 20 | 4 | 0.20 | 5.07 | 0.95 | 0.85 (n 20) |
| lidar | area | 4 | 4 | | not fitted: fewer than 5 samples | | |
| video | length | 40 | 4 | 0.23 | 6.931 | 0.93 | 0.93 (n 40) |
| video | area | 4 | 4 | | not fitted: fewer than 5 samples | | |
| photo | length | 83 | 15 | 0.36 | 5.225 | 0.92 | 0.92 (n 83) |
| photo | area | 15 | 15 | 0.27 | 10.927 | 1.00 | 0.93 (n 15) |

Reference 90 % half-widths after fitting (must widen from LiDAR to photo):

| measurement | lidar | video | photo |
|---|---|---|---|
| wall_length 3.0 m | ±0.118 | ±0.773 | ±1.362 |
| ceiling_height 2.6 m | ±0.100 | ±0.694 | ±1.200 |
| opening_width 0.9 m | ±0.150 | ±0.491 | ±0.646 |
| opening_height 2.0 m | ±0.354 | ±0.954 | ±1.266 |
| area 10.0 m2 | ±0.990 | ±5.002 | ±18.851 |

Adjustments:

- lidar area: too few samples, uses the tier's length scale 5.07
- video area: too few samples, uses the tier's length scale 6.931
