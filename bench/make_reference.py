"""Turn a LiDAR-tier result into a reference file for scoring the thinner tiers.

usage: python bench/make_reference.py <lidar result.json> <out.yaml>

This is NOT ground truth. It exists because the sample apartment has no tape or laser
measurements: the video and photo tiers of the same capture are scored against what the
LiDAR tier produced, and the report labels those numbers "vs LiDAR reference". Real
ground truth goes in bench/ground_truth/<capture>.yaml (see TEMPLATE.yaml) and is used
instead whenever it exists.
"""
import json
import sys
from pathlib import Path

import yaml


def main(result: str, out: str):
    r = json.loads(Path(result).read_text())
    rooms = []
    for room in r["rooms"]:
        rooms.append({
            "name": room["id"],
            "floor_area": room["floor_area"]["value"],
            "ceiling_height": room["ceiling_height"]["value"],
            "walls": [round(w["length"]["value"], 3) for w in room["walls"] if w["length"]["value"] >= 0.25],
            "openings": [{"type": o["type"], "width": round(o["width"]["value"], 3)} for o in room["openings"]],
        })
        if rooms[-1]["ceiling_height"] is None:
            rooms[-1].pop("ceiling_height")
    gt = {"capture": r["capture"]["id"], "source": "lidar_reference",
          "note": "LiDAR-tier output of the same capture, not a tape or laser measurement",
          "footprint": r["property"]["footprint_area"]["value"], "rooms": rooms,
          "adjacency": [list(a["rooms"]) for a in r["property"]["adjacency"] if a["kind"] != "shared_wall"]}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(yaml.safe_dump(gt, sort_keys=False))
    print("wrote", out)


if __name__ == "__main__":
    main(*sys.argv[1:3])
