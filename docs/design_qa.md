# Design decisions: questions and answers

The decisions behind roomscan, why each was taken, and the evidence, written as the
questions a reviewer is likely to ask. Numbers are from `bench/reports/benchmark.md`, the
fix-loop bundles in `fixloop/` and `bench/reports/calibration.md` unless stated.

## Numbers worth knowing

| What | Value |
|---|---|
| Synthetic room, known size | every wall to < 1 mm, ceiling to < 5 mm |
| Two LiDAR scans of one flat, as point clouds | 97 % of wall points within 15 cm, median 7 mm apart |
| Same, as plans | footprint 49.2 vs 44.1 m² (−10.4 %), 9 vs 7 rooms, none paired |
| Drift correction on scan B | moves it up to 0.52 m; wall crispness 10.02 off → 12.26 on |
| ARKit odometry error, measured | p90 3.0-3.7 cm and 0.37-0.47° (rms) per 3 s |
| 90 % interval on a 3 m wall | LiDAR ±6 cm, video ±29 cm, photo ±1.72 m |
| Held-out interval coverage | LiDAR 0.95 (two-scan precision), video 0.91, photo 0.90 |
| Video tier on the sample flat | 2 of 7 rooms, footprint −70 % vs the LiDAR reference |
| Photo tier on the proxy set | 7 rooms, footprint +137 % vs the LiDAR reference |
| Cold run times, CPU only | LiDAR flat ~3.5 min, one room < 1 min, video ~12 min (2 with cached depth), photos ~3 min |
| Clean install | 15.5 min to a first plan measured, 12.5 of it downloading at 0.75 MB/s |

## Capture

