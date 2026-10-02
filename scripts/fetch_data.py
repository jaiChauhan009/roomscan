"""Download and unpack the raw benchmark captures (0.87 GB download, 0.92 GB unpacked).

usage: python scripts/fetch_data.py [target_dir]      (default: ../data next to the repository,
                                                       the data_root of bench/manifest.yaml)

Sources: the three Stray Scanner sample scans shared with the brief
(https://drive.google.com/drive/folders/1QIzPgDXL27EzL3RB_H-Cf75NABtD68SE):
single_room.zip 89 MB, single_scan_floor_only.zip 277 MB, single_scan_with_ceiling.zip 508 MB.
Own captures and their ground truth are added to FILES when they exist.
The derived photo set is not downloaded: scripts/make_photo_set.py rebuilds it from the scan
(bench/run_all.py does that by itself).
"""
from __future__ import annotations

import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = "https://drive.google.com/drive/folders/1QIzPgDXL27EzL3RB_H-Cf75NABtD68SE"
FILES = {  # folder name -> Google Drive file id of <name>.zip
    "single_room": "1-0ZxGbWHWgbh5FwUGR5U4FJrCHp_mKCk",
    "single_scan_floor_only": "1Dio9breIRM3N5Yl1UTji7G8UfksRaOJo",
    "single_scan_with_ceiling": "1WyFSoB-hYktaXb50v00i6ZCn5hl3CCkF",
}
URL = "https://drive.usercontent.google.com/download?id={id}&export=download&confirm=t"


def download(fid: str, dest: Path) -> None:
    part = dest.with_name(dest.name + ".part")  # only a complete download gets the .zip name
    with urllib.request.urlopen(URL.format(id=fid)) as r:
        if not re.search(r'filename="([^"]+)"', r.headers.get("Content-Disposition", "")):
            # Drive answered with a web page (download quota, moved file ...) instead of the file
            sys.exit(f"Google Drive did not return {dest.name} (got {r.headers.get('Content-Type')}).\n"
                     f"Download it by hand from {FOLDER} into {dest.parent} and run this script again.")
        total = int(r.headers.get("Content-Length") or 0)
        print(f"downloading {dest.name} ({total / 1e6:.0f} MB)", flush=True)
        done, step = 0, max(total // 10, 1)
        with open(part, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
                if total and (done + len(chunk)) // step > done // step:
                    print(f"  {(done + len(chunk)) / 1e6:.0f} / {total / 1e6:.0f} MB", flush=True)
                done += len(chunk)
    if total and done != total:
        part.unlink()
        sys.exit(f"download of {dest.name} stopped at {done} of {total} bytes; run the script again")
    part.replace(dest)


def main(target: str | None = None):
    target = Path(target) if target else ROOT.parent / "data"
    target.mkdir(parents=True, exist_ok=True)
    for name, fid in FILES.items():
        if (target / name).is_dir():
            print("have", name)
            continue
        dest = target / f"{name}.zip"
        if not dest.exists():  # a zip downloaded by hand is used as is
            download(fid, dest)
        print("unpacking", dest.name, flush=True)
        tmp = target / f".{name}.unpacking"  # unpack aside, so an interrupted run never looks complete
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(dest) as z:
            z.extractall(tmp)
        # some archives hold the scan folder directly, without the wrapping folder
        (tmp / name if (tmp / name).is_dir() else tmp).rename(target / name)
        shutil.rmtree(tmp, ignore_errors=True)
        dest.unlink()
    print("data ready in", target.resolve())


if __name__ == "__main__":
    main(*sys.argv[1:2])
