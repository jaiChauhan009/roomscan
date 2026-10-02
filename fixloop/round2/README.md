# Fix loop, round 2

| Step | Where |
|---|---|
| Declaration (committed before the fix) | [declaration.md](declaration.md), commit `482b4fb` |
| Root-cause evidence | [evidence_drift.py](evidence_drift.py), output in [evidence_output.txt](evidence_output.txt), run on tag `fixloop2-before` |
| Before run | [../after/benchmark.md](../after/benchmark.md): round 1's after run. The source did not change between it and tag `fixloop2-before` (`026cea1`) |
| Refactor, no behaviour change | commit `b39f339`, [refactor.diff](refactor.diff) |
| Fix | commit `cb55b8b`, [fix.diff](fix.diff) |
| Cache key fix, needed to regenerate both runs on one machine | commit `2f4ed8b` |
| After run | [after/benchmark.md](after/benchmark.md); bedroom walls in [walls_after.txt](walls_after.txt) |

## Regenerate both runs

```bash
git checkout fixloop2-before && uv run python bench/run_all.py --out fixloop/before2
git checkout 2f4ed8b         && uv run python bench/run_all.py --out fixloop/round2/after
uv run python fixloop/round2/evidence_drift.py walls current   # bedroom walls, on the checked-out code
```

Before `2f4ed8b` the fused-cloud cache was keyed without the poses, so a run after a drift
change reused the old cloud. The two runs now write different cache entries in either order.

## The fix in one paragraph

The pose graph trusted ARKit's odometry at 1 cm and 0.29° per 3 s step; the error measured
on these scans is p90 3.0-3.7 cm and rms 0.37-0.47°. With odometry that stiff, Open3D
prunes every loop closure that asks for a large correction, so the large drift was the drift
never corrected. The odometry is now weighted by the measured error (2 cm, 0.4°), and the
pruning distance is 1.4 × the submap voxel (5.6 cm, Open3D's convention), which keeps
Open3D's pruning tolerance where it was. Second part: the unsealed-room fallback in
`layout.py` reads the gap-closed walls the sealing step uses, so a wall seen only above a
bed head no longer cuts the room.

## Result

| | before | after | declared prediction | met? |
|---|---|---|---|---|
| B: max submap shift | 0.03 m | 0.52 m | ≥ 0.4 m | yes |
| B: loop closures kept | 1 of 7 | 6 of 7 | | |
| wall crispness A / B | 8.36 / 9.58 | 8.84 / 12.26 | ≥ 8.6 / ≥ 11.5 | yes |
| footprint B vs A, sum of rooms (as benchmarked) | −17.9 % | −0.9 % | within ±6 % | yes |
| footprint B vs A, union of rooms | −16.6 % | −6.3 % | (not declared) | would just miss |
| rooms A / B | 10 / 7 | 9 / 7 | 9-10 / 7-8 | yes |
| rooms paired | 1 | 0 | 0-2 | yes |
| bedroom walls B − A: left / right / top | 0 / −26 / −32.5 cm | +2.0 / −7.0 / −1.0 cm | ±3 / ±10 / ±3 cm | yes |
| B wall points within 15 cm of A's | 92.3 % | 97.3 % | | |
| **repeatability gate** | **fail** | **fail** | fail | yes |

Every declared number came true. The gate still fails, for the reason the declaration gave.

## Post-mortem

**What the fix did.** B's drift correction now corrects: its start-up segment moves by up to
0.52 m, and the two scans agree as point clouds (97.3 % of B's wall points within 15 cm of
A's, median 0.69 cm). Walls are sharper with the correction on than off, for the first time
(B 10.02 off vs 12.26 on; it was 9.58 on). The bedroom that defined the gap now matches A to
within 2 cm on two walls and 7 cm on the third.

**Why the right wall keeps 7 cm.** B saw it only between 3 and 9 s, while ARKit was still
moving it by 18 cm (`walls shipped`). A submap is 3 s and moves as one rigid piece, so the
slide inside it stays: after the fix the wall still spreads over 9 cm across those frames
(p10-p90, `walls_after.txt`).

**Why the gate still fails.** Pairing needs both scans to cut the flat into the same rooms,
and they do not: B saw no door heads and no ceilings, so it merges spaces A keeps apart.
After the fix B's bedroom is 10.8 m² against A's 9.8 m² and has a different outline, so the
two are not paired and no wall of it is compared. That is a capture problem (B skipped the
protocol's upward sweep) that the pipeline cannot recover.

**What the declared footprint number hides.** The benchmark sums room areas, and LiDAR room
polygons overlap: wall snapping (`_refine_walls`) moves an edge up to 0.9 m outward to the
best-supported plane and can push it into the next room. This was already so before the fix
(A 1.2 m² double-counted, B 0.3 m²), but the fix gave B larger rooms and more overlap
(3.75 m²). By the union of rooms the footprint gap is −16.6 % → −6.3 %, not −0.9 %. The
declared metric met its prediction; the better metric would have missed it by 0.3 points.

**Side effects not predicted.**
- `room_lidar`: 7.26 → 11.44 m² (sum), from the layout part alone (the drift part leaves it
  at 2.45 + 4.81 m²). Its big room now extends about 1 m further, across a line of wall
  evidence the old rule took as the room's edge. This capture has no ground truth, so
  better or worse is unknown; its two polygons now overlap by 0.58 m².
- `room_lidar` wall crispness 30.9 → 29.4: the short capture is slightly less sharp with the
  looser odometry.
- Video tier (scored against B's LiDAR output): median wall error 32 → 15 cm, interval
  coverage 50 → 77 %, because the reference changed; the video output itself did not.

## Next

1. Room polygons must not overlap: limit wall snapping to the room's own side of its
   neighbours, and report the footprint as the union of rooms.
2. A repeat capture of one room that follows the protocol (upward sweep included). Only that
   tests the gate as the brief defines it.
