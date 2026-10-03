# Architecture

roomscan turns a phone capture (photos, a video, or an iPhone LiDAR scan) into a measured,
stitched floor plan. Each plan comes with damage regions, concealed-damage flags, repair
scope items, and a 90 % interval on every number.

This file covers how the code is laid out and how data flows through it. Everything here was
read from the code. Where the code and older docs disagree, the code wins. History:
[worklog.md](worklog.md). Error budget and failure modes: [tech_report.md](tech_report.md).
Hosting walkthrough: [deploy.md](deploy.md). API reference: [server/README.md](../server/README.md).

---

## 1. Overview

### 1.1 Web path

```
 iPhone / browser
 ┌───────────────────────────────┐
 │ web/ (static: HTML, ES modules)│  photos shrunk to 2048 px in the browser,
 │ files queued in IndexedDB,     │  SHA-256 per file, 3 uploads in parallel
 │ ids in localStorage            │
 └──────────────┬────────────────┘
                │ served by Vercel (web/vercel.json, no build step)
                │ API base from web/env.js -> web/config.js
                │   localhost / 127.0.0.1  -> http://localhost:8000
                │   anything else          -> https://34-14-174-240.sslip.io
                ▼  HTTPS, JSON + multipart, all under /api
 ┌──────────────────────────────────────────────────────────────────────┐
 │ VM (Ubuntu), set up by deploy/oracle/setup.sh                        │
 │  caddy:2 container (--network host): TLS for <ip-dashes>.sslip.io,   │
 │     request body <= 2 GB, proxy read/write timeout 30 min            │
 │        │ reverse_proxy 127.0.0.1:7860                                │
 │        ▼                                                             │
 │  roomscan container (server/Dockerfile, --restart unless-stopped)    │
 │   uvicorn server.app:app  (FastAPI, one process)                     │
 │    ├─ server/store.py   JSON state + uploads under DATA_DIR=/data    │
 │    │                    (host: /opt/roomscan-data)                   │
 │    ├─ server/capture.py project -> engine runs, verify               │
 │    ├─ server/jobs.py    one worker thread, one job at a time         │
 │    │     └─ roomscan.pipeline.run(...)  per run  ──► out/<run>/      │
 │    │            result.json, result.xlsx, plan.png, plan.svg,        │
 │    │            stages.json                                          │
 │    └─ server/notify.py  optional email via SMTP (Gmail by default),  │
 │                         sent when the job ends                       │
 └──────────────────────────────────────────────────────────────────────┘
```

### 1.2 CLI path (one command per capture)

```
 capture (Stray Scanner folder or .zip | .mov/.mp4/.m4v | folder of room folders)
    │
    ├─ uv run roomscan run <capture> [--out DIR] [--tier ...] [--drift ...] [--no-damage] [--no-cache]
    │      roomscan.cli:run -> roomscan.pipeline.run -> runs/<capture name>/
    │      (the CLI also writes result.xlsx)
    │
    └─ uv run python scripts/walkin.py <capture or drive root> [--check-only] [--tier ...] [--no-damage]
           capture_quality check (OK / WARN / RETAKE) -> cold pipeline.run in a temp cwd
           -> runs/walkin_<capture>_<date>_<time>/ + walkin.txt (per-room table)
```

### 1.3 Engine (shared by both paths)

```
 LiDAR (Stray)  -> frontends/lidar_stray.py ─┐
 video          -> frontends/video.py ───────┴─► PosedCapture ─► drift ─► fuse ─► layout ─► openings ─┐
 photo folders  -> frontends/photos.py: depth ─► room_fit (rectangles) ─► stitch ─► openings ──────────┤
                                                                                                      ▼
                                       damage (CLIP) ─► rules.yaml flags ─► scope.yaml ─► export (JSON, plan, stages.json)
```

---

## 2. Folder structure

Skipped: `.venv/`, `.git/`, `__pycache__/`, `.pytest_cache/`.

```
roomscan/
├── README.md                    setup, run, reproduce, timings, model disclosure
├── pyproject.toml, uv.lock      Python 3.11 only; extras: ml (torch, transformers), server (fastapi,
│                                uvicorn, python-multipart), dev (pytest, httpx); script `roomscan`
├── .dockerignore                build context for server/Dockerfile
├── schema/output.schema.json    JSON Schema of result.json (written by `roomscan schema` from schema.py)
├── src/roomscan/
│   ├── __init__.py              __version__ = "0.1.0"
│   ├── cli.py                   `roomscan run`, `roomscan schema` (typer)
│   ├── pipeline.py              input prep (.zip, unwrap), tier detection, stage order, caches, result.json
│   ├── stages.py                PLAN per tier, Stages queue, gate_* checks, stages.json
│   ├── capture.py               Frame / PosedCapture: the common form every non-photo tier produces
│   ├── schema.py                output contract as pydantic models (SCHEMA_VERSION 1.0.0)
│   ├── capture_quality.py       fast pre-run checks: OK / WARN / RETAKE with advice
│   ├── known_sizes.py           measurements.yaml: parse, match to rooms, rescale, tighten intervals
│   ├── markers.py               printed A4 ArUco scale marker (detection + scale; photo per room, video per clip)
│   ├── heif.py                  optional HEIC/HEIF decoder (pillow-heif), loaded once
│   ├── frontends/
│   │   ├── lidar_stray.py       Stray Scanner export -> PosedCapture
│   │   ├── video.py             clip -> KLT tracks, keyframe depth, PnP poses, gravity -> PosedCapture
│   │   └── photos.py            room folders -> per-room rectangles, look-back stitching, whole photo tier
│   ├── ml/
│   │   ├── depth.py             Depth Anything V2 Metric Indoor Small; DEPTH_SCALE_BIAS = 1.137
│   │   ├── matching.py          EfficientLoFTR (look-back photo matching)
│   │   └── hub.py               load weights from the HF cache first, download only if missing
│   ├── geometry/
│   │   ├── pointcloud.py        depth -> points + normals, voxel fusion
│   │   ├── planes.py            floor / ceiling / wall-top levels, Manhattan yaw
│   │   ├── layout.py            rooms + walls from the fused cloud (2 cm raster, snapped outlines)
│   │   ├── boxfit.py            rectangular room fit (photo tier; fallback for open video layouts)
│   │   ├── openings.py          doors / windows / openings from ray pass-through, mirror rejection
│   │   ├── drift.py             submap pose-graph loop closure (Open3D)
│   │   └── metrics.py           wall crispness (map self-consistency)
│   ├── damage/
│   │   ├── detect.py            surface assignment, CLIP tile classification, region outline + size
│   │   ├── pipeline.py          regions -> concealed-damage flags -> scope items
│   │   ├── rules.yaml           10 concealed-damage rules, CD-01 .. CD-10
│   │   └── scope.yaml           repair line items per damage class and surface type
│   ├── uncertainty/
│   │   ├── intervals.py         measure(): value + raw sigma -> Measurement with ci90
│   │   └── calibration.yaml     per-tier, per-kind inflate / abs / rel / scale
│   └── export/
│       ├── build.py             assembles schema.Output (rooms, walls, surfaces, adjacency, footprint)
│       ├── render.py            plan.png + plan.svg (matplotlib)
│       └── sheet.py             result.json -> result.xlsx (also `python -m roomscan.export.sheet`)
├── server/                      web API (FastAPI)
│   ├── app.py                   routes, request models, upload handling, env config
│   ├── capture.py               project -> runs (materialise), verify, content / cache keys
│   ├── jobs.py                  Worker thread, job state, stage reporting, comparison endpoint logic
│   ├── store.py                 on-disk JSON store under DATA_DIR, TTL cleanup, engine_version()
│   ├── notify.py                optional SMTP email when a job finishes
│   ├── Dockerfile               CPU image (python:3.11-slim, CPU torch, weights baked in), port 7860
│   ├── Dockerfile.dockerignore  context filter for that Dockerfile
│   └── README.md                API reference
├── web/                         static front end, no build step
│   ├── index.html               single page
│   ├── env.js                   deployment's API origin (classic script)
│   ├── config.js                API base resolution (?api=, localStorage, env.js, default)
│   ├── styles.css, favicon.svg, img/guide/*.svg   styles and capture-guide figures
│   ├── vercel.json              rewrites, security + cache headers
│   ├── js/app.js                state, rooms, whole-home captures, verify, run, polling
│   ├── js/results.js            job stages + results rendering, comparison table
│   ├── js/api.js                fetch client, multipart upload with progress (XHR)
│   ├── js/upload.js             upload queue (3 workers, retry with backoff)
│   ├── js/store.js              IndexedDB file queue + localStorage key/value
│   ├── js/shrink.js             JPEG downscale to 2048 px keeping EXIF
│   ├── js/sha256.js, js/dom.js  hashing, DOM helpers
│   └── dev/                     mock_server.py (stdlib mock API), smoke.html (headless smoke test)
├── deploy/
│   ├── oracle/setup.sh          VM setup: Docker, build, run, /etc/roomscan.env, Caddy HTTPS
│   └── hf-space/                Hugging Face Space variant: README (front matter), push.sh, github-action.yml
├── bench/                       benchmark: manifest, gates, evaluate, run_all, calibrate, repeatability,
│                                head_to_head, ground_truth/, app_exports/, reports/
├── scripts/                     walkin.py, fetch_weights.py, fetch_data.py, make_photo_set.py,
│                                make_iphone_clip.py, make_marker.py, tape_form.py,
│                                arkitscenes_to_stray.py, fetch_arkitscenes.py, dev_*.py
├── tests/                       pytest suite (section 10)
├── docs/                        capture protocol, deploy, tech report, design Q&A, matrices, worklog, this file
├── fixloop/                     fix-loop rounds: declaration, diffs, before/after runs
├── runs/                        CLI / walkin outputs (generated)
└── .cache/                      engine caches: cloud_*.npz, depth_*.npz, inputs/ (generated)
```

