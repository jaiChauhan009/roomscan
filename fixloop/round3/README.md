# Fix loop, round 3

| Step | Where |
|---|---|
| Declaration (committed before the fix) | [declaration.md](declaration.md), commit `929a035`, tag `fixloop3-before` |
| Root-cause evidence | [evidence_outline.py](evidence_outline.py), output in [evidence_output.txt](evidence_output.txt), run on the tag |
| Before run | `bench/reports/` as committed at the tag (run at `b8a95ff`; no geometry source changed between them) |
| Fix | commit `270175e`, [fix.diff](fix.diff) (`layout.py` only) |
| After run | [after/benchmark.md](after/benchmark.md), run at `270175e` |
| Before / after table | `python fixloop/round3/compare.py` |

## Regenerate both runs

```bash
git checkout fixloop3-before && uv run python bench/run_all.py --out fixloop/round3/before
git checkout 270175e         && uv run python bench/run_all.py --out fixloop/round3/after
uv run python fixloop/round3/compare.py --before fixloop/round3/before
uv run python fixloop/round3/evidence_outline.py synthetic   # furnished rooms, on the checked-out code
```

## The fix in one paragraph

Wall snapping moves each edge of a room's raster outline onto the wall plane behind it.
Furniture taller than about 1.1 m stops the raster, so a wardrobe becomes a notch: wall,
step, front, step, wall. Two things went wrong after snapping. When the front snapped back
onto the wall, the steps stayed as walls of nearly zero length. When a snap overshot, the
outline crossed itself, and the code threw away every snap in the room and drew it from the
raw contour. That happened in 4 of the 16 LiDAR rooms and both video rooms, and none of
their walls had evidence. Now the outline is settled after snapping. Parallel walls within
6 cm of one plane merge. A snap that makes the outline cross is undone on its own (the least
covered first), not every snap in the room. Each final wall's evidence is measured where it
ends up. Separately, a candidate plane that rises to within 0.3 m of the room's ceiling now
beats a lower one that covers at least 80 % as much of the edge, so a wardrobe front no
longer wins over the wall seen above it. That needs the ceiling in view, so scan B is
untouched by it.

## Result

From `python fixloop/round3/compare.py` (before: the tag's `bench/reports/`; after: `after/`).

| | before | after | declared prediction | met? |
|---|---|---|---|---|
| rooms drawn from the raw contour, A / B / video | 2 / 2 / 2 | 0 / 0 / 0 | 0 / 0 / 0 | yes |
| walls without evidence, A / B | 70 of 110 / 50 of 89 | 41 of 87 / 39 of 81 | ≤ 45 / ≤ 35 | A yes, B no |
| walls in total, A / B | 110 / 89 | 87 / 81 | fewer in both | yes |
| synthetic furnished rooms (4 cases) | 4-10 walls, 11.28-12.00 m² | 4 walls each, 12.00 m², every wall exact to 1 cm | 4 walls, 12.00 ± 0.05 m², walls within 2 cm | yes |
| rooms A / B | 9 / 7 | 9 / 7 | 9 / 7 | yes |
| room overlap | 0 | 0 | 0 | yes |
| footprint A / B | 49.25 / 44.11 m² | 55.29 / 45.96 m² | each grows, by less than 6 % | grows: yes; A +12.3 %: no; B +4.2 %: yes |
| rooms paired | 0 | 1 (room_7 with room_7; 7 walls, median difference 27 cm) | 1-4 | yes |
| **repeatability gate** | **fail** | **fail** | **fail** | yes |

Not predicted, and worse: **the video tier's calibration gate went from pass (0.94) to fail
(0.57)**, and its wall pass fraction from 0.056 to 0.015. Its two rooms leave the raw
contour, but in a monocular-depth cloud the snapped outline has 79 walls, 73 of them
without evidence, against 64 before. A snapped wall carries a plane-fit sigma of
millimetres, while the raw contour gave every wall 5 cm, so the video interval scale
fitted on raw-contour walls now covers 57 %. The scale is refitted after the round, like
any recalibration (`bench/calibrate.py`). Photo: footprint +137 % → +111 % and walls
2.09 → 1.78 m against a LiDAR reference that changed; calibration still passes (0.93).

The after run's wall times are not comparable with the before run: the machine was shared
with other jobs (scan A 485 s against 212 s, every stage 2-3x slower). The final benchmark
in `bench/reports/` has clean timings.

## Post-mortem

**The footprint prediction for A was off by half.** The settle step alone grew A by 9 %
(49.25 → 53.66 m²), all of it in the two raw-contour rooms: their outlines had followed
furniture and cut diagonally through room_5, and they were far more under-measured than
I assumed. The reaches-the-ceiling rule added 1.6 m², about half of it room_2's notch. B
grew less because B never looked up: the rule needs the ceiling in view, and B's
furniture notches stay. So the fix widens the A-B footprint gap (−10.4 % → −16.9 %). A
reports more of the real floor, and B cannot. Whether A is now right needs ground truth
for the flat, which does not exist.

**A mistake caught before the commit.** My first version also rejected any outline in
which a wall had turned round (runs opposite to its raw direction). On B's room_6 a 0.83 m
snap reversed a short step into a perfectly straight wall line, which the old code had
accepted. The check undid the snap and cut the room from 6.78 to 5.91 m². A turned-round
step is not wrong in itself (it can be a real niche); only crossings are. The committed
version checks for a simple, counter-clockwise polygon only.

**What the fix does not reach.** Most of B's notches have no wall seen above them. On the
laser-truth rooms added after this round (ARKitScenes), furniture also leaves diagonal
cuts and can split a room at a gap between two pieces. That is a segmentation problem,
upstream of snapping. A post-round fix (`0a57664`) let the ceiling rule see a wall behind
dense furniture (the six highest histogram bins were all the furniture's own peak). It
does not change the split.
