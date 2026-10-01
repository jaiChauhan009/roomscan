# Compliance matrix

Requirement → where it is → the artifact that shows it → status.
Status: **done** (implemented and exercised), **partial** (implemented, evidence incomplete
or a gate failing), **missing** (not done). Reasons for partial / missing are given.

## Part 1: capture

| Requirement | File | Artifact | Status |
|---|---|---|---|
| Capture route (Route 2: stock tools + one-page protocol) | `docs/capture_protocol.md` | protocol page | done; not yet followed by a non-engineer |
| Photo tier: 2-8 stills per room, folder per room, any iPhone 15+ | `src/roomscan/frontends/photos.py` | `fixloop/*/runs/apt_photo_a/` | partial: runs and stitches; accuracy far from gate |
| Photo tier produces the stitched whole-property plan | `photos.py` (look-back stitching), `export/render.py` | `runs/apt_photo_a/plan.png` | partial: one plan, no overlaps; 2 of 7 links by photo match, rest by capture order (flagged) |
| Video tier: handheld clip, any iPhone 15+ | `src/roomscan/frontends/video.py` | `runs/apt_video_b/` | partial: runs; 1 of 6 rooms on the sample clip |
| LiDAR tier: depth, poses, intrinsics on Pro devices | `src/roomscan/frontends/lidar_stray.py` | `runs/apt_lidar_*` | done |
| Device matrix | `docs/device_matrix.md` | table | done |

## Part 2: output contract and gates

| Requirement | File | Artifact | Status |
|---|---|---|---|
| Per-room plan: walls, ceiling height, floor area, openings | `geometry/layout.py`, `geometry/openings.py` | `result.json` → `rooms[]` | done |
| Stitched multi-room plan with adjacency | `layout.py`, `export/build.py` (`_adjacency`) | `property.adjacency`, `plan.png` | done for LiDAR; partial for photo / video |
| Damage regions per surface, class, metric extent | `damage/detect.py` | `damage[]` | partial: synthetic stain found, area under-measured; no real staged damage yet |
| Concealed-damage flags with the rule that fired | `damage/rules.yaml`, `damage/pipeline.py` | `concealed_damage_flags[].rule_id/rule` | done (tested in `tests/test_contract.py`) |
| Scope line items keyed to surfaces | `damage/scope.yaml`, `damage/pipeline.py` | `scope[]` | done (tested) |
| Confidence interval on every measurement | `uncertainty/intervals.py` | every `{value, ci90, sigma}` | done (tested); calibration not yet fitted on truth |
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
| Gate: repeatability 1 cm / 0.5 % per wall | `bench/repeatability.py` | `fixloop/before` and `fixloop/after` | partial: failing; fix-loop target |
| Gate: drift accountability + on/off ablation | `geometry/drift.py`, `bench/run_all.py` | drift ablation table | done |
| Gate: photo whole-property stitch, no overlaps, ±8 % footprint | `photos.py`, `evaluate.py` | photo rows | partial: no overlaps passes; footprint fails |
| Photo ±8 %, video ±3 %, calibration at every tier | `evaluate.py` (`calibration` row) | report rows | partial: measured, failing |

## Part 3: head-to-head

| Requirement | File | Artifact | Status |
|---|---|---|---|
| Two rooms, LiDAR tier vs a consumer app, dimension table, ≥ 70 % win or tie | none | none | missing: needs an iPhone to scan the same rooms with the app |

## Part 4: fix loop

| Requirement | File | Artifact | Status |
|---|---|---|---|
| Declaration: worst gate + failing number, root cause + evidence, fix + prediction | `fixloop/declaration.md` | committed before the fix (`c9255e9`) | done |
| Shipped fix | commit `1e8c490` | diff in `fixloop/README.md` | done |
| Before and after runs, regenerable | `fixloop/before/`, `fixloop/after/`, tag `fixloop-before` | benchmark reports | done |
| Readable diff | `fixloop/README.md` | | done |

## Part 5: process

| Requirement | File | Artifact | Status |
|---|---|---|---|
| Commit as you work | git history | 15+ commits, each a working stage | done |

## Deliverables

| Deliverable | Where | Status |
|---|---|---|
| 1. Compliance matrix | this file | done |
| 2. Capture route + device matrix | `docs/capture_protocol.md`, `docs/device_matrix.md` | done |
| 3. Repo, README to running in < 15 min, one command per capture | `README.md` | partial: not yet timed on a clean machine |
| 4. Reproduction bundle | `scripts/fetch_data.py`, `scripts/fetch_weights.py`, `bench/run_all.py` | done |
| 5. Benchmark report: gates at three tiers, repeatability, head-to-head, timing | `fixloop/after/benchmark.md` | partial: no head-to-head, no real ground truth |
| 6. Fix loop bundle | `fixloop/` | done |
| 7. Technical report, max 6 pages | `docs/tech_report.md` | missing |
| 8. Raw benchmark data | `scripts/fetch_data.py` (sample scans) | partial: no own captures, no app exports |
| Mirrors, glass, wet-look surfaces, low light covered | `openings.py` (mirror test), `planes.py` (glossy floor), `damage/pipeline.py` (low-light warning), tests | partial: handled in code and tests; no real capture of each |
