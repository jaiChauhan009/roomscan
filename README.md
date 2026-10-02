# roomscan

Phone capture (photos, video or LiDAR) → one dimensioned, stitched floor plan of the whole
property, with damage regions, concealed-damage flags, scope line items and a 90 %
confidence interval on every measurement.

One command per capture. The input tier is detected from what you give it:

| You give it | Tier | Capture tool |
|---|---|---|
| a Stray Scanner folder (`rgb.mp4`, `depth/`, `odometry.csv` ...) | LiDAR | Stray Scanner (App Store) on a Pro iPhone |
| a video file (`.mov`, `.mp4`) | video | iPhone Camera app |
| a folder of per-room photo folders | photo | iPhone Camera app |

How to capture: [docs/capture_protocol.md](docs/capture_protocol.md) (one page).

## Setup (clean machine, about 10 minutes, mostly downloads)

Needs Python 3.11 and git. Windows, macOS or Linux. No GPU needed.

```bash
git clone https://github.com/jaiChauhan009/roomscan.git
cd roomscan
pip install uv                          # installer; any recent pip works
uv sync --extra ml                      # creates .venv with all dependencies (about 1.5 GB)
uv run python scripts/fetch_weights.py  # pretrained models into the local cache (about 0.8 GB)
```

After this the pipeline needs no network access and calls no external service.

## Run on a capture

```bash
uv run roomscan run <capture>                 # writes runs/<capture name>/
uv run roomscan run <capture> --out my_out    # choose the output folder
```

Output folder:

| File | Content |
|---|---|
| `result.json` | everything, to the schema in [schema/output.schema.json](schema/output.schema.json) |
| `plan.png`, `plan.svg` | the stitched, dimensioned floor plan |

Useful options: `--drift off` (drift-correction ablation), `--no-damage` (geometry only,
faster), `--no-cache` (recompute cached stages), `--tier lidar|video|photo` (override detection).

## Reproduce the reported numbers

```bash
uv run python scripts/fetch_data.py     # raw captures into ../data (about 0.9 GB)
uv run python bench/run_all.py          # every table in bench/reports/benchmark.md
```

`bench/run_all.py` runs each capture in `bench/manifest.yaml` through the same code path as
`roomscan run`, scores it against `bench/ground_truth/<capture>.yaml` (gates in
`bench/gates.yaml`), then runs the repeatability pairs and the drift ablation.
Intermediate results (fused clouds, depth maps) are cached in `.cache/` and replay
deterministically; `--no-cache` forces the live path.

To add your own capture with ground truth: copy `bench/ground_truth/TEMPLATE.yaml`, fill in
the laser measurements, add one line to `bench/manifest.yaml`.

## How it works (short)

```
LiDAR (Stray Scanner) ─┐  depth + poses from the phone
video ─────────────────┼─ KLT tracking + mono depth + PnP ──► posed depth frames ─► shared back end
photos ────────────────┘  mono depth + per-room rectangle fit + look-back stitching ─► shared export

shared back end: drift correction → fused cloud → floor / ceiling / walls → rooms → openings
                 → damage → concealed-damage rules → scope → intervals → JSON + plan
```

Details, error budget and known failure modes: [docs/tech_report.md](docs/tech_report.md).
Requirement-by-requirement status: [docs/compliance_matrix.md](docs/compliance_matrix.md).

## Pretrained models (disclosure)

| Model | Licence | Used for |
|---|---|---|
| Depth Anything V2 Metric Indoor Small | Apache-2.0 | depth for video and photo tiers |
| EfficientLoFTR | Apache-2.0 | matching the look-back photo (photo-tier stitching) |
| CLIP ViT-B/32 | MIT | zero-shot damage classification |

Weights are fetched by `scripts/fetch_weights.py`; none are stored in the repository.

## Repository layout

```
src/roomscan/
  cli.py, pipeline.py        one command per capture
  frontends/                 lidar_stray.py, video.py, photos.py
  geometry/                  pointcloud, planes, layout, boxfit, openings, drift, metrics
  damage/                    detect.py, rules.yaml, scope.yaml, pipeline.py
  uncertainty/               intervals.py, calibration.yaml
  export/                    build.py (output contract), render.py (plan)
bench/                       gates, evaluation, repeatability, run_all, ground truth
docs/                        capture protocol, device matrix, compliance matrix, report
fixloop/                     fix loop round 1 (declaration, before / after runs); round2/
scripts/                     fetch_weights, fetch_data, make_photo_set, dev tools
```
