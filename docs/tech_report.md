# Technical report

roomscan: phone capture → measured, stitched floor plan with damage assessment.
Six pages maximum. Structure in [architecture.md](architecture.md), history in
[worklog.md](worklog.md), current numbers in `bench/reports/benchmark.md`, fix-loop runs in `fixloop/`.

## 1. Summary

One command turns a LiDAR scan, a video or per-room photo folders into a JSON plan and a
rendered plan, with a 90 % interval on every number. All three tiers run end to end on a
CPU-only laptop. The LiDAR geometry is precise on synthetic rooms (under 1 mm, furnished
ones included). On four unseen rooms with laser truth (ARKitScenes, Faro scans):
- footprint within 8 % in all four (−7.6, −5.7, +0.2, −2.0 %);
- ceilings within 1-5 cm, read low;
- walls within 3-10 cm in two rooms, but in the other two furniture fragments the outline
  or splits the room, and the wall gates fail.

Two scans of the sample flat agree as point clouds to a median 7 mm but divide the flat
into different rooms, so the repeatability gate fails. The video and photo tiers are far
from their gates on the only data available, which is derived from a LiDAR scan rather
than captured per protocol.

**The main limitation of this report: little ground truth.** Laser truth exists for four
public rooms (LiDAR tier only). The sample flat has none, so its numbers are
self-consistency (repeatability, drift) or comparisons against our own LiDAR output,
labelled as such. A real iPhone 16 Pro scan was taken through the whole chain (Stray
Scanner → laptop → plan in 24 s); its tape measurements are pending.

## 2. Architecture

Every tier is converted to one form: frames with a depth map and a camera pose in a
gravity-aligned world. One back end then does drift correction, fusion, layout, openings,
damage, rules, scope, intervals and export. The photo tier fits each room as a rectangle
first and joins the back end for openings, damage and export.

```
LiDAR ─► Stray loader ──────────┐
video ─► KLT + depth + PnP ─────┼─► posed depth frames ─► drift ─► cloud ─► layout ─► openings ─► damage ─► JSON + plan
photos ► per-room fit + stitch ─┘                                       (rectangles for photos)
```

Rationale: wall, door and damage logic is the hard part; writing it once and tuning it
on the cleanest data costs less than three pipelines.

## 3. Tier design

**Capture route.** Route 2, stock tools (`docs/capture_protocol.md`): Stray Scanner for
LiDAR, the Camera app for video and photos. The protocol is shaped to make the hard
problems easier:

- **All tiers:** every door open, one upward sweep per room.
- **Photo tier:** a fixed doorway standpoint and a look-back photo per room.

**LiDAR.** Stray Scanner depth (256×192), confidence and ARKit poses. The poses map
OpenCV camera axes into the ARKit world; that was measured, not assumed (7 % of points in
one 2 cm floor slice with OpenCV axes, a smeared cloud otherwise).

**Video.** No COLMAP: its Python package is blocked by Application Control on the
development machine. Instead:

- KLT optical flow at 25 fps, keyframes chosen to keep ≥ 320 shared tracks.
- Depth Anything V2 (metric indoor, small) per keyframe.
- Incremental PnP against tracked 3D points.
- Metric scale = median of the model's own scale over all keyframes ÷ 1.137. On the
  sample scans the model's single-frame scale ranges 0.4×-3.4× against LiDAR, while its
  many-frame median is stable at 1.09-1.18×.
- iPhone clips filmed upright are stored landscape with a rotation flag that OpenCV does
  not apply by default; the tier applies it (and turns a calibration file with the
  frames). Before, every upright clip was processed sideways: 0.31 m² instead of 10.1 m²
  on a single-room clip. The benchmark clip is the scan's own video with that flag added.

**Photos.** Stills of white rooms barely match (SIFT: 8 of 703 pairs), so room shape
uses no matching:

- **Per photo:** depth, gravity from normals, wall direction, camera height.
- **Per room:** one standpoint; headings from wall direction plus sweep order; scale tied
  to a shared camera height; rectangle from the outermost wall planes.
- **Stitching:** the look-back photo is located in an earlier room with EfficientLoFTR +
  PnP; otherwise capture order, flagged.

Device matrix: `docs/device_matrix.md`.

## 4. Geometry back end

- **Floor:** best-supported upward plane, not the lowest; robust to ghost points under
  glossy floors (tested).
- **Ceiling:** for the whole capture, the highest supported plane; per room, its own.
  Taking the best-supported plane for the whole capture cut a 3.0 m flat at its 2.4 m
  dropped ceilings (a regression caught by the benchmark, now tested).
- **Walls:** a 2 cm top-down grid. A cell is wall when wall points cover half of the
  observed height band (fix-loop change). Wall above 2.15 m over anything less than a full
  wall is a lintel: a doorway, a low window or a wall mirror (before, a mirror let the room
  leak round its walls, +27 %).
