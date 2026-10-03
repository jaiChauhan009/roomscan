# roomscan iOS app

iPhone capture app for the roomscan backend: RoomPlan LiDAR scans (live size panel, keyframes
for damage detection, each room uploaded as soon as it is done), video walkthroughs, rooms with
photos and sizes, check / compute, and live results. Swift + SwiftUI, iOS 17+, no third-party
dependencies. Full guide, including the `capture.json` format: [docs/ios_app.md](../docs/ios_app.md).

## Screens

- **Home** (one form, like the web app): email for the report · your property (project) · whole
  home: LiDAR scan (scan button, scanned rooms with size and upload progress, share zip) · whole
  home: video walkthrough · rooms (photos) · uploads · check captures · start computing · server URL.
- **Scan**: RoomPlan `RoomCaptureView` with a live panel (walls / doors / windows, area,
  width × length, ceiling), Cancel / Done.
- **Room**: sizes and photos for one room.
- **Results**: job stages live, then per run: rooms with 90 % ranges, plan image, files to share.

## Code

| File | |
|---|---|
| `RoomscanCapture/AppModel.swift` | state, project, upload queue (3 parallel, retry, resume) |
| `RoomscanCapture/API.swift` | backend client (mirrors `web/js/api.js`) |
| `RoomscanCapture/ScanView.swift` | RoomPlan capture, live stats, keyframe recorder |
| `RoomscanCapture/CaptureModel.swift` | RoomPlan-free room model, room numbers, `capture.json`, zip assembly |
| `RoomscanCapture/RoomPlanBridge.swift` | CapturedRoom → model, StructureBuilder merge, USDZ |
| `RoomscanCapture/HomeView.swift`, `SpaceViews.swift`, `JobView.swift`, `Media.swift` | screens, camera / library pickers, photo shrink with EXIF |
| `RoomscanCapture/Util.swift` | JSON writer (4 decimals), stored zip writer, SHA-256 |
| `RoomscanCaptureTests/` | unit + live-backend tests |
| `RoomscanCaptureUITests/` | screen walk with screenshots |
| `project.yml` | XcodeGen spec (the `.xcodeproj` is generated, not committed) |

## Build

There is no Mac: `.github/workflows/ios.yml` runs on pushes to `ios-app` touching `ios/**`
(or by hand): `xcodegen generate`, an unsigned Release build for devices, packaged as
`roomscan-unsigned.ipa`, uploaded as the artifact `roomscan-unsigned-ipa` and to the release
[`ios-latest`](https://github.com/jaiChauhan009/roomscan/releases/tag/ios-latest).
On a Mac: `brew install xcodegen && cd ios && xcodegen generate && open RoomscanCapture.xcodeproj`.

## Install

Sideloadly from Windows with a free Apple ID (7-day expiry): see
[docs/ios_app.md](../docs/ios_app.md#install-on-an-iphone-from-windows-no-mac).

## Tests

The `test-simulator` CI job runs the unit tests, the live-backend tests (skipped when the server
is unreachable; the RoomPlan end-to-end check is an expected failure until the backend reads
`roomscan.roomplan/1`) and the UI test; results are in the run summary, screenshots in the
artifact `simulator-screenshots`. RoomPlan, the camera and the keyframes need a real iPhone with
LiDAR and are not covered.
