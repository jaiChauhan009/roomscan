# roomscan iOS capture app

An iPhone app (Swift, SwiftUI, iOS 17+) that captures a home for the roomscan backend. It has
the same parts as the web app (`web/`):

- **Email me the report (optional)**: sent with the run request; results are always shown in the app.
- **Your property**: a backend project created at first launch (`POST /api/projects`), kept on
  the phone; "Start a new project" makes a fresh one.
- **Whole home: LiDAR scan** (iPhone/iPad Pro with LiDAR only): Apple RoomPlan guided capture,
  like magicplan's Auto-Scan, one room at a time. While you scan, a live panel shows the walls,
  doors and windows found so far, the approximate floor area, width × length and ceiling height.
  The app also keeps up to 120 still frames per room (every ~0.7 s once the phone has moved
  25 cm or turned 15°) with their camera pose and intrinsics for damage detection. When you tap
  Done, the room is packed as its own `.roomscan.zip` and uploaded in the background while you
  scan the next room (the server takes up to 5 LiDAR items per project).
- **Whole home: video walkthrough**: record (up to 3 min, 1080p) or pick a video; up to 5.
- **Rooms (photos)**: add a room by name, optional sizes (length / breadth / height in metres), and
  2–8 photos taken in the app or picked from the library. Photos are shrunk to 2048 px on the
  long side, keeping the EXIF block (focal length sets the scale), as `web/js/shrink.js` does.
- **Uploads**: up to 3 at a time with progress; a failed upload has Retry; unfinished uploads
  resume at the next launch.
- **Check captures** (`POST /verify`): OK / Check / Retake per room and per whole-home item, with
  the server's advice. **Start computing** (`POST /run`); if the check asked for a retake the app
  asks before running anyway (`"force": true`).
- **Results**: the job's stages live (polled every 2.5 s) and, as soon as each run finishes, its
  rooms (floor area with the 90 % range, ceiling, perimeter, wall count), the plan image, and
  result.xlsx / result.json / plan.png through the share sheet.
- **Share the whole scan (.zip)**: one zip of all LiDAR rooms for AirDrop / Files (offline route to
  the laptop). With 2+ rooms the app tries RoomPlan's `StructureBuilder` to put them in one
  frame (`"merged": true`); otherwise each room keeps its own frame (`"merged": false`).

## Install on an iPhone from Windows (no Mac)

The app is built by GitHub Actions on a macOS runner (`.github/workflows/ios.yml`, branch
`ios-app`) as an unsigned `.ipa`. Download it from the release:
<https://github.com/jaiChauhan009/roomscan/releases/download/ios-latest/roomscan-unsigned.ipa>

1. On the iPhone: Settings > Privacy & Security > **Developer Mode** > on, restart, confirm.
   (The switch appears after a first sideload attempt if it is not listed yet.)
2. On the PC: install **iTunes from Apple's website** (not the Microsoft Store version) and
   **Sideloadly** (sideloadly.io).
3. Connect the iPhone by USB, unlock it and tap "Trust this computer".
4. Open Sideloadly, drag `roomscan-unsigned.ipa` onto it, enter your Apple ID, Start. Sideloadly
   signs the app with your free developer certificate (bundle id `com.roomscan.capture`; it may
   change it, which is fine).
5. On the iPhone: Settings > General > **VPN & Device Management** > your Apple ID > Trust.
6. Open "roomscan".

With a free Apple ID the app **expires after 7 days**: sideload it again (the data in it is
lost only if you delete the app). At most 3 sideloaded apps can be active with a free ID.

## How to scan

- One room at a time. Type a room name (default "Room N"), tap **Scan a room**, and follow
  RoomPlan's on-screen guidance: move slowly along the walls, point at the floor and ceiling edges,
  keep the phone at chest height.
- Open the doors (RoomPlan detects open / closed doors), turn the lights on, avoid mirrors and
  direct sunlight.
- Tap **Done** when the outline is complete. The room is built, shown in the list with its size,
  and starts uploading. Scan the next room straight away; RoomPlan keeps the same AR session, so
  rooms share one coordinate frame while the app stays open.
- **Upload vs Share**: "Start computing" uses whatever has been uploaded to the project. "Share
  the whole scan" makes a single zip you can AirDrop / save to Files and process offline on the
  laptop.

## The `.roomscan.zip` and `capture.json`

Each zip holds `capture.json`, `frames/NNNNNN.jpg` (keyframes, sensor orientation, landscape,
JPEG quality 0.8, ≤ 1920 px) and `room.usdz` (RoomPlan parametric export; for unmerged multi-room
zips `room_<i>.usdz` per room). The per-room upload zips are named `<timestamp>-room<N>.roomscan.zip`;
the shared one `<timestamp>.roomscan.zip`.

