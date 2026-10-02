# Worklog: what was built, why, and how

A running account of the work so far, stage by stage. Each stage says what was done, why
it was done that way, where it lives in the code, and what was measured.
For the structure of the finished system see [architecture.md](architecture.md).

## Where things stand

| Area | State |
|---|---|
| LiDAR tier | Works end to end. Accurate on synthetic rooms (under 1 mm). Two real scans of the sample flat now agree as point clouds (7 mm median) but are divided into different rooms (9 vs 7, none paired), so they are **not repeatable** by the gate. Footprint B vs A −10.4 % (it was −45 % before round 1). Room outlines no longer overlap. |
| Video tier | Runs end to end on upright iPhone-style clips. Weak: 2 of 7 rooms on the sample flat, footprint −70 % vs the LiDAR reference. |
| Photo tier | Runs end to end and stitches rooms without overlaps. Sizes far off on the proxy photo set (footprint +133 %). |
| Damage, rules, scope | Working. 0-2 small false positives on the undamaged flat; a painted test stain is found in 3 of 5 frames, area underestimated. |
| Benchmark harness | Working. All numbers regenerate with `python bench/run_all.py`. |
| Tests | 60 tests pass (synthetic rooms, a drifting loop, calibration, every iPhone input type, exact video frames, photo-set recipe, head-to-head); 1 needs ffmpeg and is skipped. |
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

## Stage 12: room outlines no longer overlap (commit `a88f7ef`)

**Problem.** Found while checking round 2: room outlines overlapped in every LiDAR plan
(scan A 1.2 m², scan B 3.75 m², single room 0.58 m²). The raster outlines never overlap;
all of it came from wall snapping, which moves an edge up to 0.9 m outward to the
best-covered wall plane and could land on a surface inside the next room. The footprint
summed rooms, so the overlap was counted twice.

**Fix.** An edge's outward search stops at the first cell of another room; the footprint
is the union of rooms; the benchmark reports room overlap for every capture. A test
rebuilds the failure: room a's barely scanned wall lost to a dresser 0.7 m inside room b
(2.04 m² overlap), and now does not.

**Result** (`bench/reports/benchmark.md`). Overlap 0 on all captures. Footprint A 49.2 m²,
B 44.1 m²: B vs A −10.4 %. The round-2 headline of −0.9 % was mostly double counting.
Scan A gained about 1 m²: the snapper scores only the 6 heaviest candidate planes, and
with the far ones excluded a nearer, denser wall now makes the cut.

## Stage 13: calibrated intervals (commit `e858ef1`)

**Problem.** 90 % intervals held the truth 28 % of the time for photos and 77 % for video
(against the LiDAR reference), and 58 % for LiDAR walls (same wall in the two scans). The
brief caps the total score for confident garbage on thin input.

**How.** The evaluator records each compared value with its sigma. `bench/calibrate.py`
fits one scale per tier with split conformal (the (n+1)·0.9 quantile of |error| / sigma),
reports leave-one-room-out coverage as the honest estimate, and makes sure a thinner tier
never gets a narrower interval. LiDAR has no ground truth, so it is fitted on the same wall
in both scans: precision only.

**Result.** LiDAR ×2.6 (held out 0.95), video ×8.3 (one room, cannot be validated), photo
×6.6 (held out 0.90). A 3 m wall now reads ±6 cm (LiDAR), ±0.93 m (video), ±1.72 m (photo).
The photo set does not follow the protocol, so real protocol photos may be tighter than
these intervals; that errs on the safe side until real captures refit them.

## Stage 14: ready for a reviewer's iPhone (merges `0d09250`, `dbf80bd`, `cee589e`)

Three parallel pieces of work, each on its own branch, merged when its tests passed.

**Inputs from a real iPhone.** Every file type an iPhone 15+ produces was generated and run:
HEIC (also renamed .JPG), EXIF orientations, 24 and 48 MP photos (now decoded small:
48 MP JPEG 631 MB -> 73 MB peak memory), Live Photo .MOV / .AAE / macOS `._` files next to
photos, HEVC Main and Main 10 video, variable frame rate, zipped Stray recordings,
non-ASCII and space-containing Windows paths, unreadable files. Biggest finding: OpenCV
does not apply the iPhone's rotation flag by default, so every upright clip, which is what
the protocol asks for, was processed sideways. On the single-room footage made into an
iPhone-style clip, sideways gave 1 room of 0.31 m²; upright gives 10.12 m² (LiDAR 10.64).
Also fixed: depth reads and result.json crashed on non-ASCII paths, and a noise-free
ceiling crashed `ceiling_level` (all points in one histogram bin). Bad input now ends with
one line and exit code 2, not a traceback.