---

## 3. Inputs

### 3.1 Capture types

| Tier | What | Accepted formats | Limits |
|---|---|---|---|
| photo | one folder per room, 2 to 8 photos from the doorway | `.heic`, `.heif`, `.jpg`, `.jpeg`, `.png`, any case | engine uses at most `MAX_PER_ROOM = 8` per room (`frontends/photos.py`): the last photo plus 7 evenly spaced others |
| video | one clip of the whole home | `.mp4`, `.mov`, `.m4v` (H.264 / HEVC, 8 or 10 bit, any frame rate, VFR, rotation flag applied) | web: up to 5 clips per project (`MAX_ITEMS = 5`), each its own run |
| LiDAR | Stray Scanner export, as `.zip` or as loose files | see 3.2 | web: up to 5 scans per project, each its own run |
| RoomPlan | our iOS app's export: `capture.json` + `frames/*.jpg` + `room.usdz`, one `.zip` per room (uploaded into the LiDAR capture) | see 3.6 | web: shares the LiDAR capture's 5 items, one run each, plus a combined run per session |

**Tier detection** (`pipeline.detect_tier`, used when `--tier auto`):

1. A video file is the video tier. An image file is the photo tier.
2. A folder holding a `capture.json` (itself, up to 2 levels down, or zips of such captures)
   is the RoomPlan tier.
3. A folder holding a Stray export (itself or up to 2 levels down) is the LiDAR tier.
4. Otherwise a folder with images (directly or in sub-folders) is the photo tier.
5. Otherwise a folder with clips is the video tier. Live Photo `.MOV` files next to a
   same-stem image are ignored.

A `.zip`, or a folder holding only one `.zip`, is first extracted to `.cache/inputs/<key>/`.
ProRAW `.dng` is rejected with advice. Hidden and OS files (`.DS_Store`, `._*`, `Thumbs.db`,
`desktop.ini`, `__MACOSX`, `@eaDir`) are ignored.

**Photo naming and order** (`frontends/photos.py`, `server/capture.py`):

- Folders sorted in natural order give the walk order. The web server names them
  `NN_<room name>` in list order (`01_hall`, `02_kitchen`). A leading number in the user's
  name is stripped first.
- Files inside a folder are sorted in natural file-name order, which is also the sweep order
  (left to right from the doorway).
- In every folder except the first, the **last** photo is the look-back photo into the
  previous room. It is used only when the capture has more than one room and that room has
  3 or more photos.
- The same photo in two formats (`IMG_0001.HEIC` + `.JPG`) keeps one copy. An iOS edit
  `IMG_E0001` next to `IMG_0001` is dropped.
- Images directly in the capture folder form a single room. If room folders also exist,
  those loose images are ignored with a warning.
- A photo without an EXIF focal length uses the iPhone main-camera default (warning).

