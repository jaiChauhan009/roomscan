# Fix declaration, round 3

Written and committed before the fix. Before run: the benchmark in `bench/reports/` (run at
`b8a95ff`; no geometry source has changed since), tagged `fixloop3-before` with this file.
Evidence: `python fixloop/round3/evidence_outline.py <section>`, output in `evidence_output.txt`.

## 1. Worst-performing gate

**Repeatability, LiDAR tier.** Scan A vs scan B of one flat: rooms 9 vs 7, **0 rooms paired**,
so not one wall is compared. LiDAR is the walk-in tier and this is its only gate on the flat.

## 2. Root cause and evidence

Round 2 explained part of this: B never looked above about 1.6 m, so it divides the flat
differently (7 rooms, not 9). The rest is the pipeline's fault.

**Rooms that are plainly the same room do not pair, because their wall counts differ**
(`pairing`). The gate pairs rooms by their sequences of walls of 0.25 m or more, and each
extra wall costs 0.3.

| A | B | areas m² | walls | cost | of which count | threshold |
|---|---|---|---|---|---|---|
| room_1 | room_3 | 9.36 / 9.49 | 6 / 5 | 0.71 | 0.3 | 0.54 |
| room_9 | room_5 | 5.44 / 5.31 | 6 / 8 | 0.88 | 0.6 | 0.46 |
| room_7 | room_7 | 3.22 / 3.44 | 9 / 7 | 0.74 | 0.6 | 0.41 |

Without the count penalty each of these pairs is under its threshold. The extra walls are
furniture notches. The room mask stops at anything taller than about 1.1 m, so a wardrobe
against a wall becomes a notch: wall, step in, front, step out, wall. A has 46 walls under
0.5 m and B has 41, out of 110 and 89.

Snapping was meant to remove notches. Each outline edge searches up to 0.9 m outward for
the wall plane. It fails in two ways.

**(a) Steps that snapping flattens are kept, and if they cross, the room loses every wall**
(`fallback`). When the front of a notch and the walls beside it all snap onto the same
plane, the steps between them shrink to nothing but stay in the outline. If the snaps
overshoot, a step turns round and the outline crosses itself. The code then throws away
every snap in the room and draws it from the raw raster contour. Every wall of that room
then has no evidence.

| room | segments | valid before snapping | after | drawn as |
|---|---|---|---|---|
| A room_4 | 29 | yes | no (one 10 cm snap alone breaks it) | raw contour: 29 walls, 0 with evidence |
| A room_5 | 23 | yes | no (snaps of 0.5-0.87 m together) | raw contour: 24 walls, 0 with evidence |
| B room_1 | 18 | yes | no (several together) | raw contour: 20 walls, 0 with evidence |
| B room_3 | 9 | yes | no (one 11 cm snap alone breaks it) | raw contour: 9 walls, 0 with evidence |

Both video rooms are drawn from the raw contour too (42 and 22 walls, none with evidence).
On a synthetic 4 × 3 m room with two wardrobes (`synthetic`), snapping finds the wall behind
both. Area is right (12.00 m²), but the outline keeps two 0.00 m walls: 8 walls, not 4.

**(b) A furniture front beats the wall visible above it** (`synthetic`, `planes`). Among
candidate planes, snapping takes the best covered and breaks ties by nearness. A wardrobe
front and the wall above it can cover the edge equally. On the synthetic room with one
1.8 m wardrobe, the front (points 0.37-1.73 m high) beats the wall 0.6 m behind it (points
1.85-2.65 m, same coverage). The room comes out 8 walls and 11.28 m², not 4 walls and
12.00 m². On scan A, 3 of 67 snapped edges do this. Each stops on a plane that ends below
1.7 m, while a plane as well covered, 0.14-0.76 m further out, rises to within 0.3 m of the
ceiling.

## 3. Fix and prediction

All in `layout.py`, after the existing snapping; the room split does not change.

1. **Settle the outline after snapping.** Merge parallel walls that ended up within 6 cm of
   one plane, and drop the step between them. Keep the outline a simple polygon in which no
   wall has turned round. Where snaps conflict, undo the snap whose undoing resolves the
   conflict (the least covered if several do), not every snap in the room. Then measure
   each final wall's evidence where it ends up (±3 cm). The raw-contour fallback stays only
   as a last resort.
2. **A wall rises to the ceiling.** Among candidate planes, one whose points reach within
   0.3 m of the room's ceiling beats one that stops lower, if it covers at least 80 % as much
   of the edge. This only applies where the ceiling was seen: in scan B nothing changes
   (B never looked up).

| after the fix | before | predicted |
|---|---|---|
| rooms drawn from the raw contour: A / B / video | 2 / 2 / 2 | 0 / 0 / 0 |
| walls without evidence: A / B | 70 of 110 / 50 of 89 | ≤ 45 / ≤ 35 |
| walls in total: A / B | 110 / 89 | fewer in both |
| synthetic furnished rooms (4 cases, `synthetic`) | 4-10 walls, 11.28-12.00 m² | 4 walls each, 12.00 ± 0.05 m², every wall within 2 cm |
| rooms A / B | 9 / 7 | 9 / 7 |
| room overlap | 0 | 0 |
| footprint A / B | 49.25 / 44.11 m² | each grows (rooms reach the walls behind furniture), by less than 6 % |
| rooms paired | 0 | 1-4 (low confidence) |
| **gate** | **fail** | **fail** |

**Why pairing is a weak prediction, and the gate will still fail.** The settle step removes
a notch only where the wall behind it was found. Most of B's notches are fronts with no
wall seen above them, so they stay. Rule 2 cannot act in B, while it can in A, so for some
pairs the counts may diverge, not converge. Paired walls then face the 1 cm / 0.5 %
threshold. Round 2 measured differences of centimetres between the two scans, so even
perfect pairing fails the gate.

Other rows move too: video is scored against B's LiDAR output and photos against A's. No
direction is predicted for either.
