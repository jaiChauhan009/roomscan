"""Repeatability gate: two captures of the same space at the same tier must give the same plan.

usage: python bench/repeatability.py <result_a.json> <result_b.json> [--out report.json]

Rooms are paired by best agreement of wall lengths; walls are compared in cyclic order.
Gate (brief): every wall agrees within 1 cm or 0.5 %; ceiling heights within 1 cm.
Rooms present in only one capture are reported and fail the gate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

sys.path.insert(0, str(Path(__file__).parent))
from evaluate import GATES, _wall_cost, pred_walls  # noqa: E402


def compare(a: dict, b: dict, max_room_cost: float = 0.35) -> dict:
    A, B = a["rooms"], b["rooms"]
    C = np.full((len(A), len(B)), 9.0)
    for i, ra in enumerate(A):
        for j, rb in enumerate(B):
            wa = [w["length"]["value"] for w in pred_walls(ra)]
            wb = [w["length"]["value"] for w in pred_walls(rb)]
            C[i, j] = _wall_cost(wa, wb)[0] + 0.1 * abs(ra["floor_area"]["value"] - rb["floor_area"]["value"])
    ri, ci = linear_sum_assignment(C) if C.size else ([], [])
    gate = GATES["repeatability"]
    rows, ceil = [], []
    paired = []
    for i, j in zip(ri, ci):
        if C[i, j] > max_room_cost + 0.1 * A[i]["floor_area"]["value"] * 0.2:
            continue
        paired.append((A[i]["id"], B[j]["id"]))
        wa, wb = pred_walls(A[i]), pred_walls(B[j])
        la, lb = [w["length"]["value"] for w in wa], [w["length"]["value"] for w in wb]
        _, pairs = _wall_cost(la, lb)
        for x, y in pairs:
            d = la[x] - lb[y]
            ok = abs(d) <= max(gate["abs_m"], gate["rel"] * max(la[x], lb[y]))
            rows.append({"room_a": A[i]["id"], "room_b": B[j]["id"], "a": la[x], "b": lb[y], "diff": round(d, 4),
                         "ok": bool(ok)})
        if len(la) != len(lb):
            rows.append({"room_a": A[i]["id"], "room_b": B[j]["id"], "a": None, "b": None, "diff": None, "ok": False,
                         "note": f"wall count differs: {len(la)} vs {len(lb)}"})
        ha, hb = A[i]["ceiling_height"]["value"], B[j]["ceiling_height"]["value"]
        if ha is not None and hb is not None:
            ceil.append({"room_a": A[i]["id"], "room_b": B[j]["id"], "a": ha, "b": hb, "spread": round(abs(ha - hb), 4),
                         "ok": bool(abs(ha - hb) <= GATES["ceiling_height"]["repeat_spread_m"])})
    diffs = [abs(r["diff"]) for r in rows if r["diff"] is not None]
    res = {
        "a": a["capture"]["id"], "b": b["capture"]["id"], "tier": a["capture"]["tier"],
        "rooms_a": len(A), "rooms_b": len(B), "rooms_paired": len(paired), "pairs": paired,
        "walls": rows, "ceilings": ceil,
        "summary": {
            "walls_compared": len(diffs),
            "walls_within_gate": int(sum(r["ok"] for r in rows)),
            "median_abs_diff_m": round(float(np.median(diffs)), 4) if diffs else None,
            "p90_abs_diff_m": round(float(np.percentile(diffs, 90)), 4) if diffs else None,
            "max_abs_diff_m": round(float(np.max(diffs)), 4) if diffs else None,
            "ceiling_max_spread_m": round(max((c["spread"] for c in ceil), default=float("nan")), 4) if ceil else None,
            "footprint_a_m2": a["property"]["footprint_area"]["value"],
            "footprint_b_m2": b["property"]["footprint_area"]["value"],
        },
    }
    res["pass"] = bool(rows and all(r["ok"] for r in rows) and len(paired) == len(A) == len(B)
                       and all(c["ok"] for c in ceil))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--out")
    x = ap.parse_args()
    res = compare(json.loads(Path(x.a).read_text()), json.loads(Path(x.b).read_text()))
    print(f"\n== repeatability {res['a']} vs {res['b']} | tier {res['tier']} ==")
    print(f"  rooms: {res['rooms_a']} vs {res['rooms_b']}, paired {res['rooms_paired']}")
    print(f"  [{'PASS' if res['pass'] else 'FAIL'}] {res['summary']}")
    if x.out:
        Path(x.out).parent.mkdir(parents=True, exist_ok=True)
        Path(x.out).write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
