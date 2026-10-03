# Compliance matrix

Requirement → where it is → the artifact that shows it → status.
Status: **done** (implemented and exercised), **partial** (implemented, evidence incomplete
or a gate failing), **missing** (not done). Reasons for partial / missing are given.

## Part 1: capture

| Requirement | File | Artifact | Status |
|---|---|---|---|
| Capture route (Route 2: stock tools + one-page protocol) | `docs/capture_protocol.md`, `scripts/walkin.py` | protocol page; hand-off on a USB-C drive, one command for every tier | done; not yet followed by a non-engineer |
| Photo tier: 2-8 stills per room, folder per room, any iPhone 15+ | `src/roomscan/frontends/photos.py` | `fixloop/*/runs/apt_photo_a/` | partial: runs and stitches; accuracy far from gate |
| Photo tier produces the stitched whole-property plan | `photos.py` (look-back stitching), `export/render.py` | `runs/apt_photo_a/plan.png` | partial: one plan, no overlaps; 2 of 7 links by photo match, rest by capture order (flagged) |
| Video tier: handheld clip, any iPhone 15+ | `src/roomscan/frontends/video.py` | `runs/apt_video_b/` | partial: runs; 1 of 6 rooms on the sample clip |
| LiDAR tier: depth, poses, intrinsics on Pro devices | `src/roomscan/frontends/lidar_stray.py` | `runs/apt_lidar_*` | done |
| Device matrix | `docs/device_matrix.md` | table | done |
| Input checks with retake advice, before the long run | `src/roomscan/capture_quality.py`, `scripts/walkin.py --check-only`, server verify | OK / WARN / RETAKE per check with one line of advice; thresholds placed on the sample and our own captures | done in code and tests; partial as evidence: not yet seen to save a real retake |
| HEIC photos (iPhone default) | `src/roomscan/heif.py` | other formats work where the HEIC decoder is blocked; a HEIC photo then fails with a hint | done; optional by design |

## Part 2: output contract and gates

