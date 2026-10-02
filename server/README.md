# roomscan server

A web API around the engine: a client creates a project, adds its rooms (name, optional
sizes, optional photos) and at most one whole-home capture (one video clip or one LiDAR scan
of every room), uploads the files, verifies the capture, runs it and downloads the plans. JSON throughout, all under `/api`; errors are `{"detail": "..."}`. Interactive docs at
`/docs` once it runs.

## Run locally

```
uv sync --extra ml --extra server
uv run python scripts/fetch_weights.py          # once: the models, into the Hugging Face cache
uv run --extra server uvicorn server.app:app --port 8000
```

Then `curl localhost:8000/api/health`. State goes to `./server_data` (projects, uploads,
jobs); it survives a restart. One job runs at a time (the models need the memory); others
queue. A job that was running when the server stopped is marked failed ("server restarted")
and can be run again.

## API

| method | path | |
|---|---|---|
| GET | `/api/health` | `{"ok": true, "version"}` (version = engine version + source hash) |
| POST | `/api/projects` | `{"project_id"}` |
| GET | `/api/projects/{pid}` | `{"project_id", "spaces": [...], "capture": {"kind","files"} \| null, "last_job_id"}` |
| PUT | `/api/projects/{pid}/order` | `{"space_ids": [...]}` (every room once): walk order; returns the project |
| POST | `/api/projects/{pid}/spaces` | a room: `{"name", "kind": "photos" (default), "sizes": {"length","width","height"}}` (metres or null) -> space; `video` / `lidar` are refused (422): they are the whole-home capture |
| PATCH | `/api/projects/{pid}/spaces/{sid}` | `{"name"?, "sizes"?}`; a size given as null is cleared |
| DELETE | `/api/projects/{pid}/spaces/{sid}` | |
| GET | `/api/projects/{pid}/spaces/{sid}/files` | `{"files": [{"name","sha256","size"}]}` (resume: skip hashes listed) |
| PUT | `/api/projects/{pid}/spaces/{sid}/files` | multipart `file`, `sha256` (hex, of the bytes), `name` -> file record; idempotent by hash; hash checked (400 on mismatch); 413 above the size limit |
| DELETE | `/api/projects/{pid}/spaces/{sid}/files/{sha256}` | |
| PUT | `/api/projects/{pid}/capture` | `{"kind": "video"/"lidar"}`: creates the whole-home capture; the same kind again keeps it, another kind replaces it (its files are dropped) |
| GET | `/api/projects/{pid}/capture` | `{"kind", "files"}`; 404 when there is none |
| DELETE | `/api/projects/{pid}/capture` | removes it and its files |
| GET | `/api/projects/{pid}/capture/files` | `{"files": [...]}` |
| PUT | `/api/projects/{pid}/capture/files` | multipart as for a room; same hash rules; **409** when it already holds a video (video capture) or a `.zip` (LiDAR capture): it takes exactly one, delete the old one first |
| DELETE | `/api/projects/{pid}/capture/files/{sha256}` | |
| POST | `/api/projects/{pid}/verify` | capture check: `{"ok", "spaces": [{"space_id","name","status","findings"}], "capture": {"name": "whole home: video"/"whole home: LiDAR", "kind", "status", "findings", "advice"} \| null, "project_findings"}`, levels ok / warn / retake |
| POST | `/api/projects/{pid}/run` | `{"damage": true, "force": false}` -> `{"job_id", "cached"}`; 409 when there is nothing to compute (no room with photos and no whole-home capture), or the last verify (of the same files) asked for a retake, unless `force` |
| GET | `/api/jobs/{jid}` | status, live stages, error, outputs (URLs, of the first run), runs (each: tier, title, label, prefix, status, error, outputs) |
| GET | `/api/jobs/{jid}/files/{name}` | `result.json`, `result.xlsx`, `plan.png`, `plan.svg`, `stages.json`; with two runs prefixed by the run label: `photos/result.json`, `whole_home/result.json` |
| GET | `/api/jobs/{jid}/comparison` | given sizes vs computed, per room and run: `{"rows": [{"space","space_id","tier","run","room_id","quantity","given","computed","ci90","diff","diff_pct"}]}` |

How a project becomes engine runs (at most two):

