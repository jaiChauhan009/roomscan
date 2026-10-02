# Architecture

What the project is, how the code is organised, and how data flows through it.
For the history of how it got here and why, see [worklog.md](worklog.md).

## 1. What the system does

Input: one capture of a property made with an iPhone, in one of three forms.

| Tier | Input | What the phone gives us |
|---|---|---|
| LiDAR | Stray Scanner folder | colour video, depth image per frame, camera position per frame |
| video | one video clip | colour video only |
| photo | one folder of photos per room | 2 to 8 still photos per room, nothing else |

Output (the same for every tier): `result.json` and `plan.png`.

- every room: outline, wall lengths, ceiling height, floor area, doors and windows
- one stitched plan of the whole property, with which rooms connect to which
- damage regions on walls, floors and ceilings, with type and size in metres
- concealed-damage flags, each naming the rule that fired
- repair line items tied to the damaged surface
- a 90 % interval on every number; wider for video, widest for photos

One command runs everything: `roomscan run <capture>`.

## 2. The central design decision

All three tiers are converted into one common form, and one shared back end does the rest.

```
                 FRONT ENDS (one per tier)                    SHARED BACK END
 LiDAR folder ─► lidar_stray.py ─┐
                                 ├─► PosedCapture ─► drift ─► fused cloud ─► layout ─► openings ─┐
 video file ───► video.py ───────┘   (frames with                                                │
                                      depth + pose)                                              ├─► damage ─► rules ─► scope ─► JSON + plan
 photo folders ► photos.py ──────────► rooms as rectangles + PosedCapture ──────────► openings ─┘
```

`PosedCapture` (in `capture.py`) is a list of frames. Each frame has a colour image, a
depth image in metres, the camera intrinsics and the camera pose in a world frame where
+Y is up. Once a tier has produced that, the code no longer cares where it came from.

Why: wall, door and damage logic is the hardest part to get right. Writing it once and
tuning it on the cleanest data (LiDAR) is cheaper and more consistent than three pipelines.

The photo tier is the exception for room shapes: a few stills cannot produce walls that
enclose a room, so it fits each room as a rectangle (`boxfit.py`) and then joins the
shared path for openings, damage and export.

## 3. Repository layout

```
roomscan/
├── README.md                     setup, run, reproduce
├── pyproject.toml, uv.lock       dependencies (Python 3.11, CPU only)
├── schema/output.schema.json     the published output schema (generated from schema.py)
├── src/roomscan/
│   ├── cli.py                    `roomscan run`, `roomscan schema`
│   ├── pipeline.py               tier detection, stage order, caching, timing
│   ├── capture.py                Frame and PosedCapture (the common form)
│   ├── schema.py                 output contract as pydantic models
│   ├── frontends/
│   │   ├── lidar_stray.py        reads a Stray Scanner folder
│   │   ├── video.py              video -> poses and depth
│   │   └── photos.py             photo folders -> rooms and stitched plan
│   ├── ml/
│   │   ├── depth.py              Depth Anything V2 wrapper (depth from one image)
│   │   └── matching.py           EfficientLoFTR wrapper (match two images)
│   ├── geometry/
│   │   ├── pointcloud.py         depth -> 3D points, normals, voxel fusion
│   │   ├── planes.py             floor, ceiling, dominant wall direction
│   │   ├── layout.py             rooms and walls from the fused cloud
│   │   ├── boxfit.py             rectangular room fit for thin or noisy input
│   │   ├── openings.py           doors and windows, mirror rejection
│   │   ├── drift.py              drift correction (loop closure)
│   │   └── metrics.py            wall crispness (map self-consistency)
│   ├── damage/
│   │   ├── detect.py             damage regions on surfaces
│   │   ├── rules.yaml            concealed-damage rules
│   │   ├── scope.yaml            repair line items
│   │   └── pipeline.py           regions -> flags -> scope
│   ├── uncertainty/
│   │   ├── intervals.py          turns a value + raw sigma into a 90 % interval
│   │   └── calibration.yaml      per-tier terms: priors + a fitted scale
│   └── export/
│       ├── build.py              assembles result.json
│       └── render.py             draws plan.png / plan.svg
├── bench/
│   ├── gates.yaml                pass/fail thresholds
│   ├── manifest.yaml             which captures form the benchmark
│   ├── evaluate.py               one result vs ground truth, every gate
│   ├── repeatability.py          two results of the same space
│   ├── make_reference.py         LiDAR output as a labelled stand-in for ground truth
│   ├── run_all.py                regenerates every number (--eval-only re-scores outputs)
│   ├── calibrate.py              fits the interval scale per tier, held-out check
│   ├── head_to_head.py           our LiDAR result vs a consumer app (brief Part 3)
│   ├── photo_set_recipe.yaml     pins the benchmark photo set to exact frames
│   ├── app_exports/TEMPLATE.yaml the app's dimensions, as read from its plan
│   └── ground_truth/TEMPLATE.yaml  how to record laser measurements
├── fixloop/                      worst gate, root cause, before / after runs
├── tests/                        synthetic rooms with exactly known dimensions
├── scripts/                      walkin (live test), fetch_weights, fetch_data,
│                                 make_photo_set, make_iphone_clip, dev tools
└── docs/                         capture protocol, this file, worklog
```

