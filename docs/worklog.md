# Worklog: what was built, why, and how

A running account of the work so far, stage by stage. Each stage says what was done, why
it was done that way, where it lives in the code, and what was measured.
For the structure of the finished system see [architecture.md](architecture.md).

## Where things stand

| Area | State |
|---|---|
| LiDAR tier | Works end to end. Accurate on synthetic rooms (under 1 mm). Two real scans of the sample flat now agree as point clouds (7 mm median) but are divided into different rooms (9 vs 7, none paired), so they are **not repeatable** by the gate. Footprint −0.9 % (sum of rooms) / −6.3 % (union); it was −45 % before round 1. |
| Video tier | Runs end to end. Weak: 1 of 6 rooms recovered on the sample flat. |
| Photo tier | Runs end to end and stitches rooms without overlaps. Sizes far off on the proxy photo set (footprint +133 %). |
| Damage, rules, scope | Working. 0-2 small false positives on the undamaged flat; a painted test stain is found in 3 of 5 frames, area underestimated. |
| Benchmark harness | Working. All numbers regenerate with `python bench/run_all.py`. |
| Tests | 19 tests on synthetic rooms and a synthetic drifting loop pass. |
| Not done yet | Head-to-head against a consumer app, real captures with laser ground truth, interval calibration on real truth, clean-machine install timing. All need an iPhone or a second machine. |

Two limits apply to every number below:

1. **No ground truth.** The sample scans have no tape or laser measurements. LiDAR is
   scored only on repeatability and drift; video and photo are scored against the LiDAR
   output, which is a reference and not truth.
2. **No iPhone on hand.** The video input is the scan's own RGB video, and the photo set
   is cut from that video. Neither was captured the way the protocol asks.

## Stage 0: understanding the input

**What.** The three sample folders are Stray Scanner exports: `rgb.mp4` (1920×1440, 46 fps),
depth PNGs (256×192, millimetres), confidence PNGs, `odometry.csv`, `imu.csv`.

**Finding that shaped everything after it.** Building a point cloud with ARKit camera
axes gave a smeared cloud. Trying the four axis/inverse combinations showed that the poses
use OpenCV camera axes: with those, 7 % of all points fall in a single 2 cm floor slice.
World up is +Y; the floor is near y = −1.5 m.

## Stage 1: skeleton, loader, fusion (commit `ec8e634`)

**What.** `capture.py` (the common `Frame` / `PosedCapture` form), `frontends/lidar_stray.py`,
`geometry/pointcloud.py`.

**Why a common form.** So that wall, door and damage logic is written once and every tier
feeds it.

**How.** Depth pixels are back-projected, normals computed from neighbouring pixels,
low-confidence pixels and depth edges dropped, and points merged into 2 cm voxels.
343 frames fuse in about 14 s.

## Stage 2: room layout (commit `70e17d7`)

**What.** `geometry/planes.py` and `geometry/layout.py`.

**How it evolved.**
1. First attempt: rooms = connected free floor between walls. Result: rooms broke up
   around furniture and door lintels were mistaken for walls across whole rooms.
2. Fix: lintels are only *narrow* downward strips above door height; wide ones are
   dropped ceilings. Rooms are regions *enclosed by walls*, so floor hidden by furniture
   still counts.
3. Rooms with an unscanned wall gap leaked to the outside and vanished. Fix: space far
   from any observation counts as solid; unsealed regions keep cells that have a wall in
   all four directions.
4. Regions joined through a doorway with no lintel are split by a watershed on the
   distance to walls, merged back only when the opening is as wide as the room.
5. Each room outline becomes an axis-aligned polygon; each edge is snapped onto the wall
   plane measured from 3D points.

**Measured.** Bedrooms of the sample flat come out as clean rectangles (8.8 and 10.0 m²).

## Stage 3: openings, output, CLI (commit `fc3c8cc`)

