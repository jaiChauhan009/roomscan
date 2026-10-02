# Technical report

roomscan: phone capture → measured, stitched floor plan with damage assessment.
Six pages maximum. Structure in [architecture.md](architecture.md), history in
[worklog.md](worklog.md), current numbers in `bench/reports/benchmark.md`, fix-loop runs in `fixloop/`.

## 1. Summary

One command turns a LiDAR scan, a video or per-room photo folders into a JSON plan and a
rendered plan, with a 90 % interval on every number. All three tiers run end to end on a
CPU-only laptop. The LiDAR geometry is precise on synthetic rooms (under 1 mm, furnished
ones included). On four unseen rooms with laser truth (ARKitScenes, Faro scans):
- footprint within 8 % in all four (−7.6, −2.5, +0.6, −2.0 %);
- ceilings within 1-5 cm, read low;
- median wall error 3-13 cm in all four. The fourth room was split in two by a gap
  between furniture (walls 1.48 m off); it is now one room, median 0.047 m
  (`f83f66c`). Wall gates (1 cm) still fail.

On our own captures, an iPhone 16 Pro with Stray Scanner gave a 4-room plan of our flat in
2.5 minutes. A second scan of one room agrees with it to 1.7 cm on the walls both see. The
video and photo tiers on the same flat are far off (one room found from video, footprint
+32 % and +79 % from photos), with intervals that say so.

Two scans of the sample flat agree as point clouds to a median 7 mm but divide the flat
into different rooms, so the repeatability gate fails (section 8). The video and photo
tiers are far from their gates on the only data available, none of it captured per protocol.

**The main limitation of this report: little ground truth.** Laser truth exists for four
public rooms (LiDAR tier only). Numbers on the sample flat and our own captures are
self-consistency or comparisons against our own LiDAR output, labelled as such.

Around the engine there is a product: capture check, optional tape sizes, a gated stage
queue, Excel output and a web app (section 10).

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

## 3. Tier design and device matrix

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

**Device matrix** (full table in [device_matrix.md](device_matrix.md)): LiDAR tier on
iPhone 15-17 Pro / Pro Max and iPad Pro 2020+; video and photo tiers on any phone. Run on
an iPhone 16 Pro (LiDAR, video, photos) and a moto g45 (video, photos). Processing: a
CPU-only Windows 11 laptop (Ryzen 7, 16 GB); a whole-flat LiDAR scan takes 212 s.

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

| Tier | Truth used | Samples | Scale | Held-out coverage | 3 m wall with a plane, 90 % |
|---|---|---|---|---|---|
| LiDAR | laser (4 ARKitScenes rooms) | 16 walls + 4 ceilings | 5.07 | 0.85 | ±12 cm |
| video | LiDAR reference (sample flat, our flat) | 40 lengths, 4 rooms | 6.93 | 0.93 | ±0.77 m |
| photo | LiDAR reference (sample flat, our flat ×2) | 83 lengths, 15 rooms | 5.23 | 0.92 | ±1.36 m |

**LiDAR intervals now know where a wall rests on nothing.** A wall's length is the
distance between the two walls that end it. When an end wall was placed on a furniture
front or a raster edge, rather than on a measured plane, its position is uncertain by tens
of centimetres, not millimetres:
- The end's sigma gets 0.5 m × its coverage deficit, capped at 0.2 m.
- Doorways count as evidence, and short steps between well-covered walls add nothing.
- k and the cap were chosen where well-evidenced and poorly-evidenced walls get the same
  error-to-sigma ratio on laser truth (3.3 each). Before, it was 3.3 against 22.

The effect on the laser rooms:
- Fitting without the term needed scale 99, ±2.3 m on every wall.
- With it the fit is 5.07, and all 16 walls fall inside their intervals.
- The scale is pinned by one value: the split room's ceiling, 5 cm off. It could fall to
  about 3.5 once that room is no longer split. It no longer is (section 9); the refit has
  not been run yet.

`bench/reports/benchmark.md` predates this refit (its scales are in `benchmark.json` as
`calibration_used`); `bench/reports/calibration.md` has coverage at the new scales.
Geometry is unchanged by a refit.

Repeatability cannot calibrate this term: two scans see the same furniture and make the
same fragment, so it is a bias both share. The fit uses laser truth only.

Held-out coverage is leave-one-room-out: fitted on the other rooms, scored on the room left
out. In-sample coverage is 90 % or more by construction and is not evidence. Limits, stated
plainly:
- **LiDAR** rests on 16 laser walls in four rooms of one public dataset (an iPad Pro).
- **Photo and video** are scored against our own LiDAR, not a laser.
- **None of the photo sets follows the capture protocol** (one is a LiDAR proxy; ours were
  taken while walking), so protocol photos may well come out better than these intervals
  say.

Thinner data never gets a narrower interval than richer data: the script checks a
reference measurement per tier and raises the thinner tier where needed. With laser ground
truth the same script refits on it (`run_all.py --eval-only`, then `calibrate.py --write`).
Missing quantities are reported as missing: an unscanned ceiling is `null` with a lower
bound.

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
the drift correction never corrected anything (section 5). Fix: odometry weighted by ARKit's measured error, and the unsealed-room
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

**Round 4** (ceiling height, `fixloop/round4/`, declaration tagged `fixloop4-before`).

