"""Fit the interval scale per tier so that 90 % intervals cover about 90 % of the truth.

usage: python bench/calibrate.py [--report bench/reports/benchmark.json] [--write]

Samples: every measurement bench/evaluate.py compares against truth (bench/ground_truth/
<capture>.yaml when it exists, else the LiDAR reference named in bench/manifest.yaml): wall
lengths, ceiling heights, opening widths and room floor areas, each with the value and the
sigma it was reported with.

Method, split conformal per tier: z = |value - truth| / sigma, with sigma taken back to scale
1 using the calibration the run was made with (so refitting the same run does not
compound). The fitted scale is q / 1.6449, where q is the ceil((n + 1) * 0.9)-th smallest z:
for n exchangeable samples a new one then falls inside its interval with probability at
least 90 %. Samples are few, so lengths (walls, ceilings, openings) share one scale per tier
and areas get their own when there are enough of them.

The in-sample coverage after fitting is 90 % or more by construction. The honest number is
the leave-one-room-out coverage: fit on every other room, score the room left out. That is
the estimate for a capture the scale has not seen, and the report gives it next to the fit.

LiDAR has no ground truth yet and is never scored against a reference, so until laser
measurements exist its samples come from the repeatability pair in bench/manifest.yaml: the
same wall measured in two captures (see repeat_samples). That calibrates precision only.

Thinner sensor data must never get a narrower interval than richer data (the brief: intervals
"widen honestly as sensor data thins"), so after fitting, a reference measurement's interval
is checked to widen from LiDAR to video to photo, and a thinner tier's scale is raised where
it does not.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).parent
CAL = HERE.parent / "src" / "roomscan" / "uncertainty" / "calibration.yaml"
Z90 = 1.6449
NOMINAL = 0.90
MIN_N = 5  # fewer samples than this: keep the prior scale
TIERS = ("lidar", "video", "photo")  # richest to thinnest sensor data
GROUPS = {"length": ("wall_length", "ceiling_height", "opening_width", "opening_height"), "area": ("area",)}
# reference measurements for the widening check: (value, raw sigma)
REFERENCE = {"wall_length": (3.0, 0.005), "ceiling_height": (2.6, 0.005), "opening_width": (0.9, 0.01),
             "opening_height": (2.0, 0.02), "area": (10.0, 0.05)}


def conformal_q(z: np.ndarray, level: float = NOMINAL) -> tuple[float, bool]:
    """ceil((n+1) level)-th smallest score; False when n is too small for the finite-sample bound."""
    z = np.sort(np.asarray(z, float))
    k = math.ceil((len(z) + 1) * level)
    return (float(z[k - 1]), True) if k <= len(z) else (float(z[-1]), False)


def collect(report: dict) -> dict[str, list[dict]]:
    """Samples per tier with z at scale 1 (sigma divided by the scale the run was made with)."""
    used = report.get("calibration_used")
    out: dict[str, list[dict]] = defaultdict(list)
    for cap, ev in report["evaluations"].items():
        tier = ev["tier"]
        for s in ev.get("interval_samples", []):
            scale = float(used[tier][s["kind"]].get("scale", 1.0)) if used else 1.0
            base = s["sigma"] / scale
            if base <= 0:
                continue
            out[tier].append({**s, "room": f"{cap}:{s['room']}", "base_sigma": base,
                              "z": abs(s["pred"] - s["truth"]) / base, "source": ev.get("gt_source")})
    return out


def _axis_walls(res: dict) -> list[tuple]:
    """Axis-aligned walls of a plan, >= 0.5 m: (orient, coord, lo, hi, sigma, room id, length)."""
    out = []
    for r in res["rooms"]:
        for w in r["walls"]:
            p, q = np.array(w["start"], float), np.array(w["end"], float)
            L = float(np.linalg.norm(q - p))
            if L < 0.5 or not w["length"].get("sigma"):
                continue
            d = (q - p) / L
            if abs(d[0]) > 0.996:
                out.append(("H", (p[1] + q[1]) / 2, min(p[0], q[0]), max(p[0], q[0]), w["length"]["sigma"], r["id"], L))
            elif abs(d[1]) > 0.996:
                out.append(("V", (p[0] + q[0]) / 2, min(p[1], q[1]), max(p[1], q[1]), w["length"]["sigma"], r["id"], L))
    return out


def _wall_points(ws: list[tuple], step: float = 0.05) -> np.ndarray:
    pts = []
    for o, c, lo, hi, *_ in ws:
        t = np.arange(lo, hi, step)
        pts.append(np.stack([t, np.full_like(t, c)], 1) if o == "H" else np.stack([np.full_like(t, c), t], 1))
    return np.concatenate(pts)


def repeat_samples(runs: Path, pairs: list, used: dict | None) -> list[dict]:
    """LiDAR samples from two captures of the same space: the same wall's length in both,
    walls matched by position (match_walls). With independent errors,
    |L_a - L_b| / sqrt(s_a^2 + s_b^2) is a z score of one capture's sigma. This measures
    precision only; a bias both captures share is invisible to it.
    """
    scale = float(used["lidar"]["wall_length"].get("scale", 1.0)) if used else 1.0
    out = []
    for a, b in pairs:
        fa, fb = runs / a / "result.json", runs / b / "result.json"
        if not (fa.exists() and fb.exists()):
            continue
        for (_, _, _, _, sg, rid, L), (_, _, _, _, sg2, _, L2) in match_walls(
                json.loads(fa.read_text(encoding="utf-8")), json.loads(fb.read_text(encoding="utf-8"))):
            sigma = float(np.hypot(sg, sg2)) / scale
            out.append({"kind": "wall_length", "room": f"{a}:{rid}", "pred": L, "truth": L2,
                        "sigma": sigma * scale, "base_sigma": sigma, "z": abs(L - L2) / sigma,
                        "source": f"repeatability ({a} vs {b})"})
    return out


def match_walls(res_a: dict, res_b: dict, max_d: float = 0.3) -> list[tuple]:
    """The same physical wall in two plans of one space: (wall of A, wall of B) pairs.

    Both plans are wall-aligned, so B is turned by a multiple of 90 degrees (picked by
    correlating rasterised walls) and shifted by the median nearest-wall offset. A wall of
    A pairs with a wall of B when both of its ends are within `max_d` of B's. Walls are
    axis-aligned and at least 0.5 m long (_axis_walls).
    """
    from scipy.spatial import cKDTree
    wa, wb = _axis_walls(res_a), _axis_walls(res_b)
    out = []
    if wa and wb:
        pa, pb = _wall_points(wa), _wall_points(wb)
        best = None
        for k in range(4):
            c, s = np.cos(k * np.pi / 2), np.sin(k * np.pi / 2)
            R = np.array([[c, -s], [s, c]])
            q = pb @ R.T
            lo = np.minimum(pa.min(0), q.min(0)) - 1
            hi = np.maximum(pa.max(0), q.max(0)) + 1
            shape = (int((hi[1] - lo[1]) / 0.05) * 2, int((hi[0] - lo[0]) / 0.05) * 2)

            def ras(p):
                g = np.zeros(shape)
                np.add.at(g, (((p[:, 1] - lo[1]) / 0.05).astype(int), ((p[:, 0] - lo[0]) / 0.05).astype(int)), 1)
                return g
            F = np.fft.irfft2(np.fft.rfft2(ras(pa)) * np.conj(np.fft.rfft2(ras(q))), s=shape)
            ij = np.unravel_index(np.argmax(F), F.shape)
            t = np.array([(ij[1] if ij[1] < shape[1] // 2 else ij[1] - shape[1]) * 0.05,
                          (ij[0] if ij[0] < shape[0] // 2 else ij[0] - shape[0]) * 0.05])
            if best is None or F[ij] > best[0]:
                best = (F[ij], R, t)
        _, R, t = best
        tree = cKDTree(pa)
        for _ in range(20):
            d, j = tree.query(pb @ R.T + t, distance_upper_bound=max_d)
            ok = np.isfinite(d)
            t = t + np.median(pa[j[ok]] - (pb[ok] @ R.T + t), axis=0)
        moved = []
        for o, c, lo, hi, sg, rid, L in wb:
            P = (np.array([[lo, c], [hi, c]]) if o == "H" else np.array([[c, lo], [c, hi]])) @ R.T + t
            horiz = abs(P[1, 0] - P[0, 0]) > abs(P[1, 1] - P[0, 1])
            moved.append(("H" if horiz else "V", P[:, 1 if horiz else 0].mean(),
                          P[:, 0 if horiz else 1].min(), P[:, 0 if horiz else 1].max(), sg, rid, L))
        for w in wa:
            o, c, lo, hi = w[:4]
            for w2, orig in zip(moved, wb):
                o2, c2, lo2, hi2 = w2[:4]
                if o2 == o and abs(c - c2) < max_d and abs(lo - lo2) < max_d and abs(hi - hi2) < max_d:
                    out.append((w, orig))
                    break
    return out


def fit_tier(samples: list[dict]) -> dict:
    """Scale per kind group, with in-sample and leave-one-room-out coverage."""
    res = {}
    for group, kinds in GROUPS.items():
        S = [s for s in samples if s["kind"] in kinds]
        row = {"n": len(S), "rooms": len({s["room"] for s in S}), "kinds": sorted({s["kind"] for s in S})}
        if len(S) < MIN_N:
            res[group] = {**row, "scale": None, "note": f"fewer than {MIN_N} samples"}
            continue
        z = np.array([s["z"] for s in S])
        q, exact = conformal_q(z)
        scale = q / Z90
        rooms = sorted({s["room"] for s in S})
        loro, loro_n = None, 0
        if len(rooms) > 1:
            hits = []
            for r in rooms:
                train = np.array([s["z"] for s in S if s["room"] != r])
                test = np.array([s["z"] for s in S if s["room"] == r])
                if len(train) < MIN_N or not len(test):
                    continue
                qr, _ = conformal_q(train)
                hits += list(test <= qr)
            if hits:
                loro, loro_n = float(np.mean(hits)), len(hits)
        res[group] = {**row, "q": round(q, 3), "scale": round(scale, 3), "finite_sample_bound": exact,
                      "coverage_at_scale_1": round(float(np.mean(z <= Z90)), 3),
                      "coverage_in_sample": round(float(np.mean(z <= q)), 3),
                      "coverage_leave_one_room_out": None if loro is None else round(loro, 3),
                      "loro_n": loro_n}
    return res


def half_width(c: dict, kind: str) -> float:
    v, raw = REFERENCE[kind]
    t = c[kind]
    return Z90 * float(t.get("scale", 1.0)) * math.sqrt((t["inflate"] * raw) ** 2 + t["abs"] ** 2 + (t["rel"] * v) ** 2)


def apply(cal: dict, fits: dict) -> tuple[dict, list[str]]:
    """New calibration dict with fitted scales, then the widening check across tiers."""
    new = json.loads(json.dumps(cal))
    notes = []
    for tier, groups in fits.items():
        length_scale = groups.get("length", {}).get("scale")
        for group, kinds in GROUPS.items():
            scale = groups.get(group, {}).get("scale")
            if scale is None and group == "area" and length_scale is not None:
                scale = length_scale  # area errors come from the same length errors
                notes.append(f"{tier} area: too few samples, uses the tier's length scale {length_scale}")
            if scale is None:
                continue
            for kind in kinds:
                if kind in new[tier]:
                    new[tier][kind]["scale"] = round(float(scale), 3)
    for kind in REFERENCE:
        for thin, rich in (("video", "lidar"), ("photo", "video")):
            hw_rich, hw_thin = half_width(new[rich], kind), half_width(new[thin], kind)
            if hw_thin < hw_rich:
                old = float(new[thin][kind].get("scale", 1.0))
                new[thin][kind]["scale"] = round(old * hw_rich / hw_thin, 3)
                notes.append(f"{thin} {kind}: interval narrower than {rich}'s ({hw_thin:.3f} < {hw_rich:.3f}); "
                             f"scale {old} -> {new[thin][kind]['scale']}")
    return new, notes


def write_yaml(cal: dict, header: list[str]) -> None:
    lines = ["# Per-tier uncertainty terms: sigma = scale * sqrt((inflate * raw)^2 + abs^2 + (rel * value)^2)",
             "# inflate / abs / rel: priors from sensor specs. scale: fitted by bench/calibrate.py.", *header]
    for tier, kinds in cal.items():
        lines.append(f"{tier}:")
        w = max(len(k) for k in kinds) + 1
        for kind, t in kinds.items():
            items = ", ".join(f"{k}: {t[k]}" for k in ("inflate", "abs", "rel", "scale") if k in t)
            lines.append(f"  {kind + ':':<{w + 1}}{{{items}}}")
    CAL.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=str(HERE / "reports" / "benchmark.json"))
    ap.add_argument("--out", default=str(HERE / "reports" / "calibration.md"))
    ap.add_argument("--write", action="store_true", help="write the fitted scales into calibration.yaml")
    ap.add_argument("--lidar-from", choices=["truth", "repeatability"], default="truth",
                    help="LiDAR samples: ground truth when there is any (default), or the repeatability pair. "
                         "Use repeatability while the laser rooms' outlines have more walls than the truth: "
                         "each truth wall is then paired with an outline fragment, a segmentation error the "
                         "intervals do not model (fitting on it gave scale 99, +-2.3 m on every 3 m wall)")
    ap.add_argument("--only", nargs="*", choices=TIERS,
                    help="refit only these tiers; the others keep their scales (the widening check uses them)")
    a = ap.parse_args()
    report = json.loads(Path(a.report).read_text(encoding="utf-8"))
    cal = yaml.safe_load(CAL.read_text(encoding="utf-8"))
    samples = collect(report)
    laser = len(samples.get("lidar", []))
    if a.lidar_from == "repeatability" or not samples.get("lidar"):
        # the agreement of two captures of the same space (precision only)
        man = yaml.safe_load((HERE / "manifest.yaml").read_text(encoding="utf-8"))
        samples["lidar"] = repeat_samples(Path(a.report).parent / "runs", man.get("repeatability", []),
                                          report.get("calibration_used"))
    fits = {tier: fit_tier(samples[tier]) for tier in TIERS if samples.get(tier)}
    shown_fits = fits
    if a.only:
        fits = {t: f for t, f in fits.items() if t in a.only}
    new, notes = apply(cal, fits)
    if a.only:
        notes.append(f"refitted: {', '.join(a.only)}; kept: {', '.join(t for t in TIERS if t not in a.only)} "
                     f"(their fits are shown above for information)")
    fits = shown_fits
    sources = sorted({s["source"] for t in samples.values() for s in t})
    shown = Path(os.path.relpath(Path(a.report).resolve(), HERE.parent.resolve())).as_posix()
    tag = report.get("tag") or "untagged"

    md = ["# Interval calibration", "",
          f"From `{shown}`, run {tag}; truth source: "
          f"{', '.join(sources) or 'none'}. Method in `bench/calibrate.py`.", ""]
    if a.lidar_from == "repeatability" and laser:
        md += [f"LiDAR is fitted on the repeatability pair, not on its {laser} laser-truth samples: the laser "
               "rooms' outlines have more walls than the truth, so each truth wall is paired with an outline "
               "fragment, a segmentation error these intervals do not model. Their coverage is reported in "
               "the benchmark's calibration rows.", ""]
    md += [
          "| tier | group | samples | rooms | coverage at scale 1 | fitted scale | in-sample | leave-one-room-out |",
          "|---|---|---|---|---|---|---|---|"]
    for tier, groups in fits.items():
        for group, f in groups.items():
            if f.get("scale") is None:
                md.append(f"| {tier} | {group} | {f['n']} | {f['rooms']} | | not fitted: {f['note']} | | |")
                continue
            lo = (f"{f['coverage_leave_one_room_out']:.2f} (n {f['loro_n']})" if f["coverage_leave_one_room_out"]
                  is not None else "n/a: one room")
            md.append(f"| {tier} | {group} | {f['n']} | {f['rooms']} | {f['coverage_at_scale_1']:.2f} | {f['scale']}"
                      f"{'' if f['finite_sample_bound'] else ' (max z: too few samples for the bound)'} | "
                      f"{f['coverage_in_sample']:.2f} | {lo} |")
    for tier in TIERS:
        if tier not in fits:
            md.append(f"| {tier} | all | 0 | 0 | | not fitted: no samples (no ground truth or reference) | | |")
    md += ["", "Reference 90 % half-widths after fitting (must widen from LiDAR to photo):", "",
           "| measurement | " + " | ".join(TIERS) + " |", "|---|" + "---|" * len(TIERS)]
    for kind, (v, raw) in REFERENCE.items():
        unit = "m2" if kind == "area" else "m"
        md.append(f"| {kind} {v} {unit} | " + " | ".join(f"±{half_width(new[t], kind):.3f}" for t in TIERS) + " |")
    if notes:
        md += ["", "Adjustments:", ""] + [f"- {n}" for n in notes]
    Path(a.out).write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    if a.write:
        header = [f"# Fitted on {shown}, run {tag}. Truth: {', '.join(sources) or 'none'}.",
                  "# Report with held-out coverage: bench/reports/calibration.md."]
        write_yaml(new, header)
        print("wrote", CAL)


if __name__ == "__main__":
    main()