**What.** `geometry/openings.py`, `schema.py`, `uncertainty/`, `export/`, `cli.py`, `pipeline.py`.

**Why ray evidence for openings.** A door is where camera rays go *through* the wall
plane. Counting hits and passes per wall cell needs no trained detector and gives the
width directly at 1 cm resolution.

**Why pydantic for the schema.** The contract and its JSON schema come from one source,
and every output is validated when written.

**Uncertainty.** Raw sigmas from plane fits describe noise only, so each tier adds an
absolute and a relative term (`calibration.yaml`). These are priors until real ground
truth exists.

## Stage 4: drift correction (commit `867dd15`)

**What.** `geometry/drift.py`, `geometry/metrics.py`, `--drift off|loop|...`.

**Why.** The brief fails "poses used as-is" automatically and asks for an on/off ablation.

**How.** 3 s submaps, ICP between revisits, pose-graph optimisation, gravity kept from
the phone.

**Measured.** On scan A: 30 loop closures, residual 8.3 cm → 2.7 cm. On scan B: 7
closures, 16.9 cm → 2.5 cm. *(Wrong reading, found in stage 11: the residuals fell because
closures were pruned. On B the correction moved nothing by more than 2.8 cm.)*

**Tried and switched off.** A per-submap heading snap to the wall directions made walls
*less* sharp (crispness 8.4 → 6.5), because 3 s of data holds too little wall. It remains
an option, off by default. A second bug found here: rotating submaps about the world
origin instead of their own centre moved them by metres.

## Stage 5: video tier (commit `f5719f6`)

**What.** `frontends/video.py`, `ml/depth.py`.

**Constraint.** No GPU. `pycolmap` is blocked by Application Control on this machine, so
no COLMAP. The pose estimation is written with OpenCV and the depth model.

**How it evolved.**
1. SIFT matching between frames at 3 fps: 14 of 115 frames tracked. Blank walls and
   motion blur gave too few matches, and once lost it never recovered.
2. KLT optical flow at 25 fps: far more robust, but the track broke into 20 segments.
3. Root cause: fast motion made a keyframe on nearly every frame, and thinning them
   "every second one" cut the tracks. Replaced by a selection that keeps at least 320
   shared tracks between consecutive keyframes. Only 4 real breaks remain, all at
   heavily blurred frames.
4. Measured the depth model against LiDAR: a single frame's scale is unreliable (0.4× to
   3.4×), but the median over frames is stable at 1.09-1.18×. So scale uses a many-frame
   median with a 1.137 bias correction.

**Measured.** Against ARKit on the single-room scan: scale within 4-20 %, trajectory
error 9-55 cm depending on which segment survives. On the 115 s flat video: 1 room.

**Honest limit.** Scale from a depth model cannot reach the ±3 % gate. The tier reports
wide intervals instead.

## Stage 6: photo tier (commit `e2dbdbc`)

**What.** `frontends/photos.py`, `geometry/boxfit.py`, `ml/matching.py`, `scripts/make_photo_set.py`.

**How it evolved.**
1. First design: match all photo pairs with SIFT, build a pose graph. Result: 8 of 703
   pairs matched, 31 disconnected groups.
2. EfficientLoFTR (a learned matcher): matched only pairs that already overlapped, at 4 s
   per pair on CPU.
3. Redesign so that room shape needs **no matching**: doorway standpoint, headings from
   wall directions plus sweep order, per-photo scale from a shared camera height,
   rectangle from the outermost wall planes.
4. Stitching uses one look-back photo per room, matched with EfficientLoFTR; capture
   order is the flagged fallback.

**Measured on the proxy photo set.** 7 rooms placed, no overlaps, 2 rooms linked by photo
match. Footprint 116.7 m² against the LiDAR reference 50.1 m².

## Stage 7: damage, rules, scope (commit `6e8fc6d`)

**What.** `damage/detect.py`, `rules.yaml`, `scope.yaml`, `damage/pipeline.py`.

