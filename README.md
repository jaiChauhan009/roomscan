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

## Live demo

| | Link |
|---|---|
| **Web app (front end, Vercel)** | **https://roomscan-web-rose.vercel.app** |
| API (back end, Google Cloud VM, HTTPS) | https://34-14-174-240.sslip.io (health: [`/api/health`](https://34-14-174-240.sslip.io/api/health), interactive API: [`/docs`](https://34-14-174-240.sslip.io/docs)) |
| iOS capture app (RoomPlan live scan, like magicplan) | [`ios/`](ios/README.md); install file: [roomscan-unsigned.ipa](https://github.com/jaiChauhan009/roomscan/releases/download/ios-latest/roomscan-unsigned.ipa) (Sideloadly from Windows, see [docs/ios_app.md](docs/ios_app.md)) |

How to use it from a phone:
1. Open the site and, optionally, enter an email for the report.
2. Add rooms with photos, and/or whole-home videos and LiDAR scans (Stray Scanner zips).
3. Press **Check captures**, then **Start computing**.

The job runs on the server, so the tab can be closed. Results appear run by run (plan, sizes with
90 % ranges, Excel and JSON) and, with an email, arrive as a report.

The hosted app is a convenience. The brief requires everything to run without our infrastructure,
so the official path is the local command line below, which needs no network after setup.

## Project structure

| Part | Folder | README |
|---|---|---|
| **Back end** (FastAPI API, job queue, email) | [`server/`](server/) | [server/README.md](server/README.md) |
| **Web front end** (Vercel) | [`web/`](web/) | [web/README.md](web/README.md) |
| **iOS capture app** (RoomPlan live scan, CI-built `.ipa`) | [`ios/`](ios/) | [ios/README.md](ios/README.md), [docs/ios_app.md](docs/ios_app.md) |
| **Engine** (photos / video / LiDAR → plan) | [`src/roomscan/`](src/roomscan/) | [docs/architecture.md](docs/architecture.md) |
| **Benchmark** | [`bench/`](bench/) | [bench/README.md](bench/README.md) |
| **Fix loop** | [`fixloop/`](fixloop/) | [fixloop/README.md](fixloop/README.md) |
| **Deployment** | [`deploy/`](deploy/) | [docs/deploy.md](docs/deploy.md) |
| **Documentation** | [`docs/`](docs/) | [docs/README.md](docs/README.md) |

## Documentation map

| Document | What it covers |
|---|---|
| [docs/report.md](docs/report.md) | **Project report**: what was built, how we tested it, scores per tier, fix-loop rounds, deployment, limitations, optimization steps and next steps |
| [docs/architecture.md](docs/architecture.md) | **Architecture**: folder structure, inputs (required and optional), API endpoints and fields, pipeline stages and gates, the output JSON field by field, the front end, deployment |
| [docs/tech_report.md](docs/tech_report.md) | Technical report (max 6 pages): method, tier design, drift, error budget, calibration, fix loop, failure modes |
| [docs/compliance_matrix.md](docs/compliance_matrix.md) | Every requirement of the brief → file → artifact → status |
| [bench/reports/benchmark.md](bench/reports/benchmark.md), [calibration.md](bench/reports/calibration.md), [synth_damage.md](bench/reports/synth_damage.md) | Benchmark: gates at all tiers, repeatability, drift ablation, timing; interval calibration; synthetic damage |
| [fixloop/](fixloop/README.md) | Fix-loop rounds 1-4: declarations (committed before each fix), evidence, before/after runs, diffs, post-mortems |
| [docs/capture_protocol.md](docs/capture_protocol.md), [docs/device_matrix.md](docs/device_matrix.md) | Capture route (one page) and which tier runs on which device, with accuracy |
| [docs/scale_marker.md](docs/scale_marker.md) | Optional printed A4 marker for exact photo / video scale |
| [docs/deploy.md](docs/deploy.md), [deploy/oracle/setup.sh](deploy/oracle/setup.sh) | Hosting: Vercel front end, Docker back end on a VM (Google Cloud / Oracle), HTTPS, email settings |
| [docs/raw_data.md](docs/raw_data.md) | Raw benchmark data: what it is, where it comes from, checksums (`bench/raw_data.sha256`) |
| [docs/worklog.md](docs/worklog.md), [docs/design_qa.md](docs/design_qa.md) | Build log by stage; design decisions and their reasons |
| [server/README.md](server/README.md) | Web API reference |
| [docs/testing.md](docs/testing.md) | **How to run every test** (engine, back end, web, iOS app on the simulator, live system, benchmark) and the latest results |
| [docs/ios_app.md](docs/ios_app.md) | iOS app: screens, install with Sideloadly, how to scan, `capture.json` format, CI and simulator tests |

## What we achieved (summary)

Every number comes from the reports above. Details and caveats are in [docs/report.md](docs/report.md).

| Area | Result |
|---|---|
| All three tiers | photos, video and LiDAR each produce the same output contract: per-room and stitched plan, walls, ceilings, openings, damage, concealed-damage flags with the rule that fired, scope items, a 90 % interval on every number, JSON to the schema, rendered plan, Excel |
| LiDAR vs laser truth (4 public ARKitScenes rooms) | footprint within 0.6-7.6 %; median wall error 3-13 cm; ceilings 2 of 4 within 1.5 cm (fix-loop round 4) |
| Repeatability, our iPhone 16 Pro | the same room scanned twice: shared walls agree to 1.7 cm (median) |
| Interval honesty (held-out coverage, target 0.90) | LiDAR 0.85, video 0.93, photo 0.92 |
| Synthetic damage benchmark | stain and crack both found with the right class, extents within 16 %, 0 false positives: PASS |
| Fix loop | 4 rounds, each declared before the fix; rounds 2 and 4 hit every declared number |
| Tests | Python: 218 passed, 2 skipped (HEIC on Windows), plus RoomPlan / marker / server / web suites; iOS: device build and simulator unit, UI and end-to-end tests green on GitHub Actions ([docs/testing.md](docs/testing.md)) |
| iOS app | RoomPlan live guided scan (automatic walls, doors, windows, live area), room-by-room upload, photos, video, live results; RoomPlan tier on the back end tested end to end on the cloud (synthetic rooms exact); not yet run on a real iPhone |
| Live system | Vercel front end + Google Cloud back end; jobs keep running with the tab closed; report emailed; tested end to end on real captures |
| Known gaps | photo / video accuracy far from the gates without a typed length or the A4 marker; head-to-head vs a consumer app and real staged damage still need the iPhone; benchmark to rerun after round 4 |

## Use it

Setup first (next section). Then either of two ways.

**1. Web app, locally.** Two terminals in the repository folder:

```bash
uv run --extra server uvicorn server.app:app --port 8000   # API + job queue (runs the engine)
python -m http.server -d web 5173                          # the static web front end
```

Open http://localhost:5173. Add a space per room (photos, a video or a LiDAR zip), upload,
press verify (OK / Check / Retake per space, with advice), run, and download the results.
Typed-in room sizes are optional; the results show them beside ours. API details:
[server/README.md](server/README.md). Hosting it: [docs/deploy.md](docs/deploy.md).

**2. Command line.**

```bash
uv run python scripts/walkin.py <capture or drive root>         # quality check, cold run, per-room table
uv run python scripts/walkin.py <capture> --check-only          # quality check only (exit 3 on RETAKE)
uv run roomscan run <capture>                                   # writes runs/<capture name>/
```

**Outputs** (one folder per run):

| File | Content |
|---|---|
| `result.json` | everything, to [schema/output.schema.json](schema/output.schema.json) |
| `result.xlsx` | the same as sheets: Summary, Rooms, Walls, Openings, Damage, Flags, Scope, Warnings; each number as value, 90 % low, 90 % high |
| `plan.png`, `plan.svg` | the stitched, dimensioned floor plan |
| `stages.json` | every stage with its time and its gate (ok / warn / fail, with a note) |
| `walkin.txt` | `walkin.py` only: the printed per-room report |

Optional: a `measurements.yaml` with a tape length per room sets the photo / video scale
(see "Known sizes" in [docs/architecture.md](docs/architecture.md)). Also optional: a printed A4
marker on a wall sets the scale of that room's photos, or of the whole clip, when it is seen and no
length was typed ([docs/scale_marker.md](docs/scale_marker.md)).

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

**Fastest path** (same files, same results): the two downloads come from different servers
(PyPI and Hugging Face), so fetch the weights while `uv sync` runs, and only the weights your
tier needs (`--tier lidar` is CLIP only, 0.61 GB; `video` adds depth, 0.71 GB; `photo` and
the default `all` add matching, 0.77 GB). The prefetch needs no project environment, only
`huggingface-hub` (a few MB, pinned to the locked version):

```powershell
# Windows PowerShell, from the repository folder
$here = (Get-Location).Path
$w = Start-Job { Set-Location $using:here; uv run --no-project --with huggingface-hub==1.33.0 python scripts/fetch_weights.py --download-only --tier lidar }
uv sync --extra ml
Receive-Job $w -Wait
uv run python scripts/fetch_weights.py --tier lidar    # finds the files cached, then the offline check
```

```bash
# macOS / Linux
uv run --no-project --with huggingface-hub==1.33.0 python scripts/fetch_weights.py --download-only --tier lidar &
uv sync --extra ml; wait
uv run python scripts/fetch_weights.py --tier lidar
```

Two terminals work too: the first line in one, `uv sync --extra ml` in the other. The last
step loads each model the way the pipeline does, fetches anything the prefetch missed, and
checks that the models load offline. In Windows PowerShell 5.1 `Receive-Job` shows the
download progress bars in red; that is not an error. A tier whose weights are not cached still runs: the
pipeline downloads the missing model on first use.

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

**With the fastest path (estimate, not measured on a clean machine).** The weights then
download during `uv sync` instead of after it: at the speeds above, 0.61 GB for `--tier
lidar` takes about 1.5 min against 9 min 15 s for `uv sync`, so it adds nothing to the wall
time unless the two downloads share a saturated link (here PyPI was the slow side at 0.75 MB/s
while Hugging Face gave 7 MB/s, so they did not). What stays after `uv sync` is the loading
and offline check, estimated at 30-60 s (importing torch and loading CLIP twice; not timed
separately). One-time setup: about 11 min instead of 12.5, and README to a first LiDAR plan
about 13 min instead of 14.5 (15.5 measured end to end when `fetch_weights.py` still pulled
the unused 605 MB CLIP copy). The remaining time is almost all
`uv sync`: it already downloads in parallel (uv's default is 50 concurrent downloads,
`UV_CONCURRENT_DOWNLOADS`), so raising that does not help a link that delivers 0.75 MB/s in
total. On Windows and macOS the locked torch wheel from PyPI is already the CPU-only build
(win_amd64: 124 MB), so the PyTorch CPU index would save nothing there; it would on Linux
(below), but switching it changes `uv.lock` and is not done here.

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
| `result.xlsx` | the same, as spreadsheet sheets (`python -m roomscan.export.sheet <result.json>` converts an older run) |
| `plan.png`, `plan.svg` | the stitched, dimensioned floor plan |
| `stages.json` | stage times and gate results |

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
  export/                    build.py (output contract), render.py (plan), sheet.py (xlsx)
  capture_quality.py         quick check before the long run, with retake advice
  stages.py                  stage queue with a gate after each stage
  known_sizes.py, markers.py optional tape sizes and printed A4 marker
server/                      web API (FastAPI): projects, uploads, verify, job queue
web/                         static web front end (no build step)
bench/                       gates, evaluation, repeatability, run_all, ground truth
docs/                        capture protocol, device and compliance matrices, report,
                             design Q&A, architecture, worklog, iPhone session plan
fixloop/                     fix loop round 1 (declaration, before / after runs); round2/
scripts/                     fetch_weights, fetch_data, make_photo_set, dev tools
```
