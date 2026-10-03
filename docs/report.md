# roomscan: project report

Applied AI Engineer case study: a phone capture becomes a dimensioned, stitched floor plan with 90 %
confidence intervals, damage regions, concealed-damage flags, scope line items, JSON to a schema and a rendered plan.

Status as of 3 October 2026.
- Every number below comes from a file in this repository or from our own test logs, and names its source.
- Numbers that are stale or unknown are marked as such.

Companion documents:
- [tech_report.md](tech_report.md) (method, max 6 pages), [architecture.md](architecture.md)
- [compliance_matrix.md](compliance_matrix.md), [device_matrix.md](device_matrix.md)
- [worklog.md](worklog.md), [capture_protocol.md](capture_protocol.md), [deploy.md](deploy.md)
- [../fixloop/](../fixloop/README.md), [../bench/reports/](../bench/reports/benchmark.md)

---

## Results at a glance

### Strengths, measured

| What | Result | Source |
|---|---|---|
| LiDAR footprint vs laser truth (4 public rooms, Faro scans) | within **0.6-7.1 %** (one room passes the gate outright at +0.6 %) | [benchmark.md](../bench/reports/benchmark.md), [tech_report.md](tech_report.md) |
| LiDAR walls vs laser | median error **3-13 cm** per room | same |
| LiDAR ceilings vs laser (after fix-loop round 4) | **2 of 4** rooms within 1.5 cm; every room within **0.22 cm of its declared prediction** | [fixloop/round4](../fixloop/round4/README.md) |
| LiDAR repeatability, our iPhone 16 Pro | the same room scanned twice: shared walls agree to **1.7 cm** (median), **5 mm** on the clean wall | [benchmark.md](../bench/reports/benchmark.md) |
| Drift correction | loop residual on the sample's scan B **0.169 → 0.026 m**; walls sharper with it on (ablation) | benchmark.md |
| Honest intervals (held-out coverage, target 0.90) | LiDAR **0.95**, video **0.93**, photo **0.92**; LiDAR's 90 % range on a 3 m wall tightened from ±11.8 cm to **±6.8 cm** after the refit | [calibration.md](../bench/reports/calibration.md) |
| Damage (synthetic staged, two classes) | both found with the right class, extents within 16 %, **0 false positives: PASS** | [synth_damage.md](../bench/reports/synth_damage.md) |
| RoomPlan tier (our iOS app's scans), synthetic | exact: 12.00 m² and 9.00 m² rooms, 2.60 m ceilings, live on the cloud | [testing.md](testing.md) |
| Fix loop | 4 rounds declared before each fix; rounds 2 and 4 met **every** declared number | [fixloop/](../fixloop/README.md) |

### Gates that pass

- Interval calibration at all three tiers: held-out coverage LiDAR 0.95, video 0.93, photo 0.92 (target 0.90).
- Photo-tier stitch with **no room overlaps**.
- Drift accountability: correction on/off ablation reported, with a measurable gain.
- Synthetic damage: found, classified and sized within 30 %, no phantoms.
- Footprint within ±3 % on one laser room (+0.6 %); the others are within 7.1 %.
- Ceilings within 1.5 cm on 2 of 4 laser rooms.

### Photos and video: plain vs assisted

The brief allows calibration aids. With one tape-measured length per room typed in (the optional
`measurements.yaml`, or the room's L/B/H boxes in the web and iOS apps), the photo tier gets the metric scale it otherwise
has to guess. Both rows are real measurements, held out on the sample flat
([compliance_matrix.md](compliance_matrix.md), [architecture.md](architecture.md) "Known sizes").

| Tier | Mode | Footprint error | Median wall error |
|---|---|---|---|
| Photo (sample flat) | plain: monocular depth only | +109 % | 1.41 m |
| Photo (sample flat) | **assisted: one tape length per room** | **−23 %** | **0.27 m** |
| Video (sample flat) | plain | −70 % | 0.37 m |
| Video (sample flat) | assisted: one length per room | −87 % (not helped: its error is missing rooms, not scale) | n/a |

The printed A4 marker is the second aid. It is wired into both tiers and corrects a 1.6× scale error to
within 2 % on synthetic rooms. It has not yet been measured on a real capture.

### Gates that fail: cause and fix path

| Gate | Where we are | Cause | Fix path |
|---|---|---|---|
| LiDAR walls ≤ 1 cm | 3-13 cm median | furniture taller than ~1.1 m hides wall bases; a uniform ~−1 % scale bias in the converted laser clouds | round 5: per-frame depth vs laser at the same pixels; wall evidence from above furniture |
| Ceiling ≤ 1.5 cm on all rooms | 2 of 4 | the residual −1 % scale bias (all four rooms read low by the same fraction) | same as above; no scale factor fitted on the test set, by choice |
| Repeatability, sample flat | rooms split 9 vs 7 | scan B never looked up, so no door heads or ceilings; open-plan rooms have no wall to split on | the protocol's upward sweep; the capture checker flags such a scan as RETAKE |
| Video footprint ±3 % | −70 % | tracking breaks on fast sweeps and blank walls, so only part of the home survives | slower capture (the checker flags speed); the A4 marker for scale; more keyframes |
| Photo footprint ±8 % | +32 % to +109 % plain, −23 % assisted | no depth sensor: monocular scale per photo | one tape length or the A4 marker per room |
| Opening widths ≤ 2 cm | not met on video / photo | doors cannot be measured from thin, unscaled depth | LiDAR or RoomPlan for openings |

## Testing without a permanent iOS device

The brief assumes an iPhone 15 or newer with LiDAR. We **did not own one**. We borrowed an iPhone 16 Pro for one short
session (about 30 minutes) and used it for everything that needed real hardware:
- three Stray Scanner LiDAR scans of our flat, including a repeat of one room;
- photos and a video;
- a magicplan Auto-Scan export for the head-to-head.

Later attempts to get the device again were only partly successful, so we tested as much as possible without it:

| What | How we tested it without the device | Result |
|---|---|---|
| LiDAR tier accuracy | public ARKitScenes rooms (iPad Pro LiDAR) with Faro laser truth; our own iPhone scans for repeatability | numbers above |
| Video and photo tiers | the sample flat's video and photo set; our iPhone and Android captures | numbers above |
| Damage | synthetic stain and crack painted into real scan frames | PASS |
| Our iOS app | built on GitHub's macOS runners; unit, UI-screenshot and end-to-end tests on the **iOS Simulator** against the live back end | all green ([testing.md](testing.md)); screens in [img/ios_app_screens.jpg](img/ios_app_screens.jpg) |
| RoomPlan tier | synthetic `capture.json` rooms through the deployed cloud API | exact sizes |
| Web app and back end | real captures through the Vercel site and the Google Cloud back end; email reports | done |

What could **not** be tested without a device, and is reported as open, not claimed:
- a real RoomPlan scan with our app;
- tape truth of our flat, and therefore the magicplan head-to-head score;
- real staged damage;
- the A4 marker on a real print.

---

## 1. Summary

**What was built.**

- **Engine.** One command (`roomscan run <capture>`) takes one of:
  - a LiDAR scan (Stray Scanner);
  - a video;
  - per-room photo folders.

  It writes `result.json` (to [schema/output.schema.json](../schema/output.schema.json)), `result.xlsx`,
  `plan.png`/`plan.svg` and `stages.json`. Every measurement carries a value, a sigma and a 90 % interval.
- **Same output from every tier.** All three tiers feed one shared back end:
  - drift correction, fusion, layout;
  - openings, damage, concealed-damage rules, scope;
  - intervals, export.

  All tiers produce the **same output contract**. Only `capture.tier` and the width of the intervals differ, as the brief asks.
- **Web app.** It is live: the front end is on Vercel and the back end on a Google Cloud VM, with optional email of results.
  - A phone browser uploads photos per room, up to 5 whole-home videos and up to 5 LiDAR scans.
  - The app checks the captures, runs the job and shows results as each run finishes.

**What works.**

- **LiDAR tier.** Runs end to end on a CPU-only laptop and on the cloud VM, and gives plausible whole-flat plans.
  Our own flat: 4 rooms, 59.7 m², in 2.5 min cold.
- **Video and photo tiers.** They run and produce a plan with honest (very wide) intervals, but they are far
  from the brief's accuracy gates.

**Headline accuracy by tier.** Ground truth is scarce, so the "truth" column matters.

| Tier | Truth | Headline numbers | Source |
|---|---|---|---|
| LiDAR | laser (4 public ARKitScenes rooms, Faro scans) | footprint −7.1 / −2.5 / +0.6 / −2.0 %; median wall error 3-13 cm per room (the 1 cm gate fails); ceilings −2.93 / −0.81 / −1.66 / −0.92 cm, 2 of 4 within the 1.5 cm gate | [tech_report.md §1](tech_report.md), [fixloop/round4/after/summary.md](../fixloop/round4/after/summary.md) |
| LiDAR | self-consistency, own iPhone 16 Pro | repeat scan of one room: walls seen by both scans agree to 1.7 cm median (2 walls, max 3.0 cm) | [bench/reports/benchmark.md](../bench/reports/benchmark.md) |
| LiDAR | self-consistency, sample flat (2 scans) | point clouds agree to 7 mm median, but the scans split the flat into 9 vs 7 rooms; the repeatability gate fails | [fixloop/round2/README.md](../fixloop/round2/README.md), benchmark.md |
| Video | our own LiDAR output (not a laser) | finds 1-2 rooms; footprint −70 % (sample), −87 % and −95 % (own clips) | benchmark.md |
| Photo | our own LiDAR output (not a laser) | footprint +109 % (sample proxy), +32 % (moto g45), +79 % (iPhone via WhatsApp); no metric scale without a tape length or marker | benchmark.md |
| All | held-out interval coverage (target 0.90) | LiDAR 0.95, video 0.93, photo 0.92 (wall lengths; final refit, 3 October 2026) | [bench/reports/calibration.md](../bench/reports/calibration.md) |

---

## 2. Problem and deliverables

The brief scores seven parts. Status of each:

| Part (weight) | What we have | Status |
|---|---|---|
| Walk-in test (30 %) | `scripts/walkin.py` (quality check, cold run, per-room table; `--check-only` exits 3 on RETAKE); the live web app; [capture_protocol.md](capture_protocol.md); walk-in checklist in [iphone_session.md](iphone_session.md) | ready, not yet performed by an assessor. We tested it on our own iPhone and moto g45 captures |
| Fix loop (25 %) | 4 rounds, each with a declaration committed before the fix, an evidence script, before/after runs, a diff and a post-mortem ([fixloop/](../fixloop/README.md)) | done. Rounds 2 and 4 met every declared number; round 1 missed badly; round 3 met most numbers and missed two (section 7) |
| Verified benchmark (15 %) | 16 captures in [bench/manifest.yaml](../bench/manifest.yaml); laser truth for 4 public rooms; repeatability pairs; drift ablation; synthetic damage bench | partial (see notes below) |
| Compliance matrix (10 %) | [compliance_matrix.md](compliance_matrix.md) | done, but it lists fix-loop rounds 1-3 only. Round 4 and the cloud deployment need adding |
| Head-to-head vs consumer app (10 %) | `bench/head_to_head.py`, `bench/app_exports/TEMPLATE.yaml`, tested on synthetic and sample data | **not done**: it needs an iPhone running magicplan (or similar) on the same two rooms |
| Capture route (5 %) | Route 2 (stock tools): Stray Scanner + Camera app, a one-page protocol, the device matrix, plus the web upload app | done. Not yet followed by a non-engineer |
| Process (5 %) | git history with stage commits, [worklog.md](worklog.md), [design_qa.md](design_qa.md), fix-loop tags | done |

Why the verified benchmark is only partial:
- Laser truth covers the LiDAR tier only, and those rooms are public data, not ours.
- Video and photo are scored against our own LiDAR output.
- The committed benchmark predates the round 3-4 fixes for one room (section 8).

---

## 3. Capture route and real-device testing

**Route.** Route 2: stock capture tools, no app of our own.
- LiDAR uses Stray Scanner (free, App Store).
- Video and photos use the Camera app.

The protocol is shaped to make the hard problems easier ([capture_protocol.md](capture_protocol.md)):
- every door open;
- one upward sweep per room;
- for photos, a fixed doorway standpoint and one look-back photo per room.

**The iPhone session.** We borrowed an iPhone 16 Pro with LiDAR for about 30 minutes and captured our own flat.
We also used a moto g45 for video and photos.

| Capture | Device | Result (benchmark name) |
|---|---|---|
| LiDAR, whole flat, 147 s | iPhone 16 Pro, Stray Scanner | 4 rooms, 60.8 m² in the current benchmark (`own_lidar_1`); 59.7 m² at capture time |
| LiDAR, part of the flat, 55 s | same | 4 rooms, 34.9 m² (`own_lidar_2`); two ceiling lights reported as "holes" (false positive) |
| LiDAR, room 4 alone, 31 s | same | 13.6 m² (`own_lidar_3`); repeat of `own_lidar_1` room 4, walls agree to 1.7 cm median |
| Video via WhatsApp (464×832) | iPhone 16 Pro | 1 room (`own_video_iphone`) |
| 58 photos via WhatsApp (960×1280, EXIF stripped) | iPhone 16 Pro | 5 rooms, +79 % footprint (`own_photos_iphone`) |
| Video 84 s, 1080p | moto g45 (Android) | 1 room of 3.3 m²; tracking broke (`own_video_1`) |
| 186 photos, taken while walking | moto g45 | 6 rooms, +32 % footprint (`own_photos_1`) |

**What the 30 minutes could not cover.** We had the iPhone only briefly, not permanently. So we could not:
- build and test a native iOS app;
- run the head-to-head against a consumer app (magicplan);
- stage real damage and capture it;
- take tape measurements of our flat. Tape truth is still pending; the form, with our values next to blanks, is
  [tape_form_own_flat.md](tape_form_own_flat.md).

The iPhone video and photos reached us through WhatsApp, recompressed. They therefore test robustness, not protocol accuracy.

**Why a web app instead of an iOS app.** Without an iPhone at hand, a native app could not be built, signed or tested.
A web app needs no install and works from any phone browser. It accepts what the stock tools produce:
- photos, per room;
- whole-home videos (up to 5);
- Stray Scanner LiDAR scans, zipped (up to 5).

Before the long run it checks every space (OK / Check / Retake, with advice), then shows the stages live and the
results of each run as soon as that run finishes.

**Device matrix (short).**
- LiDAR tier: iPhone 15-17 Pro / Pro Max and iPad Pro 2020+.
- Video and photo tiers: any phone. Tested on the iPhone 16 Pro and a moto g45.
- Photos without an EXIF focal length fall back to a 26 mm default, with a warning.
- Full table: [device_matrix.md](device_matrix.md). Its accuracy table predates the 16-capture benchmark and round 4;
  use section 8 for current numbers.

### 3.1 Our own iOS app (Route 1, added late)

After the borrowed-iPhone session we also built a native app: `ios/`, Swift, iOS 17+, no third-party packages.
- **Build and install without a Mac:**
  - GitHub Actions macOS runners build it into `roomscan-unsigned.ipa` (release `ios-latest`);
  - it is installed from Windows with Sideloadly and a free Apple ID (7-day signing).
- **Scanning:** it uses Apple RoomPlan, the technology behind magicplan's Auto-Scan.
  - The live guided scan finds walls, doors and windows automatically.
  - A live panel shows the walls, doors, windows, area and size.
  - Each room is uploaded as soon as it is scanned.
  - Keyframes with poses are saved for our damage stage.
- **Same structure as the web app:** email, rooms with photos, video, check, compute, live results.
- **Back end:** a new RoomPlan tier turns the app's `capture.json` into the same output contract.
  - It is exact on synthetic rooms and live on the cloud (Kitchen 12.00 m², Hall 9.00 m², ceilings 2.60 m).
- **Tests:** CI runs unit, UI (screenshots) and end-to-end tests against the deployed back end on the iOS Simulator, all green.
- **Not done:** it has **not yet been run on a real iPhone**, because we no longer had one when it was finished.
- See [ios_app.md](ios_app.md) and [testing.md](testing.md).

**magicplan for the head-to-head.** A magicplan Auto-Scan of our flat was exported:
- 3 rooms, 66.92 m² in total; our LiDAR run says 60.8 m², with the rooms divided differently;
- it is stored in `bench/app_exports/magicplan_own_flat.yaml` with the PDF;
- the comparison needs tape measurements of the same rooms to say which is closer.

---

## 4. System overview

```
LiDAR (Stray Scanner) ─┐  depth + poses from the phone
video ─────────────────┼─ KLT tracking + mono depth (Depth Anything V2) + PnP ─► posed depth frames
photos ────────────────┘  mono depth + per-room rectangle fit + look-back stitching (EfficientLoFTR)

shared back end: drift correction → fused cloud → floor / ceiling / walls → rooms → openings
                 → damage (CLIP zero-shot) → concealed-damage rules → scope → intervals → JSON + plan
```

The central decision: every tier is converted into posed depth frames, so the wall, door and damage logic is written once.
- Details: [architecture.md](architecture.md).
- Method and error budget: [tech_report.md](tech_report.md).

Around the engine:
- a capture-quality checker (`capture_quality.py`);
- optional known sizes (`measurements.yaml`) and a printed A4 ArUco scale marker;
- a gated stage queue (`stages.py`);
- the web app (`server/` FastAPI, `web/` static).

---

## 5. Deployment (live)

```
phone / browser ──► Vercel (static front end, web/) ──► Google Cloud VM (FastAPI + engine in Docker, Caddy HTTPS)
                                                          └─► results on the VM disk, optional report email (Gmail SMTP)
```

| Piece | Where | Notes |
|---|---|---|
| Front end | static site in `web/` on **Vercel** (Hobby plan): **https://roomscan-web-rose.vercel.app** | redeploys automatically from GitHub `main`; `web/env.js` uses the cloud API unless the page runs on localhost |
| Back end | FastAPI + engine in Docker on a **Google Cloud Compute Engine VM** | see the details below |
| Setup script | `deploy/oracle/setup.sh` | installs Docker, builds the image on the VM, runs it with restart, sets up Caddy HTTPS on `<ip>.sslip.io`. Works on Oracle ARM and on GCP. Re-run it to update |
| Email | Gmail SMTP (`server/notify.py`) | optional email field at the top of the page; the report (every room's sizes with ranges, plan, spreadsheet) is mailed when the job ends. Credentials live only in the server's env file (`/etc/roomscan.env`), never in the repository |
| Monitoring | UptimeRobot on `/api/health` (suggested) | alerts if the server goes down; GCP VMs do not sleep |

Back-end VM details:
- e2-standard-4 (4 vCPU, 16 GB), Ubuntu 24.04, asia-south1 (Mumbai), static IP.
- HTTPS through Caddy at `https://34-14-174-240.sslip.io`.
- The container restarts automatically after a crash or a reboot.
- Data stays on the VM disk; results are kept 7 days.

**Hosting choices.**
- **Hugging Face Docker Spaces** were the first plan ([deploy.md](deploy.md) sections B-E). They now require a PRO subscription.
- **Oracle Cloud Always Free** (ARM, 4 OCPU / 24 GB) was prepared as the free alternative ([deploy.md](deploy.md) section G);
  every dependency has an ARM build.
- **Google Cloud** runs the live instance, on the free-trial credit (about $3.50 a day for this VM).

**Limits.** Treat the deployment as a demo:
- no authentication or rate limits;
- a single job worker;
- uploads on the VM's local disk ([deploy.md §F](deploy.md)).

**How the background work behaves.**
- Files upload from the browser, and the queue survives a reload via IndexedDB.
- Once "Start computing" is pressed, the job runs on the server, so the tab can be closed.
- Reopening the site in the same browser shows the project and the job's live stages or finished result.
- Re-running unchanged captures returns the saved result immediately: the cache key is the content hash of the files,
  plus the room settings and the engine version.

---

## 6. How we tested

### 6.1 Unit and integration tests

- **Full suite (final code):** 257 passed, 2 skipped; 1 min 15 s with 4 parallel workers (`pytest -n 4`).
  - The 2 skips are the HEIC tests: Windows Application Control blocks the `pillow_heif` DLL on the development laptop.
    The Linux server image decodes HEIC.
  - Server and web tests pass.
- **Coverage:** 30 files under `tests/`:
  - geometry on synthetic rooms, furnished ones included (`test_geometry.py`, `test_outline.py`);
  - drift: a synthetic loop reproducing scan B's closure pattern (`test_drift.py`);
  - the output contract, flags and scope (`test_contract.py`);
  - intervals and calibration (`test_intervals_evidence.py`, `test_calibration.py`);
  - damage outline and crack (`test_damage_*.py`, `test_synth_damage.py`);
  - capture quality, known sizes, the A4 marker, the stage queue and the xlsx export;
  - photo thinning, depth batching and video speed;
  - the head-to-head tool;
  - the FastAPI server: projects, uploads, verify, run, cache, several videos and scans, email
    (`test_server.py`);
  - a headless static web smoke test at 360 px (`test_web_static.py`).
- **Browser-side photo shrinking** was checked in headless Chrome on a real 12 MP iPhone photo:
  - the file went from 1.57 MB to 0.27 MB;
  - the focal length was kept;
  - the engine read the same 960×723 input with the same intrinsics, with a mean pixel difference of 0.57/255;
  - the capture checker passed it as an original.

### 6.2 Benchmark

[bench/manifest.yaml](../bench/manifest.yaml) lists 16 captures, scored by `bench/evaluate.py` against `bench/gates.yaml`.
The brief's Round 1 document was not available, so some gate values are assumed; `gates.yaml` marks them.

| Group | Captures | Truth |
|---|---|---|
| Sample flat | `apt_lidar_a`, `apt_lidar_b`, `room_lidar`; `apt_video_b` and `apt_photo_a` derived from the scans' own video | none; video and photo scored against the LiDAR output ("lidar_reference") |
| ARKitScenes (public, iPad Pro LiDAR) | `arkit_42446532`, `arkit_44358446`, `arkit_47332890`, `arkit_47331988` | laser (Faro) scans; the selection rule was fixed before any run |
| Own flat | `own_lidar_1..3`, `own_video_1`, `own_photos_1` (moto g45), `own_video_iphone`, `own_photos_iphone` | none yet; video and photo vs `own_lidar_1` |

The benchmark scores these metrics:
- wall length (pass fraction, median and max error);
- ceiling height (1.5 cm gate);
- opening width (2 cm gate, missed and phantom openings counted);
- footprint, adjacency, room overlap and rooms found;
- interval coverage;
- repeatability (1 cm / 0.5 % per wall);
- drift on/off ablation;
- stage timing.

The latest scores are in section 8.

### 6.3 Interval calibration

Every measurement has `sigma = scale · sqrt((k·raw)² + abs² + (rel·value)²)`.
- `scale` is fitted per tier by split-conformal calibration (`bench/calibrate.py`), stored in
  [calibration.yaml](../src/roomscan/uncertainty/calibration.yaml).
- Coverage is measured held out, leaving one room out ([calibration.md](../bench/reports/calibration.md)).

| Tier | Truth | Samples (rooms) | Coverage at scale 1 | Fitted scale | Held-out coverage | 90 % on a 3 m wall |
|---|---|---|---|---|---|---|
| LiDAR | laser | 20 (4) | 0.25 | 2.909 | 0.95 | ±0.068 m |
| video | own LiDAR | 40 (4) | 0.23 | 6.931 | 0.93 | ±0.773 m |
| photo | own LiDAR | 83 (15) | 0.36 | 5.225 | 0.92 | ±1.362 m |
| photo area | own LiDAR | 15 (15) | 0.27 | 10.927 | 0.93 | ±18.9 m² on 10 m² |

**Final refit (3 October 2026).** The full benchmark was rerun on the final code (split-room fix `f83f66c` and fix-loop round 4 included) and refitted: the LiDAR scale fell from 5.07 to 2.909, as tech_report §7 expected, and held-out coverage rose to 0.95.
- With the priors alone, coverage was 0.20-0.36: confident but wrong on thin input. Calibration is what makes the intervals honest.

### 6.4 Repeatability

| Pair | What agrees | What differs | Gate |
|---|---|---|---|
| Sample flat, scans A vs B | point clouds to 7 mm median | rooms split 9 vs 7; 1 room paired; 2 of 20 compared walls within the gate; median difference 8.4 cm | **FAIL**. Scan B skipped the upward sweep, so it saw no door heads or ceilings |
| Own iPhone, `own_lidar_1` room 4 vs `own_lidar_3` | walls seen by both, to 1.7 cm median; 4.162 vs 4.157 m (5 mm) on the one wall no furniture touches | 4 rooms vs 1, because the second scan covered one room by design | **FAIL** on the room-count rule |

### 6.5 Synthetic damage benchmark

Real staged damage needs the iPhone. As a proxy, `bench/synth_damage.py` paints damage onto bare walls of scan A, in
every frame that shows them:
- a 0.52 × 0.55 m water stain;
- a 0.58 × 0.44 m crack.

Then the benchmark's own damage scoring runs ([synth_damage.md](../bench/reports/synth_damage.md)):

| Check | Result |
|---|---|
| Clean capture | no region reported (0 false positives) |
| Stain | found as a stain; extents −12 % / −13 % |
| Crack | found as a crack; extents −14 % / −16 % |
| Phantoms | 0 |
| Flags and scope | one concealed-damage flag (CD-04), 7 scope items |
| Gate (assumed: all found with class, extents within 30 %, 0 phantoms) | **PASS**, median extent error 0.132 |

Notes:
- Older documents quote the crack width as +61 %, from an earlier run. The committed report shows −14 %.
- On our own iPhone scans, ceiling lights are reported as a "hole", which is a real false positive.

### 6.6 Capture-quality checker

`capture_quality.py` gives OK / WARN / RETAKE per check, with one line of advice. It runs in seconds
(photos 2-12 s, LiDAR < 1 s). It catches what hurt our captures:
- 9-55 photos per room taken while walking;
- WhatsApp copies (long side < 1600 px);
- walking too fast (our walk 0.88 picture widths/s, the sample 0.26);
- a LiDAR scan that never looked up (scan B: 0 % of frames above 20°).

### 6.7 End-to-end tests

| Where | Job | Result |
|---|---|---|
| Laptop (Ryzen 7, 16 GB, no GPU), web app | photos + LiDAR of the own flat | finished in ~5 min. LiDAR: 4 rooms with 90 % ranges, e.g. 20.4 m² (18.6-22.3), 18.1, 9.4 and 12.9 m²; ceilings 2.39-2.57 m. Photos without a typed length have no metric scale (a kitchen at 44 m², range 0-224 m²) |
| Laptop, web app with email | report email for a finished job | sent and received with every room's sizes, plans and spreadsheets attached |
| Google Cloud VM, through the Vercel front end (2026-10-03) | job `e7cb3519c1a3`, photos of 6 rooms | finished, report email **sent**. Stages: load+depth 82 s, room_fit 10 s, **stitch 518 s**, openings 5 s, damage 41 s, export 1 s: ~11 min |
| Clean Windows copy | README to the first LiDAR plan | 15.5 min measured (12.5 min of it downloading at ~0.75 MB/s from PyPI); about 13-14.5 min estimated without the duplicate CLIP download ([README](../README.md)) |

Stitch took 518 s on the VM, against 148-304 s in the laptop benchmark for the own photo sets. This is open (section 10).

---

## 7. Fix loop, rounds 1-4

Each round has the same steps:
1. Pick the worst failing gate.
2. Find the root cause with an evidence script.
3. Commit a declaration with numeric predictions, before the fix.
4. Apply the fix, run before and after, and write a post-mortem.

| Round | Gate | Root cause found | Declared | Measured | Verdict |
|---|---|---|---|---|---|
| 1 ([README](../fixloop/README.md)) | LiDAR repeatability (scans A/B: 9 vs 6 rooms, 50.1 vs 27.7 m²) | the wall test demanded evidence above the height scan B ever looked at | footprint gap within ±10 %, 6+ rooms paired, walls ≤ 5 cm | gap −45 % → −18 %, 1 room paired, 24 cm median wall difference | right direction, prediction badly wrong; the post-mortem (furniture occlusion) was later shown wrong |
| 2 ([README](../fixloop/round2/README.md)) | same | drift correction never corrected: odometry weighted too stiffly, so the pose graph pruned large loop closures | B moves ≥ 0.4 m, crispness up, gap within ±6 %, bedroom walls within ±3/±10/±3 cm | B moves 0.52 m; gap −0.9 % (by union −6.3 %); bedroom walls +2.0 / −7.0 / −1.0 cm | every declared number met; the gate still fails, as declared (rooms split differently) |
| 3 ([README](../fixloop/round3/README.md)) | same (0 rooms paired) | snapped outlines kept furniture-notch steps; a self-crossing snap threw the room back to its raw contour; wardrobe fronts beat the walls above them | raw-contour rooms 0, A/B walls without evidence ≤ 45/≤ 35, footprint growth < 6 % | raw-contour rooms 0; 41/39 walls without evidence; A footprint +12.3 %; 1 room paired | partly met (B walls and A growth missed); video calibration fell 0.94 → 0.57 (not predicted), refitted after the round |
| 4 ([README](../fixloop/round4/README.md)) | ceiling height ≤ 1.5 cm on the 4 laser rooms (1 of 4 passed, all low) | floor and ceiling levels were histogram peaks weighted by viewing time, so they landed on the most-watched patch | 2 of 4 pass, each error to ±0.3 cm | 2 of 4 pass (−2.93, −0.81, −1.66, −0.92 cm); every room within 0.22 cm of its prediction | met; the gate still fails, as declared. A uniform ~−1 % scale bias remains in the converted laser clouds |

---

## 8. Results and scores

Source: [bench/reports/benchmark.md](../bench/reports/benchmark.md) (16 captures) unless noted.
- The benchmark was rerun on the final code on 3 October 2026 (16 captures, split-room fix and round 4 included).

**LiDAR vs laser (ARKitScenes).**

| Room | Footprint error | Median wall error | Ceiling error (after round 4) | Coverage | Rooms found |
|---|---|---|---|---|---|
| 42446532 | −7.1 % | 0.047 m | −2.93 cm | 0.8 | 1/1 |
| 44358446 | −2.5 % | 0.104 m | −0.81 cm (pass) | 1.0 | 1/1 |
| 47332890 | +0.6 % (pass) | 0.130 m | −1.66 cm | 0.8 | 1/1 |
| 47331988 | −2.0 % | 0.033 m (2 of 4 walls within the gate) | −0.92 cm (pass) | 1.0 | 1/1 |

**Video and photo vs the LiDAR reference.** "In CI" means the reference lies inside our 90 % interval.

| Capture | Rooms (found / ref) | Footprint | Median wall error | Interval coverage | Calibration gate |
|---|---|---|---|---|---|
| apt_video_b | 2 / 7 | −70 % (not in CI) | 0.37 m | 0.93 | PASS |
| own_video_1 (moto g45) | 1 / 4 | −95 % | 1.20 m | 0.67 | FAIL |
| own_video_iphone | 1 / 4 | −87 % | 0.21 m (7.2 %) | 0.83 | PASS |
| apt_photo_a | 7 / 9 | +109 % (in CI) | 1.41 m | 0.93 | PASS |
| own_photos_1 (moto g45) | 6 / 4 | +32 % (in CI) | 0.85 m | 0.96 | PASS |
| own_photos_iphone | 5 / 4 | +79 % (in CI) | 1.52 m | 1.0 | FAIL (too wide) |

Opening-width pass fraction is 0.0 on every video and photo set.

**Other results.**

| Item | Result |
|---|---|
| Drift ablation | scan A crispness 8.43 → 8.84, scan B 10.02 → 12.26 with correction on; loop residual on B 0.169 → 0.026 m |
| Synthetic damage | PASS (2/2 found, 0 phantoms, 0 false positives on the clean scan) |
| Known sizes (photo, one tape length per room, held out on the sample flat) | footprint +109 % → −23 %, walls 1.41 → 0.27 m; video not helped (−70 % → −87 %) |
| A4 ArUco marker | scale vs LiDAR depth 1.002 (0.998-1.006, 11 frames); not yet wired into the photo and video tiers |
| Run times, laptop | whole-flat LiDAR 195 s / 138 s; own iPhone LiDAR 100 s; sample photo set 240 s; own videos 698-1680 s (mostly depth on CPU) |

---

## 9. Known limitations and failure modes

- **Little ground truth.**
  - Laser truth exists only for 4 public LiDAR rooms.
  - The sample flat and our own flat have none; tape for our flat is pending.
  - Video and photo numbers are scored against our own LiDAR.
- **LiDAR gates.**
  - The 1 cm wall gate fails everywhere (median 3-13 cm). Ceilings: 2 of 4 rooms within 1.5 cm.
  - A ~−1 % scale bias in the converted ARKitScenes clouds is unexplained: it could be the data, the converter or ARKit LiDAR.
  - Furniture taller than ~1.1 m leaves notches where no wall is seen above it.
- **Segmentation repeatability.**
  - Rooms with no wall between them (open plan, corridors) split or merge differently between captures.
  - Scans without an upward sweep miss door heads and ceilings.
- **Video tier.**
  - Fast sweeps, blur and blank walls break tracking, and only the longest segment survives (1-2 rooms found).
  - Scale from monocular depth is ±10-20 %.
- **Photo tier.**
  - No metric scale unless one tape length per room is typed in, or the A4 marker is used (the marker is not wired in yet).
    Example: a kitchen at 44 m² with a 0-224 m² range.
  - Stitching falls back to capture order when the look-back photo does not match (flagged).
- **Damage.**
  - Only synthetic (painted) staged damage has been tested.
  - Ceiling lights are reported as holes on real scans.
  - Each wall patch is seen in few frames, which limits recall.
- **Not tested on real captures:** mirrors (rendered only), glass to bright outdoors, wet-look surfaces,
  closed doors (measured as wall by design).
- **Deployment.**
  - No authentication or rate limits; one worker; uploads on the VM's local disk.
  - Stitch is slower on the cloud VM than on the laptop.
- **Schema.** We publish our own schema; the brief's Round 1 schema was not available.

---

## 10. Optimization

**Done.**

| Step | Where | Effect |
|---|---|---|
| Thin photos to 8 per room, keeping the look-back | `frontends/photos.py` (`MAX_PER_ROOM = 8`, `thin_room`) | moto g45 rooms of 9-55 photos had made the run take over 40 min |
| Depth estimation batched by image shape | `ml/depth.py`, `tests/test_depth_batches.py` | mixed portrait/landscape no longer crashes; fewer, larger batches |
| Caching of depth maps and fused clouds | `.cache/`, keyed by content | video rerun 8 min 10 s → 3 min 27 s (README) |
| Fastest-first run order | `server/capture.py` (photos, then LiDAR items, then videos) | LiDAR results (2-3 min) arrive before a slow video |
| Progressive results | `web/js/app.js`, `web/js/results.js` | each run's results are shown as soon as that run finishes |
| Video tracking speed-up | `frontends/video.py`, `tests/test_video_speed.py` | tracking 158 s → 105 s on the sample clip (working session) |
| Browser-side photo shrinking to 2048 px, EXIF focal length kept | `web/js/shrink.js` | 1.57 MB → 0.27 MB per photo, identical engine input verified; matters on 6-8 Mbit/s uplinks |
| Parallel uploads | `web/js/upload.js` (`UPLOAD_WORKERS = 3`) | 3 files at a time instead of one |
| Content-hash keyed uploads and results | `server/store.py`, `server/capture.py` | re-uploading the same file is a no-op; an unchanged project reuses its saved result instantly |
| Local model files first | `ml/hub.py` | no network round trip when weights are cached |

**Next.**
1. **Stitch speed on the cloud VM** (518 s): profile thread count and BLAS, CPU type and memory.
2. **More compute:** a bigger VM, or a GPU for depth and matching.
3. **Uploads:** move them to object storage (S3 / R2), and delete raw uploads after the job.
4. **Hardening:** authentication (a shared token, see [deploy.md §F](deploy.md)) and rate limits.
5. **Jobs:** a worker queue for more than one concurrent job.

---

## 11. Next steps

**Needs an iPhone** (a session plan exists in [iphone_session.md](iphone_session.md)):
1. Head-to-head vs magicplan on two rooms (`bench/head_to_head.py` is ready).
2. A furnished room with real staged damage in two classes.
3. Video and photo captures that follow the protocol, as original files rather than WhatsApp copies.
4. A repeat whole-flat LiDAR scan with the upward sweep, to test the repeatability gate as defined.

**No iPhone needed:**
1. Fill the tape form of our own flat ([tape_form_own_flat.md](tape_form_own_flat.md)) and add it as ground truth.
   That gives the video and photo tiers a real truth.
3. Test the −1 % scale bias per frame against the laser (fix-loop round 5).
4. Wire the A4 marker into the photo and video tiers.
5. Investigate the cloud stitch speed. Set up UptimeRobot on `/api/health`.
6. Add a Google Cloud section to [deploy.md](deploy.md).