## 4. Front ends

### 4.1 LiDAR (`frontends/lidar_stray.py`)

Stray Scanner writes `rgb.mp4`, `depth/NNNNNN.png` (256×192, millimetres),
`confidence/NNNNNN.png` (0, 1, 2), `odometry.csv` (camera position and rotation per
frame, plus intrinsics) and `imu.csv`.

The loader reads `odometry.csv` and builds one `Frame` per used frame (every 5th by
default). Depth and colour are loaded lazily. Colour frames are decoded forward to the
exact frame (seeking by frame number is wrong on Stray's variable-frame-rate video) and
cached as JPEG bytes. A zipped export, a parent folder and non-ASCII paths are accepted.

One fact that had to be measured: the poses map **OpenCV** camera axes (x right, y down,
z forward) into the ARKit world. With ARKit camera axes the cloud is smeared; with OpenCV
axes the floor falls into one 2 cm slice.

### 4.2 Video (`frontends/video.py`)

A video has no depth and no camera positions, so both are estimated.

1. **Track** corner points frame to frame with KLT optical flow at about 25 fps.
2. **Pick keyframes** so that consecutive keyframes still share at least 320 tracked points.
3. **Depth** for each keyframe from Depth Anything V2 (one image in, depth map out).
4. **Pose**: each keyframe is located by PnP against the 3D positions of tracked points.
   The keyframe's depth map is rescaled to agree with the map before it adds new points.
5. **Broken segments** (tracking lost on a blank wall) are re-attached by SIFT matching
   where possible; otherwise only the longest segment is kept.
6. **Metric scale**: median of the depth model's own scale over all keyframes, divided by
   a measured bias (the model reads about 14 % long on the sample scans).
7. **Gravity**: the direction along which the camera height stays constant and a large
   plane (the floor) lies below. Works for a phone held upright or sideways.
8. **Rotation**: an iPhone clip filmed upright is stored landscape with a rotation flag;
   the flag is applied (OpenCV does not by default) and a calibration file is turned with
   the frames.

### 4.3 Photos (`frontends/photos.py`)

Photos of white rooms have almost nothing to match between them (SIFT matched 8 of 703
pairs), so room shape does not depend on matching photos.

Per photo: depth from the model; gravity from floor, ceiling and wall normals; wall
direction; camera height above the floor.

Per room: the protocol has the person stand in the doorway and sweep left to right. So
all photos share one standpoint. Each photo's heading is known from the wall direction
up to a quarter turn, and the sweep order removes that ambiguity. Each photo is rescaled
so all agree on the camera height. The room is then the rectangle bounded by the
outermost wall plane on each side (`boxfit.py`); the wall behind the standpoint is placed
10 cm behind it.

Stitching: in every folder but the first, the last photo looks back into the previous
room. That photo is located inside an earlier room with EfficientLoFTR matches plus PnP
on that room's depth, which gives the door position. If no match is found, the room is
attached to the previous folder and the output says the position is a guess. Rooms that
overlap after placement are separated at the shared wall.

## 5. Shared back end

### 5.1 Drift correction (`geometry/drift.py`)

Phone tracking drifts: on the sample scans 1-4 cm and 0.2-0.5° per 3 s, up to 0.5 m
around a flat. The trajectory is cut into 3 s submaps. `_find_loops` aligns submaps that
revisit a place with point-to-plane ICP; `_optimise` runs a robust pose graph (Open3D)
whose odometry edges are weighted by ARKit's measured error (`ODO_SIGMA_M`,
`ODO_SIGMA_DEG`), and prunes closures that stay inconsistent (`PRUNE_DIST`). Only heading
and position are corrected; gravity from the phone is kept. `--drift off` disables it for
the ablation. The fused-cloud cache is keyed on the corrected poses, so a change here is
never served an old cloud.

### 5.2 Fused cloud (`geometry/pointcloud.py`)

Every depth pixel becomes a 3D point with a surface normal. Low-confidence pixels and
depth edges are dropped. Points are merged into 2 cm voxels; near observations weigh more.

### 5.3 Layout (`geometry/layout.py`)

1. **Floor**: the best-supported upward-facing plane (not the lowest: a glossy floor
   produces ghost points below itself).
2. **Ceiling**: for the whole capture, the highest plane with real support; per room, the
   best-supported plane inside that room.
3. **Wall direction**: the dominant direction of wall normals, modulo 90°.
4. **Raster**: a top-down grid of 2 cm cells. For each cell, how much of the height band
   the capture actually observed on walls (from 0.3 m up to at most 1.9 m) carries wall
   points. A cell covered over half that band (at least 0.5 m) is wall; a cell with wall
   only above 2.15 m is a door lintel; furniture is neither.
5. **Rooms**: regions sealed by walls and lintels, after closing wall gaps under 50 cm. A
   region that is not sealed keeps the cells with a (gap-closed) wall in all four
   directions. Regions joined through a narrow neck are split unless the opening is as
   wide as the room and the ceilings match.
