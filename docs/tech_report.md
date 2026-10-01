# Technical report

roomscan: phone capture → measured, stitched floor plan with damage assessment.
Six pages maximum. Structure in [architecture.md](architecture.md), history in
[worklog.md](worklog.md), numbers in `fixloop/after/benchmark.md`.

## 1. Summary

One command turns a LiDAR scan, a video or per-room photo folders into a JSON plan and a
rendered plan, with a 90 % interval on every number. All three tiers run end to end on a
CPU-only laptop. The LiDAR geometry is precise (synthetic rooms recovered to under 1 mm)
but room segmentation is not yet repeatable across two scans of the same flat. The video
and photo tiers are far from their gates on the only data available, which is derived
from a LiDAR scan rather than captured per protocol.

**The main limitation of this report: no ground truth.** No iPhone and no laser measurer
were available. All real-capture numbers are either self-consistency (repeatability,
drift) or comparisons against our own LiDAR output, and are labelled as such.

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
  observed height band (fix-loop change). Door lintels are wall above 2.15 m with nothing
  below.
- **Rooms:** wall-sealed regions, split at narrow necks unless open-plan with equal
  ceilings. Outlines are made rectilinear and each edge snapped onto its 3D wall plane.
- **Openings:** per wall, camera rays that pass through versus stop at the plane. Widths
  at 1 cm. Mirrors are rejected when the "through" points, reflected back, land on real
  surfaces of the room.

## 5. Drift handling

The trajectory is cut into 3 s submaps. Revisits are aligned with point-to-plane ICP, and
an Open3D pose graph spreads the correction. Gravity is kept from ARKit and only heading
and position change.

| Capture | Drift | Footprint m² | Wall crispness | Loop residual |
|---|---|---|---|---|
| A | off | 47.1 | 8.43 | n/a |
| A | on | 50.2 | 8.36 | 8.3 → 2.7 cm |
| B | off | 40.9 | 10.02 | n/a |
| B | on | 41.2 | 9.58 | 16.9 → 2.5 cm |

Loop closure removes the measurable loop error but does not sharpen walls, so ARKit
drift on these captures is small. A heading snap to wall directions was tried and left
off: it blurred walls (crispness 8.4 → 6.5) because 3 s submaps hold too little wall.

## 6. Error budget (LiDAR tier)

| Source | Size | Evidence |
|---|---|---|
| Depth noise on a wall plane | ~1 cm per point, < 2 mm on a fitted plane of 1,000+ points | plane spread in `result.json` |
| Plane fitting on synthetic rooms | < 1 mm | `tests/test_geometry.py` |
| Residual drift after loop closure | ~2.5 cm across a flat | ablation table |
| Wall occluded by furniture (no upper sweep) | 30-50 cm per side | fix-loop post-mortem |
| Segmentation differences between captures | whole rooms | repeatability before / after |

The budget is dominated by segmentation and occlusion, not by sensor noise.

## 7. Calibration analysis

Every measurement uses `sigma = sqrt((k·raw)² + abs² + (rel·value)²)`. The per-tier
terms in `calibration.yaml` are priors. Against the LiDAR reference, 90 % intervals
contain the reference value 50 % of the time for video and 27 % for photos: **both are
far too narrow.** LiDAR coverage cannot be measured without ground truth. Missing
quantities are reported as missing: an unscanned ceiling is `null` with a lower bound.

The harness to refit the terms exists (`bench/evaluate.py` reports coverage). The data
to refit them (laser ground truth) does not.

## 8. Fix loop

Worst gate: LiDAR repeatability. Two scans of one flat gave 9 vs 6 rooms and 50.1 vs
27.7 m². An experiment ruled out the first hypothesis (missing door lintels) and pointed
to a wall test that demanded evidence above the height scan B ever looked at. The
declaration was committed before the fix.

The fix closed the footprint gap from −45 % to −18 %. The prediction (±10 %, 6+ rooms
paired, ≤ 5 cm walls) was badly wrong. It exposed a second cause: furniture occluding
the low part of walls. The full post-mortem is in `fixloop/README.md`, including why the
evidence experiment could not have shown that second cause.

## 9. Known failure modes

- **Scans without an upward sweep:** walls snap to furniture fronts (fix-loop finding).
- **Open-plan areas and corridors:** room boundaries differ between captures; rooms split
  or merge.
- **Video tier:** fast sweeps, motion blur and blank walls break tracking into segments;
  only the longest survives (1 of 6 rooms on the sample clip). Scale is ±10-20 %.
- **Photo tier:** fails when the floor is out of view (scale from an assumed phone height)
  or the look-back photo cannot be matched (position along the wall is guessed and flagged).
- **Damage:** 3 small water-stain false positives on the undamaged flat in the latest run
  (ceiling and floor, about 0.02 m² each, single frame each). A synthetic stain was found
  but its area under-measured.
- **Closed doors** are measured as wall; the protocol asks for doors open.
- **Mirrors:** rejected by a reflection test, verified only in code, not on a real mirror.
- **Glass:** windows to bright outdoors return no depth and may be missed.
- **Low light:** produces a warning only; no handling.
- **Wet-look surfaces:** not tested.
- **Not measured at all:** head-to-head against a consumer app, interval calibration,
  timing of the clean-machine install.
