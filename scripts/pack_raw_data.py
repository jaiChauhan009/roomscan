"""Pack our own raw captures (deliverable 8: raw benchmark data) into one zip with checksums.

    uv run python scripts/pack_raw_data.py --data ../data --out ../submission/roomscan_own_captures.zip

The zip holds data/own/ (iPhone 16 Pro LiDAR scans, iPhone and moto g45 videos and photos) and
data/iphone/ (the copy log of the iPhone session), plus SHA256SUMS and a short README. Media is
already compressed, so entries are stored, not deflated: packing is I/O bound and needs little
memory. The same checksums are written next to the zip and to bench/raw_data.sha256 in the repo,
so a reviewer can check a download against the committed list:

    uv run python scripts/pack_raw_data.py --verify ../submission/roomscan_own_captures.zip

The sample flat and the ARKitScenes laser rooms are not packed: scripts/fetch_data.py and
scripts/fetch_arkitscenes.py download them, and their laser truth is in bench/ground_truth/.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARTS = ("own", "iphone")  # under --data
README = """roomscan: our own raw captures (deliverable 8)

own/lidar_1..3     iPhone 16 Pro, Stray Scanner exports (depth, confidence, odometry, intrinsics, rgb video)
own/video_1        moto g45 walkthrough video (84 s, 1080p)
own/photos_1       moto g45 photos, one folder per room
own/iphone_video_1 iPhone 16 Pro video, received through WhatsApp (recompressed)
own/iphone_photos_1 iPhone 16 Pro photos, received through WhatsApp (EXIF stripped)
iphone/            copy log of the iPhone session

Benchmark names (bench/manifest.yaml): own_lidar_1..3, own_video_1, own_photos_1, own_video_iphone,
own_photos_iphone. Unzip next to the repo as data/ and run bench/run_all.py.
SHA256SUMS lists every file; scripts/pack_raw_data.py --verify checks a download.
"""


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def files(data: Path) -> list[tuple[Path, str]]:
    out = []
    for part in PARTS:
        root = data / part
        if not root.is_dir():
            sys.exit(f"missing {root}")
        for p in sorted(root.rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                out.append((p, p.relative_to(data).as_posix()))
    return out


def pack(data: Path, out: Path) -> None:
    items = files(data)
    out.parent.mkdir(parents=True, exist_ok=True)
    sums = []
    total = sum(p.stat().st_size for p, _ in items)
    done = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        for p, arc in items:
            sums.append(f"{sha256(p)}  {arc}")
            z.write(p, arc)
            done += p.stat().st_size
            print(f"\r{done / 1e9:5.2f} / {total / 1e9:.2f} GB  {len(sums)}/{len(items)} files", end="", flush=True)
        text = "\n".join(sums) + "\n"
        z.writestr("SHA256SUMS", text)
        z.writestr("README.txt", README)
    print()
    out.with_suffix(".sha256").write_text(text, encoding="utf-8")
    (REPO / "bench" / "raw_data.sha256").write_text(text, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1e9:.2f} GB, {len(items)} files)")
    print(f"zip sha256: {sha256(out)}")


def verify(zpath: Path) -> int:
    want = {}
    for line in (REPO / "bench" / "raw_data.sha256").read_text(encoding="utf-8").splitlines():
        if line.strip():
            h, name = line.split("  ", 1)
            want[name] = h
    bad = 0
    with zipfile.ZipFile(zpath) as z:
        names = set(z.namelist()) - {"SHA256SUMS", "README.txt"}
        for name, h in want.items():
            if name not in names:
                print("missing", name); bad += 1; continue
            d = hashlib.sha256()
            with z.open(name) as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    d.update(chunk)
            if d.hexdigest() != h:
                print("changed", name); bad += 1
    print(f"{len(want) - bad}/{len(want)} files match bench/raw_data.sha256")
    return 1 if bad else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=REPO.parent / "data")
    ap.add_argument("--out", type=Path, default=REPO.parent / "submission" / "roomscan_own_captures.zip")
    ap.add_argument("--verify", type=Path, help="check a zip against bench/raw_data.sha256")
    a = ap.parse_args()
    if a.verify:
        sys.exit(verify(a.verify))
    pack(a.data, a.out)


if __name__ == "__main__":
    main()