**Pre-run photo checks** (`capture_quality.py`, used by `walkin.py` and the server's verify):

| Condition | Level |
|---|---|
| more than 12 photos in a room | RETAKE |
| fewer than 2 or more than 8 photos | WARN |
| long side under 1600 px (chat-app copy) | RETAKE. The browser's 2048 px shrink stays above this |
| no EXIF focal length | WARN |
| blur, overlap, look-back found in fewer than half of the rooms | WARN |
| folders not numbered | WARN |
| server: a room with fewer than 2 photos | RETAKE |
| server: `.dng` file | RETAKE |
| server: other non-image files | WARN, ignored |

### 3.2 LiDAR zip contents (`frontends/lidar_stray.py`)

| Path | Required | Content |
|---|---|---|
| `odometry.csv` | **yes** | timestamp, frame, x, y, z, qx, qy, qz, qw (+ fx, fy, cx, cy) per frame |
| `depth/NNNNNN.png` | **yes** | 256×192 uint16 depth in mm (`.npy` in older app versions) |
| `rgb.mp4` | no | 1920×1440 colour. Without it there is no colour and no damage detection (verify: WARN "no rgb.mp4") |
| `confidence/NNNNNN.png` | no | ARKit confidence 0/1/2. Without it, depth is not confidence-filtered |
| `camera_matrix.csv` | only if `odometry.csv` lacks fx, fy, cx, cy | 3×3 intrinsics |
| `imu.csv` | no | unused |

A root is recognised when `odometry.csv` and `depth/` exist (`is_stray`), at the top or up to
two folder levels down. More than one export in one item is an error. The server's verify
extracts a zip "lite": it skips `depth/`, `confidence/` and `rgb.mp4` contents and writes
empty stand-ins, then runs `check_lidar`. That check covers duration (< 20 s WARN), looking up
at the ceiling (never: RETAKE), pose jumps over 0.30 m, and turn rate above 100 °/s at the
95th percentile.

### 3.3 Known sizes (`measurements.yaml`, `known_sizes.py`)

Where the file is found: `<capture folder>/measurements.yaml`, `<video>.measurements.yaml`,
or `<video stem>.measurements.yaml`. The CLI parameter `measurements=` (a dict or path) takes
precedence.

```yaml
rooms:
  01_hall:    {length: 4.20, width: 3.10, height: 2.60}   # any subset, metres
  02_kitchen: {height: 2.60}
  any:        {height: 2.50}      # every room without its own entry
# or a list without names (whole-home video / LiDAR): [{length, width, height}, ...]
```

**What each size means:**

- `length` is the longer side and `width` the shorter, wall to wall. They are swapped if
  given the wrong way round.
- `height` is floor to ceiling.
- Unnamed list entries are matched to rooms by aspect ratio, then by size rank.

**Validation:**

- Each number must be within 0.3 to 50 m, otherwise it is an error. Centimetres and
  millimetres are refused.
- Unknown keys are an error.
- Numbers that are used but look unusual are flagged in the warnings: a length or width over
  12 m, or a height outside 2.0 to 4.5 m ("a typo, or centimetres?").
- Given numbers implying scales more than 10 % apart are reported, and the intervals are widened.

**How each tier uses them:**

| Tier | Effect |
|---|---|
| photo | Per-room scale = median of given/fitted over length and width, applied before stitching. Rooms without a number get the median scale, unless the A4 marker is seen in that room (3.4). **A height alone is compared only, never used for scale.** |
| video | One global scale = median over the measured rooms. |
| LiDAR | Never rescaled. The differences are added to `warnings` as a self-check. |

A measured wall's interval shrinks to the tape's ±0.01 m (`TAPE_M`). A given height:

- when the ceiling was observed, the value is kept and a note "measured X; user gave Y (±%)"
  is added;
- when the ceiling was not observed, the user's number is used, labelled as such.

**Web app:** the per-room L / B / H fields become:

- for the photo run, `measurements.yaml` keyed by folder name;
- for each whole-home run, an unnamed list.

Server validation: `length` and `width` must be > 0 and < 1000, `height` > 0 and < 100. The
page warns (but still saves) when a value is below 0.3 or above 50.

### 3.4 A4 ArUco marker (`markers.py`, `scripts/make_marker.py`, `docs/scale_marker.md`)

The marker is `DICT_4X4_50` id 0 with a 180 mm black square, printed on A4. Detection uses
`solvePnP` (IPPE_SQUARE, LM-refined). A detection is accepted when:

- the side is at least 40 px;
- the tilt is at most 60°;
- the blur ratio is at most 0.06;
- the reprojection error is at most 1.5 px.

`marker_scale()` returns the median ratio of metric depth to model depth. It is used
automatically when the marker is seen, and nothing changes when it is not:

- **photo:** per room, on the room's sweep photos (`photos.apply_scales`), before stitching;
  it is not passed on to other rooms;
- **video:** once per clip, pooled over all keyframes (`video.clip_marker_scale`); it replaces
  the depth model's median scale.

Priority: typed length / width > marker > (photo) median of measured rooms > depth model.
With a marker scale the intervals' relative term is `max(spread, 2 %)` instead of the tier
prior (twice that for areas). The calibrated multiplier is unchanged. It is recorded in
`capture.meta.scale` and in a warning line. Details are in `docs/scale_marker.md`.

### 3.5 Size limits and browser-side handling

- **Server limit:** `ROOMSCAN_MAX_FILE_MB` (default 2048) per file. A larger upload returns 413.
- **Caddy limit:** request body `max_size 2GB`.
- **Photo shrinking** (`web/js/shrink.js`). Applied only to JPEGs of at least 1200 KB whose
  long side is over 2048 px:
  - re-encoded at quality 0.9 to 2048 px on the long side;
  - the original APP1 EXIF segment is copied in, so the focal length is kept, with
    orientation reset to 1;
  - the shrunk file is kept only if it is under 80 % of the original size;
  - HEIC, PNG, small files and decode failures are uploaded unchanged.
- **Upload queue** (`web/js/upload.js`, `web/js/store.js`):
  - Each picked file becomes a record in IndexedDB `roomscan` / store `files`, holding the
    Blob until the server confirms it.
  - `UPLOAD_WORKERS = 3` files upload in parallel. For each: SHA-256, skip if the server
    already lists that hash, otherwise a multipart PUT with progress.
  - Network, 5xx, 408 and 429 errors retry with exponential backoff from 1 s to 60 s, woken
    early by `online` or by the tab becoming visible. Other 4xx errors park the record in
    state `error` until Retry or Remove.
- **Client-side filters** (looser than the server):
  - photos: `image/*`, `.jpg`/`.jpeg`/`.heic`/`.heif`/`.png`/`.dng`/`.tif(f)`;
  - video: `.mp4`/`.mov`/`.m4v`/`.3gp`/`.webm`/`.mkv`;
  - LiDAR: `.zip`.

  The server treats only `.mp4`/`.mov`/`.m4v` as video items. Other files in a video capture
  are accepted, but verify warns that they are ignored.

### 3.6 RoomPlan tier (`frontends/roomplan.py`)

Our own iOS app (iPhone / iPad Pro with LiDAR, iOS 17+) runs Apple RoomPlan live capture
and writes one `.zip` per scan. RoomPlan has already done the geometry on the phone, so the
engine reads its walls, openings and floor directly. It builds no point cloud and runs no
depth model.

**Input** (zip or folder):

| Path | Required | Content |
|---|---|---|
| `capture.json` | **yes** | the contract below |
| `frames/*.jpg` | no | colour keyframes named in `capture.json`. Without them there is no damage detection (verify: WARN) |
| `room.usdz` | no | RoomPlan's 3D model. Not read |

`capture.json` (`"format": "roomscan.roomplan/1"`, fixed contract with the app):

```json
{"format": "roomscan.roomplan/1", "app_version": "0.1.0",
 "device": {"model": "iPhone16,1", "system": "iOS 17.5"}, "captured_at": "2026-10-03T14:00:00Z",
 "units": "m", "coordinate_frame": "arkit_world_y_up", "merged": true,
 "rooms": [{"name": "Kitchen", "index": 0, "story": 0,
   "walls": [{"id": "uuid", "start": [x, z], "end": [x, z], "height": 2.6, "thickness": 0.0, "confidence": "high|medium|low"}],
   "doors": [{"id": "uuid", "wall_id": "uuid-or-null", "center": [x, y, z], "width": 0.9, "height": 2.1, "confidence": "high", "is_open": null}],
   "windows": [...], "openings": [...],
   "floor": {"polygon": [[x, z], ...], "y": 0.0},
   "objects": [{"category": "bed", "center": [x, y, z], "dimensions": [w, h, d], "yaw": 0.0}],
   "section_labels": ["kitchen"]}],
 "frames": [{"file": "frames/000001.jpg", "room_index": 0, "t": 12.34,
   "transform": [16 floats, column-major, camera-to-world, ARKit camera axes],
   "intrinsics": [9 floats, column-major], "width": 1920, "height": 1440}]}
```

Coordinates are the ARKit world (metres, +Y up). The plan is world (x, z) with no rotation.
`floor` may be `null`. An optional `session_id` (outside the contract) groups one-room zips
of one session. Every field is validated: a wrong format, unit, frame, number, list length,
`room_index` or non-invertible matrix raises `InputError` naming the field
(`capture.json: rooms[0].walls[3].end must be a list of 2 numbers`). A row-major transform
(last row `0 0 0 1`) is also accepted. Frames whose image is missing, unreadable, or not the
stated width × height (rotated) are skipped with a warning.

**Detection.** A folder with a `capture.json` (itself, up to 2 levels down, or `.zip` files
of such captures inside it) is the `roomplan` tier. It is checked before Stray Scanner. A
folder of several RoomPlan zips (the app uploads one per room) is **one** run.

**Geometry:**

- **Outline.** The floor polygon when given. Otherwise the walls are chained into a closed
  outline: endpoints within 5 cm are one corner, walls are ordered by connectivity, and the
  outline is turned counter-clockwise. If they do not form one closed loop, the convex hull of
  the wall ends is used, with a warning.
- **Walls.** RoomPlan's walls in outline order. Length is the segment length. Segments under
  5 cm are dropped. `evidence_coverage` is 1.0.
- **Ceiling.** Height = median wall height, `ceiling_source: "roomplan"` (a measurement, not a
  lower bound).
- **Floor height.** `floor.y`. Without it, the doors' bottoms. Without doors, 0 (warning).
- **Openings.** Doors, windows and openings go on the wall named by `wall_id`, otherwise on
  the nearest wall. Width and height are kept. Sill = centre y − height/2 − floor. A door or
  opening within 0.3 m of another room's outline connects the two rooms.
- **Adjacency.** By door or opening as above, and by shared wall (parallel walls within
  0.4 m overlapping > 0.5 m, as in the other tiers).
- **`merged: false`.** The rooms have independent frames, so each room and its frames are
  shifted to sit in a row 1 m apart. Adjacency between them is unknown (warning). Several
  captures are kept in their world frame only when all say `merged: true`, share a session,
  and no two rooms overlap by more than 0.5 m². Otherwise they go side by side (warning).
- **Objects** (furniture) are copied to `capture.meta.rooms[].objects`.
- **Known sizes** are compared only (`lidar_check(..., label="RoomPlan")`). They never set
  the scale.

**Damage.** RoomPlan records no depth, so each frame's depth (256 px wide) is ray-cast from
its pose and intrinsics against the RoomPlan walls (doors and openings cut out), floors and
ceilings (`PlaneScene`). The poses are ARKit camera axes, converted to OpenCV
(`T @ diag(1, -1, -1, 1)`). The unchanged LiDAR damage code (`damage.detect`) then assigns
pixels to surfaces with a 6 cm tolerance and runs the CLIP tiles. Regions therefore land on
RoomPlan surfaces with metric extent. Frames: at most 40 evenly spaced per room, thinned by
surface coverage. Known limit: furniture is not in the rendered depth, so a stain seen on a
sofa in front of a wall is put on the wall behind it.

**Stages** (`PLAN["roomplan"]`):

| Stage | What it does | Gate |
|---|---|---|
| `load` | find and parse every `capture.json`, check frames, side-by-side layout if needed | **fail** if no room has walls. **warn** with no frames |
| `layout` | outlines, walls, ceiling, plan raster for the damage code | `gate_layout` |
| `openings` | openings onto walls, rooms connected | `gate_openings` |
| `damage` | as above | `gate_damage` |
| `export` | `build_output(..., labels=room names)`, `result.json`, `plan.png` / `.svg` | `gate_export` |

A capture without frames runs in well under a second after the imports.

**Output.** The same contract as the other tiers:

- `capture.tier = "roomplan"` and `capture.source = "roomplan_app"`.
- `capture.meta` holds `device`, `app_version`, `captured_at`, `merged`, `frames` (count),
  `frames_used_for_damage` and `rooms[]`. Each room entry has its RoomPlan name, story,
  section labels, outline source and objects.
- Room `label` = first section label, else the room name (lower case).
- `property.stitch_method` = `roomplan_world_frame` or `roomplan_side_by_side`.

**Intervals.** The `roomplan` block in `calibration.yaml` is RoomPlan's typical error
(abs 1 cm + 0.4 % for lengths, 1 % for areas). It is **uncalibrated**: `scale` is 1.0 until
there is truth. The raw sigma per wall or opening comes from RoomPlan's confidence: high
0.01, medium 0.03, low 0.08 m. A wall's length sigma is at least √2 × its own sigma, so
medium and low walls get wider intervals. Areas propagate from the walls as in the other
tiers.

**Verify** (`capture_quality.check_roomplan`):

| Condition | Level |
|---|---|
| `capture.json` unreadable or invalid | RETAKE (the parser's message) |
| a room without walls | RETAKE |
| a room with low-confidence walls | WARN |
| no frames, or listed frames missing | WARN |
| otherwise | OK |

---

## 4. Backend API (`server/`)

Everything is JSON under `/api`, and errors are `{"detail": "..."}`. Interactive docs are at
`/docs`.

Ids are 12 hex characters (`uuid4().hex[:12]`). A malformed id is handled as not found (404).

### 4.1 Endpoints

| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/api/health` | none | `{ok: true, version: "<roomscan version>+<10-hex source hash>", email: bool}` | |
| POST | `/api/projects` | none (body ignored) | `{project_id}` | |
| GET | `/api/projects/{pid}` | none | `{project_id, spaces: [Space], captures: {lidar: Capture\|null, video: Capture\|null}, last_job_id}` | 404 |
| PUT | `/api/projects/{pid}/order` | `{space_ids: [str]}`: every space exactly once | the project | 404, 422 |
| POST | `/api/projects/{pid}/spaces` | SpaceIn (below) | Space | 404, 422 (`kind` other than `photos`; `video`/`lidar` point to the captures route) |
| PATCH | `/api/projects/{pid}/spaces/{sid}` | `{name?: str 1..120, sizes?: Sizes}`. Sizes are merged; a key sent as `null` clears it | Space | 404, 422 |
| DELETE | `/api/projects/{pid}/spaces/{sid}` | none | `{ok: true}`; deletes its files | 404 |
| GET | `/api/projects/{pid}/spaces/{sid}/files` | none | `{files: [File]}` | 404 |
| PUT | `/api/projects/{pid}/spaces/{sid}/files` | multipart: `file` (required), `sha256` (required, 64 hex, of the bytes), `name` (optional, may contain `/`, sanitised by `safe_relpath`) | File. Idempotent: the same hash returns the existing record | 400 hash mismatch, 404, 413 too large, 422 bad sha |
| DELETE | `/api/projects/{pid}/spaces/{sid}/files/{sha}` | none | `{ok: true}` | 404 |
| PUT | `/api/projects/{pid}/captures/{kind}` | `kind` = `video` or `lidar`; body optional (`{}`) | Capture: creates the container if absent, otherwise returns it unchanged | 404, 422 |
| GET | `/api/projects/{pid}/captures/{kind}` | none | Capture | 404 if none |
| DELETE | `/api/projects/{pid}/captures/{kind}` | none | `{ok: true}`; removes the container and its files | 404 |
| GET | `/api/projects/{pid}/captures/{kind}/files` | none | `{files: [File]}` | 404 |
| PUT | `/api/projects/{pid}/captures/{kind}/files` | multipart, as for a space | File | as for a space, plus **409** when the file would start a 6th item |
| DELETE | `/api/projects/{pid}/captures/{kind}/files/{sha}` | none | `{ok: true}` | 404 |
| POST | `/api/projects/{pid}/verify` | none | Verify (4.3); stored as `last_verify` with the content key | 404 |
| POST | `/api/projects/{pid}/run` | RunIn (below), body optional | `{job_id, cached: bool}` | 404, 409, 422 bad email |
| GET | `/api/jobs/{jid}` | none | Job (4.4) | 404 ("finished jobs are kept N days") |
| GET | `/api/jobs/{jid}/files/{name}` | `name` = `[<prefix>]result.json\|result.xlsx\|plan.png\|plan.svg\|stages.json` | the file | 404 when not an output or not yet written |
| GET | `/api/jobs/{jid}/comparison` | none | `{rows: [Row]}` (4.6) | 404, 409 if the job is not done |

**Body models** (`server/app.py`):

| Model | Field | Type | Required | Default | Validation |
|---|---|---|---|---|---|
| Sizes | `length` | float \| null | no | null | > 0, < 1000 |
| | `width` | float \| null | no | null | > 0, < 1000 |
| | `height` | float \| null | no | null | > 0, < 100 |
| SpaceIn | `name` | str | **yes** | | 1..120 characters, stripped |
| | `kind` | str | no | `"photos"` | must be `"photos"` |
| | `sizes` | Sizes | no | all null | |
| RunIn | `damage` | bool | no | `true` | |
| | `force` | bool | no | `false` | |
| | `email` | str \| null | no | null | must match `^[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}$` |

**Response shapes:**

- Space: `{space_id, name, kind: "photos", sizes: {length, width, height}, files: [File]}`
- File: `{name, sha256, size}`
- Capture: `{kind, files: [File]}`

**Items in a whole-home capture** (`capture_items`):

- every video file is one item;
- every `.zip` is one item;
- all loose LiDAR files together form one item, named after their top folder.

### 4.2 Run order and run planning (`server/capture.py: plan_runs`)

1. **Rooms (photos)**: all rooms that have photos, as **one** photo walk.
   - Label `photos`, title `Rooms (photos)`.
   - Folder per room `NN_<name>`, plus `measurements.yaml` from the typed sizes.
2. **One run per LiDAR item**, in upload order.
   - Label `whole_home_lidar`, or `whole_home_lidar_<i>` when there are 2 or more.
   - Title `Whole home (LiDAR: x.zip)`, or `Whole home (LiDAR 2: x.zip)`.
   - A LiDAR `.zip` holding a `capture.json` is a **RoomPlan** capture from our app
     (`roomplan_info` reads it from the zip; engine tier `roomplan`, section 3.6). Label
     `roomplan_<i>`. Title `Room (RoomPlan): Kitchen` for a one-room zip (the app uploads
     one per room), `Whole home (RoomPlan: Kitchen, Hall)` for several rooms. A repeated title
     gets the zip name added. The run reports on, and gets the typed sizes of, the rooms
     whose name matches a RoomPlan room name (all rooms when none matches).
   - Two or more RoomPlan zips with `merged: true` and the same `session_id` (or none) also
     get **one combined run** after the LiDAR items. Label `roomplan_home`, title
     `Whole home (RoomPlan: N rooms)`, each zip extracted into its own sub-folder. It does not
     count towards the 5 items, and verify checks its zips one by one.
3. **One run per video item**, in the same pattern (`whole_home_video[_i]`).

The order is set by `CAPTURE_KINDS = ("lidar", "video")`. LiDAR runs before video because it
is faster: about 2-3 min against 8-12 min on a CPU.

Each whole-home run receives every room's sizes as `measurements={"rooms": [...]}` (unnamed
list).

With more than one run, outputs go under `out/<label>/` and file URLs carry that prefix. Stage
names are `"<run title>: <stage>"`; the stage is the text after the last `": "`.

### 4.3 Verify

`POST /verify` materialises the project "lite" into a temporary folder and checks it.

**Rooms** (`verify_room`):

| Room state | Result |
|---|---|
| no photos, but a whole-home capture exists | OK: its sizes are a reference only |
| no photos and no capture | RETAKE |
| fewer than 2 photos | RETAKE |
| otherwise | `check_photos` per room |

**Whole-home items** (`verify_item`):

- video: `check_video`, which checks duration < 30 s (WARN), short side < 720 px (RETAKE),
  motion, blur, blank frames and decode failure;
- LiDAR: the Stray root must be found (otherwise RETAKE), then `check_lidar`.

**Project-wide findings:**

- nothing to compute → RETAKE;
- an OK note listing the runs when there is more than one;
- the look-back check across rooms.

Response:

```
{ ok: bool,                                   # false if any retake anywhere
  spaces:   [{space_id, name, status, findings}],
  captures: [{kind, item, name, status, findings, advice}],   # one per item; LiDAR first
  project_findings: [finding] }
finding = {level: "ok"|"warn"|"retake", check, message, files: [names]}
```

`status` is the worst finding level. The UI shows the levels as **OK / Check / Retake**; the
engine's own `capture_quality` uses OK / WARN / RETAKE.

**When verify blocks a run** (`POST /run`):

- If the stored `last_verify` matches the current content key, is not `ok`, and `force` is
  false, the run returns **409**.
- If there is nothing to compute, the run returns **409**.
- Inside the job, the `verify` stage reuses the stored result when the content key matches,
  and verifies otherwise. With retake findings and no `force`, the job fails at that stage.
  With `force`, the stage is marked done with the note "(forced)".

### 4.4 Job lifecycle (`server/jobs.py`)

```
queued ──► running ──► done     (at least one run done; job.error lists failed runs, if any)
                   └─► failed   (verify retake without force, every run failed, or internal error)
```

**How jobs execute:**

- One `Worker` thread processes one job at a time from a `queue.Queue`.
- On startup, jobs still `running` become `failed` ("server restarted while this job was
  running: run it again"), and `queued` jobs are re-queued.
- Run status is `pending` / `running` / `done` / `failed`. A failing run does not stop the
  next one.
- Stage status is `pending` / `running` / `done` / `failed`; the engine can also report
  `skipped`.
- The stage list is planned up front: `verify`, then for each run its `stages.PLAN[tier]`
  without `damage` when `damage=false`. It is updated live through `pipeline.run(on_stage=...)`.
- After each successful run the worker writes `result.xlsx` and sets the run's `n_rooms`,
  `warnings` (first 50) and `outputs`.
- `job.outputs` holds the first finished run's outputs.

`GET /api/jobs/{jid}` returns:

| Field | Type | Notes |
|---|---|---|
| `job_id`, `project_id` | str | |
| `status` | `queued`\|`running`\|`done`\|`failed` | |
| `stage` | str \| null | name of the stage now running |
| `stages` | `[{name, status, seconds, note}]` | `verify` first |
| `error` | str \| null | |
| `outputs` | `{result_json, result_xlsx, plan_png, plan_svg}` | URL paths of the first done run |
| `runs` | `[{tier, title, label, space_ids, whole_home, status, error, outputs, prefix, n_rooms?, warnings?}]` | `tier` is `photos`\|`lidar`\|`video` |
| `damage` | bool | |
| `email_status` | str \| absent | `sent` or `failed: ...` |
| `created`, `started`, `finished` | epoch seconds \| null | |

### 4.5 Cache key, email, TTL

**Cache key.** `cache_key = sha256(content_key | damage=<bool> | engine=<engine_version>)`.

- `content_key` hashes the rooms in walk order (kind, name, sizes, sorted `(sha256, name)` of
  files) and every whole-home item in order.
- `engine_version` is `roomscan.__version__` plus a 10-hex SHA-256 of every `.py` file under
  `src/roomscan`. Any engine code change therefore invalidates the cache.

**Effect of a hit** (`Store.find_cached` prefers the newest `done`, then `running`, then
`queued` job with the same key):

- The run returns that job: `cached: true` if it is done, `false` if it is still in flight.
- An email given with a cached request is sent at once if the job is done, otherwise
  attached to the running job.

**Email** (`server/notify.py`):

- Email is enabled when both `ROOMSCAN_SMTP_USER` and `ROOMSCAN_SMTP_PASSWORD` are set.
  `/api/health` then reports `email: true`.
- The message is sent from a background thread after the job reaches `done` or `failed`,
  using SMTP with STARTTLS.
- It contains, per run: room lines (area, perimeter, ceiling with 90 % ranges), damage, flag
  and scope counts, and up to 3 warnings.
- It links to `ROOMSCAN_API_URL` + output path and to `ROOMSCAN_WEB_URL`.
- `plan.png` and `result.xlsx` are attached while the total stays at or under 8 MB.
- The result is recorded in `job.email_status`. A mail failure never fails the job.

**TTL** (`Store.cleanup`):

- At **startup only**, `done` and `failed` jobs whose `finished` (or `created`) time is older
  than `ROOMSCAN_RESULT_TTL_DAYS` are deleted, together with half-written job folders past
  the cutoff.
- Projects and uploads are never deleted by the server.

### 4.6 Comparison

`GET /api/jobs/{jid}/comparison` returns one row per room × quantity (`length`, `width`,
`height`) for each run that produced rooms:

```
{space, space_id, tier, run, room_id, quantity, given, computed, ci90, diff, diff_pct}
```

How each user room is matched:

- **Photo runs:** to the room fitted from its folder.
- **Whole-home runs:**
  - rooms with a length or width are matched by `known_sizes.assign` (aspect ratio, then size
    rank), falling back to the nearest floor area;
  - a height-only room gets the largest unmatched room;
  - a room without sizes gets none.

What "computed" means:

- `length` / `width` are the sides of the room polygon's bounding box, with the interval
  taken from the longest wall along that side;
- `height` is `ceiling_height`.

### 4.7 Storage layout (`server/store.py`)

```
DATA_DIR/
├── projects/<pid>/project.json                      {project_id, created, spaces, captures, last_job_id, last_verify}
├── projects/<pid>/files/<sid>/<sha256>              room photos, stored by content hash
├── projects/<pid>/files/_capture_video/<sha256>     whole-home videos
├── projects/<pid>/files/_capture_lidar/<sha256>     whole-home LiDAR zips / loose files
├── jobs/<jid>/job.json                              full job state (adds cache_key, engine, spaces,
│                                                    captures, force, notify_email, runs[].folders)
├── jobs/<jid>/out/[<label>/]result.json ...         outputs per run
├── jobs/<jid>/work/                                 materialised capture (deleted after the run)
├── engine_cache/                                    engine .cache (pipeline.CACHE_DIR and video.CACHE redirected here)
└── verify_*/                                        temporary, deleted after each verify
```

JSON writes are atomic (temporary file, then `os.replace`, retried on Windows). Uploads are
written to a `.part` file, hash-checked, then renamed into place.

### 4.8 Environment variables

| Variable | Default | Used by | Meaning |
|---|---|---|---|
| `DATA_DIR` | `./server_data` (image: `/data`) | app | state root |
| `ROOMSCAN_CORS` | `*` | app | allowed origins, comma separated. Credentials are allowed only when not `*` |
| `ROOMSCAN_MAX_FILE_MB` | `2048` | app | per-file upload limit |
| `ROOMSCAN_RESULT_TTL_DAYS` | `7` (image: `2`; setup.sh env file: `7`) | app | finished-job retention, applied at startup |
| `ROOMSCAN_SMTP_USER` | none | notify | sender account; email is off without it |
| `ROOMSCAN_SMTP_PASSWORD` | none | notify | for Gmail an App Password; email is off without it |
| `ROOMSCAN_SMTP_HOST` | `smtp.gmail.com` | notify | |
| `ROOMSCAN_SMTP_PORT` | `587` | notify | STARTTLS |
| `ROOMSCAN_SMTP_FROM` | `ROOMSCAN_SMTP_USER` | notify | |
| `ROOMSCAN_WEB_URL` | `http://localhost:5173` | notify | "open the results" link |
| `ROOMSCAN_API_URL` | `http://localhost:8000` | notify | base for download links |
| `PORT` | `7860` | Dockerfile CMD | uvicorn port |
| `HF_HOME`, `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE` | `/opt/hf`, `1`, `1` (image) | engine | weights baked in, offline |
| `DISABLE_SAFETENSORS_CONVERSION` | `1` | engine | avoids a second 605 MB CLIP download |
| `ROOMSCAN_DATA` | `../data` | bench/run_all.py | benchmark data root |

---

## 5. Engine pipeline (`pipeline.py`, `stages.py`)

`pipeline.run(path, out_dir, tier="auto", stride=5, drift="loop", damage=True, use_cache=True,
progress=True, measurements=None, on_stage=None)` runs in four steps:

1. Prepares the input (unzip, unwrap) and loads the known sizes.
2. Detects the tier.
3. Builds a `Stages(tier, on_stage, skip=("damage",) if not damage)`.
4. Runs the tier.

**How stages behave:**

- A stage is marked done when its time is recorded. A gate then sets `gate` to `ok`, `warn`
  or `fail`, and `fail` raises `StageFailed`.
- On any exception the running stage is marked failed with the error text.
- `stages.json` is always written, in the `finally` block.

**Invalid arguments** raise `InputError` (CLI exit code 2):

- `tier` outside `auto|lidar|video|photo|roomplan`;
- `drift` outside `off|loop|heading|loop+heading`;
- `stride` < 1.

### 5.1 PLAN

```python
PLAN = {
  "lidar": ["load", "drift", "fuse", "layout", "openings", "damage", "export"],
  "video": ["load", "drift", "fuse", "layout", "openings", "damage", "export"],
  "photo": ["load+depth", "room_fit", "stitch", "openings", "damage", "export"],
  "roomplan": ["load", "layout", "openings", "damage", "export"],  # section 3.6
}
```

### 5.2 LiDAR and video stages (`_run_tier` → `run_posed`)

| Stage | What it does | Gate | Fails / warns when |
|---|---|---|---|
| `load` | **LiDAR:** `load_stray(path, stride=5)` builds one Frame per 5th odometry row; poses map OpenCV camera axes into the ARKit world (+Y up); depth and colour are loaded lazily. **Video:** `load_video` tracks KLT at 25 fps, picks keyframes (max 320), runs Depth Anything per keyframe (cached `depth_*.npz`), solves PnP poses, re-attaches broken segments, sets metric scale = median model scale / 1.137 and gravity from the camera-height plane; clips shorter than 3 s are rejected | `gate_load` | **fail** with fewer than 10 frames |
| `drift` | `correct_drift`: 3 s submaps, ICP loop closures, Open3D pose graph; heading and position corrected, gravity kept. Mode from `--drift` (default `loop`) | `gate_drift` | never fails. **warn** when `max_submap_shift_m` (or `largest_move_m`) > 1.0 m; `ok "off"` when disabled |
| `fuse` | `cached_fuse`: depth → points and normals in 2 cm voxels; cached as `cloud_<key>.npz`, keyed on the corrected poses | `gate_fuse` | **fail** with fewer than 5000 points |
| `layout` | `extract_layout`: floor / ceiling / Manhattan yaw, 2 cm raster, wall band 0.3-1.9 m, lintels above 2.15 m, rooms, snapped outlines. If not LiDAR and no rooms, falls back to `box_layout` (one rectangle, warning). Known sizes: video is rescaled (`video.apply_known_sizes`), LiDAR only compared (`lidar_check`) | `gate_layout` | never fails. **warn** with no closed room, or room overlap > 0.1 m² |
| `openings` | `detect_openings`: ray hit / pass grid per wall; door if it reaches the floor, otherwise window or opening; mirrors rejected and listed in `warnings` | `gate_openings` | **warn** if any width is outside 0.3-4.0 m |
| `damage` | `assess_damage`: frame choice, surface assignment, CLIP ViT-B/32 tiles, region outline and metric size, merge, `rules.yaml` flags, `scope.yaml` items. Not in the plan when `damage=False` | `gate_damage` | always `ok` ("N region(s)") |
| `export` | `build_output`, then `known_sizes.finish` (if scaled); writes `result.json`, `plan.png` and `plan.svg` | `gate_export` | **fail** if any measurement has a value but an empty `ci90` |

### 5.3 Photo stages (`frontends/photos.run_photo_tier`)

| Stage | What it does | Gate | Fails / warns when |
|---|---|---|---|
| `load+depth` | `find_rooms`, thinning to 8 per room, `read_photo` (HEIC via pillow-heif, EXIF orientation and focal), Depth Anything per photo (cached), reduced to 256 px wide | `gate_load` | **fail** with fewer than 2 readable photos. No photos at all → `InputError` |
| `room_fit` | per room `fit_room`: gravity, wall directions, headings unwrapped by sweep order, rescaled to a common camera height, rectangle from the outermost wall planes (`boxfit`). Per-room known-size scale | none: the stage is done when timed | no rooms reconstructed → `InputError` |
| `stitch` | look-back photo located in an earlier room (EfficientLoFTR + PnP on that room's depth; stops early at 150 or more inliers). Fallback: attach to the previous folder, door position guessed (warning). `_resolve_overlaps` | `gate_layout(name="stitch")` | as `gate_layout` |
| `openings` | fuse at 3 cm, `detect_openings`, plus one assumed 0.80 m entry door per linked room (`sigma_w` 0.15, with a note) | `gate_openings` | as above |
| `damage` | as for LiDAR / video | `gate_damage` | |
| `export` | as for LiDAR / video, with `stitch_method` = look-back text and `drift_correction` = `{enabled: false, method: "not applicable ..."}`. Not timed by the tier: `gate` times it from its start | `gate_export` | as above |

### 5.4 Typical timings (laptop CPU, no GPU)

| Capture | Time | Source |
|---|---|---|
| LiDAR flat, 9 rooms, 215 s scan | 3 min 39 s cold (drift 28 s, fuse 39 s, layout 10 s, openings 6 s, damage 131 s) | README |
| LiDAR, 1306 frames, 4 rooms (server job `16e8f55fd75d`) | load 0.7, drift 18.5, fuse 33.5, layout 10.7, openings 4.2, damage 83.3, export 0.02 s | `stages` of that job |
| Photos, 3 rooms, 24 photos (same job) | load+depth 35.8, room_fit 4.4, stitch 56.6, openings 2.4, damage 14.4, export 0.7 s; verify 30.0 s | same |
| Photos, 7 rooms, 28 photos (`apt_photo_a`) | 240 s; stitch 185 s dominates | `bench/reports/benchmark.md` |
| Video, 115 s clip | 8 min 10 s first run, 3 min 27 s with cached depth; `load` (depth) dominates | README |

Damage is usually the longest LiDAR stage, and `load` (keyframe depth) the longest video stage.

### 5.5 `stages.json`

```json
{ "total_seconds": 153.56,
  "stages": [ {"name": "load",  "status": "done", "seconds": 0.67, "gate": "ok", "note": "1306 frames"},
              {"name": "drift", "status": "done", "seconds": 18.45, "gate": "ok",
               "note": "2 loop closure(s), largest correction 0.08 m"}, ... ] }
```

| Field | Values |
|---|---|
| `status` | `pending`\|`running`\|`done`\|`failed`\|`skipped` |
| `gate` | `ok`\|`warn`\|`fail`\|null |

`result.json.timing_s` repeats the per-stage seconds.

---

## 6. Output

### 6.1 Files per run

| File | Written by | Content |
|---|---|---|
| `result.json` | `pipeline.run_posed` / `photos.run_photo_tier` | the full result, schema in 6.2 (UTF-8) |
| `plan.png`, `plan.svg` | `export/render.py` | stitched, dimensioned plan: walls, doors, windows, wall length ± interval, room area and height, damage markers |
| `stages.json` | `Stages.write` (always, even on failure) | 5.5 |
| `result.xlsx` | `export/sheet.py`, called by the **CLI**, `walkin.py` and the **server worker** (not by `pipeline.run` itself) | 6.4 |
| `error.log` | CLI, on an unexpected error | traceback |
| `walkin.txt` / `walkin_error.txt` | `scripts/walkin.py` | printed report / traceback |

### 6.2 `result.json` schema (`schema.py`, `schema/output.schema.json`)

Coordinates are a top-down plan frame in metres. Axes follow the dominant wall directions,
and the origin is the minimum corner of the property. x grows to the right of the plan and
y grows downward. In the tables, **Required** means the key is always present. A type with
`\| null` may be null.

**Top level (`Output`)**

| Field | Type | Required | Description |
|---|---|---|---|
| `schema_version` | str | no (default `"1.0.0"`, always written) | contract version |
| `units` | str | no (default, always written) | `"metres; areas in square metres"` |
| `interval` | str | no (default, always written) | `"90 % (ci90); sigma = 1-sigma after per-tier calibration"` |
| `capture` | CaptureInfo | yes | |
| `property` | Property | yes | |
| `rooms` | [Room] | yes | |
| `damage` | [DamageRegion] | yes | may be empty |
| `concealed_damage_flags` | [ConcealedFlag] | yes | may be empty |
| `scope` | [ScopeItem] | yes | may be empty |
| `warnings` | [str] | yes | plain strings: loader notes, mirrors, unobserved ceilings, known-size notes |
| `timing_s` | {str: float} | yes | seconds per stage |

**Measurement** (used for every measured number)

| Field | Type | Required | Description |
|---|---|---|---|
| `value` | float \| null | yes | point estimate; null = not observed |
| `ci90` | [low, high] \| null | yes | 90 % interval; null when `value` is null |
| `sigma` | float \| null | yes | 1-sigma after tier calibration |
| `unit` | `"m"`\|`"m2"` | no (default `"m"`) | |
| `lower_bound` | float \| null | no (default null) | hard lower bound when the value is not observed (e.g. top of the observed walls) |
| `note` | str \| null | no (default null) | e.g. "ceiling not captured; lower bound = top of observed walls", "room length given (tape)" |

**CaptureInfo**

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | str | yes | capture name |
| `tier` | `lidar`\|`video`\|`photo` | yes | |
| `source` | str | yes | `stray_scanner`, `video`, `photo_folders` |
| `n_frames_used` | int | yes | |
| `meta` | object | no (default `{}`) | loader metadata. Photo tier: `n_photos`, `n_rooms`, `depth_model`, `matcher`, `links`, `overlaps_resolved`, `camera_height_m`. Plus known-size scale info |

**Property**

| Field | Type | Required | Description |
|---|---|---|---|
| `footprint_area` | Measurement (m2) | yes | area of the **union** of room polygons; sigma = √Σ room floor σ²; lower end clipped at 0 |
| `bbox` | Measurement (m) | yes | diagonal of the plan's bounding box |
| `room_ids` | [str] | yes | |
| `adjacency` | [{`rooms`: [a, b], `via`: opening id \| null, `kind`: `door`\|`opening`\|`shared_wall`}] | yes | doors and openings that connect rooms, or a parallel wall overlap > 0.5 m within 0.40 m |
| `stitch_method` | str | yes | `single_capture`, or the photo look-back text |
| `drift_correction` | object | yes | e.g. `{enabled, method, n_submaps, loop_edges, loop_residual_m_before/after, max_submap_shift_m, mean_submap_shift_m, wall_crispness}` |

**Room**

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | str | yes | `room_N` (LiDAR / video), or the folder name (photo, e.g. `01_hall`) |
| `label` | str | yes | `corridor` (short side < 1.6 m and aspect > 2.2), `small_room` (< 4 m²) or `room` |
| `polygon` | [[x, y]] | yes | outline in the plan frame |
| `floor_area` | Measurement (m2) | yes | |
| `perimeter` | Measurement (m) | yes | |
| `ceiling_height` | Measurement (m) | yes | value null + `lower_bound` unless `ceiling_source == "ceiling_plane"` |
| `ceiling_source` | `ceiling_plane`\|`wall_top`\|`none`\|`model` | yes | `model` is allowed by the schema but no code path emits it |
| `walls` | [Wall] | yes | |
| `openings` | [Opening] | yes | |
| `surfaces` | [Surface] | yes | one per wall, plus `<room>_floor` and `<room>_ceiling` |

**Wall**

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | str | yes | `<room>_wN` |
| `start`, `end` | [x, y] | yes | |
| `length` | Measurement (m) | yes | |
| `height` | Measurement (m) | yes | the room's `ceiling_height` |
| `area` | Measurement (m2) | yes | gross (length × height, or × 2.4 m if unknown) minus openings |
| `opening_ids` | [str] | yes | |
| `evidence_coverage` | float 0..1 | yes | fraction of the length with direct plane evidence |
| `plane_spread` | float (m) | yes | robust std of wall points about the plane |

**Opening**

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | str | yes | `op_N` |
| `type` | `door`\|`window`\|`opening` | yes | |
| `wall_id`, `room_id` | str | yes | |
| `connects_to` | str \| null | yes | neighbouring room id |
| `width`, `height`, `sill_height` | Measurement (m) | yes | |
| `offset_along_wall` | float (m) | yes | from wall start to opening start |

**Surface**: `id`, `type` (`wall`\|`floor`\|`ceiling`), `ref` (the wall id, or the room id
for floor and ceiling) and `area` (Measurement). All are required.

**DamageRegion**

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | str | yes | `dmg_N` |
| `surface_id`, `room_id` | str | yes | the surface the region is on |
| `damage_class` | `water_stain`\|`crack`\|`mold`\|`peeling_paint`\|`hole`\|`other` | yes | |
| `score` | float 0..1 | yes | detector confidence |
| `area` | Measurement (m2) | yes | |
| `extent_u`, `extent_v` | Measurement (m) | yes | horizontal / vertical extent on the surface |
| `center` | [x, y, h] | yes | plan x, plan y, height above the floor |
| `evidence_frames` | [int] | yes | frame indices |

**ConcealedFlag** (one per rule that fired on a surface)

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | str | yes | `flag_N` |
| `surface_id`, `room_id` | str | yes | |
| `rule_id` | str | yes | `CD-01` .. `CD-10` (`damage/rules.yaml`) |
| `rule` | str | yes | the rule's sentence |
| `triggered_by` | [str] | yes | damage region ids |
| `risk` | `low`\|`medium`\|`high` | yes | |
| `recommendation` | str | yes | |

The rules in `damage/rules.yaml`:

| Rule | Condition | Risk |
|---|---|---|
| CD-01 | water stain on a ceiling | high |
| CD-02 | moisture damage within 30 cm of the floor | medium |
| CD-03 | water stain within 40 cm of the ceiling on a wall | high |
| CD-04 | moisture damage on a wall backing onto a `small_room` | high |
| CD-05 | any mold | medium |
| CD-06 | water stain or mold over 1 m² | high |
| CD-07 | moisture damage within 0.5 m of a window | medium |
| CD-08 | water stain on a floor | medium |
| CD-09 | crack longer than 1 m | medium |
| CD-10 | crack within 0.4 m of a door, window or opening | medium |

**ScopeItem** (keyed to a surface)

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | str | yes | `scope_N` |
| `surface_id`, `room_id` | str | yes | the surface the work is on |
| `code` | str | yes | e.g. `DRY-RPL`, `PNT-SEAL`, `PNT-WALL`, `CRK-FILL`, `MLD-TRT` |
| `description` | str | yes | |
| `quantity` | Measurement | yes | from `damage_area`, `damage_length` or `surface_area` (× factor, at least the minimum), or `each` |
| `unit` | `m2`\|`m`\|`each` | yes | |
| `source` | str | yes | damage id or flag id this item comes from |

Items marked `once_per_surface` in `scope.yaml` (such as a full repaint) appear once per
surface, however many regions it has.

### 6.3 Real example (trimmed)

From `server_data/jobs/16e8f55fd75d/out/whole_home_lidar/result.json`, a LiDAR whole-home
run, 4 rooms. Lists are cut to one element, and `room_1` has 16 walls in full:

```json
{
  "schema_version": "1.0.0",
  "units": "metres; areas in square metres",
  "interval": "90 % (ci90); sigma = 1-sigma after per-tier calibration",
  "capture": {"id": "609c9be5d1", "tier": "lidar", "source": "stray_scanner", "n_frames_used": 1306,
              "meta": {"source": "stray_scanner", "root": "...\\work\\whole_home_lidar\\609c9be5d1",
                       "rgb_size": [1920, 1440], "n_frames_total": 6528}},
  "property": {
    "footprint_area": {"value": 60.8168, "ci90": [58.3795, 63.2541], "sigma": 1.48174, "unit": "m2",
                       "lower_bound": null, "note": null},
    "bbox": {"value": 17.7074, "ci90": [17.2569, 18.1579], "sigma": 0.27388, "unit": "m", "lower_bound": null, "note": null},
    "room_ids": ["room_1", "room_2", "room_3", "room_4"],
    "adjacency": [{"rooms": ["room_1", "room_2"], "via": "op_1", "kind": "door"},
                  {"rooms": ["room_2", "room_4"], "via": null, "kind": "shared_wall"}],
    "stitch_method": "single_capture",
    "drift_correction": {"enabled": true, "method": "pose_graph_loop_closure", "n_submaps": 49, "loop_edges": 2,
                         "heading_corrections_deg": [], "loop_residual_m_before": 0.1311,
                         "loop_residual_m_after": 0.0315, "max_submap_shift_m": 0.0792,
                         "mean_submap_shift_m": 0.0075, "wall_crispness": 7.61}
  },
  "rooms": [{
    "id": "room_1", "label": "room",
    "polygon": [[6.3674, 3.6511], [6.3051, 3.6511], [6.3051, 6.6751], "..."],
    "floor_area": {"value": 20.4229, "ci90": [18.5791, 22.2666], "sigma": 1.12087, "unit": "m2", "lower_bound": null, "note": null},
    "perimeter": {"value": 22.0852, "ci90": [21.0102, 23.1601], "sigma": 0.65348, "unit": "m", "lower_bound": null, "note": null},
    "ceiling_height": {"value": 2.3882, "ci90": [2.3288, 2.4477], "sigma": 0.03613, "unit": "m", "lower_bound": null, "note": null},
    "ceiling_source": "ceiling_plane",
    "walls": [{"id": "room_1_w1", "start": [6.3674, 3.6511], "end": [6.3051, 3.6511],
               "length": {"value": 0.0622, "ci90": [0.0, 2.2619], "sigma": 1.33728, "unit": "m", "lower_bound": null, "note": null},
               "height": {"value": 2.3882, "ci90": [2.3288, 2.4477], "sigma": 0.03613, "unit": "m", "lower_bound": null, "note": null},
               "area": {"value": 0.1486, "ci90": [0.0, 5.4177], "sigma": 3.20328, "unit": "m2", "lower_bound": null, "note": null},
               "opening_ids": [], "evidence_coverage": 0.0, "plane_spread": 0.0}],
    "openings": [{"id": "op_1", "type": "door", "wall_id": "room_1_w5", "room_id": "room_1", "connects_to": "room_2",
                  "width": {"value": 1.07, "ci90": [0.8573, 1.2827], "sigma": 0.12931, "unit": "m", "lower_bound": null, "note": null},
                  "height": {"value": 1.85, "ci90": [1.399, 2.301], "sigma": 0.2742, "unit": "m", "lower_bound": null, "note": null},
                  "sill_height": {"value": 0.15, "ci90": [0.0, 0.601], "sigma": 0.2742, "unit": "m", "lower_bound": null, "note": null},
                  "offset_along_wall": 0.78}],
    "surfaces": [{"id": "room_1_floor", "type": "floor", "ref": "room_1",
                  "area": {"value": 20.4229, "ci90": [18.5791, 22.2666], "sigma": 1.12087, "unit": "m2", "lower_bound": null, "note": null}}]
  }],
  "damage": [{"id": "dmg_1", "surface_id": "room_1_w15", "room_id": "room_1", "damage_class": "water_stain", "score": 0.727,
              "area": {"value": 0.0423, "ci90": [0.0105, 0.074], "sigma": 0.0193, "unit": "m2", "lower_bound": null, "note": null},
              "extent_u": {"value": 0.3891, "ci90": [0.2018, 0.5764], "sigma": 0.11388, "unit": "m", "lower_bound": null, "note": null},
              "extent_v": {"value": 0.1814, "ci90": [0.0587, 0.3041], "sigma": 0.07459, "unit": "m", "lower_bound": null, "note": null},
              "center": [6.133, 0.04, 1.926], "evidence_frames": [1030]}],
  "concealed_damage_flags": [],
  "scope": [{"id": "scope_1", "surface_id": "room_1_w15", "room_id": "room_1", "code": "DRY-RPL",
             "description": "Cut out and replace water-damaged wallboard",
             "quantity": {"value": 0.4, "ci90": [0.2318, 0.5682], "sigma": 0.10228, "unit": "m2", "lower_bound": null, "note": null},
             "unit": "m2", "source": "dmg_1"}],
  "warnings": ["room_3_w4: reflective surface 0.616 m wide treated as a mirror, not reported as an opening"],
  "timing_s": {"load": 0.67, "drift": 18.45, "fuse": 33.51, "layout": 10.7, "openings": 4.24, "damage": 83.32, "export": 0.02}
}
```

That run fired no concealed-damage rule. A flag from `runs/iphone_lidar_1/result.json`:

```json
{"id": "flag_1", "surface_id": "room_1_w13", "room_id": "room_1", "rule_id": "CD-03",
 "rule": "Water stain at the top of a wall suggests water running down inside the wall from above.",
 "triggered_by": ["dmg_1"], "risk": "high",
 "recommendation": "Open the wall at the stain and trace the source above; check the cavity for wet insulation."}
```

The example also shows the evidence term at work. `room_1_w1` has no plane evidence
(`evidence_coverage` 0.0), so its 6 cm length carries a 0-2.26 m interval.

### 6.4 `result.xlsx` (`export/sheet.py`)

Each measurement takes three columns: value, `low`, `high`. A missing value is left blank,
with its `lower_bound` in the low column. The first row is frozen.

| Sheet | Columns |
|---|---|
| Summary | item, value, 90 % low, 90 % high: capture, tier, source, rooms, footprint, damage regions, concealed-damage flags, scope items, interval, schema version |
| Rooms | room, label, floor area ×3, ceiling height ×3, perimeter ×3, walls, openings, ceiling source |
| Walls | room, wall, length ×3, height ×3, area ×3, evidence coverage, openings |
| Openings | room, opening, type, wall, connects to, width ×3, height ×3, sill height ×3 |
| Damage | damage, room, surface, class, score, area ×3, width ×3 (extent_u), height ×3 (extent_v), centre above floor, evidence frames |
| Flags | flag, room, surface, rule id, risk, rule, triggered by, recommendation |
| Scope | item, room, surface, code, description, quantity, quantity low, quantity high, unit, from |
| Warnings | warning |

---

## 7. Uncertainty (`uncertainty/intervals.py`, `calibration.yaml`, `bench/calibrate.py`)

**The interval formula.** Every number goes through
`measure(value, raw_sigma, tier, kind, unit, lower_bound, note)`:

```
sigma = scale × sqrt((inflate × raw_sigma)² + abs² + (rel × |value|)²)
ci90  = value ± 1.6449 × sigma      (low clipped at 0 for areas, lengths, heights, opening sizes)
value None / non-finite  ->  {value: null, ci90: null, sigma: null, lower_bound, note}
```

- `kind` is one of `wall_length`, `ceiling_height`, `opening_width`, `opening_height`,
  `area` or `damage`.
- `raw_sigma` comes from geometry: the plane-fit standard error, and `hypot(floor σ,
  ceiling σ)` for heights.
- `inflate`, `abs` and `rel` are per-tier priors.
- `scale` is fitted per tier. Current values: LiDAR 5.07, video 6.931, photo 5.225 (photo
  `area` 10.927). The `damage` kind has no fitted scale (1.0).

**Evidence term** (`export/build.py`). A wall's length is fixed by the two walls that end it,
so its raw σ is `hypot(end_σ(prev), end_σ(next))`:

```
end_σ   = hypot(plane σ, min(EVIDENCE_K × deficit, EVIDENCE_CAP))
deficit = clip((0.90 − coverage) / 0.90, 0, 1)
```

- Door and window widths on a wall count as covered.
- A jog shorter than 0.35 m between two fully covered parallel walls adds nothing.
- `EVIDENCE_K = {"lidar": 0.50}` and `EVIDENCE_CAP = {"lidar": 0.20}`. Video and photo keep
  plane-only sigmas, which is what their scales were fitted with.
- Floor area and perimeter use plane sigmas only.

**Footprint.** `footprint_area` uses `±1.6449 × √Σσ²` over the rooms, computed directly
rather than through `measure`.

**Known sizes** (`known_sizes.tighten`):

- a measured quantity's interval becomes [min(v, g) − 0.01, max(v, g) + 0.01];
- the other intervals are widened by the given numbers' residual spread.

**Calibration** (`bench/calibrate.py --write`). Split conformal per tier:

1. Collect samples from `bench/reports/benchmark.json`: wall lengths, ceiling heights,
   opening widths and room floor areas, each with the σ it was reported with.
2. Compute `z = |pred − truth| / σ₁`, where σ₁ is the σ brought back to scale 1.
3. Take `q`, the ⌈(n+1)·0.9⌉-th smallest z; the fitted scale is `q / 1.6449`.

Lengths, heights and openings share one scale per tier. Areas get their own scale only with
5 or more samples (`MIN_N`). Otherwise the tier's length scale is used (LiDAR and video areas
today).

After fitting, a reference check ensures the intervals widen from LiDAR to video to photo.
A thinner tier's scale is raised where it does not.

Truth comes from `bench/ground_truth/*.yaml` (ARKitScenes Faro laser scans) or a LiDAR
reference. The report with leave-one-room-out coverage is `bench/reports/calibration.md`
(LiDAR length 0.85, video 0.93, photo length 0.92, photo area 0.93). The fitted values are
written to `src/roomscan/uncertainty/calibration.yaml`.

---

## 8. Frontend (`web/`)

### 8.1 Page structure

A single page, `index.html`, loads `env.js` (classic script) and then `js/app.js` (module).

| Section | Contents |
|---|---|
| Header | brand and the API status pill (`#api-status`): green "API online" / "API offline"; click to re-check; polled every 30 s |
| Your property | project id, "Start a new project", and the optional **email box** (`#notify-email`, remembered in localStorage). `#email-off` shows when `/api/health` reports `email: false` |
| How to capture | collapsible guide with inline SVG figures (video walk, tilt to ceiling, photos from the doorway, look-back, marker and tape). Open on the first visit, then remembered |
| Whole-home capture | two blocks, Video walkthrough and LiDAR scan (Stray `.zip`). Each lists its items with upload state, Remove, the per-item verify findings and advice. Up to 5 items each; the add button is disabled when full |
| Rooms | ordered list of room cards: name (rename), ↑/↓ walk order (`PUT /order`), delete, Add photos / Add folder (folder picker not on iOS), Clear files, L / B / H inputs in metres, up to 3 thumbnails, verify badge and findings with "Remove these files". Then the add-room form |
| Check and compute | upload summary, **Check captures** (verify, 120 s timeout), **Start computing** (enabled only after a fresh verify without retakes), **Run anyway** (shown when retakes exist; sends `force: true`), hint text, project findings |
| Job | status badge, job id, email status line, live stage list (`prettyStage` maps `load+depth`, `room_fit` etc. to labels, keeping the run title), error box, Run again / Compute again |
| Results | one tab per finished run (run title), summary (rooms, footprint ± 1.645σ and range, damage and flag counts, tier, total time), downloads (`result.json`, `result.xlsx`, `plan.png`, `plan.svg`), plan image, rooms table, "Your sizes vs ours" (comparison, with an "inside range?" column), damage list with flags (risk badge, rule, recommendation), pipeline notes (`warnings`) |
| Footer | API address form: saves to localStorage and reloads |

### 8.2 State

**localStorage** (prefix `roomscan.`):

| Key | Content |
|---|---|
| `pid` | current project id |
| `project.<pid>` | cached project, used offline |
| `order.<pid>` | walk order |
| `verify.<pid>` | `{result, at, dirty}`; any change marks it dirty |
| `job.<pid>` | last job id |
| `notify.email` | email address |
| `guide.open` | guide open or closed |
| `roomscan.api` | API base (key from `config.js`) |

**IndexedDB** `roomscan` v1, store `files` (keyPath `id`, index `sid`). Each record holds:
`{pid, sid, name, size, type, lastModified, blob, sha256, state: pending|error, error, added}`.

- Whole-home captures use the pseudo space ids `_capture_video` and `_capture_lidar`.
- A record is deleted once the server confirms the file.
- `navigator.storage.persist()` is requested.

**On load:**

1. `GET /projects/{pid}` (a 404 starts a new project).
2. Re-apply the local walk order.
3. Resume the upload queue.
4. Resume polling `job.<pid>`, or the server's `last_job_id`.

### 8.3 Polling and progressive results

- `GET /api/jobs/{jid}` is polled every 2.5 s, and every 10 s or more while the tab is hidden.
- On error it backs off as 2.5 s × 2ⁿ, up to 30 s. A 404 forgets the job.
- The poll is triggered immediately when the tab becomes visible or the browser comes online.
- Results render as soon as **any run** is done, while the job is still running (the LiDAR
  tab appears before a video finishes). The panel re-renders whenever the count of finished
  runs changes.
- The comparison table loads only when the whole job is `done`.
- After the job ends, polling continues for up to 20 more tries at 3 s intervals while
  `email_status` is still empty.

### 8.4 API base (`env.js` + `config.js`)

`env.js` sets the default:

```js
window.ROOMSCAN_API = window.ROOMSCAN_API ||
  ((location.hostname === "localhost" || location.hostname === "127.0.0.1")
     ? "http://localhost:8000" : "https://34-14-174-240.sslip.io");
```

`config.js` resolves the base in this order:

1. `?api=<url>` in the page URL (saved to localStorage; `?api=reset` clears it).
2. localStorage `roomscan.api`.
3. `window.ROOMSCAN_API` from `env.js`, if it looks like an `http(s)` URL.
4. `http://localhost:8000`.

A browser that was once given `?api=` keeps using it until `?api=reset`.

---

## 9. Deployment and operations

### 9.1 Frontend on Vercel

- **Project setup:** Root Directory `web`, Framework Preset *Other*, no build or install
  command. Every push to `main` redeploys.
- **`web/vercel.json`:**
  - `/` is rewritten to `index.html`, with clean URLs;
  - `nosniff` and `strict-origin-when-cross-origin` headers;
  - HTML: `max-age=0, must-revalidate`;
  - `js/` and `styles.css`: 5 min, stale-while-revalidate 1 day;
  - `img/` and the favicon: 1 day;
  - `env.js`: `no-cache`;
  - `/dev/*`: `noindex`.
- **Pointing at the backend:** the backend URL lives in `web/env.js` (hard-coded above). The
  server's `ROOMSCAN_CORS` must allow the Vercel origin, or stay at `*`.

### 9.2 Backend VM (`deploy/oracle/setup.sh`)

Run it from a clone:

```
ssh -i <key> <user>@<public-ip> 'bash -s' < deploy/oracle/setup.sh
```

The script is written for an Oracle Ampere VM, but it is plain Ubuntu 22.04 / 24.04 and its
comments say the firewall step is "harmless elsewhere". The live API host in `web/env.js` is
`https://34-14-174-240.sslip.io`, the sslip.io name this script derives from the public IP.
The script:

1. Installs `docker.io`, `git` and `iptables-persistent`, and enables Docker.
2. Opens TCP 80 and 443 in iptables and persists them. A cloud firewall (security list or VPC
   rule) must also allow 80 and 443.
3. Clones `REPO` (default `https://github.com/jaiChauhan009/roomscan.git`) to `/opt/roomscan`,
   or `git pull --ff-only` on later runs.
4. Creates **`/etc/roomscan.env`** (mode 600) on the first run only:

   ```
   ROOMSCAN_CORS=*
   ROOMSCAN_RESULT_TTL_DAYS=7
   ROOMSCAN_API_URL=https://<host>
   ROOMSCAN_WEB_URL=https://roomscan.vercel.app
   # ROOMSCAN_SMTP_USER / ROOMSCAN_SMTP_PASSWORD (commented)
   ```

   Edit this file by hand, for example to add Gmail SMTP credentials, then re-run the script.
5. Builds the image: `docker build -f server/Dockerfile -t roomscan /opt/roomscan`. The first
   build takes about 15-25 min on 4 ARM cores. The image uses CPU torch from the PyTorch CPU
   index, bakes the weights in with `scripts/fetch_weights.py`, runs as uid 1000, has a
   HEALTHCHECK on `/api/health`, and starts `uvicorn server.app:app --host 0.0.0.0 --port
   ${PORT:-7860} --proxy-headers`.
6. Runs the container:

   ```
   docker run -d --name roomscan --restart unless-stopped -p 127.0.0.1:7860:7860 \
     -v /opt/roomscan-data:/data --env-file /etc/roomscan.env roomscan
   ```

   `/opt/roomscan-data` is owned by 1000:1000.
7. Runs Caddy as `caddy:2`, `--network host`, `--restart unless-stopped`, with the volume
   `caddy_data`. `/opt/caddy/Caddyfile` contains `<ip-with-dashes>.sslip.io {
   request_body { max_size 2GB } reverse_proxy 127.0.0.1:7860 { transport http {
   read_timeout 30m; write_timeout 30m } } }`. Caddy obtains the Let's Encrypt certificate.
8. Polls `http://127.0.0.1:7860/api/health` for up to 5 min and prints the URL.

**Updating:** re-run the same command. The script pulls `main`, rebuilds, and replaces both
containers. Data in `/opt/roomscan-data` and the env file are kept.

**Logs:** the repository does not document these. Use standard Docker on the container
names: `sudo docker logs -f roomscan`, `sudo docker logs caddy`, `sudo docker ps` (health
status).

**Capacity:** one container and one worker thread, so jobs queue. Memory peaks at about
4 GB per job (8 GB for long videos), per `docs/deploy.md`. There is no authentication
(`docs/deploy.md` §F).

Alternatives in the repository: a Hugging Face Space (`deploy/hf-space/push.sh`), Railway,
Render, Fly.io, or a laptop behind a Cloudflare Tunnel (`docs/deploy.md`).

### 9.3 Local development

```bash
uv sync --extra ml --extra server                                # once; add --extra dev for tests
uv run python scripts/fetch_weights.py                           # once: models into the HF cache
uv run --extra server uvicorn server.app:app --port 8000          # API + worker, state in ./server_data
python -m http.server -d web 5173                                 # front end -> http://localhost:5173
python web/dev/mock_server.py                                     # optional: stdlib mock API on :8000
uv run roomscan run <capture>                                     # CLI, writes runs/<name>/
uv run python scripts/walkin.py <capture> [--check-only]          # quality check + cold run + table
uv run roomscan schema                                            # regenerate schema/output.schema.json
uv run python bench/run_all.py                                    # benchmark -> bench/reports/
```

On localhost, `env.js` targets `http://localhost:8000` automatically.

---

## 10. Testing (`tests/`, pytest)

The suite runs on synthetic inputs: rooms with exactly known dimensions (`tests/synth.py`),
generated images and clips, and a mock API. Run it with `uv run --extra dev pytest`.

| Area | Files |
|---|---|
| Geometry and layout | `test_geometry.py`, `test_outline.py`, `test_drift.py` |
| Inputs and capture checks | `test_inputs.py`, `test_capture_quality.py`, `test_photo_thinning.py`, `test_depth_batches.py`, `test_photo_set.py` |
| Known sizes and marker | `test_known_sizes.py`, `test_markers.py`, `test_marker_wiring.py` |
| Damage | `test_damage_crack.py`, `test_damage_outline.py`, `test_synth_damage.py` |
| Output contract, intervals, calibration | `test_contract.py`, `test_intervals_evidence.py`, `test_calibration.py`, `test_sheet.py` |
| Stage queue | `test_stages.py` |
| Speed and robustness | `test_video_speed.py`, `test_speed_lowlight.py` (includes the mirror test) |
| Models and weights | `test_hub.py`, `test_fetch_weights.py` |
| Benchmark tooling | `test_arkitscenes.py`, `test_repeatability.py`, `test_head_to_head.py`, `test_tape_form.py` |
| Web API and front end | `test_server.py` (FastAPI app: projects, uploads, verify, jobs), `test_web_static.py` (static checks, mock API contract, headless smoke via `web/dev/smoke.html`) |