- **Rooms:** wall-sealed regions, split at narrow necks unless open-plan with equal
  ceilings. Outlines are made rectilinear and each edge snapped onto its 3D wall plane, up
  to 0.9 m outward (the raster stops at furniture). A plane that reaches the ceiling beats
  a lower one (a wardrobe front). After snapping, walls on one plane merge and a snap that
  makes the outline cross itself is undone alone (fix-loop round 3).
- **Openings:** per wall, camera rays that pass through versus stop at the plane. Widths
  at 1 cm. Mirrors are rejected when the "through" points, reflected back, land on real
  surfaces of the room.

## 5. Drift handling

The trajectory is cut into 3 s submaps. Revisits are aligned with point-to-plane ICP, and
an Open3D pose graph spreads the correction. Gravity is kept from ARKit and only heading
and position change. Odometry edges are weighted by ARKit's measured error between
consecutive submaps (p90 3.0-3.7 cm, rms 0.37-0.47° per step, by ICP).

| Capture | Drift | Footprint m² | Wall crispness | Largest submap move |
|---|---|---|---|---|
| A | off | 46.9 | 8.43 | n/a |
| A | on | 49.2 | 8.84 | 0.13 m |
| B | off | 42.1 | 10.02 | n/a |
| B | on | 44.1 | 12.26 | 0.52 m |

**Until fix-loop round 2 this correction did almost nothing.** Odometry was weighted at 1 cm
and 0.29° per step, so the pose graph could not bend far enough for a large loop closure
and pruned it. On scan B it moved nothing by more than 2.8 cm and made walls *less* sharp
(9.58 vs 10.02 off), while B's first 10 s sat 26-33 cm off. The loop residual we reported
(16.9 → 2.5 cm) fell because closures were pruned, not because drift was corrected; the
ablation table still prints it, but the submap move and crispness are the evidence. A
synthetic loop with B's closure pattern reproduces the threshold: the
old weights work up to 0.25 m of drift and do nothing at 0.5 m (`tests/test_drift.py`).

A heading snap to wall directions was tried and left off: it blurred walls (crispness
8.4 → 6.5) because 3 s submaps hold too little wall.

## 6. Error budget (LiDAR tier)

| Source | Size | Evidence |
|---|---|---|
| Depth noise on a wall plane | ~1 cm per point, < 2 mm on a fitted plane of 1,000+ points | plane spread in `result.json` |
| Plane fitting on synthetic rooms | < 1 mm | `tests/test_geometry.py` |
| Drift left after correction | 7 mm median between two scans; up to 7 cm on a wall seen only in a scan's first seconds | `fixloop/round2/walls_after.txt` |
| Wall snapping into the next room | fixed in `a88f7ef`; before, up to 0.8 m and 1.2-3.8 m² double-counted per flat | `bench/reports/benchmark.md` (room overlap column) |
| Segmentation differences between captures | whole rooms | repeatability before / after |

The budget is dominated by segmentation, not by sensor noise or drift.

## 7. Calibration analysis

Every measurement uses `sigma = scale · sqrt((k·raw)² + abs² + (rel·value)²)`. `k`,
`abs` and `rel` are priors; `scale` is fitted per tier by `bench/calibrate.py` (split
conformal: the (n+1)·0.9 quantile of |error| / sigma). With the priors alone, 90 %
intervals held the truth 28 % of the time for photos, 77 % for video and, by the agreement
of the two scans, 58 % for LiDAR walls: confident garbage on thin input.

| Tier | Truth used | Samples | Scale | Held-out coverage | 3 m wall, 90 % |
|---|---|---|---|---|---|
| LiDAR | same wall in both scans of the flat (run `80bce56`) | 19 walls, 8 rooms | 2.61 | 0.95 | ±6 cm |
| video | LiDAR reference, refitted after fix-loop round 3 | 28 lengths, 2 rooms | 6.42 | 0.75 | ±0.72 m |
| photo | LiDAR reference | 40 lengths, 7 rooms | 6.62 | 0.90 | ±1.72 m |

**LiDAR is the open question.** Its scale is still the two-scan fit, for three reasons:
- Fitting on the laser rooms gives 99, ±2.3 m on every 3 m wall. Every laser room's outline
  has more walls than the truth, so each truth wall is paired with a fragment. That is a
  segmentation error, not measurement noise.
- The two-scan fit after round 3 gives 17.8 (±0.41 m). Scan A now finds walls behind
  furniture that scan B cannot see, so the scans disagree about which plane is the wall.
- On the laser rooms the shipped LiDAR intervals hold the truth for 10 of 20 values (four
  walls and the ceiling per room): 5 of 5 where the outline is right, 1 of 5 where it is
  fragmented.

So LiDAR intervals cover measurement noise on walls whose plane was found, not outline
errors. The fix is a wall sigma that grows when the wall's position rests on furniture or
on no plane at all, refitted on our own taped room.

