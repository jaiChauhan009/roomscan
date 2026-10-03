# Testing roomscan

How to run every kind of test (engine, back end, web, iOS app, benchmark, live system), and the
latest results. Results and scores are explained in [report.md](report.md).

## 1. Python tests: engine, back end, web

```bash
uv sync --extra ml --extra server --extra dev
uv run python scripts/fetch_weights.py                 # once: model weights
uv run --extra dev pytest -q                           # everything (~30 files)
uv run --extra dev pytest -q tests/test_server.py      # one area
```

| Area | Files |
|---|---|
| Geometry, layout, outlines, drift | `test_geometry.py`, `test_outline.py`, `test_drift.py` |
| Inputs and tiers | `test_inputs.py`, `test_photo_set.py`, `test_photo_thinning.py`, `test_depth_batches.py`, `test_video_speed.py`, `test_speed_lowlight.py`, `test_arkitscenes.py`, `test_hub.py`, `test_fetch_weights.py` |
| RoomPlan tier (iOS app scans) | `test_roomplan.py`, `test_server_roomplan.py`; synthetic captures from `roomplan_synth.py` |
| Output contract, flags, scope, schema | `test_contract.py` |
| Intervals and calibration | `test_intervals_evidence.py`, `test_calibration.py` |
| Damage | `test_damage_outline.py`, `test_damage_crack.py`, `test_synth_damage.py` |
| Known sizes, A4 marker | `test_known_sizes.py`, `test_markers.py`, `test_marker_wiring.py`, `test_tape_form.py` |
| Capture checks, stage queue, Excel | `test_capture_quality.py`, `test_stages.py`, `test_sheet.py` |
| Benchmark tools | `test_repeatability.py`, `test_head_to_head.py` |
| Back end (FastAPI) | `test_server.py` (projects, uploads, verify, run, cache, several videos and scans, email, retry of failed runs), `test_server_roomplan.py` |
| Web front end | `test_web_static.py` (module imports and element ids; headless smoke at 360 px against `web/dev/mock_server.py`) |

**Latest results (3 October 2026):**
- Full suite: 218 passed, 2 skipped (before the RoomPlan and marker merges).
- After the merges:
  - RoomPlan, server, web, marker, known-sizes, contract and stages: 71 passed, 1 skipped.
  - RoomPlan + contract + geometry, after the room-id fix: 50 passed.
- The HEIC tests skip on the Windows development laptop, because Application Control blocks the `pillow_heif` DLL. The Linux server image decodes HEIC.

## 2. iOS app (no Mac needed)

The app is tested on GitHub Actions macOS runners ([`.github/workflows/ios.yml`](../.github/workflows/ios.yml)) on every
push that changes `ios/`.

| Job | What it checks |
|---|---|
| `build` | XcodeGen project → device build → `roomscan-unsigned.ipa`. It fails if the app binary is missing, and publishes the file to release `ios-latest` |
| `test-simulator` | see below |

What `test-simulator` covers:
- **Unit tests:**
  - the `capture.json` contract: keys, units, rounding, column-major matrices, `session_id`;
  - room size maths;
  - the zip writer, re-read with CRC checks;
  - photo shrinking keeps the 35 mm focal length.
- **End-to-end against the deployed back end:** project, room and photo upload, then a synthetic 4 × 3 m RoomPlan room through upload, verify, run and poll. The result must be 1 room of about 12 m².
- **UI test with screenshots:** home, the "no LiDAR" message, a room screen, video, check and compute.

Artifacts per run: `roomscan-unsigned-ipa`, `simulator-screenshots`, `simulator-test-log`.

**Latest:** [run 37117212947](https://github.com/jaiChauhan009/roomscan/actions/runs/37117212947): build ✓, simulator tests ✓.
Screens: [img/ios_app_screens.jpg](img/ios_app_screens.jpg).

**Not testable without an iPhone with LiDAR:**
- RoomPlan scanning itself;
- keyframe quality;
- StructureBuilder merging;
- camera and video capture;
- the install itself.

See [ios_app.md](ios_app.md) to install it and test it on a phone.

## 3. Live system (cloud)

```bash
curl https://34-14-174-240.sslip.io/api/health          # {"ok": true, "version": ..., "email": true}
```

End-to-end runs on the deployed back end (3 October 2026):

| Test | Result |
|---|---|
| Photos, 6 rooms (job `e7cb3519c1a3`), through the Vercel site | done in ~11 min; report email sent |
| Video (job `690f2a3de81a`) | done; email sent; its LiDAR run exposed a missing Linux library (fixed, and checked at image build time since) |
| Two RoomPlan rooms + combined run (job `c52a1f8b0096`), synthetic | Kitchen 12.00 m² (11.76-12.24), Hall 9.00 m² (8.80-9.20), ceilings 2.60 m: exact; 82 s for 3 runs |
| iOS app CI end-to-end (jobs `09bb02990601`, `33e1ff174762`, `3f435657fd6b`) | done |

## 4. Benchmark (accuracy)

```bash
uv run python bench/run_all.py            # all 16 captures -> bench/reports/
uv run python bench/calibrate.py --write  # refit the 90 % intervals per tier
uv run python bench/calibrate.py --eval-only
```

See [bench/README.md](../bench/README.md) for the captures, metrics and gates, and
[report.md](report.md) §6-8 for the results.

## 5. Capture checks before a run

```bash
uv run python scripts/walkin.py <capture> --check-only   # OK / WARN / RETAKE per check; exit 3 on RETAKE
```
