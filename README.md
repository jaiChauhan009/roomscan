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

## Setup (once)

Needs git and [uv](https://docs.astral.sh/uv/); no GPU. Tested on Windows 11 (x86_64). The
locked torch and open3d wheels also exist for macOS 14 or newer on Apple silicon and for
Linux with glibc 2.35 or newer (Ubuntu 22.04 and later), x86_64 or arm64, but those have not
been run; Intel Macs, older macOS and older Linux have no wheels and `uv sync` fails there.
You do not need to install Python: if the machine has no Python 3.11, `uv sync` downloads
one (24 MB).

```bash
git clone https://github.com/jaiChauhan009/roomscan.git
cd roomscan
uv --version                            # no uv yet? install it first (below)
uv sync --extra ml                      # .venv with Python 3.11 and all dependencies (1.4 GB on disk)
uv run python scripts/fetch_weights.py  # pretrained models into the Hugging Face cache (0.77 GB)
```

Installing uv (once per machine), any one of:

- any OS with Python: `pip install uv` (if `pip` is not found: `python -m pip install uv`,
  on Windows `py -m pip install uv`)
- macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows PowerShell: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

The two installer lines need no Python; they put uv on PATH, so open a new terminal after
them. Debian/Ubuntu and Homebrew Pythons refuse `pip install` outside a virtual environment
("externally-managed-environment"): use the installer line there. If `pip install uv`
works but `uv` is then "not found" (its Scripts folder is not on PATH, common on Windows),
type `python -m uv` (Windows: `py -m uv`) wherever this README says `uv`.

`fetch_weights.py` ends by loading every model with the network switched off. After it the
pipeline runs without network access, but unless `HF_HUB_OFFLINE=1` is set, transformers
still asks the Hugging Face Hub whether the cached models are current: with the network
blocked that cost 30-40 s of retries per run here, with a connection a few small requests. The
"unauthenticated requests to the HF Hub" warning it prints is harmless; no token is needed.

**Windows 11 with Smart App Control on:** if `uv run` stops with "An Application Control
policy has blocked this file (os error 4551)", Smart App Control has blocked the small
unsigned launcher that uv writes as `.venv\Scripts\python.exe` when it downloaded Python
itself (seen with uv 0.12.22; the launcher of uv 0.12.21 was allowed). Asking for the exact
Python version makes uv copy Python's own launcher instead, which is allowed:

```powershell
Remove-Item -Recurse -Force .venv
uv sync --extra ml --python 3.11.17     # the version uv sync printed as "Using CPython 3.11.x"
```

## Time to a first result (measured)

Measured by following this README on a clean copy: fresh clone, empty uv, Python and Hugging
Face caches, on a Windows 11 laptop with an AMD Ryzen AI 7 PRO 350 (8 cores), 16 GB RAM and
no GPU. Downloads ran at about 0.75 MB/s from PyPI, 7 MB/s from Hugging Face and 5 MB/s from
Google Drive.

One-time setup, almost all of it downloading:

| Step | Downloads | Time |
|---|---|---|
| `git clone` | 14 MB | 4 s |
| `pip install uv` (only if uv is missing) | 18 MB | 25 s |
| `uv sync --extra ml` | 0.40 GB (+24 MB, about 20 s, if Python 3.11 is missing) | 9 min 15 s |
| `uv run python scripts/fetch_weights.py` | 0.77 GB | about 2 min 20 s (estimate, see below) |
| total | 1.2 GB | about 12.5 min |

Then per capture, weights already fetched (sample captures, see below):

| Capture | Tier | Run time |
|---|---|---|
| `single_scan_with_ceiling`: 215 s scan, 9 rooms | LiDAR | 3 min 39 s (`scripts/walkin.py`, cold, idle machine) |
| an iPhone 16 Pro scan, 13 s, 1 room | LiDAR | 24 s |
| `single_room`: 37 s scan, 2 rooms | LiDAR | 1 min 54 s |
| `photos_with_ceiling`: 7 rooms, 28 photos | photo | 3 min 48 s |
| `rgb.mp4` of `single_scan_floor_only`: 115 s clip | video | 8 min 10 s first run, 3 min 27 s again |

The flat's cold run, by stage: drift 28 s, fuse 39 s, layout 10 s, openings 6 s, damage
131 s. The first video run estimates depth for each keyframe on the CPU; the depth maps are
then cached in `.cache/`, so a second run of the same video is faster. Other jobs shared the
CPU during the other runs, so an idle machine is faster; `bench/reports/benchmark.md` lists
per-stage times with warm caches.

`fetch_weights.py` took 3 min 31 s on the clean copy while it also downloaded a second copy of
the CLIP weights (605 MB) that is never loaded; it no longer does, and its time above is that
measurement minus the copy's share of the download. Back to back on a slower connection here
(about 3 MB/s from Hugging Face), the script took 6 min 54 s with the copy and 4 min 54 s
without.

Adding up the steps with that estimate, README to a first LiDAR plan takes about 14.5
minutes here, about 11.5 of them downloading, and to a first video plan about 20.5 minutes. So
the 15-minute target depends on the connection: the one-time downloads are 1.2 GB, about 2
minutes at 10 MB/s (the LiDAR path would then take about 5 minutes; estimate, not measured)
but 20 minutes at 1 MB/s.

Linux downloads much more. The PyPI build of torch for Linux bundles CUDA libraries (cuDNN,
cuBLAS, NCCL, triton) that this CPU-only pipeline never uses, so `uv sync --extra ml` fetches
about 3.6 GB of wheels instead of 0.40 GB (sizes read from `uv.lock`; no Linux machine was
used for this check). macOS on Apple silicon: about 0.37 GB, also from `uv.lock`.

## Run on a capture

From the repository folder (cached intermediate results go to `.cache/` there):

```bash
uv run roomscan run <capture>                 # writes runs/<capture name>/
uv run roomscan run <capture> --out my_out    # choose the output folder
uv run python scripts/walkin.py <capture or drive root>   # live test: cold run, per-room table
```

`<capture>` is the Stray Scanner folder, the video file, or the folder of room folders. The
output folder is named after it, so a video `rgb_upright.mp4` writes `runs/rgb_upright.mp4/`.

To try it on the sample captures (no iPhone needed):

```bash
uv run python scripts/fetch_data.py       # the three sample scans into ../data (0.87 GB, 3 min here)
uv run roomscan run ../data/single_room                                  # LiDAR
uv run python scripts/make_iphone_clip.py ../data/single_scan_floor_only/1a8384c3f6 ../data/video_floor_only
uv run roomscan run ../data/video_floor_only/rgb_upright.mp4             # video: the scan's video as an upright iPhone clip
uv run python scripts/make_photo_set.py ../data/single_scan_with_ceiling ../data/photos_with_ceiling
uv run roomscan run ../data/photos_with_ceiling                          # photo
```

The sample flat has no photo or video capture of its own. `make_photo_set.py` rebuilds the
benchmark's 7-room, 28-photo set from the scan's video, byte for byte, from the recipe in
`bench/photo_set_recipe.yaml` (about 30 s; `--check <set>` verifies a set, `--select` cuts a
new one). `make_iphone_clip.py` gives the scan's video the rotation flag an upright iPhone
recording carries (Stray stores the frames sideways without one; nothing is re-encoded).
The photo run warns "Corrupt EXIF data" on these stills; that is harmless, the focal length
is still read.

Output folder:

| File | Content |
|---|---|
| `result.json` | everything, to the schema in [schema/output.schema.json](schema/output.schema.json) |
| `plan.png`, `plan.svg` | the stitched, dimensioned floor plan |

Useful options: `--drift off` (drift-correction ablation), `--no-damage` (geometry only,
faster), `--no-cache` (recompute cached stages), `--tier lidar|video|photo` (override detection).

## Reproduce the reported numbers

```bash
uv run python scripts/fetch_data.py     # raw captures into ../data (0.87 GB download, 3 min here)
uv run python bench/run_all.py          # every table in bench/reports/benchmark.md
uv run python bench/head_to_head.py <ground_truth.yaml> <result.json> <app.yaml>   # app comparison table
```

`bench/run_all.py` runs each capture in `bench/manifest.yaml` through the same code path as
`roomscan run`, scores it against `bench/ground_truth/<capture>.yaml` (gates in
`bench/gates.yaml`), then runs the repeatability pairs and the drift ablation: five captures,
one of them the video (about 8 minutes on its own the first time), plus two ablation runs.
It builds the photo set and the upright clip (the manifest's `prepare:` steps) when they are
missing, reads the data from `ROOMSCAN_DATA` instead of `../data` when that variable is
set, and overwrites `bench/reports/` (pass `--out <folder>` to keep the committed report).
Intermediate results (fused clouds, depth maps) are cached in `.cache/` and replay
deterministically; `--no-cache` forces the live path.

Checked on the clean copy: `fetch_data.py` gives the same files and sizes as the brief's
scans, and `bench/run_all.py --only room_lidar` reproduces the committed `room_lidar` row
(2 rooms, 10.6381 m2); the full benchmark was not rerun there. The photo set rebuilt from the
recipe is byte-identical to the one behind the report, and the photo tier on it reproduces
the committed row (7 rooms, 116.7447 m2). After new ground truth, refit the intervals with
`bench/run_all.py --eval-only` then `bench/calibrate.py --write` (`bench/reports/calibration.md`).

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
Every design decision with its reason and evidence: [docs/design_qa.md](docs/design_qa.md).
Structure: [docs/architecture.md](docs/architecture.md); history: [docs/worklog.md](docs/worklog.md).

## Pretrained models (disclosure)

| Model | Licence | Used for | Download |
|---|---|---|---|
| Depth Anything V2 Metric Indoor Small | Apache-2.0 | depth for video and photo tiers | 99 MB |
| EfficientLoFTR | Apache-2.0 | matching the look-back photo (photo-tier stitching) | 64 MB |
| CLIP ViT-B/32 | MIT | zero-shot damage classification | 609 MB |

Weights are fetched by `scripts/fetch_weights.py`; none are stored in the repository.
`openai/clip-vit-base-patch32` publishes only `pytorch_model.bin` (605 MB), which transformers
loads; roomscan sets `DISABLE_SAFETENSORS_CONVERSION=1` when it is unset, so transformers does
not also download the hub's converted `model.safetensors` (another 605 MB, never loaded).

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
docs/                        capture protocol, device and compliance matrices, report,
                             design Q&A, architecture, worklog, iPhone session plan
fixloop/                     fix loop round 1 (declaration, before / after runs); round2/
scripts/                     fetch_weights, fetch_data, make_photo_set, dev tools
```