| Requirement | File | Artifact | Status |
|---|---|---|---|
| Per-room plan: walls, ceiling height, floor area, openings | `geometry/layout.py`, `geometry/openings.py` | `result.json` → `rooms[]` | done |
| Stitched multi-room plan with adjacency | `layout.py`, `export/build.py` (`_adjacency`) | `property.adjacency`, `plan.png`; room overlap column in the benchmark | done for LiDAR (no room overlaps); partial for photo / video |
| Damage regions per surface, class, metric extent | `damage/detect.py`, scored by `bench/evaluate.py` | `damage[]`; damage rows of the benchmark report | partial: scoring done (found / wrong class / missed / phantom, extent errors; gate assumed, the brief gives none); 0 false positives on the undamaged flat; no real staged damage yet |
| Concealed-damage flags with the rule that fired | `damage/rules.yaml`, `damage/pipeline.py` | `concealed_damage_flags[].rule_id/rule` | done (tested in `tests/test_contract.py`) |
| Scope line items keyed to surfaces | `damage/scope.yaml`, `damage/pipeline.py` | `scope[]` | done (tested) |
| Confidence interval on every measurement | `uncertainty/intervals.py`, `calibration.yaml`, `bench/calibrate.py` | every `{value, ci90, sigma}`; `bench/reports/calibration.md` | done (tested). Scales fitted per tier; held-out coverage LiDAR 0.85 (laser truth, 4 rooms), video 0.93, photo 0.92. LiDAR wall sigmas grow where a wall's ends rest on no plane (`export/build.py`) |
| One command per capture | `cli.py` | `roomscan run <capture>` | done |
| JSON to the published schema | `schema.py` | `schema/output.schema.json` | done with our own schema; Round 1 schema not available to us |
| Rendered plan | `export/render.py` | `plan.png`, `plan.svg` | done |
| Outputs for non-engineers | `export/sheet.py`, `stages.py` | `result.xlsx` (each number with its 90 % bounds), `stages.json` (each stage timed and gated) | done (tested); not in the brief's contract, added |
| Known sizes as an optional calibration aid | `src/roomscan/known_sizes.py` (`measurements.yaml`); `src/roomscan/markers.py`, `docs/scale_marker.md` (A4 marker) | held out on the sample flat: photos, one length per room, footprint +109 % → −23 %, walls 1.41 → 0.27 m; a height alone made it worse (+177 %), so heights are only compared; video worse with one length (−87 %, its error is missing rooms) | partial: photo scale helped on one proxy set; video not helped. A4 marker (optional) now wired in: photo tier per room, video tier per clip, priority typed size > marker > model, intervals from the marker spread (`tests/test_marker_wiring.py`, synthetic room 1.6x off corrected to within 2 %); scale 1.002 vs LiDAR depth on the sample; not yet run on a real printed-marker capture |
| Web app: upload, verify, run, results | `server/` (FastAPI, job queue), `web/` (static front end), `deploy/oracle/setup.sh` | `tests/test_server.py`, `tests/test_web_static.py`; live: front end on Vercel, back end on a Google Cloud VM (`https://34-14-174-240.sslip.io`); cloud jobs `e7cb3519c1a3` (6 photo rooms, ~11 min) and `690f2a3de81a` (video) finished with the report emailed | done as an extra user path. The official path stays the local CLI: the brief says everything runs without calling our infrastructure |
| Background jobs and email report | `server/jobs.py`, `server/notify.py`; optional email box at the top of the page | job keeps running with the tab closed; report email with every room's sizes and 90 % ranges, plan and spreadsheet attached; failed runs are retried, not served from the cache | done (tested in `tests/test_server.py`, and live) |
| Uploads on slow links | `web/js/shrink.js`, `web/js/upload.js` | JPEGs shrunk to 2048 px in the browser with the EXIF focal length kept (1.57 MB to 0.27 MB, identical engine input checked in headless Chrome); 3 uploads at a time; IndexedDB queue survives a reload | done |
| Benchmark: multi-room capture, 3+ rooms + connector | `bench/manifest.yaml` | `apt_lidar_a`, `apt_lidar_b` | partial: sample flat, not our own capture |
| Benchmark: furnished room with staged damage, two classes | `bench/synth_damage.py` (proxy) | `bench/reports/synth_damage.md` | partial: real staged damage needs an iPhone. Proxy: a stain and a crack of known size painted onto two walls of scan A in every frame, scored by the benchmark's own damage scoring: 0 false positives on the clean capture; stain found as one region, extents within 13 %; crack found as a crack, extents −14 % / −16 % in the committed report (an earlier run had +61 % when a lamp cable joined its outline); gate PASS. Own iPhone scans: ceiling lights reported as a hole |
| Benchmark: same rooms at all three tiers, photo tier stitching | `scripts/make_photo_set.py`, manifest | `apt_video_b`, `apt_photo_a` | partial: thin tiers derived from the LiDAR scan, not captured |
| Benchmark: one room captured twice at the same tier | manifest `repeatability` | scans A and B of one flat; our own flat (iPhone 16 Pro) and its room 4 alone | done: sample flat captured twice; our room 4 scanned twice (walls both scans see agree to 1.7 cm median) |
| Laser / tape ground truth, raw data submitted | `scripts/fetch_arkitscenes.py`, `bench/make_laser_truth.py`, `bench/ground_truth/arkit_*.yaml` | laser truth for four unseen rooms (ARKitScenes, Faro scans; selection rule fixed before any run) | partial: LiDAR tier only, public rooms not ours. Median wall error 3-13 cm in all four (42446532 one room since `f83f66c`, 1.48 → 0.047 m); the 1 cm wall gate fails. The sample flat and the video / photo tiers have no laser truth |
| Gate: opening widths ≤ 2 cm on ≥ 85 %, missed and phantom count | `bench/evaluate.py` | `opening_width` rows | implemented; not scorable on LiDAR without truth |
| Gate: ceiling ≤ 1.5 cm, repeat spread ≤ 1 cm, say biased vs unrepeatable | `evaluate.py`, `repeatability.py` | report rows | implemented; laser ceiling truth on 4 rooms: 2 of 4 within 1.5 cm after fix-loop round 4 (−2.93, −0.81, −1.66, −0.92 cm); repeat spread not scorable (no room captured twice with laser truth) |
| Gate: repeatability 1 cm / 0.5 % per wall | `bench/repeatability.py` | `fixloop/after`, `fixloop/round2/after` | partial: failing. Point clouds of the two scans agree to 7 mm, but the scans divide the flat into different rooms (scan B has no upward sweep); fix-loop target, two rounds |
| Gate: drift accountability + on/off ablation | `geometry/drift.py`, `bench/run_all.py`, `tests/test_drift.py` | drift ablation table | done. Before round 2 the correction was a no-op on scan B; now B moves 0.52 m and walls are sharper with it on |
| Gate: photo whole-property stitch, no overlaps, ±8 % footprint | `photos.py`, `evaluate.py` | photo rows | partial: no overlaps passes; footprint fails |
| Photo ±8 %, video ±3 %, calibration at every tier | `evaluate.py` (`calibration` row), `bench/calibrate.py` | report rows, `bench/reports/calibration.md` | partial: accuracy gates fail on every set (video finds 1-2 rooms; photo footprints +32 % to +109 %); calibration held out 0.93 video, 0.92 photo, against our LiDAR, not a laser. Our own video / photos: moto g45 and iPhone (via WhatsApp) |

