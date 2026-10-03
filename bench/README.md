# roomscan benchmark

This folder regenerates every reported number from raw inputs. Results are in [`reports/`](reports/benchmark.md);
the fix-loop runs are in [`../fixloop/`](../fixloop/README.md).

## Run it

```bash
uv run python scripts/fetch_data.py            # sample flat (public Google Drive folder)
uv run python scripts/fetch_arkitscenes.py     # 4 ARKitScenes rooms with Faro laser scans
# our own captures: unzip roomscan_own_captures.zip as ../data (see docs/raw_data.md)
uv run python bench/run_all.py                 # every capture in manifest.yaml -> reports/
uv run python bench/calibrate.py --write       # refit the 90 % interval scale per tier
```

`run_all.py` caches model outputs in `.cache/` (deterministic replay). Pass `--no-cache` for a live run,
and `--only <name>` to run one capture.

## Files

| File | What it does |
|---|---|
| `manifest.yaml` | the 16 benchmark captures: sample flat, ARKitScenes laser rooms, our own iPhone / moto captures; repeatability pairs |
| `gates.yaml` | gate thresholds; values the brief does not state are marked as assumed |
| `run_all.py` | runs the pipeline on every capture (one command per capture) and scores everything |
| `evaluate.py` | scores one `result.json` against ground truth: walls, ceilings, openings (missed and phantom), footprint, adjacency, overlaps, interval coverage, damage |
| `calibrate.py` | split-conformal fit of the per-tier interval scale; held-out coverage (leave one room out) |
| `repeatability.py` | two captures of the same space must agree within 1 cm / 0.5 % per wall |
| `ablation_drift.py` | the same capture with drift correction on and off: footprint, crispness, loop residual |
| `synth_damage.py` | paints a stain and a crack of known size onto bare walls, then scores the damage stage |
| `head_to_head.py` | our LiDAR result vs a consumer app (magicplan) on the same rooms, both against tape or laser truth |
| `make_laser_truth.py` | ground truth from the ARKitScenes Faro laser scans, independent of roomscan |
| `make_reference.py` | a LiDAR result as the reference for the thinner tiers (explicitly **not** ground truth) |
| `ground_truth/` | `arkit_*.yaml` laser truth; `TEMPLATE.yaml` for tape measurements |
| `app_exports/` | consumer-app exports for the head-to-head (`TEMPLATE.yaml`) |
| `photo_set_recipe.yaml` | byte-identical rebuild of the sample photo set from the scan's video |
| `raw_data.sha256` | SHA-256 of every file of our own raw captures (`scripts/pack_raw_data.py --verify`) |
| `reports/` | `benchmark.md` / `.json`, `calibration.md`, `synth_damage.md`, per-capture `runs/` |

## Headline results

Source: `reports/`; see [`../docs/report.md`](../docs/report.md) §8 for the full tables.

| Tier | Truth | Result |
|---|---|---|
| LiDAR | laser (4 rooms) | footprint 0.6-7.6 %; median wall error 3-13 cm; ceilings 2/4 within 1.5 cm (after round 4) |
| LiDAR | repeat scan | shared walls agree to 1.7 cm (median) |
| Video / photo | our LiDAR | far from the gates without a typed length or the marker; intervals still cover the reference (held-out coverage 0.93 / 0.92) |
| Damage | synthetic | PASS: both classes found, extents within 16 %, 0 false positives |

The committed `benchmark.md` predates the split-room fix `f83f66c` and fix-loop round 4. A rerun and refit are pending.