**How it evolved.**
1. Grounding DINO: 8 s per image on CPU and it reported stains and holes on undamaged
   walls. Dropped.
2. CLIP on image tiles, but only tiles that the geometry says lie on a wall, floor or
   ceiling. 0 false positives on the first run.
3. A painted test stain was missed. Cause: frames were sampled evenly in time, and the
   wall with the stain is in view in 11 of 1,949 frames. Replaced by coverage-based
   selection (every surface patch seen twice).
4. Frames are sideways when the phone is held upright; tiles are now rotated before CLIP.
5. The colour-based outline failed on a patterned background; finer CLIP tiles are the
   fallback, with a wider interval.

**Also added here.** Floor = best-supported upward plane, so ghost points below a glossy
floor do not pull it down. Mirror test for openings.

## Stage 8: benchmark and tests (commits `2263885`, `e6ad293`)

**What.** `bench/` and `tests/`.

**Why synthetic tests.** With no ground truth for real captures, a synthetic room with
exact dimensions is the only place the geometry can be checked against truth. Result:
4.0 × 3.0 × 2.7 m recovered to under 1 mm; two rooms joined by a door are separated.

## Stage 9: a regression, caught by the benchmark (commit `0a9c23c`)

**What happened.** The glossy-floor change also switched the ceiling to "best-supported
downward plane". In the sample flat that is the 2.4 m dropped ceiling of the bathrooms
and corridor, so everything above it was discarded and the 3.0 m bedrooms read 2.44 m
with broken walls.

**Fix.** The capture-level ceiling is the *highest* plane with real support; each room
still gets its own best-supported ceiling. A test now covers a flat with a dropped ceiling.

**Lesson.** The first baseline was run with this regression in place and had to be redone.

## Baseline benchmark (at commit `0a9c23c`, in `fixloop/before/`)

| Check | Result |
|---|---|
| LiDAR repeatability, scan A vs scan B | **FAIL**: 9 rooms vs 6, no rooms paired, footprint 50.1 vs 27.7 m² |
| Drift ablation, scan A | footprint 48.8 (off) → 50.1 m² (on), loop residual 8.3 → 2.7 cm |
| Drift ablation, scan B | footprint 31.0 (off) → 27.7 m² (on), loop residual 16.9 → 2.5 cm |
| Video vs LiDAR reference | FAIL: 1 of 6 rooms, footprint −85 %, interval coverage 54 % |
| Photo vs LiDAR reference | FAIL: footprint +133 %, median wall error 97 %, interval coverage 30 % |
| Room overlap (all tiers) | PASS |

## Stage 10: fix loop (commits `c9255e9`, `1e8c490`)

**Worst gate.** LiDAR repeatability. It uses two real scans, and the other tiers are
scored against LiDAR output, so nothing else can be trusted until it holds.

**First hypothesis, ruled out.** Room separation depends on door lintels above 2.15 m,
and scan B never looked that high. Experiment (`fixloop/evidence_height_cut.py`): delete
scan A's points above a cut height and rerun.

| Scan A, points removed above | Rooms | Footprint m² |
|---|---|---|
| nothing | 9 | 50.1 |
| 1.8 m (no lintels left) | 9 | 55.1 |
| 1.4 m | 7 | 32.2 |
| 1.2 m | 4 | 20.1 |

Without lintels A still has 9 rooms; it collapses only when walls end below ~1.4 m.

**Declared root cause.** The wall test demanded 0.8 m of evidence in a fixed band up to
1.9 m; scan B's wall evidence ends at ~1.6 m (phone pointed down). Declaration committed
before the fix (`fixloop/declaration.md`).

**Fix.** The band ends where the capture's own evidence ends; a wall must cover half of
it. The declaration said 60 %; that was an arithmetic slip (the old rule is 50 %), and at
60 % scan B barely moved. Shipped 50 %, deviation recorded in the commit.

**Result** (`fixloop/README.md`).

