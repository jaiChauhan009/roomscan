# roomscan server

A web API around the engine: a client creates a project, adds spaces (a room of photos, a
video, a LiDAR scan), uploads the files, verifies the capture, runs it and downloads the
plan. JSON throughout, all under `/api`; errors are `{"detail": "..."}`. Interactive docs at
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
| GET | `/api/projects/{pid}` | `{"project_id", "spaces": [...], "last_job_id"}` |
| PUT | `/api/projects/{pid}/order` | `{"space_ids": [...]}` (every space once): walk order; returns the project |
| POST | `/api/projects/{pid}/spaces` | `{"name", "kind": "photos"/"video"/"lidar", "sizes": {"length","width","height"}}` (metres or null) -> space |
| PATCH | `/api/projects/{pid}/spaces/{sid}` | `{"name"?, "sizes"?}`; a size given as null is cleared |
| DELETE | `/api/projects/{pid}/spaces/{sid}` | |
| GET | `/api/projects/{pid}/spaces/{sid}/files` | `{"files": [{"name","sha256","size"}]}` (resume: skip hashes listed) |
| PUT | `/api/projects/{pid}/spaces/{sid}/files` | multipart `file`, `sha256` (hex, of the bytes), `name` -> file record; idempotent by hash; hash checked (400 on mismatch); 413 above the size limit |
| DELETE | `/api/projects/{pid}/spaces/{sid}/files/{sha256}` | |
| POST | `/api/projects/{pid}/verify` | capture check: `{"ok", "spaces": [{"space_id","name","status","findings"}], "project_findings"}`, levels ok / warn / retake |
| POST | `/api/projects/{pid}/run` | `{"damage": true, "force": false}` -> `{"job_id", "cached"}`; 409 if the last verify (of the same files) asked for a retake, unless `force` |
| GET | `/api/jobs/{jid}` | status, live stages, error, outputs (URLs), runs |
| GET | `/api/jobs/{jid}/files/{name}` | `result.json`, `result.xlsx`, `plan.png`, `plan.svg`, `stages.json`; with several runs prefixed by the run label, e.g. `photos/result.json` |
| GET | `/api/jobs/{jid}/comparison` | given sizes vs computed, per space: `{"rows": [{"space","quantity","given","computed","ci90","diff","diff_pct"}]}` |

How a project becomes engine runs:

* **photos** spaces together are one walk: a folder per space, `NN_<name>` in walk order,
  plus `measurements.yaml` with the sizes given (the engine uses them for scale).
* each **video** space (exactly one clip) is its own run; its sizes go to the engine as known sizes.
* each **lidar** space (one Stray Scanner export as a `.zip`, or the export's files uploaded
  with their relative names such as `depth/000001.png`) is its own run.

A run with the same files, names, sizes, damage flag and engine version as a finished job
returns that job at once (`"cached": true`). Finished jobs are kept for
`ROOMSCAN_RESULT_TTL_DAYS` and deleted at the next start after that.

The comparison matches a photo space to the room fitted from its folder; for a video or LiDAR
space it takes the room whose floor area is closest to the given length x width, else the
largest room. Length / width are the longer / shorter side of the room's bounding box in its
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

**Hugging Face Spaces** (free CPU: 2 vCPU, 16 GB RAM): create a Space with the *Docker* SDK,
push this repository to it with `server/Dockerfile` copied to `Dockerfile` at the root (Spaces
build the root Dockerfile) and this front matter at the top of the Space's `README.md`:

```
---
title: roomscan
sdk: docker
app_port: 7860
---
```

Without paid persistent storage the Space's disk is wiped on restart (projects and results
are lost); with it, storage is mounted at `/data`, which is `DATA_DIR`. Set `ROOMSCAN_CORS`
to the web front end's origin under Settings > Variables.

**Alternatives** (all build `server/Dockerfile`; set the port to 7860 or pass `--port $PORT`):

* **Railway**: new project from the repository, set `RAILWAY_DOCKERFILE_PATH=server/Dockerfile`,
  add a volume mounted at `/data`.
* **Render**: a Web Service, runtime Docker, Dockerfile path `server/Dockerfile`, a persistent
  disk at `/data` (paid plans); pick an instance with >= 4 GB RAM.
* **Fly.io**: `fly launch --dockerfile server/Dockerfile`, `fly volumes create data`, mount it
  at `/data` in `fly.toml`, `internal_port = 7860`, a machine with >= 4 GB RAM.

Run one instance: jobs and state live on one disk and one worker thread.
