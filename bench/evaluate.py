"""Score one pipeline result against ground truth and print every gate.

usage: python bench/evaluate.py <result.json> <ground_truth.yaml> [--out report.json]

Ground truth file (hand measurements; see bench/ground_truth/TEMPLATE.yaml):

    capture: flat_a
    source: tape            # tape | laser | lidar_reference
    rooms:
      - name: kitchen
        match: room_3       # optional: predicted room id; found automatically if absent
        ceiling_height: 2.70
        walls: [4.20, 3.50, 4.20, 3.50]      # metres, in order around the room
        openings:
          - {type: door, width: 0.90}
    adjacency: [[kitchen, hall]]
    footprint: 45.2         # optional, m2

Rooms are matched by explicit `match` or by best agreement of wall lengths and area.
Walls are compared in cyclic order when the counts agree, otherwise longest-to-longest
and the count mismatch is reported. A missed opening and a phantom opening each count as
a miss, as the brief requires.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import yaml
from scipy.optimize import linear_sum_assignment

GATES = yaml.safe_load((Path(__file__).parent / "gates.yaml").read_text())


def _area(walls: list[float]) -> float:
    return float(sum(walls))


def _wall_cost(gt: list[float], pred: list[float]) -> tuple[float, list[tuple[int, int]]]:
    """Best cyclic alignment (either direction) when counts agree, else sorted matching."""
    if len(gt) == len(pred) and gt:
        best = None
        n = len(gt)
        for rev in (False, True):
            p = pred[::-1] if rev else pred
            idx = list(range(n))[::-1] if rev else list(range(n))
            for s in range(n):
                cost = float(np.mean([abs(gt[i] - p[(i + s) % n]) for i in range(n)]))
                if best is None or cost < best[0]:
                    best = (cost, [(i, idx[(i + s) % n]) for i in range(n)])
        return best
    gi = np.argsort(gt)[::-1]
    pi = np.argsort(pred)[::-1]
    pairs = [(int(a), int(b)) for a, b in zip(gi, pi)]
    cost = float(np.mean([abs(gt[a] - pred[b]) for a, b in pairs])) if pairs else 1e3
    return cost + 0.3 * abs(len(gt) - len(pred)), pairs


def pred_walls(room: dict, min_len: float = 0.25) -> list[dict]:
    return [w for w in room["walls"] if w["length"]["value"] and w["length"]["value"] >= min_len]


def match_rooms(gt_rooms: list[dict], pred_rooms: list[dict]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    free_gt, free_pred = [], [r["id"] for r in pred_rooms]
    for g in gt_rooms:
        if g.get("match") in free_pred:
            out[g["name"]] = g["match"]
            free_pred.remove(g["match"])
        else:
            free_gt.append(g)
    if free_gt and free_pred:
        P = {r["id"]: r for r in pred_rooms}
        C = np.zeros((len(free_gt), len(free_pred)))
        for i, g in enumerate(free_gt):
            for j, pid in enumerate(free_pred):
                pw = [w["length"]["value"] for w in pred_walls(P[pid])]
                C[i, j] = _wall_cost(list(g.get("walls", [])), pw)[0] if g.get("walls") else 0.0
                if g.get("floor_area"):
                    C[i, j] += 0.2 * abs(g["floor_area"] - P[pid]["floor_area"]["value"])
        ri, ci = linear_sum_assignment(C)
        for i, j in zip(ri, ci):
            out[free_gt[i]["name"]] = free_pred[j]
    for g in gt_rooms:
        out.setdefault(g["name"], None)
    return out


def _inside(m: dict, truth: float) -> bool | None:
    return None if not m.get("ci90") else bool(m["ci90"][0] <= truth <= m["ci90"][1])


def evaluate(pred: dict, gt: dict) -> dict:
    tier = pred["capture"]["tier"]
    P = {r["id"]: r for r in pred["rooms"]}
    mapping = match_rooms(gt["rooms"], pred["rooms"])
    walls, ceil, opens, cover = [], [], [], []
    for g in gt["rooms"]:
        pid = mapping[g["name"]]
        if pid is None:
            walls += [{"room": g["name"], "gt": w, "pred": None, "err": None, "ok": False} for w in g.get("walls", [])]
            opens += [{"room": g["name"], "type": o["type"], "gt": o["width"], "pred": None, "status": "missed"}
                      for o in g.get("openings", [])]
            if g.get("ceiling_height"):
                ceil.append({"room": g["name"], "gt": g["ceiling_height"], "pred": None, "err": None, "ok": False})
            continue
        room = P[pid]
        pw = pred_walls(room)
        gw = list(g.get("walls", []))
        if gw:
            _, pairs = _wall_cost(gw, [w["length"]["value"] for w in pw])
            gate = GATES["wall_length"][tier]
            used = set()
            for gi, pi in pairs:
                m = pw[pi]["length"]
                err = m["value"] - gw[gi]
                ok = abs(err) <= max(gate.get("abs_m", 0.0), gate.get("rel", 0.0) * gw[gi])
                walls.append({"room": g["name"], "pred_id": pw[pi]["id"], "gt": gw[gi], "pred": m["value"],
                              "err": round(err, 4), "rel": round(err / gw[gi], 4), "ok": bool(ok),
                              "in_ci": _inside(m, gw[gi])})
                cover.append(_inside(m, gw[gi]))
                used.add(gi)
            for gi in set(range(len(gw))) - used:
                walls.append({"room": g["name"], "gt": gw[gi], "pred": None, "err": None, "ok": False})
        if g.get("ceiling_height"):
            m = room["ceiling_height"]
            if m["value"] is None:
                ceil.append({"room": g["name"], "gt": g["ceiling_height"], "pred": None, "err": None, "ok": False,
                             "note": "not observed" + (f", lower bound {m['lower_bound']}" if m.get("lower_bound") else "")})
            else:
                err = m["value"] - g["ceiling_height"]
                ceil.append({"room": g["name"], "gt": g["ceiling_height"], "pred": m["value"], "err": round(err, 4),
                             "ok": bool(abs(err) <= GATES["ceiling_height"]["abs_m"]),
                             "in_ci": _inside(m, g["ceiling_height"])})
                cover.append(_inside(m, g["ceiling_height"]))
        if "openings" in g:
            go = list(g["openings"])
            po = [o for o in room["openings"]]
            # a doorway between two rooms is reported once, on either room: borrow from the neighbour
            for other in pred["rooms"]:
                if other["id"] != pid:
                    po += [o for o in other["openings"] if o.get("connects_to") == pid]
            C = np.array([[abs((p["width"]["value"] or 9) - o["width"]) + (0 if p["type"] == o["type"] or
                           {p["type"], o["type"]} <= {"door", "opening"} else 5) for p in po] for o in go]) \
                if go and po else np.zeros((len(go), len(po)))
            ri, ci = linear_sum_assignment(C) if C.size else ([], [])
            matched_g, matched_p = set(), set()
            for i, j in zip(ri, ci):
                if C[i, j] > 0.5:  # widths differ by more than 50 cm or types clash: not the same opening
                    continue
                err = po[j]["width"]["value"] - go[i]["width"]
                opens.append({"room": g["name"], "type": go[i]["type"], "gt": go[i]["width"],
                              "pred": po[j]["width"]["value"], "err": round(err, 4),
                              "status": "ok" if abs(err) <= GATES["opening_width"]["abs_m"] else "off",
                              "in_ci": _inside(po[j]["width"], go[i]["width"])})
                cover.append(_inside(po[j]["width"], go[i]["width"]))
                matched_g.add(int(i))
                matched_p.add(int(j))
            for i, o in enumerate(go):
                if i not in matched_g:
                    opens.append({"room": g["name"], "type": o["type"], "gt": o["width"], "pred": None, "status": "missed"})
            for j, p in enumerate(room["openings"]):  # only the room's own openings can be phantoms
                if j not in matched_p:
                    opens.append({"room": g["name"], "type": p["type"], "gt": None, "pred": p["width"]["value"],
                                  "status": "phantom"})
    res = {"capture": pred["capture"]["id"], "tier": tier, "gt_source": gt.get("source", "unknown"),
           "room_mapping": mapping, "walls": walls, "ceilings": ceil, "openings": opens, "gates": {}}
    g = res["gates"]
    if walls:
        frac = float(np.mean([w["ok"] for w in walls]))
        errs = [abs(w["err"]) for w in walls if w["err"] is not None]
        rels = [abs(w["rel"]) for w in walls if w.get("rel") is not None]
        g["wall_length"] = {"n": len(walls), "pass_fraction": round(frac, 3),
                            "median_abs_err_m": round(float(np.median(errs)), 4) if errs else None,
                            "median_rel_err": round(float(np.median(rels)), 4) if rels else None,
                            "max_abs_err_m": round(float(np.max(errs)), 4) if errs else None,
                            "gate": GATES["wall_length"][tier], "pass": bool(frac >= GATES["wall_length"]["pass_fraction"])}
    if ceil:
        errs = [abs(c["err"]) for c in ceil if c["err"] is not None]
        g["ceiling_height"] = {"n": len(ceil), "n_ok": int(sum(c["ok"] for c in ceil)),
                               "max_abs_err_m": round(float(np.max(errs)), 4) if errs else None,
                               "gate_m": GATES["ceiling_height"]["abs_m"], "pass": bool(all(c["ok"] for c in ceil))}
    if any("openings" in r for r in gt["rooms"]):
        n = len(opens)
        ok = sum(o["status"] == "ok" for o in opens)
        g["opening_width"] = {"n_scored": n, "ok": ok, "off": sum(o["status"] == "off" for o in opens),
                              "missed": sum(o["status"] == "missed" for o in opens),
                              "phantom": sum(o["status"] == "phantom" for o in opens),
                              "pass_fraction": round(ok / n, 3) if n else None,
                              "pass": bool(n and ok / n >= GATES["opening_width"]["pass_fraction"])}
    cov = [c for c in cover if c is not None]
    if cov:
        c = float(np.mean(cov))
        lo, hi = GATES["calibration"]["accept"]
        g["calibration"] = {"n": len(cov), "coverage_of_90pct_intervals": round(c, 3), "pass": bool(lo <= c <= hi)}
    if gt.get("footprint"):
        fp = pred["property"]["footprint_area"]
        rel = (fp["value"] - gt["footprint"]) / gt["footprint"]
        g["footprint"] = {"gt_m2": gt["footprint"], "pred_m2": fp["value"], "rel_err": round(rel, 4),
                          "in_ci": _inside(fp, gt["footprint"]),
                          "pass": bool(abs(rel) <= GATES["footprint"][tier]["rel"])}
    if gt.get("adjacency") is not None:
        inv = {v: k for k, v in mapping.items() if v}
        pa = {frozenset(inv.get(x, x) for x in a["rooms"]) for a in pred["property"]["adjacency"] if a["kind"] != "shared_wall"}
        ga = {frozenset(a) for a in gt["adjacency"]}
        g["adjacency"] = {"gt": len(ga), "found": len(ga & pa), "missing": sorted(sorted(a) for a in ga - pa),
                          "extra": sorted(sorted(a) for a in pa - ga), "pass": bool(ga <= pa and not (pa - ga))}
    from shapely.geometry import Polygon
    polys = [(r["id"], Polygon(r["polygon"]).buffer(0)) for r in pred["rooms"]]
    overlaps = [(a, b, round(pa_.intersection(pb_).area, 3)) for i, (a, pa_) in enumerate(polys)
                for b, pb_ in polys[i + 1:] if pa_.intersection(pb_).area > 0.02]
    g["no_room_overlap"] = {"overlaps": overlaps, "pass": not overlaps}
    g["rooms_found"] = {"gt": len(gt["rooms"]), "pred": len(pred["rooms"]),
                        "matched": sum(v is not None for v in mapping.values()),
                        "pass": all(v is not None for v in mapping.values())}
    return res


def print_report(res: dict) -> None:
    print(f"\n== {res['capture']} | tier {res['tier']} | ground truth: {res['gt_source']} ==")
    for name, g in res["gates"].items():
        flag = "PASS" if g.get("pass") else "FAIL"
        detail = {k: v for k, v in g.items() if k not in ("pass", "gate")}
        print(f"  [{flag}] {name}: {detail}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("result")
    ap.add_argument("ground_truth")
    ap.add_argument("--out")
    a = ap.parse_args()
    res = evaluate(json.loads(Path(a.result).read_text()), yaml.safe_load(Path(a.ground_truth).read_text()))
    print_report(res)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