Held-out coverage is leave-one-room-out: fitted on the other rooms, scored on the room left
out. In-sample coverage is 90 % or more by construction and is not evidence. Limits, stated
plainly: LiDAR is calibrated for precision only (a bias both scans share is invisible);
video rests on two rooms; photo and video are scored against our own LiDAR, not a laser;
and the photo set does not follow the capture protocol, so protocol photos may well come
out better than these intervals say. Thinner data never gets a narrower interval than
richer data: the script checks a reference measurement per tier and raises the thinner
tier where needed. With laser ground truth the same script refits on it
(`run_all.py --eval-only`, then `calibrate.py --write`). Missing quantities are reported as
missing: an unscanned ceiling is `null` with a lower bound.

## 8. Fix loop

Worst gate: LiDAR repeatability. Two scans of one flat gave 9 vs 6 rooms and 50.1 vs
27.7 m². An experiment ruled out the first hypothesis (missing door lintels) and pointed
to a wall test that demanded evidence above the height scan B ever looked at. The
declaration was committed before the fix.

The fix closed the footprint gap from −45 % to −18 %. The prediction (±10 %, 6+ rooms
paired, ≤ 5 cm walls) was badly wrong. The round-1 post-mortem blamed furniture occluding
the low part of walls (`fixloop/README.md`).

**Round 2** (`fixloop/round2/`) tested that and found it wrong: where B puts the bedroom's far
wall, A has no surface at all. The walls B saw in its first 10 s were 26-33 cm off because
the drift correction never corrected anything (section 5). The declaration was committed
before the fix. Fix: odometry weighted by ARKit's measured error, and the unsealed-room
fallback reading gap-closed walls. Every declared number came true: B moves 0.52 m,
bedroom walls within +2 / −7 / −1 cm of A's, footprint gap −18 % → −0.9 %. The gate still
fails as predicted: B saw no door heads or ceilings, so it divides the flat differently and
no room pairs. The post-mortem also reports what the footprint number hides: room
polygons overlapped, and by the union of rooms the gap was −6.3 %. The overlap was fixed
after the round (`a88f7ef`): wall snapping stops at the next room and the footprint is a
union. With rooms that no longer overlap the gap is −10.4 %.

**Round 3** (`fixloop/round3/`): still repeatability, now 0 rooms paired. Rooms that are
plainly the same did not pair because their wall counts differed, and the extra walls were
furniture notches. Snapping kept the steps of a flattened notch as walls. When a snap made
the outline cross itself, the room fell back to the raw contour with no wall evidence (4 of
16 LiDAR rooms, both video rooms). A wardrobe front also beat the wall seen above it.

The fix settles the outline after snapping, and a plane reaching the ceiling wins.
- **Met:** raw-contour rooms gone, furnished synthetic rooms exact, 1 room paired, gate
  still failing as declared.
- **Missed:** A's footprint grew 12 % (declared < 6 %), and B kept 39 walls without
  evidence (declared ≤ 35).
- **Not predicted:** the video calibration gate failed (0.94 → 0.57), refitted after the
  round.

On the laser rooms that arrived after the round, the same code brought two rooms' median
wall error down to 3 and 10 cm. Before it, their walls were 0.03-1.6 m off.

## 9. Known failure modes

- **Scans without an upward sweep:** no door heads and no ceilings, so rooms merge that a
  full scan keeps apart (fix-loop round 2).
- **Rooms with no wall between them** (open plan, wide openings): the boundary is where
  the watershed split put it, and wall snapping cannot move it (it stops at the next
  room), so that boundary is only as good as the split.
- **Drift at the very start of a scan:** a submap moves as one piece, so tracking that is
  still settling inside one 3 s submap leaves up to 7 cm.
- **Open-plan areas and corridors:** room boundaries differ between captures; rooms split
  or merge.
- **Video tier:** fast sweeps, motion blur and blank walls break tracking into segments;
  only the longest survives (1 of 6 rooms on the sample clip). Scale is ±10-20 %.
- **Photo tier:** fails when the floor is out of view (scale from an assumed phone height)
  or the look-back photo cannot be matched (position along the wall is guessed and flagged).
- **Furnished rooms (LiDAR, laser truth):** furniture taller than about 1.1 m is a barrier
  in the wall grid. Where no wall is seen above it, the outline keeps the notch. A gap
  between two pieces can cut a room in two (ARKitScenes 42446532). Each laser wall is then
  paired with a fragment, so wall gates fail while areas and ceilings are close. Next step:
  outlines from the wall planes that reach the ceiling.
- **Damage recall:** no false positive on the undamaged flat, but a painted 0.5 m stain
  and a 0.7 m crack were both missed (`bench/reports/synth_damage.md`). Each wall patch is
  examined in only one or two of the 64 frames, often at an angle.
- **Closed doors** are measured as wall; the protocol asks for doors open.
- **Mirrors:** rejected as openings by a reflection test, and a mirror's gap in the wall no
  longer unseals the room. Tested on rendered mirrors, not on a real one.
- **Glass:** windows to bright outdoors return no depth and may be missed.
- **Low light:** dim frames are brightened before damage classification, with a stricter
  threshold. Brightening did not help video tracking (measured), so tracking is unchanged.
  Every tier warns on a dim capture.
- **Wet-look surfaces:** not tested.
- **Not measured at all:** head-to-head against a consumer app (needs the iPhone).
