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
uv run python scripts/fetch_weights.py  # pretrained models into the Hugging Face cache (1.4 GB)
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
| `uv run python scripts/fetch_weights.py` | 1.4 GB | 3 min 31 s |
| total | 1.8 GB | about 13.5 min |

Then per capture, weights already fetched (sample captures, see below):

| Capture | Tier | Run time |
|---|---|---|
| `single_room`: 37 s scan, 2 rooms | LiDAR | 1 min 54 s |
| `photos_with_ceiling`: 7 rooms, 28 photos | photo | 3 min 48 s |
| `rgb.mp4` of `single_scan_floor_only`: 115 s clip | video | 8 min 10 s first run, 3 min 27 s again |

The first video run estimates depth for each keyframe on the CPU; the depth maps are then
cached in `.cache/`, so a second run of the same video is faster. Other jobs shared the CPU
during several of these runs, so an idle machine is faster; `bench/reports/benchmark.md`
lists per-stage times with warm caches.

Adding up the steps, README to a first LiDAR plan took about 15.5 minutes here, about 12.5 of
them downloading, and to a first video plan about 22 minutes. So the 15-minute target depends
on the connection: the one-time downloads are 1.8 GB, about 3 minutes at 10 MB/s (the LiDAR
path would then take about 6 minutes; estimate, not measured) but 30 minutes at 1 MB/s.

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
output folder is named after it, so a video `rgb.mp4` writes `runs/rgb.mp4/`.

To try it on the sample captures (no iPhone needed):

```bash
uv run python scripts/fetch_data.py       # the three sample scans into ../data (0.87 GB, 3 min here)
uv run roomscan run ../data/single_room                                  # LiDAR
uv run roomscan run ../data/single_scan_floor_only/1a8384c3f6/rgb.mp4    # video: the scan's own RGB video
uv run python scripts/make_photo_set.py ../data/single_scan_with_ceiling ../data/photos_with_ceiling
uv run roomscan run ../data/photos_with_ceiling                          # photo
```

The sample flat has no photo capture: `make_photo_set.py` cuts stills from the scan's video
the way the photo protocol prescribes (6 min here; at this commit it builds 6 of the 7 rooms,
see the known gap below). The photo run warns "Corrupt EXIF data" on these stills; that is
harmless, the focal length is still read.

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
It builds the photo set with `scripts/make_photo_set.py` when `../data/photos_with_ceiling`
is missing, reads the data from `ROOMSCAN_DATA` instead of `../data` when that variable is
set, and overwrites `bench/reports/` (pass `--out <folder>` to keep the committed report).
Intermediate results (fused clouds, depth maps) are cached in `.cache/` and replay
deterministically; `--no-cache` forces the live path.

Checked on the clean copy: `fetch_data.py` gives the same files and sizes as the brief's
scans, and `bench/run_all.py --only room_lidar` reproduces the committed `room_lidar` row
(2 rooms, 10.6381 m2); the full benchmark was not rerun there. Known gap: at this commit
`make_photo_set.py` builds 6 rooms and 25 photos (it skips room_5: no look-back frame), while
the photo rows of `bench/reports/benchmark.md` came from an earlier 7-room, 28-photo set.
On the rebuilt set the photo tier gives 6 rooms and 99.5 m2 (90 % CI 85.9-113.2) instead of
7 rooms and 116.7 m2, so those rows will not match until the set or the report is regenerated.

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

| Model | Licence | Used for | Download |
|---|---|---|---|
| Depth Anything V2 Metric Indoor Small | Apache-2.0 | depth for video and photo tiers | 99 MB |
| EfficientLoFTR | Apache-2.0 | matching the look-back photo (photo-tier stitching) | 64 MB |
| CLIP ViT-B/32 | MIT | zero-shot damage classification | 1.2 GB |

Weights are fetched by `scripts/fetch_weights.py`; none are stored in the repository. CLIP
counts twice: `openai/clip-vit-base-patch32` publishes only `pytorch_model.bin`, and
transformers also downloads the hub's converted `model.safetensors` (605 MB each).

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