## Part 3: head-to-head

| Requirement | File | Artifact | Status |
|---|---|---|---|
| Two rooms, LiDAR tier vs a consumer app, dimension table, ≥ 70 % win or tie | `bench/head_to_head.py`, `bench/app_exports/TEMPLATE.yaml`, `docs/iphone_session.md` | tool tested on synthetic and sample data | partial: tooling done; the data needs an iPhone (magicplan recommended: its free tier exports a floor plan) |

## Part 4: fix loop

| Requirement | File | Artifact | Status |
|---|---|---|---|
| Declaration: worst gate + failing number, root cause + evidence, fix + prediction | `fixloop/declaration.md`, `fixloop/round2/declaration.md`, `fixloop/round3/declaration.md`, `fixloop/round4/declaration.md` | each committed before its fix (`c9255e9`, `482b4fb`, `929a035`, `7f6ac11`) | done, four rounds |
| Shipped fix | commits `1e8c490` (round 1), `cb55b8b` (round 2), `270175e` (round 3), `d517a4e` (round 4) | `fixloop/fix.diff`, `fixloop/round2/fix.diff`, `fixloop/round3/fix.diff`, `fixloop/round4/fix.diff` | done. Round 4 (ceiling height on the laser rooms): 1/4 → 2/4 within 1.5 cm, every room within 0.22 cm of its declared prediction |
| Before and after runs, regenerable | `fixloop/before/`, `fixloop/after/`, `fixloop/round2/after/`, `fixloop/round3/after/`, `fixloop/round4/before|after/` (`run_laser.py`), tags `fixloop-before`, `fixloop2-before`, `fixloop3-before`, `fixloop4-before` | benchmark reports | done |
| Readable diff | `fixloop/fix.diff`, `fixloop/round2/fix.diff` (refactor separate: `refactor.diff`), `fixloop/round3/fix.diff`, `fixloop/round4/fix.diff` | | done |

## Part 5: process

| Requirement | File | Artifact | Status |
|---|---|---|---|
| Commit as you work | git history, `docs/worklog.md` | 160+ commits over the build, each a working stage; workers merged with `--no-ff` | done |

## Deliverables

| Deliverable | Where | Status |
|---|---|---|
| 1. Compliance matrix | this file | done |
| 2. Capture route + device matrix | `docs/capture_protocol.md`, `docs/device_matrix.md` | done |
| 3. Repo, README to running in < 15 min, one command per capture | `README.md`, `scripts/walkin.py` | partial: timed on a clean Windows copy, 15.5 min to a first LiDAR plan, 12.5 min of it downloads at 0.75 MB/s; under 15 min needs about 3 MB/s. macOS and Linux not run |
| 4. Reproduction bundle | `scripts/fetch_data.py`, `scripts/fetch_weights.py`, `bench/run_all.py`; photo set and upright clip rebuilt by the manifest's `prepare:` steps (`bench/photo_set_recipe.yaml`: byte-identical) | done |
| 5. Benchmark report: gates at three tiers, repeatability, head-to-head, timing | `bench/reports/benchmark.md` (fix-loop runs in `fixloop/`) | partial: no head-to-head, laser truth for the LiDAR tier only; to rerun after round 4 (LiDAR interval scale 5.07 fitted before the split-room fix) |
| 6. Fix loop bundle | `fixloop/` | done |
| 7. Technical report, max 6 pages | `docs/tech_report.md` (with round 4); longer companions `docs/report.md` (project report: testing, scores, deployment, optimization) and `docs/architecture.md` (folders, inputs, API, stages, output fields) | done |
| 8. Raw benchmark data | `scripts/fetch_data.py` (sample scans); our captures in `../data/own` (4 iPhone LiDAR scans, 2 videos, 2 photo sets) | partial: own captures packed by `scripts/pack_raw_data.py` into one zip (1.3 GB, every file in `bench/raw_data.sha256`, `--verify` checks a download; see `docs/raw_data.md`), to be shared by link; no app exports, no tape truth of our flat |
| Mirrors, glass, wet-look surfaces, low light covered | `openings.py` (mirror test), `layout.py` (a mirror's gap still seals the room), `planes.py` (glossy floor), `damage/detect.py` (dim frames brightened, stricter threshold), tests | partial: handled in code and tests (rendered mirror, darkened frames); no real capture of each |