**Manual testing and the walk-in.** `scripts/walkin.py` takes whatever arrives (Stray
folder or its zip, a clip, photo folders, a drive root) and runs cold with a per-room table
to check against a laser. `bench/head_to_head.py` builds the Part 3 table against a
consumer app. `docs/iphone_session.md` plans the 2-3 h iPhone session minute by minute;
`docs/capture_protocol.md` now ends every tier with a USB-C drive and one command.

**Clean machine.** The README was followed literally on an isolated copy: 15.5 min to a
first LiDAR plan, 12.5 of them downloads at 0.75 MB/s. Fixed on the way: the damage model's
weights were never fetched (it would only run online), and the data download could leave a
half-finished folder that looked complete.

## Stage 15: the benchmark shows what a real capture gets (commits `80bce56`-`b8a95ff`)

**Video, upright.** The benchmark fed the video tier the scan's raw `rgb.mp4`, which Stray
stores sideways with no rotation flag; no Camera-app clip looks like that. It now gets the
same video with the flag an upright iPhone clip carries (`scripts/make_iphone_clip.py`,
5 bytes changed, frames untouched), and a calibration file next to a rotated clip turns
with the frames. Rooms found 1 -> 2 of 7, footprint vs the LiDAR reference -91 % -> -70 %.
The video interval scale refitted on 32 walls in 2 rooms: 8.29 -> 2.58, held out 0.91.

**Colour frames match their poses.** The LiDAR loader jumped to colour frames by frame
number; on a variable-frame-rate rgb.mp4 OpenCV seeks by time and landed 4-80 frames late
(up to 1.8 s). Damage detection saw a different view than its pose. It now decodes forward.
Scan A's one false positive (a 0.017 m² "stain" on a ceiling) is gone with it.

**Reproducible photo set.** `make_photo_set.py` rebuilds the benchmark's 28 photos byte for
byte from a committed recipe (it had drifted to 6 of 7 rooms as the layout changed).

**Smaller download.** transformers fetched a second, unused 605 MB copy of the CLIP weights;
weights are now 0.77 GB (were 1.38 GB), identical results.

**Final benchmark** (`bench/reports/benchmark.md`): room overlap 0 everywhere; calibration
gate passes for video (0.94) and photo (0.95); every accuracy gate for video and photo and
the LiDAR repeatability gate still fail.

## Stage 16: laser truth, outlines that follow walls, a faster walk-in (commits `d142887`-)

**Laser ground truth for LiDAR** (`8736b4e`, merged `929f1b3`). Four unseen ARKitScenes
rooms (Apple iPad Pro LiDAR, Faro laser scans), chosen by a rule fixed before any roomscan
run. The truth is computed from the laser points alone (`bench/make_laser_truth.py`), and
each sequence is converted to a Stray export `roomscan run` reads unchanged. First
accuracy numbers for the walk-in tier: ceilings 1-2 cm low (one passes the 1.5 cm gate);
outlines fragment on furniture, so wall gates fail. Details in the benchmark report.

**Fix-loop round 3** (`fixloop/round3/`): furniture notches in room outlines. Declared
before the fix (`929a035`). In 4 of the flat's 16 LiDAR rooms and both video rooms, wall
snapping made the outline cross itself, and every wall of those rooms lost its evidence.
A wardrobe front also beat the wall seen above it. Fix: settle the snapped outline (merge
walls on one plane, undo only the conflicting snap); a plane reaching the ceiling beats a
lower one. Furnished synthetic rooms now come out exact (4 walls, 12.00 m²).

**After the round:** a wall mirror or low-sill window made a room leak round its walls
(+27 %), now a barrier (`9b6a0ac`). The ceiling rule could not see the wall behind dense
furniture because candidates were the six highest histogram bins, all from one peak
(`0a57664`).

**Walk-in speed** (Worker E, merged `9346642`): video decoded once, CLIP loaded in the
background, cheaper wall assignment, faster openings. Damage stage on the flat 124 -> 85 s
and openings 70 -> 5 s, each stage timed alone; outputs bit-identical. The final benchmark
shared the machine with the iPhone session (openings 7 s, damage 133 s). A clean cold
walk-in on the flat afterwards took 3 min 39 s (drift 28, fuse 39, layout 10, openings 6,
damage 131 s). The damage stage is slower than Worker E's stage-alone 85 s for two reasons,
both measured. CLIP took 69-73 ms per tile on the plugged-in, Balanced-plan laptop that
afternoon, against E's 35 ms earlier. And round 3's rooms reach their walls, so 1,448 tiles
qualify instead of 1,213. Skipping blank wall tiles before CLIP would cut this, but recall is
the damage stage's weak point already, so it is left for a validation set. Dim frames are brightened before CLIP, with a
stricter threshold. A rendered mirror is rejected and a doorway kept (`tests/test_speed_lowlight.py`).

