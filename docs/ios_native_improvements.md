# Native scanning and local preview improvements

## Research and implementation choices

- magicplan Auto-Scan uses Apple's RoomPlan, with multi-room scanning, live 2D/3D switching, capture guidance and review of incomplete walls: https://help.magicplan.app/auto-scan-your-floor-plan
- RoomScan Pro combines its own LiDAR workflows with optional RoomPlan, door-based room joining, measurement correction and exports: https://www.locometric.com/lidar and https://www.locometric.com/lidar-faq
- Apple supports custom capture interfaces and parametric room geometry through RoomPlan: https://developer.apple.com/augmented-reality/roomplan/

RoomPlan remains the native capture engine. Replacing it without device measurements would not establish an accuracy or speed improvement. This change targets observable code issues and adds local visualization rather than implementing a new SLAM engine.

## Changes

- A nonblocking single encoding slot bounds retained camera buffers. When encoding is busy, skip that sampling tick before allocating a frame index. The scanner never waits for JPEG compression. Core Image intermediate caching is disabled.
- A cancelled scan cannot deliver a late RoomBuilder result; its sampling timer stops when the screen disappears.
- Live SwiftUI Canvas floor plans use captured walls, floors and opening widths. Saved-room navigation provides a 2D/3D switch with an orbitable SceneKit wall model. These previews do not need the server. The 3D view is a structural wall preview, not a textured mesh, and does not cut holes for openings.
- Closed unordered wall chains provide concave room area when a floor polygon is missing; a missing wall prevents that chain from being used. Polygon area uses double precision and a local origin. The existing approximate bounding-box fallback remains for unclosed boundaries.
- StructureBuilder only receives rooms sharing an AR session. Matching processed rooms by identifier keeps names and photo associations intact. Empty exports throw instead of indexing an empty array.

## Synthetic verification

Run from the repository root:

```sh
PYTHONPATH=src .venv/bin/python -m pytest tests/test_roomplan.py tests/test_roomplan_randomized.py -q
PYTHONPATH=src .venv/bin/python scripts/synthetic_roomplan.py --run
```

The fixture generator writes merged, shuffled/noisy/no-floor, separate-frame and single-room captures, synthetic JPEGs and local JSON/PNG/SVG reports under `runs/synthetic-roomplan`. Eight seeded randomized cases rotate/translate the entire world and shuffle walls, checking room area, ceiling height, footprint and adjacency. These test the existing processing engine, not the Swift runtime.

Native XCTest cases cover an L-shaped boundary, missing walls, area far from origin and empty home export. Run them with the existing iOS Xcode/CI scheme. Device validation must still check encoding memory pressure, scan cancellation, multi-room registration, preview gestures and real LiDAR accuracy. No device speedup or accuracy improvement has been measured here.

## Local validation (2026-11-15)

- Targeted processing suite: **28 passed in 8.24 s**.
- Fixture pipeline completed: merged 21.00 m², noisy/no-floor 20.98 m², separate frames 21.00 m², single room 12.00 m². The merged PNG was visually inspected.
- `git diff --check` and environment dependency checks passed.
- This Linux smoke run used Python 3.12 (available on this host), NumPy 1.26.4 and OpenCV 4.11 in an isolated `.venv`. It is not a locked Python 3.11 release-environment validation. No heavy ML packages were needed for these RoomPlan processing tests.
- Xcode build, native XCTest and physical-device performance/accuracy tests were not run on this Linux host.

## Capture-quality and sensor-data follow-up

Additional Swift changes:

- Explicit camera authorization and LiDAR-device checks before starting RoomCaptureView.
- RoomPlan coaching messages for speed, distance, lighting and texture; AR camera tracking guidance without taking ownership of RoomPlan's ARSession delegate.
- Disable finishing until tracking is normal and at least three walls are present. Before saving, review final 2D/3D geometry, incomplete boundaries and low-confidence walls. This is a review aid, not proof of scan completeness.
- Under serious thermal pressure, photo sampling slows from 0.7 s to 2.1 s; critical pressure suppresses photo/depth encoding while still allowing the user to finish. The low-light threshold and sampling intervals are heuristics awaiting device calibration.
- Backgrounding pauses AR and cancels an unfinished capture. Scan metadata uses the session ID captured at scan start, avoiding an ID changed by a lifecycle callback.
- iPad is included in the device family, with portrait and landscape orientations.
- JPEG intrinsics use actual output width and height, including raster rounding.
- Optional sampled `sceneDepth` and confidence data are exported when already supplied by RoomPlan's session. No competing AR configuration is run. Absent sceneDepth is supported; raw depth is not guaranteed on every capture.

