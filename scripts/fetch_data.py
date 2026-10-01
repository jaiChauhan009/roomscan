"""Download and unpack the raw benchmark captures into ../data (about 0.9 GB).

usage: python scripts/fetch_data.py [target_dir]

Sources: the three Stray Scanner sample scans shared with the brief
(https://drive.google.com/drive/folders/1QIzPgDXL27EzL3RB_H-Cf75NABtD68SE).
Own captures and their ground truth are added to FILES when they exist.
The derived photo set is not downloaded: bench/run_all.py rebuilds it from the scan.
"""
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

FILES = [  # Google Drive file ids
    "1-0ZxGbWHWgbh5FwUGR5U4FJrCHp_mKCk",
    "1Dio9breIRM3N5Yl1UTji7G8UfksRaOJo",
    "1WyFSoB-hYktaXb50v00i6ZCn5hl3CCkF",
]
URL = "https://drive.usercontent.google.com/download?id={id}&export=download&confirm=t"
EXPECTED = {"single_room", "single_scan_floor_only", "single_scan_with_ceiling"}


def main(target: str = "../data"):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    if all((target / n).is_dir() for n in EXPECTED):
        print("data already present in", target.resolve())
        return
    for fid in FILES:
        with urllib.request.urlopen(URL.format(id=fid)) as r:
            m = re.search(r'filename="([^"]+)"', r.headers.get("Content-Disposition", ""))
            name = m.group(1) if m else f"{fid}.zip"
            dest = target / name
            if (target / Path(name).stem).is_dir():
                print("have", Path(name).stem)
                continue
            print("downloading", name, flush=True)
            with open(dest, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
        with zipfile.ZipFile(dest) as z:
            top = {n.split("/")[0] for n in z.namelist()}
            # some archives hold the scan folder directly, without the wrapping folder
            z.extractall(target if Path(name).stem in top else target / Path(name).stem)
        dest.unlink()
    print("data ready in", target.resolve())


if __name__ == "__main__":
    main(*sys.argv[1:2])