The gate: ceilings within 1.5 cm on the 4 laser rooms. Before the round 1 of 4 passed, and
all 4 read low (-5.5 to -1.0 cm).
- **Cause:** the floor and the ceiling were the peaks of a histogram weighted by how often
  each spot was seen, so a level landed on the patch the camera watched longest. In one
  room, 76 % of the floor lay below the chosen floor.
- **Fix:** each level is the median over 25 cm patches.
- **Declared:** 2 of 4 pass, with each error predicted to ±0.3 cm.
- **Measured:** 2 of 4 pass (-2.9, -0.8, -1.7, -0.9 cm). Every room landed within 0.22 cm
  of its prediction.
- **Not predicted:** one wall-top room height moved by 1.7 cm. It reports only a lower
  bound, and no gate reads it.

What remains is a uniform -1 % scale bias in the converted laser-set clouds, which the
walls share. A later round will look at it per frame; no scale factor was fitted on the
test set.

## 9. Known failure modes

- **Scans without an upward sweep:** no door heads and no ceilings, so rooms merge that a
  full scan keeps apart (fix-loop round 2).
- **Rooms with no wall between them** (open plan, corridors, wide openings): the boundary
  is where the watershed split put it, and snapping cannot move it (it stops at the next
  room); such boundaries differ between captures, and rooms split or merge.
- **Drift at the very start of a scan:** a submap moves as one piece, so tracking that is
  still settling inside one 3 s submap leaves up to 7 cm.
- **Video tier:** fast sweeps, motion blur and blank walls break tracking into segments;
  only the longest survives (1 of 6 rooms on the sample clip). Scale is ±10-20 %.
- **Photo tier:** fails when the floor is out of view (scale from an assumed phone height)
  or the look-back photo cannot be matched (position along the wall is guessed and flagged).
- **Furnished rooms (LiDAR, laser truth):** furniture taller than about 1.1 m is a barrier
  in the wall grid; where no wall is seen above it, the outline keeps the notch. A gap
  between two pieces stays inside the room only when the ceiling was seen over it
  (ARKitScenes 42446532: 1.48 m → 0.047 m, section 1); scans that never looked up keep
  such splits. Next step: outlines from the wall planes that reach the ceiling.
- **Damage:**
  - No false positive on the undamaged flat.
  - A painted 0.5 m water stain is found as one region, sized to within 13 %
    (`bench/reports/synth_damage.md`).
  - A painted 6 mm crack is found as a crack since paint goes only on bare wall
    (`c26deeb`), width +61 % (a lamp cable joins its outline), height −3 %.
  - On our own iPhone scans, ceiling lights are reported as a "hole": a false positive.
- **Closed doors** are measured as wall; the protocol asks for doors open.
- **Mirrors:** rejected as openings by a reflection test, and a mirror's gap in the wall no
  longer unseals the room. Tested on rendered mirrors, not on a real one.
- **Glass:** windows to bright outdoors return no depth and may be missed.
- **Low light:** dim frames are brightened before damage classification, with a stricter
  threshold. Brightening did not help video tracking (measured), so tracking is unchanged.
  Every tier warns on a dim capture.
- **Wet-look surfaces:** not tested.
- **Not measured at all:** head-to-head against a consumer app (needs the iPhone).

## 10. Product: capture checks, known sizes, stages, web app

- **Capture check** (`capture_quality.py`): before the long run, OK / WARN / RETAKE per
  check with one line of advice, in seconds (photos 2-12 s, LiDAR < 1 s). It catches what
  hurt our own captures: 9-55 photos per room taken while walking, chat-app copies
  (WhatsApp 1280 px), walking too fast (our walk 0.88 picture widths/s, the sample 0.26),
  a LiDAR scan that never looked up (scan B: 0 % of frames above 20°).
- **Known sizes** (`known_sizes.py`): an optional `measurements.yaml` with tape sizes per
  room. Held out on the sample flat (one number per room from the LiDAR reference, the
  others scored):
  - photos, one length per room: footprint +109 % → −23 %, held-out walls 1.41 → 0.27 m;
  - photos, ceiling height alone: +109 % → +177 %. Fitted floor and height are not off by
    the same factor, so a height is only compared, never used for scale;
  - video, one length: −70 % → −87 %. The video's error is missing rooms, not scale;
  - LiDAR is never rescaled; given sizes are a self-check.
- **Scale marker** (`markers.py`, `docs/scale_marker.md`): a printed A4 ArUco square,
  180 mm. On real LiDAR frames its scale against LiDAR depth is 1.002 (0.998-1.006, 11
  frames). Not yet used by the photo / video tiers: the depth model's scale varies per
  frame, so one frame's marker does not fix the others.
- **Stages** (`stages.py`): each stage is timed and gated (frames read, loop closures,
  rooms without wall evidence, intervals present); a failed gate stops the run and names
  the stage (`stages.json`). Outputs: `result.json`, the plan, and `result.xlsx` (every
  number with its 90 % bounds).
- **Web app:** a static front end (`web/`) and a FastAPI server (`server/`) that verifies
  uploads, queues one job at a time and shows the stages live. Exercised by tests and a
  mock server; not yet used by a non-engineer.
- **HEIC** is optional: where its decoder is blocked, other formats still work.
