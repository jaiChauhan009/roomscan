"""Layout-only scores in seconds, for developing the geometry: the four laser-truth rooms
against their truth, and the room counts / footprints of the other LiDAR captures.

usage: python scripts/dev_layout_scores.py [alternative layout.py to load instead]

Runs drift correction and fusion once per capture (cached in .cache/), then only
extract_layout + build_output + evaluate, so a layout change is scored in about a minute.
Data from ../data (or $ROOMSCAN_DATA).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import yaml
from shapely.geometry import Polygon
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("ROOMSCAN_DATA", ROOT.parent / "data"))
sys.path.insert(0, str(ROOT / "bench"))
from evaluate import evaluate  # noqa: E402

import roomscan.geometry.layout as L  # noqa: E402
from roomscan.export.build import build_output  # noqa: E402
from roomscan.frontends.lidar_stray import load_stray  # noqa: E402
from roomscan.geometry.drift import correct_drift  # noqa: E402
from roomscan.pipeline import cached_fuse  # noqa: E402

if len(sys.argv) > 1:
    spec = importlib.util.spec_from_file_location("layout_alt", sys.argv[1])
    L = importlib.util.module_from_spec(spec)
    sys.modules["layout_alt"] = L
    spec.loader.exec_module(L)

LASER = ("42446532", "44358446", "47332890", "47331988")
OTHERS = {"A": "single_scan_with_ceiling", "B": "single_scan_floor_only", "room": "single_room",
          "own1 (flat)": "own/lidar_1/609c9be5d1", "own2": "own/lidar_2/51b6138385",
          "own3 (room 4 alone)": "own/lidar_3/84d7fdb836"}


def cloud(path: Path):
    key = (str(path.resolve()), "lidar", 5)
    cap, _ = correct_drift(load_stray(path, stride=5), key=key, progress=False)
    return cached_fuse(cap, key + ("loop",), progress=False)


def as_output(lay):
    info = {"id": "x", "tier": "lidar", "source": "test", "n_frames_used": 0, "meta": {}}
    return json.loads(build_output(lay, [], "lidar", info, {"enabled": False, "method": "none"}).model_dump_json())


def main():
    for vid in LASER:
        p = DATA / "arkitscenes" / vid / f"arkit_{vid}"
        if not p.exists():
            print(f"{vid}: no data")
            continue
        lay = L.extract_layout(cloud(p))
        gt = yaml.safe_load((ROOT / "bench" / "ground_truth" / f"arkit_{vid}.yaml").read_text())
        g = evaluate(as_output(lay), gt)["gates"]
        w, fp, c = g["wall_length"], g["footprint"], g.get("ceiling_height", {})
        print(f"laser {vid}: rooms {len(lay.rooms)}, walls {[len(r.walls) for r in lay.rooms]}; wall median "
              f"{w['median_abs_err_m']:.3f} m (pass {w['pass_fraction']}); footprint {fp['rel_err']:+.3f}; "
              f"ceiling max err {c.get('max_abs_err_m')}")
    for name, sub in OTHERS.items():
        p = DATA / sub
        if not p.exists():
            print(f"{name}: no data")
            continue
        lay = L.extract_layout(cloud(p))
        walls = [w for r in lay.rooms for w in r.walls]
        polys = [Polygon(r.polygon) for r in lay.rooms]
        overlap = sum(a.intersection(b).area for i, a in enumerate(polys) for b in polys[i + 1:])
        print(f"{name}: rooms {len(lay.rooms)}, footprint {unary_union(polys).area:.2f} m2, overlap {overlap:.2f}, "
              f"walls {len(walls)}, without evidence {sum(w.coverage == 0 for w in walls)}, "
              f"areas {[round(p.area, 2) for p in polys]}")


if __name__ == "__main__":
    main()
