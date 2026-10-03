# Interval calibration

From `bench/reports/benchmark.json`, run untagged; truth source: laser_scanner (ARKitScenes visit 422386, Faro scans 173436, 173438, 173439, 173440), laser_scanner (ARKitScenes visit 460419, Faro scans 176333, 176336), laser_scanner (ARKitScenes visit 469272, Faro scans 189578, 189581, 189582), laser_scanner (ARKitScenes visit 470811, Faro scans 203839, 203840, 203843, 203844), lidar_reference. Method in `bench/calibrate.py`.

| tier | group | samples | rooms | coverage at scale 1 | fitted scale | in-sample | leave-one-room-out |
|---|---|---|---|---|---|---|---|
| lidar | length | 20 | 4 | 0.25 | 2.909 | 0.95 | 0.95 (n 20) |
| lidar | area | 4 | 4 | | not fitted: fewer than 5 samples | | |
| video | length | 40 | 4 | 0.25 | 6.931 | 0.93 | 0.93 (n 40) |
| video | area | 4 | 4 | | not fitted: fewer than 5 samples | | |
| photo | length | 82 | 15 | 0.35 | 5.225 | 0.92 | 0.92 (n 82) |
| photo | area | 15 | 15 | 0.27 | 10.927 | 1.00 | 0.93 (n 15) |

Reference 90 % half-widths after fitting (must widen from LiDAR to photo):

| measurement | lidar | video | photo |
|---|---|---|---|
| wall_length 3.0 m | ±0.068 | ±0.773 | ±1.362 |
| ceiling_height 2.6 m | ±0.057 | ±0.694 | ±1.200 |
| opening_width 0.9 m | ±0.086 | ±0.491 | ±0.646 |
| opening_height 2.0 m | ±0.203 | ±0.954 | ±1.266 |
| area 10.0 m2 | ±0.568 | ±5.002 | ±18.851 |

Adjustments:

- lidar area: too few samples, uses the tier's length scale 2.909
- video area: too few samples, uses the tier's length scale 6.931
