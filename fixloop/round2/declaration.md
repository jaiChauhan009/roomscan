# Fix declaration, round 2

Written and committed before the fix. Before run: `fixloop/after/` from round 1; the source
has not changed since (commit `1e8c490`) and is tagged `fixloop2-before`. Evidence:
`python fixloop/round2/evidence_drift.py <section>`, output in `evidence_output.txt`.

## 1. Worst-performing gate

**Repeatability, LiDAR tier**, still. Scan A (`single_scan_with_ceiling`) vs scan B
(`single_scan_floor_only`) of one flat: rooms 10 vs 7, footprint 50.2 vs 41.2 m² (−18 %),
1 room paired, 0 of 4 walls within 1 cm / 0.5 %, median wall difference 24 cm. Largest
disagreement: the big bedroom, 2.73 × 3.07 m in A, 2.41 × 2.54 m in B.

## 2. Root cause and evidence

**Round 1's post-mortem (furniture fronts stand in for walls) is wrong** (`section`). Where
B puts the bedroom's far wall, A has 6 points. A's wall is 43 cm further out (7,311 points).
Both scans see that surface start at the same height, just above the bed (0.74 vs 0.78 m).
A furniture front would appear in A too.

**Hypothesis: scan B's drift is never corrected.**

- **When B saw the walls** (`walls shipped`). B starts and ends in the bedroom. In A's frame,
  the wall B saw late agrees with A; the walls B saw in its first 10 s do not.

  | bedroom wall | B saw it at | A | B | B − A |
  |---|---|---|---|---|
  | left | 105-109 s | 5.395 | 5.395 | 0.000 m |
  | right | 4-10 s | 8.775 | 8.515 | −0.260 m |
  | top | 4-11 s | 3.005 | 2.680 | −0.325 m |

- **The correction does nothing on B** (`closures shipped`). It moves B's submaps 2.8 cm at
  most. 7 loop closures are accepted and 1 is kept. Closure 3 ↔ 33 (9-12 s and 100-103 s,
  the same corner of the flat seen 90 s apart, RMSE 1.8 cm) asks for 0.47 m and is pruned
  as an outlier.
- **Why it is pruned.** Open3D weights each closure by its error *before the first step* and
  prunes it if that error does not shrink below a tolerance. Closures converge in a cascade:
  small ones pull first and shrink the rest. Odometry edges assume ARKit errs by 1 cm and
  0.29° per 3 s step. Measured on these scans (`odometry`, ICP between consecutive
  submaps): p90 3.0-3.7 cm, rms 0.37-0.47°. With odometry that stiff the graph cannot bend
  enough for the cascade to start once drift is large.
- **The threshold, on synthetic data** (`synthetic`, B's closure pattern, exact closures).

  | end-to-end drift | shipped weights | measured weights |
  |---|---|---|
  | 0.25 m | 6/6 kept, ≤ 1.8 cm off | 6/6 kept, ≤ 0.5 cm off |
  | 0.35 m | 5/6 kept, 13 cm off | 6/6 kept, ≤ 0.8 cm off |
  | 0.50 m | **1/6 kept, nothing moves** | 6/6 kept, ≤ 1.1 cm off |
  | 0.50 m + one wrong closure | 1/7 kept, nothing moves | wrong one pruned, ≤ 1.1 cm off |

  The 0.5 m row is B. The "loop residual 16.9 → 2.5 cm" reported for B came from pruning.
  Walls are *less* crisp with the shipped correction on than off (A 8.36 vs 8.43, B 9.58 vs 10.02).
- **On the real scans** (`weights`, `walls measured`), with odometry weighted by the measured
  error: B's submaps move up to 0.52 m, 6 of 7 closures kept, crispness A 8.36 → 8.84,
  B 9.58 → 12.26. B's wall points within 15 cm of A's: 92.3 → 97.3 %, median distance
  1.25 → 0.69 cm. Bedroom walls B − A: left +2.0, right −7.0, top −1.0 cm. The right wall
  keeps 7 cm because it slid 18 cm *within* one 3 s submap, and a submap moves as one piece.
- **Second cause, exposed by the experiment.** With B corrected, the layout cuts B's bedroom
  in two. B saw no door heads, so the bedroom joins the rest of the flat through its door
  and the unsealed-region fallback applies: a cell is kept only if a wall lies in all four
  directions. The fallback reads the raw wall raster, which has gaps where B saw the wall
  only above the bed head; every row through a gap cuts the room. The sealing step
  already closes gaps under 50 cm; the fallback does not use the closed walls.

## 3. Fix and prediction

1. `drift.py`: odometry edges weighted by the measured ARKit error (2 cm, 0.4° per step).
   Open3D's pruning tolerance is max_correspondence_distance² × the mean information of
   all edges, mostly odometry, so 4× less odometry information alone would prune 2× harder
   (`tolerance`: B then keeps 1 of 7 and moves 3.8 cm). The distance goes from 3 cm to
   5.6 cm (1.4 × the 4 cm submap voxel, Open3D's own convention), which keeps the tolerance
   where it was (7.8 vs 9.0 on odometry edges). Candidates, ICP, acceptance rules and the
   0.5 m cap do not change.
2. `layout.py`: the four-direction fallback uses the gap-closed walls.

| after the fix | before | predicted |
|---|---|---|
| B max submap shift | 0.03 m | ≥ 0.4 m |
| wall crispness A / B | 8.36 / 9.58 | ≥ 8.6 / ≥ 11.5 |
| footprint B vs A | −18 % | within ±6 % |
| rooms A / B | 10 / 7 | 9-10 / 7-8 |
| rooms paired | 1 | 0-2 |
| **gate** | **fail** | **fail** |
| bedroom walls B − A (`walls current`) | 0 / −26 / −32.5 cm | left, top within ±3 cm; right within ±10 cm |

The first six rows come from `bench/run_all.py`, the last from the evidence script run on
the shipped code.

**Why rooms paired will not rise, and the gate will still fail.** Pairing needs both scans
to divide the flat the same way. B never looked above ~1.6 m: no door heads, no ceilings.
The room split uses both: lintels close doorways, and a ceiling step separates two spaces
(A's 3.07 m room from the 2.45 m area beside it). B merges what A separates. That is B
skipping the protocol's upward sweep, which no pipeline change can supply.

Other rows move too: video is scored against B's LiDAR output, photos against A's. No
direction is predicted for video; photo should barely change, since A barely moves.
