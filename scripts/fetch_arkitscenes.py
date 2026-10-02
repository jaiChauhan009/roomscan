"""Fetch ARKitScenes rooms with laser-scanner ground truth for the LiDAR benchmark.

usage:
  python scripts/fetch_arkitscenes.py                    fetch every scene in SCENES (below)
  python scripts/fetch_arkitscenes.py --video 42446519   fetch one video (+ the laser scans of its visit)
  python scripts/fetch_arkitscenes.py --candidates       re-derive the candidate list (rules 1-3 below)
  options: --dest DIR (default $ROOMSCAN_DATA/arkitscenes, else ../data/arkitscenes next to the repo),
           --laser-only, --no-laser, --slices N (laser slices per scan, default: see LASER_SLICES)

LICENSE NOTICE. ARKitScenes (Apple, https://github.com/apple/ARKitScenes; Baruch et al., "ARKitScenes - A
Diverse Real-World Dataset for 3D Indoor Scene Understanding Using Mobile RGB-D Data", NeurIPS 2021 Datasets
and Benchmarks) is NOT under CC BY-NC-SA. Its README sends dataset users to the repository's LICENSE file,
which is Apple's own licence (last changed 2024-09-21): a personal, non-exclusive licence to use, reproduce,
modify and redistribute, for non-commercial purposes (plus commercial terms for licensees below 700 million
monthly active users before August 2024), keeping Apple's notice when redistributing it unmodified, and no
use of Apple's name or marks. We use it for evaluation only and never commit or redistribute the data: it
lives under the data folder, which git ignores. Read the LICENSE file before any other use.

What is fetched per video, into <dest>/<video_id>/ (raw dataset, Validation fold):
  raw/lowres_wide.traj                 10 Hz ARKit poses: timestamp, angle-axis (rad), translation (m),
                                       WORLD-TO-CAMERA (the official loader inverts it), world +Z up
  raw/lowres_depth/<vid>_<t>.png       256x192 uint16 depth in mm  } only the frames that have a pose
  raw/confidence/<vid>_<t>.png         256x192 uint8 0..2          } (|dt| <= 5 ms): 10 of the 60 per
  raw/lowres_wide/<vid>_<t>.png        256x192 RGB                 } second; members are read out of the
  raw/lowres_wide_intrinsics/*.pincam  w h fx fy cx cy             } remote zips by HTTP range requests
  raw/meta.json                        visit, sky direction, fold, licence, frame list
  laser/<scan_id>.npz, <scan_id>_pose.txt   Faro laser scans of the visit, sampled (see below)
Frames without a pose are not fetched: arkitscenes_to_stray.py uses measured poses only (interpolating
the 10 Hz trajectory would add a rotation error of the order of 0.1 deg, centimetres on a wall 3 m away).

Laser scans. Each Faro scan is a ~1.9 GB binary PLY (about 43 M points: double x y z, uchar rgba, double
quality, radius), already in the visit's registered frame (+Z up). Points are stored column by column:
each column is one vertical sweep (elevation -60..+90 deg, ~4100-4250 points) at one azimuth, and the
azimuth turns steadily through the file. Downloading whole scans would cost 2-11 GB per room, so we fetch
LASER_POINTS consecutive points (at least one full column) at LASER_SLICES evenly spaced positions in
the file: evenly spaced floor-to-ceiling slices all around the scanner, 23-70 MB per scan instead of
1.9 GB. Every point kept is an unmodified laser measurement.

SELECTION RULE (written before any of these rooms was run through roomscan; it uses only ARKitScenes
metadata, its furniture labels and the laser scans, never our output):
  1. raw/metadata.csv: fold == Validation (the dataset's held-out split), sky_direction == Up (device
     held in landscape, frames upright), has_laser_scanner_point_clouds == True, is_in_threedod == True
     (so furniture labels exist for rule 2).
  2. An ordinary single room: the 3DOD furniture labels of every candidate video of the visit name a bed,
     a sofa or a table, and none of toilet, bathtub, sink, stove, oven, refrigerator, dishwasher, washer
     (no bathrooms, kitchens or laundries).
  3. One video per visit: the longest one (most poses in lowres_wide.traj; ties: lowest video_id), so
     the scan covers as much of the room as possible.
  4. Visits in ascending visit_id order; a visit is accepted when its laser scans show a closed,
     rectangular room, judged by bench/make_laser_truth.py from the laser points alone: floor and ceiling
     planes found, two pairs of opposite walls found (pairs parallel and perpendicular within 2 deg),
     the room's ceiling covers >= 97 % of the rectangle's 20 cm cells, <= 10 % of the ceiling-height
     points lie outside it, every wall is seen along >= 50 % of its length and is a clean plane (fit rms
     <= 1.5 cm). The first 5 accepted visits are the benchmark (4-6 wanted); rejected visits are listed.
     Screening one visit: `--video <id> --laser-only`, then `python bench/make_laser_truth.py <id> --no-yaml`.
  Rules 1-3 give 20 visits (`--candidates` prints them). Rule 4 in that order accepted 4 (SCENES); the 16
  others are listed in REJECTED with the reason. The laser method and the rule-4 thresholds were refined
  while looking at laser data only (a curtain, a partition with a door and an L-shaped room each fooled
  an earlier version); every visit was then re-screened with the final version, and roomscan had not
  been run on any of these rooms before the list was final.
"""
from __future__ import annotations

