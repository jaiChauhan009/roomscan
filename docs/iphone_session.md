# iPhone session: 2-3 hours with a borrowed iPhone

Everything in the brief that needs a phone is captured in one sitting, and the walk-in
route (phone > drive > `scripts/walkin.py`) is rehearsed on real iPhone files before the
phone goes back. Everything that does not need the phone (ground truth, props, drive,
laptop) is done the day before.

## What the session must produce

| Name (= `bench/manifest.yaml` name) | Tool | What it is | Brief requirement it covers |
|---|---|---|---|
| `flat_lidar_1`, `flat_lidar_2` | Stray Scanner | whole property, 3+ rooms and the connector (hall), same route twice | multi-room capture; repeatability; drift ablation |
| `bedroom_lidar_1`, `bedroom_lidar_2` | Stray Scanner | the furnished room with the staged damage, alone, twice | staged damage in two classes; a room captured twice at the same tier |
| `flat_video` | Camera, video | whole property, one clip | same rooms at the video tier |
| `flat_photo` | Camera, photos | whole property, one folder per room | same rooms at the photo tier, must stitch |
| `magicplan_flat` | magicplan (free) | two of the rooms, with its PDF / CSV export | Part 3 head-to-head |
| ground truth | laser + tape | every wall, ceiling, opening, damage prop | laser or tape ground truth on everything |

Example rooms used below: `hall` (the connector), `living`, `kitchen`, `bedroom` (the
furnished damage room). Use your own room names, the same everywhere.

**Decide now and write it down** (so nobody can say we picked after seeing numbers): the
two head-to-head rooms are `bedroom` and `living`, and our side of the head-to-head is
`flat_lidar_1`.

## The day before (no iPhone needed, about 2 hours)

**1. The phone.** Borrow an iPhone **15 Pro / 15 Pro Max or a newer Pro** (three cameras
and a black LiDAR dot). A non-Pro iPhone has no LiDAR: no LiDAR tier, no head-to-head.
Ask the owner to install **Stray Scanner** and **magicplan** (free; installing needs their
Apple ID) and to have 15 GB free and a charged battery. Create a free magicplan account
with your own e-mail.

**2. Equipment.** Laser distance measurer (note model and stated accuracy, e.g. ±1.5 mm),
5 m tape, USB-C flash drive of 32 GB or more, USB-C-to-A adapter if the laptop needs it,
the phone's charging cable, white or cream painter's / masking tape, A3 white paper, black
tea, a dark grey marker with a 2-3 mm tip, scissors.

**3. The drive.** Format it **exFAT** (File Explorer > right-click the drive > Format >
exFAT; the iPhone cannot write NTFS). Create the folders the session fills (PowerShell; E:
is the drive letter):

```powershell
"smoke_lidar","smoke_video","smoke_photo\01_room","smoke_default_video","smoke_default_photo\01_room",
"flat_lidar_1","flat_lidar_2","bedroom_lidar_1","bedroom_lidar_2","flat_video","flat_photo\01_hall",
"flat_photo\02_living","flat_photo\03_kitchen","flat_photo\04_bedroom","app_exports" |
  ForEach-Object { New-Item -ItemType Directory -Force "E:\$_" | Out-Null }
```

The `flat_photo` folders are the photo walk order: `01_hall` first (you enter through it),
then the rooms in the order you will walk them.

**4. The laptop** (repository root, PowerShell):

```powershell
git pull
uv sync --extra ml
uv run python scripts/fetch_weights.py
uv run python -m pytest -q
uv run python scripts/walkin.py ..\data\single_room     # about 1-2 min; must end with the room table
```

The walk-in run must print `models: all in the local cache; running offline`. If it does
not, run `uv run python scripts/fetch_weights.py` once with network (it fetches all three
models, CLIP included, and checks that they load offline). Keep 20 GB free on the laptop.

**5. The staged damage** (two classes, both removable; the tea must dry):
- **Water stain** (`water_stain`): brew strong black tea (3 bags in a mug, cooled). On A3
  paper the colour of the wall, dab an irregular blob about 35 x 25 cm, then dab more tea
  along its edge for a darker tide line. Dry flat (2-3 h, or a hair dryer). Tear the
  paper about 5 cm outside the stain so no straight edge shows. Fix it flat with loops of
  tape behind it, **bottom edge 5-20 cm above the floor**, on a bedroom wall, best the
  wall backing onto a bathroom or within 50 cm of a window (concealed-damage rules CD-02,
  CD-04, CD-07 then have something to fire on).