| | before | after | predicted |
|---|---|---|---|
| footprint B vs A | −45 % | −18 % | ±10 % |
| rooms B / A | 6 / 9 | 7 / 10 | B 8-9 |
| rooms paired | 0 | 1 | ≥ 6 |

The gate still fails and the prediction was badly wrong. The post-mortem found a second
cause hidden behind the first: with no upper sweep, furniture fronts stand in for walls,
and B's bedroom comes out 32-53 cm short per side. The evidence experiment could not
show this because it removed points but kept A's upward-looking camera paths.
*(Stage 11 tested the furniture explanation and found it wrong.)*

## Stage 11: fix loop, round 2 (commits `482b4fb`, `b39f339`, `cb55b8b`, `2f4ed8b`)

**Testing round 1's explanation first.** If B's short bedroom wall were a furniture front,
A would have a surface there too. It has 6 points; A's wall is 43 cm further out
(`fixloop/round2/evidence_drift.py section`). So the furniture explanation was wrong.

**What it actually was.** B starts and ends in the bedroom. The walls B saw in its first
10 s were 26-33 cm off A; the wall it saw 100 s later matched A exactly. The drift
correction should have fixed exactly this, so we looked at what it did on B: it moved no
submap by more than 2.8 cm and kept 1 of 7 loop closures.

**Why.** The pose graph weighted ARKit's odometry at 1 cm and 0.29° per 3 s step. ICP
between consecutive submaps measures 3.0-3.7 cm (p90) and 0.37-0.47° (rms). Open3D weights
a closure by its error before the first step and prunes it if the error does not shrink;
with stiff odometry the graph cannot bend, so every closure asking for a large correction
was pruned. A synthetic loop with B's closure pattern shows the threshold: the old weights
work up to 0.25 m of drift and do nothing at 0.5 m. The reported B loop residual (16.9 →
2.5 cm) came from the pruning, and walls were *less* sharp with the correction on.

**Fix** (declaration committed first). Odometry weighted by the measured error; pruning
distance 1.4 × submap voxel, which keeps Open3D's tolerance where it was. With B corrected,
a second cause showed: the unsealed-room fallback read raw walls and cut B's bedroom along
short gaps behind the bed head. It now reads the gap-closed walls. A behaviour-neutral
refactor came first so the fix diff is 2 constants and 1 line. A cache bug found on the
way: fused clouds were cached without the poses, so the after run would have reused the
before clouds. Fixed separately.

**Result.** Every declared prediction held: B moves 0.52 m, crispness 9.58 → 12.26 (A 8.36
→ 8.84), bedroom walls within +2 / −7 / −1 cm of A's, footprint gap −18 % → −0.9 %. The gate
still fails as declared: B saw no door heads or ceilings, so it divides the flat into
different rooms and none pair.

**Found while checking the result.** Room polygons overlap in every LiDAR plan since round
1: wall snapping can push an edge into the next room. The footprint sums rooms, so it
double-counts: by union the gap is −6.3 %, not −0.9 %. `room_lidar` changed by 4 m² from
the layout part, with no ground truth to say whether for better.

## Documentation added

`docs/architecture.md`, this worklog, `docs/device_matrix.md`,
`docs/compliance_matrix.md`, `docs/tech_report.md`, `fixloop/README.md`.

## Still to do

1. Room polygons must not overlap: wall snapping must stay on the room's own side of its
   neighbours, and the footprint should be the union of rooms.
2. Real captures with laser ground truth, in all three tiers, including a room captured
   twice per protocol (upward sweep included) and a furnished room with staged damage in
   two classes. Needs an iPhone.
3. Head-to-head against a consumer scanning app on two rooms. Needs an iPhone.
4. Refit `calibration.yaml` on real ground truth so intervals are calibrated.
5. The Round 1 document, for the real schema and gates. `bench/gates.yaml` marks the
   values that are assumed in its absence.