import argparse
import csv
import http.client
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

BASE = "https://docs-assets.developer.apple.com/ml-research/datasets/arkitscenes/v1"
ROOT = Path(__file__).resolve().parents[1]
LICENSE_URL = "https://github.com/apple/ARKitScenes/blob/main/LICENSE"

EXCLUDE = {"toilet", "bathtub", "sink", "stove", "oven", "refrigerator", "dishwasher", "washer"}
NEED_ONE = {"bed", "sofa", "table"}
MATCH_S = 0.005  # a frame belongs to a pose when their timestamps differ by at most 5 ms
LASER_POINTS = 4400  # one Faro column is ~4100-4250 points (measured), so any 4400 in a row span -60..+90 deg
LASER_SLICES = {1: 360, 2: 180}  # slices per scan by number of scans in the visit; 3 or more: 120

# Rule 4 outcome: all 20 candidate visits screened in order; 4 passed (fewer than the 5 wanted).
SCENES: dict[str, str] = {  # video_id -> visit_id
    "42446532": "422386",  # bedroom 2.54 x 4.92 m (one wall ~1 deg off square: width 2.495-2.578 along the room)
    "44358446": "460419",  # living room 3.12 x 3.28 m
    "47332890": "469272",  # bedroom 4.45 x 3.24 m
    "47331988": "470811",  # bedroom/living 3.75 x 3.17 m (ARKit jumps 0.9 m at 5.5 s: the converter drops 0-5.6 s)
}
REJECTED: dict[str, str] = {  # visit_id -> reason given by bench/make_laser_truth.py
    "422024": "no wall reaching the ceiling on one side (open to another space)",
    "422391": "ceiling covers 79.9 % of the rectangle; a wall seen along 45 % (open into a further space)",
    "422803": "ceiling covers 87.5 %; a wall not a clean plane (rms 1.8 cm) (a partition with a door)",
    "423441": "ceiling covers 94.0 % (one corner missing: L-shaped)",
    "466183": "no wall reaching the ceiling on one side",
    "467305": "no wall reaching the ceiling on one side",
    "467311": "no wall reaching the ceiling on one side",
    "468267": "a wall seen along 35 % of its length",
    "469021": "a wall seen along 48 % of its length",
    "470512": "no wall reaching the ceiling on one side",
    "470541": "ceiling covers 77.8 % of the rectangle",
    "471940": "ceiling covers 85.7 %; a wall seen along 38 %",
    "471948": "no wall reaching the ceiling on one side",
    "483620": "no wall reaching the ceiling on one side",
    "483632": "ceiling covers 72.9 %; a wall seen along 41 %",
    "484534": "no wall reaching the ceiling on one side",
}