**Tried and rejected: furniture necks.** A rule kept two halves of a room together when the
gap between them was flanked by barrier with no wall above door height (furniture, not a
doorway's jambs). It did merge ARKitScenes 42446532, but:
- that room's footprint got worse (−7.6 % → −18.3 %), because the merged outline keeps the
  furniture notches;
- scan B fell from 7 rooms to 4. B never looked up, so no real doorway showed wall above
  door height, and real doorways were merged too.

The rule needs to know where a capture actually looked high up; not shipped.

**Offline models** (`819c547`, `616a211`): weights load from the local cache without asking
the hub, so the walk-in needs no internet.

**Damage on synthetic staged damage** (`bench/synth_damage.py`): a stain and a crack of
known size painted onto two walls of scan A, consistently in every frame. No false positive
on the clean capture; both missed. Each was in view in only 2-4 of the frames examined.

## Stage 17: our own captures (2 October 2026)

An iPhone 16 Pro for about an hour, then a motorola moto g45. Copying needed the Apple
Devices app and `pymobiledevice3` (Stray Scanner's files over USB, many files at a time:
a 13 s scan in 18 s instead of 78 s). `walkin.py --check-only` checks a copy in seconds,
so the phone can go back early.

- **LiDAR, iPhone, whole flat (147 s):**
  - 4 rooms, 59.7 m², ceilings 2.40-2.57 m, doors and windows, a mirror rejected.
  - 2.5 min cold, offline.
- **LiDAR, iPhone, room 4 alone (31 s):**
  - 13.4 m² against 12.9 m² in the whole-flat scan.
  - The one wall no furniture touches agrees to 5 mm (4.162 vs 4.157 m).
  - The other sides are cut by furniture differently in each scan. That showed the
    repeatability tool paired walls by their order round the room, which compared
    different walls; it now pairs them by position.
- **LiDAR, iPhone, part of the flat (55 s):** 4 rooms, 34.9 m². Two "holes" on a ceiling,
  probably lights: a likely false positive.
- **Video, moto g45 (84 s, 1080p):** 1 room of 3.3 m². Tracking broke, as on the sample
  clip.
- **Photos, moto g45 (186, taken while walking):**
  - Mixed portrait and landscape photos crashed depth estimation (fixed).
  - Rooms with 9-55 photos made the run take over 40 min; they are now thinned to 8,
    keeping the look-back.
  - Result: 6 rooms, 80.6 m² with a 90 % interval of 3.6-157.6 m². Honest, not useful.
- **iPhone video and photos through WhatsApp:** recompressed (464×832; 960×1280 with EXIF
  stripped). Both read; photos fall back to the iPhone focal length, with a warning.

Tape measurements of the flat are still to come; until then video and photos are scored
against the iPhone LiDAR plan.

## Stage 18: three workers in parallel (commits `aa554d3`, `4fbad54`, `b1747a0`)

- **Room outlines** (`layout.py`):
  - Short raster diagonals become square corners, and slots under 0.4 m wide are filled.
  - An edge's snap stops where the room's own floor resumes.
  - Laser room 47332890: 13 → 6 walls, wall median 1.33 → 0.13 m. 44358446: footprint
    −5.7 → −2.5 %.
  - No room count changed, and no overlap appeared.
  - Tried and dropped: merging neck-split parts under a common ceiling. It fixed
    42446532 but merged two of scan A's rooms.
- **Evidence-aware LiDAR intervals** (`export/build.py`):
  - A wall's length sigma grows where an end wall rests on no plane: +0.5 m × the
    coverage deficit, capped at 0.2 m per end. Doorways count as evidence, and short
    steps between covered walls add nothing.
  - Laser walls inside the interval at the old scale: 8/16 → 12/16.
  - A refit to laser truth needs scale 3.46, against 99 without the term.
  - Well-evidenced walls stay at about ±5-7 cm.
- **Damage outline** (`damage/detect.py`): colour is measured against the wall around each
  pixel, and the region grows from strong pixels into weaker connected ones.
  - Painted stain: found whole, extents −12 / −13 %; before, it was split into two
    regions, +108 % wide.
  - Clean scan A false positives 2 → 0.
  - The crack is still missed: CLIP scores it at most about 0.6.

## Documentation added

`docs/architecture.md`, this worklog, `docs/device_matrix.md`,
`docs/compliance_matrix.md`, `docs/tech_report.md`, `fixloop/README.md`.

## Still to do

1. Real captures with laser ground truth, in all three tiers, including a room captured
   twice per protocol (upward sweep included) and a furnished room with staged damage in
   two classes. Needs an iPhone. (LiDAR now has laser truth on four public rooms.)
2. LiDAR outlines on furnished rooms: build them from the wall planes that reach the
   ceiling, and stop furniture from splitting a room at a gap (ARKitScenes 42446532).
3. Damage recall: each wall patch is seen in too few frames to confirm a stain.
4. Head-to-head against a consumer scanning app on two rooms. Needs an iPhone.
5. The Round 1 document, for the real schema and gates. `bench/gates.yaml` marks the
   values that are assumed in its absence.