Depth attachments are optional members of each frame in `capture.json`: `file`, `format: float32_le_metres`, `width`, `height`, scaled column-major `intrinsics`, `valid_fraction`, `confidence_file` and `confidence_format: uint8_arkit_confidence`. Depth values are tightly packed row-major little-endian Float32 metres, nonfinite/nonpositive samples are zero. Confidence values are tightly packed UInt8 ARKit confidence levels. `valid_fraction` counts finite positive samples with medium/high confidence, or finite positive samples when confidence is unavailable. Rows are copied using the pixel buffer's stride; image padding is excluded. Files are included in the ZIP and removed when discarding a room. These attachments are diagnostic/research data: the existing backend still builds geometry from RoomPlan surfaces and does not consume raw depth.

Apple references:

- Capture conditions, guidance and thermal considerations: https://developer.apple.com/videos/play/wwdc2022/10127/
- Coaching instruction cases: https://developer.apple.com/documentation/roomplan/roomcapturesession/instruction
- Depth and confidence data: https://developer.apple.com/documentation/arkit/ardepthdata
- Confidence limitations on reflective/dark surfaces: https://developer.apple.com/documentation/arkit/ardepthdata/confidencemap

### Additional validation

- **29 processing tests passed in 31.99 s**, including optional depth attachment backward compatibility.
- All **19 Swift files** parsed without syntax errors using tree-sitter-swift. This does **not** type-check Apple SDK calls or replace an Xcode build.
- Native tests were added for tracking/thermal decisions, padded depth-buffer packing, invalid depth values, scaled intrinsics and ZIP metadata/files. These XCTest cases remain unexecuted here.
- Regenerate clearly marked synthetic RGB/depth/confidence samples with:

```sh
PYTHONPATH=src .venv/bin/python scripts/synthetic_roomplan.py --run --depth
```

Synthetic depth is a flat 2 m plane for format tests only; it is not a real LiDAR scan or an accuracy benchmark.

## What is still needed for release quality

1. Build and run native tests in Xcode/macOS or the existing GitHub Actions workflow, then install on a physical LiDAR iPhone/iPad. Syntax parsing cannot establish SDK compatibility.
2. Collect consented device scans with laser/tape reference measurements: rectangular and L-shaped rooms, connected rooms, open plans, glass/mirrors, dark walls, clutter and low light. Repeat each capture on multiple LiDAR device generations and iPad orientations. Include the RoomPlan geometry, exported RGB/depth/confidence, device/OS version, and reference lengths with measurement uncertainty.
3. Set acceptance targets before tuning: wall-length error percentiles, area error, registration drift across rooms, repeatability, missing openings, build time, frame retention, peak memory and sustained thermal behavior. Compare against magicplan/RoomScan on the same rooms and route. No measured head-to-head result exists yet.
4. Profile with Instruments (Allocations, Time Profiler, Energy Log and thermal states), including long sessions, camera denial, calls/backgrounding, full storage and failed exports.
5. Add durable offline room-library recovery, measurement correction and editing, and deliberate room joining/relocalization workflows. The current app still depends on a server project to start scanning, and its scanned-room list is held in memory. ZIP/media files survive independently, but the list is not automatically restored.
6. If raw geometry is required beyond RoomPlan, build and evaluate a separate ARKit/Metal capture mode for confidence-filtered voxel fusion and mesh export. It must have its own session lifecycle; launching it over RoomPlan's live session is not a valid shortcut. Raw depth attachments alone do not implement reconstruction, curved-wall support or textured meshes.