def say(*a) -> None:
    print(*a, flush=True)


# ---------------------------------------------------------------- HTTP with retries and size checks
def request(url: str, rng: tuple[int, int] | None = None, method: str = "GET", tries: int = 8):
    """(headers, body). A range request must come back as exactly that range (206, right length)."""
    hdr = {"Range": f"bytes={rng[0]}-{rng[1]}"} if rng else {}
    err = None
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr, method=method), timeout=40) as r:
                body = r.read() if method == "GET" else b""
                if rng and (r.status != 206 or len(body) != rng[1] - rng[0] + 1):
                    raise OSError(f"range {rng} of {url}: status {r.status}, {len(body)} bytes")
                return r.headers, body
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise
            err = e
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            err = e
        time.sleep(min(2 ** k, 20))
    raise RuntimeError(f"giving up on {url} after {tries} tries: {err}")


def size_of(url: str) -> int:
    return int(request(url, method="HEAD")[0]["Content-Length"])


def download(url: str, dest: Path) -> int:
    """Whole file, resumable (.part + Range), size checked against Content-Length."""
    if dest.exists():
        return dest.stat().st_size
    total = size_of(url)
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    have = part.stat().st_size if part.exists() else 0
    with open(part, "ab") as f:
        while have < total:
            hi = min(have + (8 << 20), total) - 1
            f.write(request(url, (have, hi))[1])
            have = hi + 1
            if total > (16 << 20):
                say(f"    {dest.name}: {have / 1e6:.0f} / {total / 1e6:.0f} MB")
    if part.stat().st_size != total:
        raise RuntimeError(f"{dest.name}: got {part.stat().st_size} of {total} bytes; run again to resume")
    part.replace(dest)
    return total


class RangeFile(io.RawIOBase):
    """Read-only, seekable view of a remote file through HTTP range requests (for zipfile)."""

    def __init__(self, url: str):
        self.url, self.size, self.pos, self.bytes = url, size_of(url), 0, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off)
        return self.pos

    def readinto(self, b):
        n = min(len(b), self.size - self.pos)
        if n <= 0:
            return 0
        data = request(self.url, (self.pos, self.pos + n - 1))[1]
        b[:n] = data
        self.pos += n
        self.bytes += n
        return n


def zip_index(url: str) -> tuple[RangeFile, dict[str, zipfile.ZipInfo]]:
    rf = RangeFile(url)
    z = zipfile.ZipFile(io.BufferedReader(rf, 1 << 16))
    return rf, {Path(i.filename).name: i for i in z.infolist() if not i.is_dir()}


def zip_member(rf: RangeFile, info: zipfile.ZipInfo) -> bytes:
    """One member of a remote zip: local header + data in one request, CRC checked."""
    name_len = len(info.filename.encode("utf-8"))
    end = min(info.header_offset + 30 + name_len + 256 + info.compress_size, rf.size) - 1
    buf = request(rf.url, (info.header_offset, end))[1]
    if buf[:4] != b"PK\x03\x04":
        raise RuntimeError(f"{info.filename}: no local header at {info.header_offset}")
    start = 30 + int.from_bytes(buf[26:28], "little") + int.from_bytes(buf[28:30], "little")
    data = buf[start:start + info.compress_size]
    if len(data) < info.compress_size:  # very long extra field: fetch exactly
        data = request(rf.url, (info.header_offset + start, info.header_offset + start + info.compress_size - 1))[1]
    out = zlib.decompress(data, -15) if info.compress_type == zipfile.ZIP_DEFLATED else data
    if len(out) != info.file_size or zlib.crc32(out) != info.CRC:
        raise RuntimeError(f"{info.filename}: size or CRC mismatch")
    return out


# ---------------------------------------------------------------- metadata and the selection rule
def meta_dir(dest: Path) -> Path:
    d = dest / "_meta"
    d.mkdir(parents=True, exist_ok=True)
    return d