6. **Polygon**: each room outline is simplified to axis-aligned segments, and each
   segment is snapped onto the real wall plane using the 3D points, searching up to 0.9 m
   outward (the room's floor stops at furniture) but never into another room.

On a synthetic 4.0 × 3.0 × 2.7 m room this recovers every wall to under 1 mm.

### 5.4 Openings (`geometry/openings.py`)

For each wall, a grid of position along the wall × height. Camera rays that stop at the
wall count as "hit"; rays that go through and hit something beyond count as "pass".
Regions where rays pass are openings: a door if it reaches the floor, a window if not.
Width is read at 1 cm resolution. If what is seen "through" an opening, mirrored back
across the wall, lands on real surfaces of the same room, it is a mirror and is dropped.

### 5.5 Box fit (`geometry/boxfit.py`)

For the photo tier, and for video captures whose walls do not close: the room is the
rectangle bounded by the outermost strong wall plane on each of the four sides. A side
with no wall evidence falls back to the observed floor extent and gets a wide sigma.

## 6. Damage (`damage/`)

1. **Frame choice**: frames are picked until every wall, floor and ceiling patch has been
   seen twice (greedy set cover), preferring frames with slow camera rotation.
2. **Surface assignment**: every depth pixel is assigned to a wall, the floor or the
   ceiling of the room the camera is in, or to "not structure".
3. **Classification**: image tiles lying on structure are scored by CLIP against damage
   prompts (water stain, crack, mold, peeling paint, hole) and benign prompts (clean
   wall, switch, picture, shadow ...). Tiles are rotated upright first.
4. **Region and size**: inside positive tiles, pixels whose colour departs from the local
   surface colour form the region. Area is the sum of each pixel's footprint on the
   surface plane. If colour gives nothing, finer CLIP tiles outline it, with a wider interval.
5. **Merge**: the same region seen from several frames becomes one record.
6. **Rules** (`rules.yaml`): ten rules such as "water stain on a ceiling means water
   entered from above". A fired rule writes its id and sentence into the output.
7. **Scope** (`scope.yaml`): line items per damage class and surface type, with
   quantities and intervals. A wall is repainted once however many regions it has.

## 7. Uncertainty (`uncertainty/`)

Every measurement goes through `measure(value, raw_sigma, tier, kind)`:

    sigma = scale × sqrt((inflate × raw_sigma)² + abs² + (rel × value)²)

`raw_sigma` comes from the geometry (plane-fit standard error). `abs`, `rel` and `inflate`
are per-tier priors in `calibration.yaml`; a per-tier `scale` multiplies the result and is
fitted by `bench/calibrate.py` (split conformal on the benchmark, checked on held-out
rooms) so that 90 % intervals contain the truth about 90 % of the time. LiDAR is fitted on
two scans of the same flat until laser truth exists. A value that was not observed (a
ceiling that was never scanned) is reported as `null` with a lower bound, not guessed.

## 8. Output (`schema.py`, `export/`)

`schema.py` defines the contract as pydantic models; `roomscan schema` writes the JSON
schema. Top level: `capture`, `property` (footprint, adjacency, drift info), `rooms`
(polygon, walls, openings, surfaces), `damage`, `concealed_damage_flags`, `scope`,
`warnings`, `timing_s`. Every measurement is `{value, ci90, sigma, unit}`.

`render.py` draws the plan: walls, doors (green), windows (blue dashed), each wall's
length ± interval, each room's area and height, damage markers.

## 9. Benchmark (`bench/`)

- `evaluate.py` matches predicted rooms to ground-truth rooms and scores wall lengths,
  ceiling heights, openings (a missed and a phantom opening both count as misses),
  interval coverage, footprint, adjacency and room overlaps.
- `repeatability.py` compares two captures of the same space wall by wall.
- `run_all.py` runs every capture in `manifest.yaml` and writes `benchmark.md`, with a
  room-overlap column for every capture; `calibrate.py` refits the intervals from it.
- `head_to_head.py` builds the table against a consumer app; `scripts/walkin.py` runs a
  capture cold and prints a per-room table to check against a laser.
- Where no tape or laser truth exists, video and photo tiers are scored against the
  LiDAR-tier output of the same capture, and the report labels that as a reference,
  not ground truth.

## 10. Caching and timing

Fused clouds and depth maps are cached in `.cache/`, keyed by input, settings and (for
clouds) the poses actually fused. A cached run replays deterministically; `--no-cache`
forces the live path. Typical times on a laptop CPU (no GPU): LiDAR whole flat about
3.5 min, of which damage is more than half; single room under a minute; video 115 s clip
about 12 minutes cold, 2 minutes with cached depth; photo set of 7 rooms about 3 minutes.

## 11. Models used

| Model | Licence | Where |
|---|---|---|
| Depth Anything V2 Metric Indoor Small | Apache-2.0 | video and photo depth |
| EfficientLoFTR | Apache-2.0 | photo-tier look-back matching |
| CLIP ViT-B/32 | MIT | damage classification |

Everything runs locally on CPU. Weights are downloaded once by `scripts/fetch_weights.py`.