* **Rooms (photos)**: the rooms that have photos, together one walk: a folder per room,
  `NN_<name>` in walk order, plus `measurements.yaml` with their sizes by folder name (the
  engine uses them for scale). A room without photos is allowed when there is a whole-home
  capture: it is then a size reference only.
* **Whole home (video)** / **Whole home (LiDAR)**: the one clip, or the one Stray Scanner
  export (a `.zip`, or its files uploaded with relative names such as `depth/000001.png`).
  Every room's sizes go to the engine as an unnamed list,
  `measurements={"rooms": [{length,width,height}, ...]}`: its rooms are `room_1..`, matched on
  aspect and size; video is rescaled by them, LiDAR is never rescaled (only compared), and a
  height alone is compared, not used for scale.

Stage names carry the run: `Rooms (photos): load+depth`, `Whole home (video): fuse`.

A run with the same files, names, sizes, damage flag and engine version as a finished job
returns that job at once (`"cached": true`). Finished jobs are kept for
`ROOMSCAN_RESULT_TTL_DAYS` and deleted at the next start after that.

The comparison has rows for every run that produced rooms: a photo room against the room
fitted from its folder; for the whole-home run each room with a length or width against the
engine room it matches (`roomscan.known_sizes.assign`: aspect ratio, then size rank), a room
with only a height against the largest room left, a room without sizes against none. Length / width are the longer / shorter side of the room's bounding box in its
plan frame (interval from the longest wall along that side), height is the ceiling height.

## Environment

| variable | default | |
|---|---|---|
| `DATA_DIR` | `./server_data` (`/data` in the image) | projects, uploads, jobs, the engine's cache |
| `ROOMSCAN_CORS` | `*` | allowed origins, comma separated |
| `ROOMSCAN_MAX_FILE_MB` | `2048` | per-file upload limit |
| `ROOMSCAN_RESULT_TTL_DAYS` | `7` | how long finished jobs are kept |

## Docker

```
docker build -f server/Dockerfile -t roomscan-server .
docker run -p 7860:7860 -v roomscan-data:/data roomscan-server
```

CPU only (torch from the CPU wheel index); the model weights are fetched at build time, so
the container runs offline. Give it at least 4 GB of memory (8 GB for long videos).

## Deploy

Step-by-step guide (Vercel front end, Hugging Face Space back end, connecting them, a
Cloudflare Tunnel demo from a laptop, costs, security): [docs/deploy.md](../docs/deploy.md).

**Hugging Face Spaces** (free CPU: 2 vCPU, 16 GB RAM): create a Space with the *Docker* SDK
and run `HF_TOKEN=hf_... sh deploy/hf-space/push.sh <hf-user>/<space>`. It pushes the files
the image needs, with `server/Dockerfile` as the root `Dockerfile` (Spaces build the root
Dockerfile) and `deploy/hf-space/README.md` (front matter `sdk: docker`, `app_port: 7860`) as
the root `README.md`. `deploy/hf-space/github-action.yml` does the same on every push to
`main`. The container runs as uid 1000 with a writable `HOME`, as Spaces require.

Without paid persistent storage the Space's disk is wiped on restart (projects and results
are lost); with it, storage is mounted at `/data`, which is `DATA_DIR`. The image sets
`ROOMSCAN_RESULT_TTL_DAYS=2`. Set `ROOMSCAN_CORS` to the web front end's origin (e.g.
`https://roomscan.vercel.app`, no trailing slash) under Settings > Variables and secrets; the
front end's API URL goes in `web/env.js`.

**Alternatives** (all build `server/Dockerfile`; the server listens on `$PORT`, default 7860):

* **Railway**: new project from the repository, set `RAILWAY_DOCKERFILE_PATH=server/Dockerfile`,
  add a volume mounted at `/data`.
* **Render**: a Web Service, runtime Docker, Dockerfile path `server/Dockerfile`, a persistent
  disk at `/data` (paid plans); pick an instance with >= 4 GB RAM.
* **Fly.io**: `fly launch --dockerfile server/Dockerfile`, `fly volumes create data`, mount it
  at `/data` in `fly.toml`, `internal_port = 7860`, a machine with >= 4 GB RAM.

Run one instance: jobs and state live on one disk and one worker thread.