def metadata(dest: Path) -> list[dict]:
    f = meta_dir(dest) / "metadata.csv"
    download(f"{BASE}/raw/metadata.csv", f)
    return list(csv.DictReader(open(f, encoding="utf-8")))


def laser_scans(dest: Path, visit: str) -> list[str]:
    f = meta_dir(dest) / "laser_scanner_point_clouds_mapping.csv"
    download(f"{BASE}/raw/laser_scanner_point_clouds/laser_scanner_point_clouds_mapping.csv", f)
    return [r["laser_scanner_point_clouds_id"] for r in csv.DictReader(open(f, encoding="utf-8"))
            if r["visit_id"] == visit]


def visit_of(row: dict) -> str:
    return str(int(float(row["visit_id"])))


def candidates(dest: Path) -> list[dict]:
    """Rules 1-3: one video per visit, visits in ascending visit_id order."""
    rows = [r for r in metadata(dest) if r["fold"] == "Validation" and r["sky_direction"] == "Up"
            and r["has_laser_scanner_point_clouds"] == "True" and r["is_in_threedod"] == "True"]
    cache = meta_dir(dest) / "candidates_cache.json"
    info = json.loads(cache.read_text()) if cache.exists() else {}

    def get(v: str):
        if v not in info:
            ann = json.loads(request(f"{BASE}/raw/Validation/{v}/{v}_3dod_annotation.json")[1])
            traj = request(f"{BASE}/raw/Validation/{v}/lowres_wide.traj")[1].decode().strip().splitlines()
            info[v] = {"labels": sorted({d["label"] for d in ann.get("data", [])}), "poses": len(traj)}
        return info[v]

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(get, [r["video_id"] for r in rows]))
    cache.write_text(json.dumps(info, indent=1))
    by_visit: dict[str, list[dict]] = {}
    for r in rows:
        by_visit.setdefault(visit_of(r), []).append(r)
    out = []
    for visit in sorted(by_visit, key=int):
        vids = by_visit[visit]
        labels = set().union(*(info[r["video_id"]]["labels"] for r in vids))
        if labels & EXCLUDE or not labels & NEED_ONE:
            continue
        best = max(vids, key=lambda r: (info[r["video_id"]]["poses"], -int(r["video_id"])))
        out.append({"visit_id": visit, "video_id": best["video_id"], "poses": info[best["video_id"]]["poses"],
                    "labels": sorted(labels), "n_videos": len(vids), "n_laser_scans": len(laser_scans(dest, visit))})
    return out


# ---------------------------------------------------------------- video assets (pose frames only)
def match_frames(traj_ts: np.ndarray, frame_ts: dict[str, float]) -> dict[str, float]:
    """frame name -> pose timestamp, for the frame nearest each pose (within MATCH_S)."""
    names = sorted(frame_ts, key=frame_ts.get)
    t = np.array([frame_ts[n] for n in names])
    out = {}
    for ts in traj_ts:
        i = int(np.searchsorted(t, ts))
        best = min((j for j in (i - 1, i) if 0 <= j < len(t)), key=lambda j: abs(t[j] - ts), default=None)
        if best is not None and abs(t[best] - ts) <= MATCH_S:
            out[names[best]] = float(ts)
    return out


def frame_time(stem: str) -> float:
    """<video_id>_<seconds>.<ms> -> seconds."""
    return float(stem.split("_", 1)[1])


def nearest(stems: list[str], times: np.ndarray, t: float, tol: float) -> str | None:
    """The stem whose time (times: sorted, same order as stems) is nearest t, if within tol."""
    if not len(stems):
        return None
    i = int(np.clip(np.searchsorted(times, t), 1, len(times) - 1)) if len(times) > 1 else 0
    j = i - 1 if i and abs(times[i - 1] - t) <= abs(times[i] - t) else i
    return stems[j] if abs(times[j] - t) <= tol else None


