---
title: roomscan
emoji: 📐
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
short_description: Room capture to a measured floor plan (CPU API)
---

# roomscan API

The roomscan web API (FastAPI + the CPU engine). Upload a capture of a room (photos, a
video, or an iPhone LiDAR scan), get back a measured floor plan (`result.json`,
`result.xlsx`, `plan.png`, `plan.svg`) with a damage assessment.

* Health: `GET /api/health`
* Interactive API docs: `/docs`
* Front end: the static site in `web/` of the source repository (deployed on Vercel), pointed
  at this Space's URL.

This Space repository is generated from https://github.com/jaiChauhan009/roomscan by
`deploy/hf-space/push.sh`; edit the source repository, not this one. See `docs/deploy.md`
there.

Storage on the free CPU tier is ephemeral: projects and results are lost whenever the Space
restarts or sleeps, unless persistent storage is enabled (it mounts at `/data`).
