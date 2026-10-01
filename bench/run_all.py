"""Regenerate every benchmark number from raw inputs.

usage: python bench/run_all.py [--out bench/reports] [--only name ...] [--no-cache] [--tag TAG]

For every capture in bench/manifest.yaml: run the pipeline (one command per capture,
same code path as `roomscan run`), score it against ground truth when
bench/ground_truth/<name>.yaml exists, otherwise against the LiDAR reference named in the
manifest. Then the repeatability pairs and the drift ablation. Writes
<out>/benchmark.json and <out>/benchmark.md.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from evaluate import evaluate  # noqa: E402
from make_reference import main as make_reference  # noqa: E402
from repeatability import compare  # noqa: E402

from roomscan.pipeline import run  # noqa: E402


def gate_row(name: str, g: dict) -> str:
    keep = {k: v for k, v in g.items() if k not in ("pass", "gate", "overlaps", "missing", "extra")}
    return f"| {name} | {'PASS' if g.get('pass') else 'FAIL'} | {', '.join(f'{k} {v}' for k, v in keep.items())} |"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "reports"))
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--tag", default="")
    ap.add_argument("--skip-ablation", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    man = yaml.safe_load((HERE / "manifest.yaml").read_text())
    data_root = Path(os.environ.get("ROOMSCAN_DATA", ROOT / man["data_root"]))
    runs = out / "runs"
    results, evals, timing = {}, {}, {}
    for c in man["captures"]:
        if a.only and c["name"] not in a.only:
            continue
        src = data_root / c["path"]
        if not src.exists() and c.get("prepare"):
            subprocess.run([sys.executable, *c["prepare"].format(data_root=data_root).split()], check=True, cwd=ROOT)
        if not src.exists():
            print(f"skip {c['name']}: {src} not found")
            continue
        t = time.time()
        res = run(src, runs / c["name"], tier=c["tier"], use_cache=not a.no_cache, progress=False)
        timing[c["name"]] = {"wall_s": round(time.time() - t, 1), "stages": res["timing_s"],
                             "rooms": len(res["rooms"]), "footprint_m2": res["property"]["footprint_area"]["value"]}
        results[c["name"]] = res
        print(f"{c['name']}: {len(res['rooms'])} rooms, {timing[c['name']]['wall_s']} s", flush=True)
    for c in man["captures"]:
        if c["name"] not in results:
            continue
        gt_file = HERE / "ground_truth" / f"{c['name']}.yaml"
        if gt_file.exists():
            gt = yaml.safe_load(gt_file.read_text())
        elif c.get("reference") in results:
            with tempfile.TemporaryDirectory() as td:
                ref = Path(td) / "ref.yaml"
                make_reference(str(runs / c["reference"] / "result.json"), str(ref))
                gt = yaml.safe_load(ref.read_text())
        else:
            continue
        evals[c["name"]] = evaluate(results[c["name"]], gt)
    reps = []
    for x, y in man.get("repeatability", []):
        if x in results and y in results:
            reps.append(compare(results[x], results[y]))
    ablation = {}
    if not a.skip_ablation:
        for name in man.get("drift_ablation", []):
            c = next(c for c in man["captures"] if c["name"] == name)
            if (a.only and name not in a.only) or not (data_root / c["path"]).exists():
                continue
            rows = {}
            for mode in ("off", "loop"):
                r = results[name] if mode == "loop" and name in results else run(
                    data_root / c["path"], runs / f"{name}__drift_{mode}", tier=c["tier"], drift=mode,
                    damage=False, use_cache=not a.no_cache, progress=False)
                d = r["property"]["drift_correction"]
                rows[mode] = {"footprint_m2": r["property"]["footprint_area"]["value"], "rooms": len(r["rooms"]),
                              "bbox_diag_m": r["property"]["bbox"]["value"], "wall_crispness": d.get("wall_crispness"),
                              "loop_edges": d.get("loop_edges"), "loop_residual_before_m": d.get("loop_residual_m_before"),
                              "loop_residual_after_m": d.get("loop_residual_m_after")}
            ablation[name] = rows
    report = {"tag": a.tag, "timing": timing, "evaluations": evals, "repeatability": reps, "drift_ablation": ablation}
    (out / "benchmark.json").write_text(json.dumps(report, indent=2))

    md = [f"# Benchmark report {a.tag}".rstrip(), "",
          "Regenerate with `python bench/run_all.py`. Gates are defined in `bench/gates.yaml`.", ""]
    md += ["## Captures and timing", "", "| capture | tier | rooms | footprint m2 | wall time s | stages |", "|---|---|---|---|---|---|"]
    for n, t in timing.items():
        md.append(f"| {n} | {results[n]['capture']['tier']} | {t['rooms']} | {t['footprint_m2']} | {t['wall_s']} | "
                  f"{', '.join(f'{k} {v}' for k, v in t['stages'].items())} |")
    for n, e in evals.items():
        md += ["", f"## {n} (tier {e['tier']}) vs {e['gt_source']}", "", "| gate | result | numbers |", "|---|---|---|"]
        md += [gate_row(k, g) for k, g in e["gates"].items()]
    md += ["", "## Repeatability (same space, same tier, two captures)", ""]
    for r in reps:
        md += [f"**{r['a']} vs {r['b']}** (tier {r['tier']}): {'PASS' if r['pass'] else 'FAIL'}; rooms {r['rooms_a']} vs "
               f"{r['rooms_b']}, paired {r['rooms_paired']}; " + ", ".join(f"{k} {v}" for k, v in r["summary"].items()), ""]
        md += ["| room a | room b | a (m) | b (m) | diff (m) | within gate |", "|---|---|---|---|---|---|"]
        md += [f"| {w['room_a']} | {w['room_b']} | {w['a']} | {w['b']} | {w['diff']} | {'yes' if w['ok'] else 'no'} |"
               for w in r["walls"]]
        if r["ceilings"]:
            md += ["", "| room a | room b | ceiling a | ceiling b | spread | within 1 cm |", "|---|---|---|---|---|---|"]
            md += [f"| {c['room_a']} | {c['room_b']} | {c['a']} | {c['b']} | {c['spread']} | {'yes' if c['ok'] else 'no'} |"
                   for c in r["ceilings"]]
    if ablation:
        md += ["", "## Drift ablation (stitched footprint with correction off / on)", "",
               "| capture | drift | rooms | footprint m2 | bbox diagonal m | wall crispness | loop edges | loop residual before -> after (m) |",
               "|---|---|---|---|---|---|---|---|"]
        for n, rows in ablation.items():
            for mode, r in rows.items():
                md.append(f"| {n} | {mode} | {r['rooms']} | {r['footprint_m2']} | {r['bbox_diag_m']} | {r['wall_crispness']} | "
                          f"{r['loop_edges']} | {r['loop_residual_before_m']} -> {r['loop_residual_after_m']} |")
    (out / "benchmark.md").write_text("\n".join(md) + "\n")
    print("wrote", out / "benchmark.md")


if __name__ == "__main__":
    main()