def fetch_video(dest: Path, row: dict) -> None:
    vid, visit = row["video_id"], visit_of(row)
    raw = dest / vid / "raw"
    url = f"{BASE}/raw/{row['fold']}/{vid}"
    say(f"[{vid}] visit {visit}, sky {row['sky_direction']}, fold {row['fold']}")
    download(f"{url}/lowres_wide.traj", raw / "lowres_wide.traj")
    traj_ts = np.array([float(ln.split()[0]) for ln in (raw / "lowres_wide.traj").read_text().split("\n") if ln.strip()])
    idx = {}
    for asset in ("lowres_depth", "lowres_wide", "confidence", "lowres_wide_intrinsics"):
        idx[asset] = zip_index(f"{url}/{asset}.zip")
    stems = {a: {Path(n).stem: n for n in m} for a, (_, m) in idx.items()}
    common = set(stems["lowres_depth"]) & set(stems["lowres_wide"]) & set(stems["confidence"])
    frames = match_frames(traj_ts, {s: frame_time(s) for s in common})
    # intrinsics files can be named 1 ms off the image they belong to (the official loader tries +-1 ms)
    k_stems = sorted(stems["lowres_wide_intrinsics"], key=frame_time)
    k_times = np.array([frame_time(s) for s in k_stems])
    kname = {s: nearest(k_stems, k_times, frame_time(s), 0.0015) for s in frames}
    frames = {s: t for s, t in frames.items() if kname[s]}
    say(f"  {len(traj_ts)} poses; frames: depth {len(stems['lowres_depth'])}, colour {len(stems['lowres_wide'])}, "
        f"confidence {len(stems['confidence'])}, all three {len(common)}; matched to a pose: {len(frames)}")
    jobs = []
    for asset, (rf, members) in idx.items():
        for s in frames:
            name = stems[asset][kname[s] if asset == "lowres_wide_intrinsics" else s]
            out = raw / asset / name
            if not out.exists():
                jobs.append((rf, members[name], out))
    done = [0, 0]

    def one(job):
        rf, info, out = job
        data = zip_member(rf, info)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(out)
        done[0] += 1
        done[1] += info.compress_size
        if done[0] % 200 == 0 or done[0] == len(jobs):
            say(f"  {done[0]} / {len(jobs)} files, {done[1] / 1e6:.1f} MB")

    with ThreadPoolExecutor(16) as ex:
        list(ex.map(one, jobs))
    full = sum(rf.size for rf, _ in idx.values())
    (raw / "meta.json").write_text(json.dumps({
        "video_id": vid, "visit_id": visit, "fold": row["fold"], "sky_direction": row["sky_direction"],
        "source": url, "license": f"ARKitScenes, Apple licence, non-commercial use: {LICENSE_URL}",
        "frames": {n: t for n, t in sorted(frames.items(), key=lambda kv: kv[1])},
        "full_zip_bytes": full}, indent=1))
    say(f"  video assets done ({len(frames)} frames; the full zips would be {full / 1e6:.0f} MB)")


# ---------------------------------------------------------------- laser scans (sampled columns)
def ply_layout(url: str) -> tuple[int, int, np.dtype]:
    """(header bytes, vertex count, vertex dtype) of a binary little-endian PLY."""
    head = request(url, (0, 4095))[1]
    end = head.find(b"end_header\n")
    if not head.startswith(b"ply") or end < 0 or b"binary_little_endian" not in head[:end]:
        raise RuntimeError(f"{url}: not a binary little-endian PLY")
    types = {"double": "<f8", "float": "<f4", "uchar": "u1", "int": "<i4", "uint": "<u4"}
    n, fields, in_vertex = 0, [], False
    for line in head[:end].decode("ascii").splitlines():
        p = line.split()
        if p[:2] == ["element", "vertex"]:
            n, in_vertex = int(p[2]), True
        elif p and p[0] == "element":
            in_vertex = False
        elif in_vertex and p[0] == "property" and p[1] != "list":
            fields.append((p[2], types[p[1]]))
    return end + len(b"end_header\n"), n, np.dtype(fields)