- **Crack** (`crack`): a strip of white or cream tape about 1.1 m long. Draw a jagged
  line 2-3 mm wide with a few short branches (a thin pen line is invisible from 1.5 m).
  Stick it diagonally from the top corner of a bedroom door or window frame (CD-10;
  longer than 1 m also CD-09).
- Why: the detector weighs damage against "a poster on a wall", "a picture frame",
  "a shelf". Straight paper edges or blue tape make a prop read as an object.
- Leave both up for every capture, the app scans included.

**6. Ground truth** (45-60 min; can also be done after the session, walls do not move).
Copy `bench/ground_truth/TEMPLATE.yaml` to `bench/ground_truth/flat.yaml` and fill it in,
following the instructions at its top:
- walls: per room, facing the door you came in by, start with the wall on your left and
  go clockwise; corner to corner at about 1 m height; every straight segment of 25 cm or
  more. Measure each wall twice; if the readings differ by more than 3 mm, measure a
  third time and keep the median.
- ceiling: laser on the floor near the middle of the room, pointing up.
- openings: every door (frame to frame, door open), window (glass opening) and open
  passage.
- adjacency, and `instrument`, `measured_by`, `date`.
- damage: room, wall (position in that room's `walls` list), class, width, height,
  `left`, `bottom`. Photograph each prop with the tape in view.

Then make the bedroom-only file and one copy per capture (`bench/run_all.py` looks
ground truth up by capture name):

```powershell
cd bench\ground_truth
"flat_lidar_1","flat_lidar_2","flat_video","flat_photo" | ForEach-Object { Copy-Item flat.yaml "$_.yaml" }
# bedroom.yaml: flat.yaml with only the bedroom room, no adjacency, no footprint
"bedroom_lidar_1","bedroom_lidar_2" | ForEach-Object { Copy-Item bedroom.yaml "$_.yaml" }
cd ..\..
```

Set `capture:` inside each copy to its own name.

## The session

| # | Step | Minutes | Clock |
|---|---|---|---|
| 1 | Phone setup, versions | 10 | 0:10 |
| 2 | Smoke test: all three tiers on one room, through the laptop | 20 | 0:30 |
| 3 | LiDAR, whole property, twice | 15 | 0:45 |
| 4 | LiDAR, bedroom alone, twice | 8 | 0:53 |
| 5 | Video, whole property | 10 | 1:03 |
| 6 | Photos, whole property | 15 | 1:18 |
| 7 | magicplan: living and bedroom, export | 25 | 1:43 |
| 8 | Last copies, checks before the phone goes back | 15 | 1:58 |
| 9 | Reserve: retakes, cable fallback test, a non-engineer follows the protocol | 30-60 | 2:30-3:00 |

**Rule for every capture:** right after it, plug the drive into the laptop, copy the
capture to `..\data\<name>` (commands below), plug the drive back into the phone, and start
processing the local copy while you capture the next one. Problems then show up while the
phone is still there. Never process straight from the drive while it is back on the phone.
A retake gets a new name (`flat_lidar_1b`); never put two recordings in one folder.

```powershell
# LiDAR (unzips with the tar.exe built into Windows)
New-Item -ItemType Directory -Force ..\data\flat_lidar_1 | Out-Null
tar -xf (Get-ChildItem E:\flat_lidar_1\*.zip).FullName -C ..\data\flat_lidar_1
# video / photos
Copy-Item -Recurse E:\flat_video ..\data\
Copy-Item -Recurse E:\flat_photo ..\data\
# process (one at a time in one window; each prints its room table at the end)
uv run python scripts/walkin.py ..\data\flat_lidar_1 --out runs\flat_lidar_1
```

The copy and the start of a run take about 2 minutes per capture; the step times include
them. Expected processing time on the development laptop, CPU only: LiDAR whole property
2-4 min, one room 1-2 min, photos 3-4 min, video about 1 min per 10-15 s of clip (the
115 s sample clip took 10.5 min cold).

Keep a session log (`..\data\session_log.txt`, shipped with the raw data): time, capture
name, anything that went wrong or was retaken.

### 1. Phone setup (10 min)

- Write down: Settings > General > About > **Model Name** and **iOS Version**; App Store >
  Stray Scanner > Version History > **version**; the same for **magicplan** (or its
  Settings > About).
- Settings > Camera > Formats > **Most Compatible** (as the capture protocol says).
- magicplan: sign in; set units to metric.
- Do Not Disturb on (a call stops a recording). Battery 80 % or more, or keep it plugged
  into a power bank.
- Plug the drive into the phone: Files > Browse must list it under Locations with the
  folders you made.

### 2. Smoke test (20 min): the most important 20 minutes

Prove that real iPhone files go through the whole chain before spending the session on
captures. In the bedroom:
1. Stray Scanner, about 40 s, following `docs/capture_protocol.md` (LiDAR tier, one room).
   Compress > copy the .zip to `smoke_lidar`.
2. Camera video, about 20 s, the protocol's video walk. Save to Files > `smoke_video`.
3. Four photos from the doorway, the protocol's photo sweep. Save to Files >
   `smoke_photo\01_room`.
4. On the laptop, straight from the drive this once (the phone waits):
   ```powershell
   uv run python scripts/walkin.py E:\smoke_lidar --tier lidar --out runs\smoke_lidar
   uv run python scripts/walkin.py E:\smoke_photo --tier photo --no-damage --out runs\smoke_photo
   uv run python scripts/walkin.py E:\smoke_video --tier video --no-damage --out runs\smoke_video
   ```
   Pass: each prints a room table, no error. Two walls of the LiDAR table agree with a
   laser reading to about 1 cm. The LiDAR run's `Damage:` line lists the props (if not,
   note it; pause longer on them in step 4).
   If the video does not decode, the message names the codec: check the Most Compatible
   setting and record again. Do not go on until all three run.
