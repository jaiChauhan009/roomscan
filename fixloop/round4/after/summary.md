# Round 4 after run

Regenerate: `python fixloop/round4/run_laser.py after`

## Laser rooms: ceiling height (bench/evaluate.py)

| capture | truth (m) | ours (m) | error (cm) | <= 1.5 cm |
|---|---|---|---|---|
| arkit_42446532 | 2.337 | 2.3077 | -2.93 | no |
| arkit_44358446 | 2.387 | 2.3789 | -0.81 | yes |
| arkit_47332890 | 2.705 | 2.6884 | -1.66 | no |
| arkit_47331988 | 2.614 | 2.6048 | -0.92 | yes |

Rooms within the gate: 2 of 4

## Guards: room heights (extract_layout, no truth)

| capture | room | mask m2 | source | height (m) |
|---|---|---|---|---|
| apt_lidar_a | room_1 | 7.43 | ceiling_plane | 3.0569 |
| apt_lidar_a | room_2 | 7.73 | ceiling_plane | 3.0721 |
| apt_lidar_a | room_3 | 2.73 | ceiling_plane | 3.0794 |
| apt_lidar_a | room_4 | 9.1 | ceiling_plane | 2.4497 |
| apt_lidar_a | room_5 | 5.96 | ceiling_plane | 2.4557 |
| apt_lidar_a | room_6 | 1.19 | ceiling_plane | 2.4615 |
| apt_lidar_a | room_7 | 2.74 | ceiling_plane | 2.3749 |
| apt_lidar_a | room_8 | 1.13 | ceiling_plane | 2.4479 |
| apt_lidar_a | room_9 | 4.17 | ceiling_plane | 2.3462 |
| apt_lidar_b | room_1 | 4.99 | wall_top | 2.0544 |
| apt_lidar_b | room_2 | 8.45 | wall_top | 1.9411 |
| apt_lidar_b | room_3 | 9.64 | wall_top | 2.203 |
| apt_lidar_b | room_4 | 3.8 | wall_top | 1.8396 |
| apt_lidar_b | room_5 | 4.46 | wall_top | 1.5068 |
| apt_lidar_b | room_6 | 5.34 | wall_top | 2.0247 |
| apt_lidar_b | room_7 | 3.08 | wall_top | 1.8276 |
| room_lidar | room_1 | 6.33 | wall_top | 1.7559 |
| room_lidar | room_2 | 3.23 | wall_top | 1.7127 |
| own_lidar_1 | room_1 | 15.46 | ceiling_plane | 2.3882 |
| own_lidar_1 | room_2 | 13.95 | ceiling_plane | 2.4089 |
| own_lidar_1 | room_3 | 6.82 | ceiling_plane | 2.5324 |
| own_lidar_1 | room_4 | 11.87 | ceiling_plane | 2.5722 |
| own_lidar_2 | room_1 | 13.12 | ceiling_plane | 2.5969 |
| own_lidar_2 | room_2 | 1.6 | ceiling_plane | 2.5881 |
| own_lidar_2 | room_3 | 11.43 | ceiling_plane | 2.633 |
| own_lidar_2 | room_4 | 2.13 | ceiling_plane | 2.3627 |
| own_lidar_3 | room_1 | 11.58 | ceiling_plane | 2.6252 |
