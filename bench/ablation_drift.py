"""Drift ablation: the same capture with each drift-correction mode.

Per mode: stitched footprint, bounding-box diagonal, room count, wall crispness
(segmentation-independent: higher = walls seen at different times coincide), and the
loop-closure residual. Writes ablation.json and a side-by-side plan image.
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from roomscan.pipeline import run

MODES = ["off", "loop", "heading", "loop+heading"]


def main(capture: str, out: str = "runs/ablation_drift"):
    out = Path(out)
    rows = {}
    for mode in MODES:
        res = run(Path(capture), out / mode.replace("+", "_"), drift=mode, damage=False, progress=False)
        d = res["property"]["drift_correction"]
        rows[mode] = {
            "footprint_m2": res["property"]["footprint_area"]["value"],
            "bbox_diag_m": res["property"]["bbox"]["value"],
            "n_rooms": len(res["rooms"]),
            "wall_crispness": d.get("wall_crispness"),
            "loop_edges": d.get("loop_edges"),
            "loop_residual_before_m": d.get("loop_residual_m_before"),
            "loop_residual_after_m": d.get("loop_residual_m_after"),
            "max_submap_shift_m": d.get("max_submap_shift_m"),
        }
        print(mode, rows[mode], flush=True)
    (out / "ablation.json").write_text(json.dumps(rows, indent=2))
    fig, axs = plt.subplots(1, len(MODES), figsize=(8 * len(MODES), 8))
    for ax, mode in zip(axs, MODES):
        ax.imshow(plt.imread(out / mode.replace("+", "_") / "plan.png"))
        ax.axis("off")
        r = rows[mode]
        ax.set_title(f"drift: {mode} | footprint {r['footprint_m2']:.2f} m2 | crispness {r['wall_crispness']}",
                     fontsize=11)
    fig.tight_layout()
    fig.savefig(out / "ablation.png", dpi=80)


if __name__ == "__main__":
    main(*sys.argv[1:])