5. Once the runs above are done: set Formats to High Efficiency (the iPhone default),
   record 15 s of video and take 4 photos into `smoke_default_video` and
   `smoke_default_photo\01_room`, set Most Compatible again, and run `walkin.py` on both
   (`--no-damage`). If they run, an examiner who skips the Formats step loses nothing;
   write the answer in the session log either way. (The Stray Scanner sample's own video
   is HEVC and decodes here; iPhone HDR video and HEIC photos have not been tried.)

### 3. LiDAR, whole property, twice (15 min) -> `flat_lidar_1`, `flat_lidar_2`

Follow the protocol's LiDAR tier literally (this also rehearses the protocol): start in
the hall at the entrance, every room, hall included, ceiling and floor sweep in every
room. Pause 2-3 s facing each damage prop from about 1.5 m (damage detection looks at a
couple of dozen sharp frames spread over the recording). Second recording: same start,
same route, same direction. Compress each and copy it to its folder on the drive.

### 4. LiDAR, bedroom alone, twice (8 min) -> `bedroom_lidar_1`, `bedroom_lidar_2`

Start in the doorway, once around the room, ceiling and floor sweep, 3 s on each prop
from about 1.5 m, finish in the doorway. Same route twice. A single room is the cleanest
repeatability pair (on the sample flat the two whole-flat scans split the rooms
differently), and these are the damage close-ups.

### 5. Video, whole property (10 min) -> `flat_video`

The protocol's video tier, the same route as step 3. Save to Files > `flat_video`.

### 6. Photos, whole property (15 min) -> `flat_photo`

The protocol's photo tier, rooms in the order of the `flat_photo` folders (`01_hall`
first). Look-back photo in every room except the hall. At the end, in Photos > Select,
save each room's photos into its folder.

### 7. magicplan, living and bedroom (25 min) -> `magicplan_flat`

- One new project (the free plan has two; keep one spare). Scan `living`, then `bedroom`,
  with magicplan's LiDAR scan, following the app's own instructions: that is the fair
  use of the app. Name the rooms `living` and `bedroom`.
- Do not correct walls, doors or windows by hand. If the app asks to confirm a detected
  door or window, accept its values.
- Read off and type into `bench/app_exports/magicplan_flat.yaml` (copy of
  `bench/app_exports/TEMPLATE.yaml`): every wall length, ceiling height, every opening
  width, plus app version, device, iOS, date, `capture: flat_lidar_1`.