**Why a stock capture protocol (Route 2) and not our own iOS app?**
We had no Mac and no iPhone during development, so an app could not be built or tested.
Stray Scanner already exports what an app would: raw ARKit depth (256×192 mm), confidence,
camera poses and intrinsics per frame, plus the RGB video. Video and photos come from the
Camera app. The protocol (`docs/capture_protocol.md`) fixes what an examiner does, down to
the taps, and ends every tier on a USB-C drive with one command (`scripts/walkin.py E:\`).

**Why "Most Compatible" and portrait at chest height?**
Most Compatible gives JPEG and H.264, the safest formats; HEIC and HEVC also work (tested).
The photo tier gets its scale from the camera height above the floor, so a steady height
matters; portrait shows floor and ceiling in one frame.

## LiDAR tier and the shared back end

**How did you know how to read Stray's poses?**
By measuring, not assuming. With ARKit camera axes the fused cloud is smeared; with OpenCV
axes (x right, y down, z forward) 7 % of all points fall in one 2 cm floor slice.

**Why one common form (PosedCapture) for all three tiers?**
Walls, doors and damage are the hard part. Every tier produces frames with depth, pose and
intrinsics, so that logic is written once and tuned on the cleanest data. The photo tier is
the exception for room shape: a few stills cannot enclose a room, so rooms are fitted as
rectangles and then join the shared path.

**How is the floor found, and why not "the lowest plane"?**
The best-supported upward plane in the lower half of the scene. A glossy floor reflects the
room and creates ghost points below the real floor; the lowest-plane rule would follow them
(tested with synthetic ghost points).

**And the ceiling?**
For the whole capture, the highest plane with real support; per room, that room's own
best-supported plane. "Best supported" for the whole capture picked the 2.4 m dropped
ceiling of the bathrooms and cut a 3.0 m flat; the benchmark caught that regression.

**How are walls told apart from furniture?**
On a 2 cm top-down grid, a cell is wall when wall-facing points cover at least half of the
height band the capture observed (from 0.3 m up to at most 1.9 m, never less than 0.5 m).
Furniture is low or short; a door lintel is wall above 2.15 m with nothing below. The band
is relative because a capture held low never sees walls above ~1.6 m (fix loop round 1).

**How do you get from walls to rooms?**
Free floor sealed by walls and lintels, with wall gaps under 50 cm closed. A region that
leaks through a large unscanned gap keeps only cells that have a wall in all four
directions. Regions joined through a narrow neck are split unless the opening is as wide
as the room and the ceilings match. Each outline is made axis-aligned and every edge is
snapped onto the wall plane measured in the 3D points.

**Why can a wall edge move up to 0.9 m when it is snapped?**
A room's floor region stops at furniture in front of a wall, so the true wall can be well
behind the outline. The search stops at the next room, though: without that limit, edges
landed on surfaces inside neighbouring rooms and outlines overlapped (1.2-3.8 m² per flat).

**How are doors and windows found?**
For each wall, a grid of position × height. Camera rays that stop at the wall are hits;
rays that pass through and land beyond are passes. Pass regions are openings: a door if it
reaches the floor, else a window. Width is read at 1 cm.

**Mirrors, glass, wet-look surfaces, low light?**
Mirror: what is seen "through" an opening is reflected back across the wall plane; if it
lands on real surfaces of the room it is a mirror and dropped. Glossy floor: the floor rule
above. Glass to bright outdoors returns no LiDAR depth, so a window can be missed (a known
failure mode). Low light: the run warns when frames are dark. Mirrors and glossy floors are
tested on synthetic data only.

## Drift

**What do you do about drift?**
The trajectory is cut into 3 s submaps. Submaps that revisit a place are aligned with
point-to-plane ICP (accepted when fitness ≥ 0.35, RMSE ≤ 2 cm, correction ≤ 0.5 m and 8°).
An Open3D pose graph spreads the corrections: odometry edges weighted by ARKit's measured
error, loop edges by their ICP information, and closures that stay inconsistent pruned.
Only heading and position change; gravity from the phone is kept. `--drift off` gives the
ablation the brief asks for.

**How do you know it helps?**
Wall crispness (how sharply the same wall lands in one place across the capture) goes up
with it on: scan A 8.43 → 8.84, scan B 10.02 → 12.26. Scan B moves by up to 0.52 m.

**Why "measured" weights? What were they before?**
1 cm and 0.29° per step, 2-4× (in variance) tighter than ARKit's real error. Open3D weights
a closure by its error before the first step; with stiff odometry the graph cannot bend, so
every closure that asked for a big correction was pruned and the large drift was the drift
never corrected. A synthetic loop shows the threshold: the old weights work to 0.25 m of
drift and do nothing at 0.5 m (`tests/test_drift.py`). The pruning distance went from 3 to
5.6 cm (1.4 × the submap voxel, Open3D's own setting) because Open3D's tolerance scales with
the mean edge information; that keeps pruning as strict as before (7.8 vs 9.0).

**What drift is left?**
Up to 7 cm on a wall seen only in a scan's first seconds, while tracking is still settling:
a submap moves as one rigid piece, so a slide inside its 3 s stays.

## Video and photo tiers

**Why no COLMAP?**
Its Python package is blocked by Application Control on the development machine. The video
tier tracks corners with KLT at ~25 fps, keeps keyframes that share ≥ 320 tracks, takes depth
from Depth Anything V2 (metric, indoor, small) and solves each keyframe's pose with PnP.

**Where does the video's metric scale come from?**
The depth model's own scale, as the median over all keyframes, divided by 1.137. One frame's
scale is unreliable (0.4-3.4× against LiDAR); the many-frame median is stable (1.09-1.18×).
That cannot reach the ±3 % gate, so the video tier's intervals are wide.

**Why did the video tier find so few rooms?**
On the benchmark clip, fast sweeps and blank walls break tracking; only connected segments
survive. Separately, iPhone clips filmed upright carry a rotation flag OpenCV ignores by
default, so every upright clip used to be processed sideways (one room: 0.31 m² sideways,
10.1 m² upright, LiDAR 10.6). That is fixed and the benchmark clip now carries the flag.

**How does the photo tier build rooms without matching photos?**
White walls give almost nothing to match (SIFT: 8 of 703 pairs). The protocol fixes one
standpoint per room (the doorway) and a left-to-right sweep. Each photo's heading comes
from the wall direction (up to a quarter turn) and the sweep order; depth from the model;
scale from a shared camera height; the room is the rectangle bounded by the outermost wall
planes. Rooms are joined by a look-back photo matched with EfficientLoFTR + PnP, or placed
in capture order and flagged.

**Why is the photo tier so far off on the benchmark?**
Its photo set is cut from a LiDAR scan's video, not taken per the protocol: estimated camera
heights range from 0.4 to 1.7 m and two rooms show no floor (scale then assumes 1.4 m).
Protocol photos are the real test; that is part of the iPhone session.

## Damage

**Why CLIP and not an object detector?**
Grounding DINO took 8 s per image on CPU and reported stains and holes on undamaged walls.
CLIP scores image tiles against damage and benign prompts, and only tiles the geometry says
lie on a wall, floor or ceiling are scored, so pictures and furniture are not candidates.

**How is the area in m² measured?**
Inside a positive tile, pixels whose colour departs from the local surface colour form the
region; each pixel's footprint on the surface plane is summed. When colour gives nothing,
finer CLIP tiles outline it, with a wider interval.

**Concealed damage?**
Ten rules in `damage/rules.yaml`, e.g. CD-01 "water stain on a ceiling means water entered
from above the ceiling" or CD-04 "moisture damage on a wall that backs onto a bathroom-sized
room suggests a plumbing leak inside the shared wall". A fired rule writes its id and
sentence into the output. Rules, not a model: they must be explainable, and there is no
training data.

## Uncertainty

**How is each 90 % interval computed?**
`sigma = scale × sqrt((inflate·raw)² + abs² + (rel·value)²)`: raw from the plane fits,
abs/rel/inflate as per-tier priors, scale fitted per tier by split conformal on the
benchmark: the (n+1)·0.9 quantile of |error| / sigma.

**Calibrated against what?**
Video and photo against the LiDAR output of the same capture (labelled as a reference, not
truth). LiDAR, with no laser truth yet, against the same wall measured in two scans of the
flat: precision only, a bias both scans share is invisible. The honest number is
leave-one-room-out coverage (fit on the other rooms, score the room left out): 0.95, 0.91,
0.90. In-sample coverage is 90 %+ by construction and is not evidence.

**Why are photo intervals so wide?**
Because photo errors on our data are that large; a narrow interval would be confident
garbage, which the brief penalises most. Thinner data never gets a narrower interval than
richer data (checked per measurement in `calibrate.py`).

## Fix loop

**Round 1.** Worst gate: LiDAR repeatability (9 vs 6 rooms, footprint −45 %). Root cause:
the wall test demanded evidence up to a fixed 1.9 m, and scan B never looked above ~1.6 m.
Evidence: cutting scan A's points above a height reproduced the collapse. Fix: the band ends
where the capture's own wall evidence ends. Result −45 % → −18 %; the prediction was badly
wrong, and the post-mortem blamed furniture.

**Round 2.** Tested that post-mortem first: where B puts the bedroom wall, A has 6 points
and its own wall is 43 cm further out, so not furniture. Real cause: B's drift was never
corrected (above). Evidence: B saw that wall only in its first 10 s; the correction moved
nothing (2.8 cm) and kept 1 of 7 closures; measured odometry error; the synthetic threshold.
Fix: measured weights, plus the unsealed-room fallback on gap-closed walls. Every declared
prediction held; the gate still fails because B, without an upward sweep, has no door heads
or ceilings, so it divides the flat into different rooms.

**What did you get wrong, and how did you find out?**
The round 1 post-mortem (furniture), found by testing it before building on it. The round 2
footprint headline (−0.9 %) was mostly overlapping room outlines counted twice; by union it
was −6.3 %, and after fixing the overlap −10.4 %. Both are written up next to the original
claims.

## Reproducibility and the walk-in

**Can every number be regenerated?**
`scripts/fetch_data.py` downloads the scans, `scripts/fetch_weights.py` the models (checked
offline), `bench/run_all.py` reruns everything. The photo set is rebuilt byte for byte from
a recipe and the upright clip by a script, both as manifest `prepare:` steps. Caches are
keyed on content (the fused cloud on the poses), so a code change is never served an old
result.

**What happens at the walk-in?**
The examiners' drive goes in, `uv run python scripts/walkin.py E:\` finds the capture in
whatever form it arrives, runs cold with all models offline, and prints a per-room table
(each wall with its 90 % interval and which side of `plan.png` it is on) to check against
the laser on the spot. Bad input stops with one line before the long run.

**What would you do with more time?**
Real captures with laser truth at all three tiers, to refit the intervals on truth and to
fix whatever fails first; room splitting that does not depend on ceilings and door heads
being visible; a video front end that recovers from tracking breaks; GPU-free speedups of
the damage stage.
