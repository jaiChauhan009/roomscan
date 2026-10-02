# Round 4 before run

Regenerate: `python fixloop/round4/run_laser.py before`

## Laser rooms: ceiling height (bench/evaluate.py)

| capture | truth (m) | ours (m) | error (cm) | <= 1.5 cm |
|---|---|---|---|---|
| arkit_42446532 | 2.337 | 2.2822 | -5.48 | no |
| arkit_44358446 | 2.387 | 2.3699 | -1.71 | no |
| arkit_47332890 | 2.705 | 2.683 | -2.20 | no |
| arkit_47331988 | 2.614 | 2.6038 | -1.02 | yes |

Rooms within the gate: 1 of 4

## Guards: room heights (extract_layout, no truth)

| capture | room | mask m2 | source | height (m) |
|---|---|---|---|---|
| apt_lidar_a | room_1 | 7.43 | ceiling_plane | 3.0636 |
| apt_lidar_a | room_2 | 7.73 | ceiling_plane | 3.0742 |
| apt_lidar_a | room_3 | 2.73 | ceiling_plane | 3.0796 |
| apt_lidar_a | room_4 | 9.1 | ceiling_plane | 2.4433 |
| apt_lidar_a | room_5 | 5.96 | ceiling_plane | 2.4629 |
| apt_lidar_a | room_6 | 1.19 | ceiling_plane | 2.4633 |
| apt_lidar_a | room_7 | 2.74 | ceiling_plane | 2.375 |
| apt_lidar_a | room_8 | 1.13 | ceiling_plane | 2.4497 |
| apt_lidar_a | room_9 | 4.17 | ceiling_plane | 2.3464 |
| apt_lidar_b | room_1 | 4.99 | wall_top | 2.0544 |
| apt_lidar_b | room_2 | 8.45 | wall_top | 1.9386 |
| apt_lidar_b | room_3 | 9.64 | wall_top | 2.2025 |
| apt_lidar_b | room_4 | 3.8 | wall_top | 1.8567 |
| apt_lidar_b | room_5 | 4.46 | wall_top | 1.508 |
| apt_lidar_b | room_6 | 5.34 | wall_top | 2.0265 |
| apt_lidar_b | room_7 | 3.08 | wall_top | 1.827 |
| room_lidar | room_1 | 6.33 | wall_top | 1.7565 |
| room_lidar | room_2 | 3.23 | wall_top | 1.7128 |
| own_lidar_1 | room_1 | 15.46 | ceiling_plane | 2.3989 |
| own_lidar_1 | room_2 | 13.95 | ceiling_plane | 2.4017 |
| own_lidar_1 | room_3 | 6.82 | ceiling_plane | 2.5237 |
| own_lidar_1 | room_4 | 11.87 | ceiling_plane | 2.5692 |
| own_lidar_2 | room_1 | 13.12 | ceiling_plane | 2.5977 |
| own_lidar_2 | room_2 | 1.6 | ceiling_plane | 2.5876 |
| own_lidar_2 | room_3 | 11.43 | ceiling_plane | 2.6186 |
| own_lidar_2 | room_4 | 2.13 | ceiling_plane | 2.3753 |
| own_lidar_3 | room_1 | 11.58 | ceiling_plane | 2.6245 |
