"""A tape-measurement form for a capture: every wall, ceiling and opening of its plan, in the
order bench/ground_truth/*.yaml wants, with our value beside a blank for the tape reading.

usage: python scripts/tape_form.py <result.json> [--out form.md] [--min-wall 0.5]

Hand the form and plan.png to whoever measures. Walls are listed clockwise from the room's
door as you face into the room, as the ground-truth template asks; each is named by its side
on plan.png. Fill in the tape column, then: python scripts/tape_form.py --to-yaml form.md
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np


def _side(w, centre) -> str:
    mid = (np.array(w["start"]) + np.array(w["end"])) / 2 - centre
    ang = np.degrees(np.arctan2(mid[1], mid[0]))  # plan y points down on plan.png
    names = ["right", "bottom-right", "bottom", "bottom-left", "left", "top-left", "top", "top-right"]
    return names[int(((ang + 22.5) % 360) // 45)]


def form(res: dict, min_wall: float) -> str:
    md = [f"# Tape form: {res['capture']['id']}", "",
          "Measure each wall corner to corner at about 1 m above the floor; the ceiling near the middle of the "
          "room; door and window widths frame to frame (doors open). Write metres in the `tape` column. "
          "Walls are named by their side on `plan.png`; skip a row you cannot measure (leave it blank).", "",
          "instrument: ______ (tape / laser, model)    measured by: ______    date: ______", ""]
    for r in res["rooms"]:
        poly = np.array(r["polygon"])
        c = poly.mean(0)
        md += [f"## {r['id']} ({r['floor_area']['value']:.1f} m2 on the plan)", "",
               "| item | side on plan.png | ours (m) | tape (m) |", "|---|---|---|---|",
               f"| ceiling | middle | {r['ceiling_height']['value'] or 'n/a'} | |"]
        for w in r["walls"]:
            if (w["length"]["value"] or 0) >= min_wall:
                md.append(f"| wall {w['id'].split('_')[-1]} | {_side(w, c)} | {w['length']['value']:.3f} | |")
        for o in r.get("openings", []):
            if o["wall_id"].startswith(r["id"] + "_"):
                md.append(f"| {o['type']} {o['id']} | on wall {o['wall_id'].split('_')[-1]} | "
                          f"{o['width']['value']:.3f} | |")
        md.append("")
    return "\n".join(md)


def to_yaml(form_md: Path) -> str:
    text = form_md.read_text(encoding="utf-8-sig")  # Notepad and PowerShell save with a BOM
    cap = re.search(r"^# Tape form: (\S+)", text, re.M).group(1)
    out = [f"capture: {cap}", "source: tape", 'instrument: ""', 'measured_by: ""', 'date: ""', "rooms:"]
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        rid = block.split()[0]
        rows = [[c.strip() for c in ln.strip("|").split("|")] for ln in block.splitlines() if ln.startswith("| ")]
        rows = [r for r in rows if r[0] != "item" and len(r) == 4 and r[3]]
        walls = [float(r[3]) for r in rows if r[0].startswith("wall")]
        ceil = next((float(r[3]) for r in rows if r[0] == "ceiling"), None)
        ops = [r for r in rows if r[0].split()[0] in ("door", "window", "opening")]
        if not (walls or ceil):
            continue
        out += [f"  - name: {rid}", f"    match: {rid}"]
        if ceil:
            out.append(f"    ceiling_height: {ceil}")
        if walls:
            out.append(f"    walls: {walls}")
        if ops:
            out.append("    openings:")
            out += [f"      - {{type: {r[0].split()[0]}, width: {float(r[3])}}}" for r in ops]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path, help="result.json (make a form) or a filled form .md (with --to-yaml)")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--min-wall", type=float, default=0.5)
    ap.add_argument("--to-yaml", action="store_true", help="turn a filled form into ground-truth YAML")
    a = ap.parse_args()
    text = to_yaml(a.source) if a.to_yaml else form(json.loads(a.source.read_text(encoding="utf-8")), a.min_wall)
    if a.out:
        a.out.write_text(text, encoding="utf-8")
        print("wrote", a.out)
    else:
        print(text)


if __name__ == "__main__":
    main()
