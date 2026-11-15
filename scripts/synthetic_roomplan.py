"""Generate repeatable demo scans and optional local 2D plans, without a phone or server.

PYTHONPATH=src python scripts/synthetic_roomplan.py --run
Outputs are development fixtures, not real LiDAR measurements.
"""
from pathlib import Path
import argparse
import sys
import json

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
import roomplan_synth as synthetic


def attach_synthetic_depth(capture: Path):
    """A flat 2 m plane for export/format tests; not measured or reconstructed sensor data."""
    manifest = capture / "capture.json"
    data = json.loads(manifest.read_text())
    folder = capture / "depth"
    folder.mkdir(exist_ok=True)
    for frame in data["frames"]:
        stem = Path(frame["file"]).stem
        width, height = 8, 6
        depth_file = f"depth/{stem}.depth.f32"
        confidence_file = f"depth/{stem}.confidence.u8"
        np.full((height, width), 2.0, dtype="<f4").tofile(capture / depth_file)
        np.full((height, width), 2, dtype=np.uint8).tofile(capture / confidence_file)
        k = np.array(frame["intrinsics"]).reshape((3, 3), order="F")
        k[0] *= width / frame["width"]
        k[1] *= height / frame["height"]
        frame["depth"] = {"file": depth_file, "format": "float32_le_metres", "width": width, "height": height,
                          "intrinsics": k.flatten(order="F").tolist(), "valid_fraction": 1.0,
                          "confidence_file": confidence_file, "confidence_format": "uint8_arkit_confidence",
                          "source": "synthetic_flat_plane"}
    manifest.write_text(json.dumps(data, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("runs/synthetic-roomplan"))
    parser.add_argument("--run", action="store_true", help="Also generate JSON, PNG and SVG plans locally")
    parser.add_argument("--depth", action="store_true", help="Add clearly marked synthetic depth/confidence files")
    args = parser.parse_args()
    for variant in ("merged", "no_floor", "separate", "single"):
        capture = synthetic.write_folder(args.output / variant / "capture", variant, frames_per_room=2)
        if args.depth:
            attach_synthetic_depth(capture)
        print(f"Synthetic {variant}: {capture}")
        if args.run:
            from roomscan.pipeline import run
            result = run(capture, args.output / variant / "result", damage=False)
            print(f"  {len(result['rooms'])} room(s), {result['property']['footprint_area']['value']:.2f} m²")


if __name__ == "__main__":
    main()