```json
{
  "format": "roomscan.roomplan/1",
  "app_version": "0.1.0",
  "device": {"model": "iPhone16,1", "system": "iOS 17.5"},
  "captured_at": "2026-10-03T14:00:00Z",
  "units": "m",
  "coordinate_frame": "arkit_world_y_up",
  "merged": true,
  "rooms": [
    {
      "name": "Kitchen", "index": 0, "story": 0,
      "walls":    [{"id": "uuid", "start": [x, z], "end": [x, z], "height": 2.6, "thickness": 0.0, "confidence": "high"}],
      "doors":    [{"id": "uuid", "wall_id": "uuid-or-null", "center": [x, y, z], "width": 0.9, "height": 2.1, "confidence": "high", "is_open": null}],
      "windows":  [{"id": "...", "wall_id": null, "center": [x, y, z], "width": 1.2, "height": 1.2, "confidence": "medium"}],
      "openings": [{"id": "...", "wall_id": null, "center": [x, y, z], "width": 1.0, "height": 2.1, "confidence": "low"}],
      "floor": {"polygon": [[x, z], ...], "y": 0.0},
      "objects": [{"category": "bed", "center": [x, y, z], "dimensions": [w, h, d], "yaw": 0.0}],
      "section_labels": ["kitchen"]
    }
  ],
  "frames": [{"file": "frames/000001.jpg", "room_index": 0, "t": 12.34,
              "transform": [16 floats, column-major], "intrinsics": [9 floats, column-major],
              "width": 1920, "height": 1440}]
}
```

- Coordinates: ARKit world, metres, x right, y up, z toward the viewer; the plan uses (x, z).
- Wall start / end: `transform * (±width/2, 0, 0, 1)`, world x and z. Door / window / opening
  centre: the surface transform's translation; width = dimensions.x, height = dimensions.y;
  `wall_id` = RoomPlan's `parentIdentifier`.
- `floor`: RoomPlan's floor `polygonCorners` transformed to world; `null` if RoomPlan gave none.
- `room_index` / `index`: position in this file's `rooms` (a per-room upload zip has one room, index 0).
- `t`: seconds since the first saved frame of that room. `transform`: camera-to-world.
  `intrinsics`: fx, fy, cx, cy in pixels of the saved JPEG (scaled if the image was shrunk).
- Floats are written with at most 4 decimals.

## Backend calls

| Step | Call |
|---|---|
| project | `POST /api/projects` → `{project_id}`; `GET /api/projects/{pid}` |
| room | `POST /api/projects/{pid}/spaces` `{"name", "kind": "photos", "sizes": {...}}`; `PATCH .../spaces/{sid}` `{"sizes": {"length", "width", "height"}}` |
| photo | `PUT /api/projects/{pid}/spaces/{sid}/files` multipart: `sha256`, `name`, `file` |
| LiDAR / video | `PUT /api/projects/{pid}/captures/{lidar\|video}` `{}` (idempotent), then `PUT /api/projects/{pid}/captures/{kind}/files` multipart: `sha256` (hex SHA-256 of the file), `name` (e.g. `20261003-140000-room1.roomscan.zip`), `file` |
| check | `POST /api/projects/{pid}/verify` |
| compute | `POST /api/projects/{pid}/run` `{"damage": true, "force": <after a confirmed retake>, "email": <optional>}` → `{job_id}` |
| results | `GET /api/jobs/{jid}` (stages, runs[].status / prefix / outputs); `GET /api/jobs/{jid}/files/<prefix>result.json`, `plan.png`, `result.xlsx` |

## Testing without a phone

The `test-simulator` CI job runs on the iOS Simulator (no LiDAR, no camera):

- unit tests: capture.json encoding of a synthetic 4 × 3 m room against the contract (keys,
  units, rounding, column-major matrices), the zip writer (re-read with CRC checks), photo shrink
  keeping EXIF;
- backend tests against the live server (skipped if unreachable): create a project, add a room,
  upload a JPEG; upload a synthetic roomplan zip to the LiDAR list, verify, run, poll, and expect
  one room of ≈ 12 m². Until the backend reads `roomscan.roomplan/1`, that last check is marked
  as an expected failure;
- a UI test that walks the screens and saves screenshots (artifact `simulator-screenshots`).

Everything that needs the real device (RoomPlan scanning, keyframes, StructureBuilder merging,
USDZ export, camera photos and video) is untested until it runs on an iPhone with LiDAR.