- Export: project > Files and Sharing: the floor-plan PDF and Statistics as CSV, saved to
  the drive's `app_exports`. Also a screenshot of each room's plan with dimensions
  (side button + volume up). If an export turns out to be paid, the screenshots are the
  export: say so in the YAML's `notes`.
- If magicplan cannot be used on the day: Polycam (free), Room mode, same two rooms. Its
  help pages say the free plan exports only GLTF (no floor plans), so keep the GLTF and
  the screenshots. One app per table.

### 8. Before the phone goes back (15 min)

Check every `walkin` table (or `runs\<name>\walkin.txt`):
- `flat_lidar_1/2`: all rooms present, hall included; **every room has a ceiling height**
  (a `ceiling not observed` means the upward sweep was missed: retake); three walls against
  the laser within about 2 cm.
- Repeatability at a glance:
  `uv run python bench/repeatability.py runs\bedroom_lidar_1\result.json runs\bedroom_lidar_2\result.json`
- `bedroom_lidar_*`: the `Damage:` line shows water_stain and crack (if not: keep the
  result, it is honest; one retake with longer pauses is fair).
- `flat_photo`: all rooms in the plan; a warning `look-back photo could not be matched`
  means that room's last photo should be retaken (same spot, facing back).
- `flat_video`: rooms found; keep the clip either way (the video tier is the weakest).
- `app_exports`: PDF, CSV and screenshots on the laptop (`Copy-Item E:\app_exports\* bench\app_exports\`).

Then: Settings > Camera > Formats back to High Efficiency, delete the recordings, photos
and the magicplan project from the owner's phone, sign out of magicplan.

### 9. Reserve (30-60 min)

Retakes first. Then, if time is left: (a) try the cable route once, in case the drive
fails at the walk-in (Windows: the Apple Devices app or iTunes > the phone > Files >
Stray Scanner > drag the recording out, as Stray Scanner's export docs describe; photos and
videos: File Explorer > Apple iPhone > Internal Storage > DCIM); (b) hand
`docs/capture_protocol.md` to someone who has not seen it and let them do one capture
alone; write down every place they hesitate.

## After the session

**Manifest** (`bench/manifest.yaml`; paths relative to `..\data`; use the real clip name):

```yaml
  - {name: flat_lidar_1, path: flat_lidar_1, tier: lidar}
  - {name: flat_lidar_2, path: flat_lidar_2, tier: lidar}
  - {name: bedroom_lidar_1, path: bedroom_lidar_1, tier: lidar}
  - {name: bedroom_lidar_2, path: bedroom_lidar_2, tier: lidar}
  - {name: flat_video, path: flat_video/IMG_1234.MOV, tier: video}
  - {name: flat_photo, path: flat_photo, tier: photo}
repeatability:
  - [flat_lidar_1, flat_lidar_2]
  - [bedroom_lidar_1, bedroom_lidar_2]
drift_ablation: [flat_lidar_1, flat_lidar_2]
```

**Commands** (repository root):

```powershell
uv run python bench/run_all.py        # every gate, repeatability, drift ablation -> bench/reports/benchmark.md
                                      # (--only flat_lidar_1 flat_lidar_2 ... for a subset)
uv run python bench/head_to_head.py bench/ground_truth/flat_lidar_1.yaml bench/reports/runs/flat_lidar_1/result.json bench/app_exports/magicplan_flat.yaml
                                      # -> bench/reports/head_to_head.md / .json
uv run python scripts/walkin.py ..\data\flat_photo   # walk-in rehearsal, any capture, any tier
```

If a head-to-head room is paired with the wrong room of ours (the table shows large
errors on every wall of it), set `match: room_N` for that room in
`bench/ground_truth/flat_lidar_1.yaml`, with N from `plan.png`, and run it again.

Raw data for the submission (deliverable 8): the new folders in `..\data`, the session
log, the ground-truth YAMLs and `bench/app_exports/` (YAML and the app's files).

## Walk-in checklist

- Laptop: `git pull`, `uv sync --extra ml`, tests pass, a `walkin.py` run on a session
  capture of each tier prints `running offline` and its table. Charger.
- The drive: exFAT, **empty**, and the USB-C-to-A adapter.
- A printed `docs/capture_protocol.md` for the examiners.
- Expected run times per tier, from the session's `walkin.txt` files, to tell the
  examiners while it runs.
- Fallback if the drive route fails: the cable route tested in step 9.