def fetch_laser(dest: Path, vid: str, visit: str, slices: int | None = None) -> None:
    scans = laser_scans(dest, visit)
    if not scans:
        say(f"  visit {visit} has no laser scans")
        return
    per_scan = slices or LASER_SLICES.get(len(scans), 120)
    folder = dest / vid / "laser"
    folder.mkdir(parents=True, exist_ok=True)
    for sid in scans:
        url = f"{BASE}/raw/laser_scanner_point_clouds/{visit}/{sid}"
        download(f"{url}_pose.txt", folder / f"{sid}_pose.txt")
        out = folder / f"{sid}.npz"
        if out.exists():
            say(f"  laser scan {sid}: have it")
            continue
        size = size_of(f"{url}.ply")
        hdr, n, dt = ply_layout(f"{url}.ply")
        if hdr + n * dt.itemsize != size:
            raise RuntimeError(f"scan {sid}: header says {hdr + n * dt.itemsize} bytes, server has {size}")
        starts = [int(k * (n - LASER_POINTS) / max(per_scan - 1, 1)) for k in range(per_scan)]
        parts = folder / f"{sid}.parts"  # resumable: one file per batch of slices
        parts.mkdir(exist_ok=True)
        batches = [starts[i:i + 20] for i in range(0, len(starts), 20)]
        say(f"  laser scan {sid}: {n / 1e6:.1f} M points, {size / 1e9:.2f} GB; fetching {per_scan} slices of "
            f"{LASER_POINTS} points ({per_scan * LASER_POINTS * dt.itemsize / 1e6:.0f} MB)")

        def one(b):
            k, group = b
            f = parts / f"{k:03d}.npy"
            if f.exists():
                return
            recs = [np.frombuffer(request(f"{url}.ply", (hdr + s * dt.itemsize, hdr + (s + LASER_POINTS) * dt.itemsize - 1))[1], dt)
                    for s in group]
            np.save(f.with_suffix(".tmp.npy"), np.concatenate(recs))
            f.with_suffix(".tmp.npy").replace(f)
            say(f"    scan {sid}: batch {k + 1} / {len(batches)}")

        with ThreadPoolExecutor(12) as ex:
            list(ex.map(one, enumerate(batches)))
        rec = np.concatenate([np.load(parts / f"{k:03d}.npy") for k in range(len(batches))])
        np.savez(out, xyz=np.stack([rec["x"], rec["y"], rec["z"]], 1).astype(np.float64),
                 rgb=np.stack([rec["red"], rec["green"], rec["blue"]], 1).astype(np.uint8),
                 starts=np.array(starts, np.int64), slice_points=LASER_POINTS, n_points=n, file_bytes=size)
        for f in parts.iterdir():
            f.unlink()
        parts.rmdir()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dest")
    ap.add_argument("--video", nargs="*")
    ap.add_argument("--candidates", action="store_true")
    ap.add_argument("--laser-only", action="store_true")
    ap.add_argument("--no-laser", action="store_true")
    ap.add_argument("--slices", type=int)
    a = ap.parse_args()
    dest = Path(a.dest) if a.dest else Path(os.environ.get("ROOMSCAN_DATA", ROOT.parent / "data")) / "arkitscenes"
    say(f"ARKitScenes data (Apple licence, non-commercial use; see {LICENSE_URL}) -> {dest}")
    if a.candidates:
        for c in candidates(dest):
            say(f"visit {c['visit_id']}: video {c['video_id']} ({c['poses']} poses, {c['n_videos']} videos, "
                f"{c['n_laser_scans']} laser scans) {', '.join(c['labels'])}")
        return
    rows = {r["video_id"]: r for r in metadata(dest)}
    for vid in a.video or list(SCENES):
        if vid not in rows:
            sys.exit(f"unknown video_id {vid}")
        row = rows[vid]
        if not a.laser_only:
            fetch_video(dest, row)
        if not a.no_laser:
            fetch_laser(dest, vid, visit_of(row), a.slices)


if __name__ == "__main__":
    main()
