# Fix loop, round 4: ceiling height on the laser rooms

| Step | Where |
|---|---|
| Declaration (committed before the fix) | [declaration.md](declaration.md), commit `7f6ac11` (message `fixloop4-before`) |
| Root-cause evidence | [evidence_ceiling.py](evidence_ceiling.py), output in [evidence_output.txt](evidence_output.txt), run on `7f6ac11` |
| Before run | [before/summary.md](before/summary.md) (`run_laser.py before` on `7f6ac11`; matches `bench/reports/`) |
| Fix | [fix.diff](fix.diff): `planes.area_level`, used by `layout._per_room_levels`; two unit tests in `tests/test_geometry.py` |
| After run | [after/summary.md](after/summary.md) (`run_laser.py after` on the fix commit) |

## Regenerate

```bash
export ROOMSCAN_DATA=<data>          # needs the four ARKitScenes captures and the own/ scans
git checkout 7f6ac11          && python fixloop/round4/run_laser.py before
git checkout <fix commit>     && python fixloop/round4/run_laser.py after
python fixloop/round4/evidence_ceiling.py arkit_42446532 arkit_44358446 arkit_47332890 arkit_47331988 \
    apt_lidar_a apt_lidar_b room_lidar own_lidar_1 own_lidar_2 own_lidar_3   # on 7f6ac11
```

`run_laser.py` runs the four laser rooms end to end (layout + export, damage stage off, the
pipeline's cloud cache) and scores them with `bench/evaluate.py`; the guards only run
`extract_layout`. It never runs `bench/run_all.py`. The full benchmark is not rerun in this round.

## The fix in one paragraph

A room's floor and ceiling were the peak of a histogram of every point, so a patch counted as
often as the camera looked at it. A fused LiDAR floor or ceiling is 1-3 cm thick and uneven,
and the peak landed on the most-looked-at patch: in arkit_42446532, 76 % of the floor lay
below the chosen floor and 52 % of the ceiling above the chosen ceiling. Now, after the peak,
each room level is the median over 25 cm patches of the patch medians (points within 6 cm of
the peak; fewer than 8 patches keeps the peak). The laser truth is a plane over the whole
surface, which also weights by area. Only the per-room levels change; the whole-capture floor
and ceiling that cut the plan do not.

## Result

| capture | before | after | declared prediction (±0.3 cm) | met? |
|---|---|---|---|---|
| arkit_42446532 | -5.48 cm | -2.93 cm | -2.95 cm | yes |
| arkit_44358446 | -1.71 cm | **-0.81 cm (pass)** | -1.03 cm (pass) | pass: yes; number 0.22 cm off, inside the band |
| arkit_47332890 | -2.20 cm | -1.66 cm | -1.70 cm | yes (fails by 0.16 cm) |
| arkit_47331988 | -1.02 cm | -0.92 cm | -0.92 cm | yes |
| rooms within 1.5 cm | 1 / 4 | **2 / 4** | 2 / 4 | yes |
| mean error | -2.60 cm | -1.58 cm | | |
| **gate** | **fail** | **fail** | **fail** | yes |

Guards (room heights, before → after, cm moved):

| capture | declared | measured | met? |
|---|---|---|---|
| apt_lidar_a, 9 rooms | within 0.6 cm | -0.72 to +0.64 | room_5 0.12 cm over the stated bound |
| apt_lidar_b, room_lidar (wall-top lower bounds) | none | 8 of 9 within 0.25; **apt_lidar_b room_4 -1.71** | **no** (see post-mortem) |
| own_lidar_1 | within 1.3 cm | -1.07 to +0.87 | yes |
| own_lidar_2 | room_3 +1.7, room_4 -1.3, rest within 0.1 | +1.44, -1.26, +0.05, -0.08 | yes |
| own_lidar_3 | +0.25 | +0.07 | yes |

Unit tests: a synthetic room whose ceiling is 2 cm higher over 60 % of its area while the camera
dwelt five times as long on the other 40 % (peak 2.70 m, now 2.72 m), and an even room
(unchanged, 2.70 m). Full suite: 218 passed, 2 skipped.

## Post-mortem

**The prediction held, the gate did not, and that was declared.** Every laser room moved the
way and about as far as the evidence script said (largest miss 0.22 cm). Two rooms of four now
pass instead of one, and arkit_42446532 lost about half its error. All four rooms still read low
(-0.8 to -2.9 cm, -0.3 to -1.3 %). The walls the benchmark matched to the laser are short by the
same fraction (median about -1 %). That points to a scale bias of the converted ARKitScenes
cloud (depth or intrinsics), which no level estimator can remove. The next round should test it
directly: per frame, compare ARKit depth to the laser at the same pixels
(`scripts/arkitscenes_to_stray.py` already has the poses), then decide whether it is the data,
the converter, or ARKit LiDAR in general. Before that, a scale factor fitted on these four rooms
would only fit the test set.

**A guard the declaration got wrong.** I said rooms whose ceiling was never seen (apt_lidar_b,
room_lidar) would not move, because their height comes from the top of the walls. But that
height is measured from the room's floor, and the floor is now area-weighted too. On
apt_lidar_b room_4 the floor rose 1.7 cm and its wall-top height fell by the same amount. That
number is a lower bound ("ceiling not observed"), not a measurement, so no gate reads it. The
other eight moved less than 0.25 cm. apt_lidar_a room_5 moved 0.72 cm against a stated 0.6 cm;
the evidence script and the pipeline place the 25 cm cells on different grids.

**Moves above 1 cm on our own scans are the same effect, not noise.** own_lidar_2 room_3's
ceiling rises 1.4 cm because 72 % of its ceiling patches lie more than 1 cm above the old peak.
own_lidar_2 room_4 (2 m²) loses 1.3 cm because 81 % of its floor patches lie above the old
peak. own_lidar_1 room_1 loses 1.1 cm the same way. None of these has a truth, so none of them
can be called better, only more representative of the whole surface.

**Risk.** In a room where more than half the floor is covered by a rug or a low platform within
6 cm of the floor, the floor is now the rug's top. The peak had the same failure whenever the
rug was the most-looked-at patch. The laser truth takes the lowest strong peak, so such a room
would read low by the rug's thickness.
