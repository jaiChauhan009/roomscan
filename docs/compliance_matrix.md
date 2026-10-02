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

## Part 2: output contract and gates

| Requirement | File | Artifact | Status |
|---|---|---|---|
| Per-room plan: walls, ceiling height, floor area, openings | `geometry/layout.py`, `geometry/openings.py` | `result.json` → `rooms[]` | done |
| Stitched multi-room plan with adjacency | `layout.py`, `export/build.py` (`_adjacency`) | `property.adjacency`, `plan.png`; room overlap column in the benchmark | done for LiDAR (no room overlaps); partial for photo / video |
| Damage regions per surface, class, metric extent | `damage/detect.py` | `damage[]` | partial: synthetic stain found, area under-measured; no real staged damage yet |
| Concealed-damage flags with the rule that fired | `damage/rules.yaml`, `damage/pipeline.py` | `concealed_damage_flags[].rule_id/rule` | done (tested in `tests/test_contract.py`) |
| Scope line items keyed to surfaces | `damage/scope.yaml`, `damage/pipeline.py` | `scope[]` | done (tested) |
| Confidence interval on every measurement | `uncertainty/intervals.py`, `calibration.yaml`, `bench/calibrate.py` | every `{value, ci90, sigma}`; `bench/reports/calibration.md` | done (tested). Scales fitted per tier; held-out coverage LiDAR 0.95 (two-scan precision), photo 0.90, video not measurable (one room). Refit on laser truth when it exists |
| One command per capture | `cli.py` | `roomscan run <capture>` | done |
| JSON to the published schema | `schema.py` | `schema/output.schema.json` | done with our own schema; Round 1 schema not available to us |
| Rendered plan | `export/render.py` | `plan.png`, `plan.svg` | done |
| Benchmark: multi-room capture, 3+ rooms + connector | `bench/manifest.yaml` | `apt_lidar_a`, `apt_lidar_b` | partial: sample flat, not our own capture |
| Benchmark: furnished room with staged damage, two classes | none | none | missing: needs an iPhone and a room to stage |
| Benchmark: same rooms at all three tiers, photo tier stitching | `scripts/make_photo_set.py`, manifest | `apt_video_b`, `apt_photo_a` | partial: thin tiers derived from the LiDAR scan, not captured |
| Benchmark: one room captured twice at the same tier | manifest `repeatability` | scans A and B of one flat | done (whole flat captured twice) |
| Laser / tape ground truth, raw data submitted | `bench/ground_truth/TEMPLATE.yaml`, `scripts/fetch_data.py` | template + raw sample data | missing: no measurements of the sample flat |
| Gate: opening widths ≤ 2 cm on ≥ 85 %, missed and phantom count | `bench/evaluate.py` | `opening_width` rows | implemented; not scorable on LiDAR without truth |
| Gate: ceiling ≤ 1.5 cm, repeat spread ≤ 1 cm, say biased vs unrepeatable | `evaluate.py`, `repeatability.py` | report rows | implemented; ceiling truth missing |
| Gate: repeatability 1 cm / 0.5 % per wall | `bench/repeatability.py` | `fixloop/after`, `fixloop/round2/after` | partial: failing. Point clouds of the two scans agree to 7 mm, but the scans divide the flat into different rooms (scan B has no upward sweep); fix-loop target, two rounds |
| Gate: drift accountability + on/off ablation | `geometry/drift.py`, `bench/run_all.py`, `tests/test_drift.py` | drift ablation table | done. Before round 2 the correction was a no-op on scan B; now B moves 0.52 m and walls are sharper with it on |
| Gate: photo whole-property stitch, no overlaps, ±8 % footprint | `photos.py`, `evaluate.py` | photo rows | partial: no overlaps passes; footprint fails |
| Photo ±8 %, video ±3 %, calibration at every tier | `evaluate.py` (`calibration` row), `bench/calibrate.py` | report rows, `bench/reports/calibration.md` | partial: accuracy gates fail; intervals now calibrated (held out 0.90 photo) but against our LiDAR, not a laser |

## Part 3: head-to-head

| Requirement | File | Artifact | Status |
|---|---|---|---|
| Two rooms, LiDAR tier vs a consumer app, dimension table, ≥ 70 % win or tie | `bench/head_to_head.py`, `bench/app_exports/TEMPLATE.yaml`, `docs/iphone_session.md` | tool tested on synthetic and sample data | partial: tooling done; the data needs an iPhone (magicplan recommended: its free tier exports a floor plan) |

## Part 4: fix loop

| Requirement | File | Artifact | Status |
|---|---|---|---|
| Declaration: worst gate + failing number, root cause + evidence, fix + prediction | `fixloop/declaration.md`, `fixloop/round2/declaration.md` | each committed before its fix (`c9255e9`, `482b4fb`) | done |
| Shipped fix | commits `1e8c490` (round 1), `cb55b8b` (round 2) | `fixloop/fix.diff`, `fixloop/round2/fix.diff` | done |
| Before and after runs, regenerable | `fixloop/before/`, `fixloop/after/`, `fixloop/round2/after/`, tags `fixloop-before`, `fixloop2-before` | benchmark reports | done |
| Readable diff | `fixloop/fix.diff`, `fixloop/round2/fix.diff` (refactor separate: `refactor.diff`) | | done |

## Part 5: process

| Requirement | File | Artifact | Status |
|---|---|---|---|
| Commit as you work | git history | 15+ commits, each a working stage | done |

## Deliverables

| Deliverable | Where | Status |
|---|---|---|
| 1. Compliance matrix | this file | done |
| 2. Capture route + device matrix | `docs/capture_protocol.md`, `docs/device_matrix.md` | done |
| 3. Repo, README to running in < 15 min, one command per capture | `README.md`, `scripts/walkin.py` | partial: timed on a clean Windows copy, 15.5 min to a first LiDAR plan, 12.5 min of it downloads at 0.75 MB/s; under 15 min needs about 3 MB/s. macOS and Linux not run |
| 4. Reproduction bundle | `scripts/fetch_data.py`, `scripts/fetch_weights.py`, `bench/run_all.py` | done |
| 5. Benchmark report: gates at three tiers, repeatability, head-to-head, timing | `bench/reports/benchmark.md` (fix-loop runs in `fixloop/`) | partial: no head-to-head, no real ground truth |
| 6. Fix loop bundle | `fixloop/` | done |
| 7. Technical report, max 6 pages | `docs/tech_report.md` | done |
| 8. Raw benchmark data | `scripts/fetch_data.py` (sample scans) | partial: no own captures, no app exports |
| Mirrors, glass, wet-look surfaces, low light covered | `openings.py` (mirror test), `planes.py` (glossy floor), `damage/pipeline.py` (low-light warning), tests | partial: handled in code and tests; no real capture of each |
