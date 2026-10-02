"""Head-to-head (brief Part 3): our LiDAR-tier output against one consumer scanning app on
the same rooms, both scored against the same laser / tape ground truth.

usage:
    python bench/head_to_head.py <ground_truth.yaml> <result.json> <app.yaml>
           [<ground_truth.yaml> <result.json> <app.yaml> ...] [--out bench/reports/head_to_head]

One triple per capture: the two head-to-head rooms may come from one capture of ours or
from two. The three files of a triple may be given in any order (they are told apart by
their content). <app.yaml> holds the app's numbers as read from its plan or export
(template: bench/app_exports/TEMPLATE.yaml). Writes <out>.md and <out>.json and prints the
table.

What is compared: the rooms the app file lists, and in each of them every wall length, the
ceiling height and every opening width the ground truth has. Each system is paired with
the ground truth by the rules of bench/evaluate.py: our rooms by match_rooms (over all
ground-truth rooms), the walls of either system by _wall_cost (cyclic order when the counts
agree, longest to longest otherwise; segments under 25 cm are dropped, as in pred_walls),
openings by type and width (more than 50 cm apart or clashing types is no pairing). The app
file names its rooms with the ground-truth names, so the app needs no room matching.

Verdict per dimension, on absolute errors against the ground truth:
    beat        our error is smaller than the app's by more than TIE_TOL_M
    tie         the two errors are within TIE_TOL_M of each other
    lose        our error is larger by more than TIE_TOL_M, or we report no value where
                the app reports one
    not shared  the app reports no value (whether or not we do): left out of the fraction
Missing values are handled so the score can only err against us: a dimension we fail to
report is a loss, a dimension the app fails to report is not counted. Openings that only
one system reports (no ground-truth opening to pair with) are listed but not scored.
Gate: head_to_head.win_or_tie_fraction in bench/gates.yaml (brief: beat or tie on >= 70 %
of shared dimensions).
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

import numpy as np
import yaml
from scipy.optimize import linear_sum_assignment

sys.path.insert(0, str(Path(__file__).parent))
from evaluate import GATES, _wall_cost, match_rooms, pred_walls  # noqa: E402

# Two errors closer than this are a tie. 1 cm because
#  * the brief itself treats two readings of a wall within 1 cm as the same result
#    (repeatability gate: "agree within 1 cm");
#  * the ground truth is a hand laser / tape reading, good to about +-5 mm corner to corner.
#    When our value and the app's lie on opposite sides of the truth, a 5 mm error in the
#    truth shifts the difference between the two errors by 2 x 5 mm = 1 cm, so a smaller
#    difference can flip with the truth's own error and ranks nothing;
#  * consumer apps display lengths rounded to the centimetre, which alone adds up to 5 mm
#    to the app's error.
TIE_TOL_M = 0.01
# Same rule as bench/evaluate.py: an opening pairing costing more than this (width
# difference in m, +5 for clashing types) is not the same opening.
MAX_OPENING_COST = 0.5
MAX_PLAUSIBLE_M = 30.0  # anything longer was typed in centimetres or millimetres
COUNTED = ("beat", "tie", "lose")
OPENING_TYPES = ("door", "window", "opening")


class InputError(ValueError):
    """A ground-truth, result or app file that cannot be compared; reported in one line."""


def verdict(our_err: float | None, app_err: float | None) -> str:
    if app_err is None:
        return "not shared"
    if our_err is None:
        return "lose"
    d = abs(our_err) - abs(app_err)
    if abs(d) <= TIE_TOL_M + 1e-9:
        return "tie"
    return "beat" if d < 0 else "lose"


def _metres(v, what: str) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise InputError(f"{what}: {v!r} is not a number") from None
    if not 0 < x <= MAX_PLAUSIBLE_M:
        raise InputError(f"{what}: {x} is not a length in metres")
    return x


def _pair_walls(truth: list[float], values: list[float]) -> dict[int, int]:
    """Ground-truth wall index -> index into values, by evaluate._wall_cost."""
    if not truth or not values:
        return {}
    return {int(g): int(p) for g, p in _wall_cost(truth, values)[1]}


def _pair_openings(truth: list[dict], cand: list[tuple[str, float | None]]) -> dict[int, int]:
    """Ground-truth opening index -> candidate index, by width and type as in evaluate.py."""
    if not truth or not cand:
        return {}
    C = np.array([[abs((w if w is not None else 9.0) - o["width"]) +
                   (0.0 if t == o["type"] or {t, o["type"]} <= {"door", "opening"} else 5.0)
                   for t, w in cand] for o in truth])
    ri, ci = linear_sum_assignment(C)
    return {int(i): int(j) for i, j in zip(ri, ci) if C[i, j] <= MAX_OPENING_COST}


def _row(base: dict, kind: str, dim: str, truth: float, ours: dict | None, ours_id: str | None,
         app: float | None) -> dict:
    """ours: our Measurement dict (or None when we have nothing paired); app: a number or None."""
    val = None if ours is None else ours.get("value")
    note = None
    if ours is not None and val is None:
        note = "not observed" + (f" (at least {ours['lower_bound']:.2f} m)" if ours.get("lower_bound") else "")
    our_err = None if val is None else round(val - truth, 4)
    app_err = None if app is None else round(app - truth, 4)
    return dict(base, kind=kind, dimension=dim, truth=truth, ours=val,
                ours_ci90=None if ours is None else ours.get("ci90"), ours_id=ours_id, ours_note=note,
                our_err=our_err, app=app, app_err=app_err, verdict=verdict(our_err, app_err))


def _check_gt(gt: dict, src: str) -> dict[str, dict]:
    rooms = {}
    for r in gt.get("rooms") or []:
        name = r.get("name")
        if not name:
            raise InputError(f"{src}: a room has no name")
        r["walls"] = [_metres(w, f"{src}: {name} wall") for w in r.get("walls") or []]
        if None in r["walls"]:
            raise InputError(f"{src}: {name} has an empty wall length")
        r["ceiling_height"] = _metres(r.get("ceiling_height"), f"{src}: {name} ceiling_height")
        for o in r.get("openings") or []:
            if o.get("type") not in OPENING_TYPES:
                raise InputError(f"{src}: {name} opening type {o.get('type')!r} is not one of {OPENING_TYPES}")
            o["width"] = _metres(o.get("width"), f"{src}: {name} {o['type']} width")
            if o["width"] is None:
                raise InputError(f"{src}: {name} has a {o['type']} with no width")
        rooms[name] = r
    if not rooms:
        raise InputError(f"{src}: no rooms")
    return rooms


def compare(gt: dict, pred: dict, app: dict, label: str | None = None, src: str = "ground truth",
            app_src: str = "app file") -> dict:
    """Rows and per-room extras for the rooms of one capture that the app file lists."""
    gt = copy.deepcopy(gt)
    gt_rooms = _check_gt(gt, src)
    app_rooms = app.get("rooms") or []
    if not app_rooms:
        raise InputError(f"{app_src}: no rooms")
    unknown = [a.get("name") for a in app_rooms if a.get("name") not in gt_rooms]
    if unknown:
        raise InputError(f"{app_src}: rooms {unknown} are not in the ground truth ({src} has {sorted(gt_rooms)})")
    twice = sorted({a["name"] for a in app_rooms if sum(b["name"] == a["name"] for b in app_rooms) > 1})
    if twice:
        raise InputError(f"{app_src}: rooms {twice} are listed more than once")
    label = label or gt.get("capture") or pred["capture"]["id"]
    mapping = match_rooms(gt["rooms"], pred["rooms"])
    P = {r["id"]: r for r in pred["rooms"]}
    rows, extras = [], []
    for a in app_rooms:
        g = gt_rooms[a["name"]]
        pid = mapping.get(g["name"])
        room = P.get(pid) if pid else None
        base = {"capture": label, "room": g["name"], "our_room": pid}

        gw = g["walls"]
        ow = pred_walls(room) if room else []
        app_w = [_metres(w, f"{app_src}: {a['name']} wall") for w in a.get("walls") or []]
        aw = [w["length"]["value"] for w in pred_walls({"walls": [{"length": {"value": v}} for v in app_w if v]})]
        op, ap_ = _pair_walls(gw, [w["length"]["value"] for w in ow]), _pair_walls(gw, aw)
        for i, t in enumerate(gw):
            mine = ow[op[i]] if i in op else None
            rows.append(_row(base, "wall", f"wall {i + 1}", t, mine["length"] if mine else None,
                             mine["id"] if mine else None, aw[ap_[i]] if i in ap_ else None))

        if g["ceiling_height"] is not None:
            rows.append(_row(base, "ceiling", "ceiling height", g["ceiling_height"],
                             room["ceiling_height"] if room else None, None,
                             _metres(a.get("ceiling_height"), f"{app_src}: {a['name']} ceiling_height")))

        go = list(g.get("openings") or [])
        mine_o = list(room["openings"]) if room else []
        n_own = len(mine_o)  # only the room's own openings can be extras; borrowed ones belong to the neighbour
        if room:
            # a doorway between two rooms is reported once, on either room: borrow from the neighbour
            mine_o += [o for r in pred["rooms"] if r["id"] != pid for o in r["openings"] if o.get("connects_to") == pid]
        app_o = []
        for o in a.get("openings") or []:
            if o.get("type") not in OPENING_TYPES:
                raise InputError(f"{app_src}: {a['name']} opening type {o.get('type')!r} is not one of {OPENING_TYPES}")
            app_o.append((o["type"], _metres(o.get("width"), f"{app_src}: {a['name']} {o['type']} width")))
        op = _pair_openings(go, [(o["type"], o["width"]["value"]) for o in mine_o])
        ap_ = _pair_openings(go, app_o)
        count = {}
        for i, o in enumerate(go):
            count[o["type"]] = count.get(o["type"], 0) + 1
            mine = mine_o[op[i]] if i in op else None
            rows.append(_row(base, "opening", f"{o['type']} {count[o['type']]} width", o["width"],
                             mine["width"] if mine else None, mine["id"] if mine else None,
                             app_o[ap_[i]][1] if i in ap_ else None))
        used_o, used_a = set(op.values()), set(ap_.values())
        extras.append({"capture": label, "room": g["name"],
                       "ours": [{"id": o["id"], "type": o["type"], "width": o["width"]["value"]}
                                for j, o in enumerate(mine_o[:n_own]) if j not in used_o],
                       "app": [{"type": t, "width": w} for j, (t, w) in enumerate(app_o) if j not in used_a]})
    return {"label": label, "tier": pred["capture"]["tier"], "rows": rows, "unscored_openings": extras}


def _median_abs(xs: list[float]) -> float | None:
    return round(float(np.median(np.abs(xs))), 4) if xs else None


def summarise(rows: list[dict]) -> dict:
    gate = GATES["head_to_head"]["win_or_tie_fraction"]
    n = {v: sum(r["verdict"] == v for r in rows) for v in (*COUNTED, "not shared")}
    shared = sum(n[v] for v in COUNTED)
    frac = (n["beat"] + n["tie"]) / shared if shared else None
    by_kind = {}
    for kind in ("wall", "ceiling", "opening"):
        rs = [r for r in rows if r["kind"] == kind and r["verdict"] in COUNTED]
        both = [r for r in rs if r["our_err"] is not None]
        by_kind[kind] = {"shared": len(rs), **{v: sum(r["verdict"] == v for r in rs) for v in COUNTED},
                         "ours_missing": sum(r["our_err"] is None for r in rs),
                         "median_abs_err_ours_m": _median_abs([r["our_err"] for r in both]),
                         "median_abs_err_app_m": _median_abs([r["app_err"] for r in both])}
    return {"dimensions": len(rows), "shared": shared, **n,
            "win_or_tie_fraction": None if frac is None else round(frac, 4),
            "gate": gate, "pass": bool(frac is not None and frac >= gate), "by_kind": by_kind}


def head_to_head(cases: list) -> dict:
    """cases: (gt, result, app) or (gt, result, app, files) per capture; files = {"gt", "result", "app"} paths."""
    apps, parts, warnings = [], [], []
    for c in cases:
        gt, pred, app = c[:3]
        files = c[3] if len(c) > 3 else {}
        src = files.get("app", "app file")
        if not str(app.get("app") or "").strip() or not str(app.get("version") or "").strip():
            raise InputError(f"{src}: fill in `app` and `version` (the brief asks to name both)")
        apps.append(app)
        label = gt.get("capture") or (Path(files["result"]).parent.name if files.get("result") else None)
        parts.append(dict(compare(gt, pred, app, label, src=files.get("gt", "ground truth"), app_src=src),
                          files=files, gt_meta={k: gt.get(k) for k in ("source", "instrument", "measured_by", "date")}))
        if pred["capture"]["tier"] != "lidar":
            warnings.append(f"{parts[-1]['label']}: our result is the {pred['capture']['tier']} tier; "
                            f"Part 3 asks for the LiDAR tier")
        exports = app.get("export_file") or []
        for exp in [exports] if isinstance(exports, str) else exports:
            if files.get("app") and not (Path(files["app"]).parent / exp).exists():
                warnings.append(f"export file {exp} not found next to {src}: the brief asks to submit the app's export")
        if not exports:
            warnings.append(f"{src}: no export_file named; the brief asks to submit the app's export")
    names = {(str(a["app"]).strip(), str(a["version"]).strip()) for a in apps}
    if len(names) > 1:
        raise InputError(f"one app per table, got {sorted(names)}")
    app = apps[0]
    rows = [r for p in parts for r in p["rows"]]
    meta = {k: app.get(k) for k in ("app", "version", "plan", "device", "ios", "date", "mode", "export_file", "notes")}
    return {"app": meta, "tie_tolerance_m": TIE_TOL_M, "captures": [
        {k: p[k] for k in ("label", "tier", "files", "gt_meta", "unscored_openings")} for p in parts],
            "rows": rows, "summary": summarise(rows), "warnings": warnings}


def _m(v) -> str:
    return "n/a" if v is None else f"{v:.3f}"


def _cm(e) -> str:
    return "n/a" if e is None else f"{e * 100:+.1f}"


def to_markdown(rep: dict, command: str | None = None) -> str:
    a, s = rep["app"], rep["summary"]
    app_name = f"{a['app']} {a['version']}"
    lines = [f"# Head-to-head: roomscan LiDAR tier vs {app_name}", ""]
    if command:
        lines += [f"Regenerate with `{command}`.", ""]
    app_bits = [x for x in (a.get("plan") and f"{a['plan']} tier", a.get("mode") and f"mode {a['mode']}",
                            a.get("device"), a.get("ios") and f"iOS {a['ios']}",
                            a.get("date") and f"scanned {a['date']}") if x]
    exp = a.get("export_file") or []
    exp = ", ".join(f"`{e}`" for e in ([exp] if isinstance(exp, str) else exp))
    lines += ["| | |", "|---|---|",
              f"| App | {app_name}" + (f" ({', '.join(app_bits)})" if app_bits else "") +
              (f"; export {exp}" if exp else "; no export named") + " |"]
    for c in rep["captures"]:
        f, g = c["files"], c["gt_meta"]
        lines.append(f"| Ours, {c['label']} | roomscan {'LiDAR' if c['tier'] == 'lidar' else c['tier']} tier" +
                     (f", `{f['result']}`" if f.get("result") else "") + " |")
        lines.append(f"| Truth, {c['label']} | {g.get('source') or 'unknown'}" +
                     "".join(f", {x}" for x in (g.get("instrument"), g.get("measured_by") and f"measured by {g['measured_by']}",
                                                g.get("date")) if x) +
                     (f", `{f['gt']}`" if f.get("gt") else "") + " |")
    lines += [f"| Tie | the two absolute errors within {rep['tie_tolerance_m'] * 100:.1f} cm of each other |", ""]
    if s["shared"]:
        lines.append(f"**Beat or tie on {s['beat'] + s['tie']} of {s['shared']} shared dimensions = "
                     f"{s['win_or_tie_fraction'] * 100:.1f} % (gate >= {s['gate'] * 100:.0f} %): "
                     f"{'PASS' if s['pass'] else 'FAIL'}**")
    else:
        lines.append("**No shared dimensions: the app file reports none of the ground-truth dimensions. FAIL**")
    lines += ["", f"beat {s['beat']}, tie {s['tie']}, lose {s['lose']}; {s['not shared']} not shared "
                  f"(the app reports no value; not counted). A dimension we do not report counts as lose.", ""]
    for w in rep["warnings"]:
        lines += [f"WARNING: {w}", ""]
    multi = len(rep["captures"]) > 1
    lines += ["| room | dimension | truth (m) | ours (m) | our error (cm) | app (m) | app error (cm) | verdict |",
              "|---|---|---|---|---|---|---|---|"]
    for r in rep["rows"]:
        room = f"{r['capture']} / {r['room']}" if multi else r["room"]
        ours = r["ours_note"] or (_m(r["ours"]) if r["ours"] is not None else "not reported")
        app = _m(r["app"]) if r["app"] is not None else "not reported"
        lines.append(f"| {room} | {r['dimension']} | {r['truth']:.3f} | {ours} | {_cm(r['our_err'])} | {app} | "
                     f"{_cm(r['app_err'])} | {r['verdict']} |")
    lines += ["", "| kind | shared | beat | tie | lose | of which we report nothing | "
                  "median abs error where both report: ours (cm) | app (cm) |",
              "|---|---|---|---|---|---|---|---|"]
    for kind, k in s["by_kind"].items():
        med = [("n/a" if x is None else f"{x * 100:.1f}") for x in (k["median_abs_err_ours_m"], k["median_abs_err_app_m"])]
        lines.append(f"| {kind} | {k['shared']} | {k['beat']} | {k['tie']} | {k['lose']} | {k['ours_missing']} | "
                     f"{med[0]} | {med[1]} |")
    extra = [(c["label"], e) for c in rep["captures"] for e in c["unscored_openings"] if e["ours"] or e["app"]]
    if extra:
        lines += ["", "Openings with no ground-truth opening to pair with (listed, not scored):", ""]
        for label, e in extra:
            room = f"{label} / {e['room']}" if multi else e["room"]
            ours = ", ".join(f"{o['type']} {_m(o['width'])}" for o in e["ours"]) or "none"
            app = ", ".join(f"{o['type']} {_m(o['width'])}" for o in e["app"]) or "none"
            lines.append(f"- {room}: ours {ours}; app {app}")
    lines.append("")
    return "\n".join(lines)


def _load(path: str) -> tuple[str, dict]:
    p = Path(path)
    if not p.exists():
        raise InputError(f"{path} does not exist")
    text = p.read_text(encoding="utf-8")
    try:
        data = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise InputError(f"{path}: cannot parse ({str(e).splitlines()[0]})") from None
    if not isinstance(data, dict):
        raise InputError(f"{path}: expected a mapping at the top level")
    if "property" in data and "capture" in data:
        return "result", data
    if "app" in data:
        return "app", data
    if "rooms" in data:
        return "gt", data
    raise InputError(f"{path} is neither a ground-truth file, a result.json nor an app file")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", metavar="FILE",
                    help="<ground_truth.yaml> <result.json> <app.yaml>, one triple per capture")
    ap.add_argument("--out", default=str(Path(__file__).parent / "reports" / "head_to_head"),
                    help="output path without extension: writes <out>.md and <out>.json")
    a = ap.parse_args(argv)
    if len(a.files) % 3:
        ap.error("give the files in triples: <ground_truth.yaml> <result.json> <app.yaml> per capture")
    try:
        cases = []
        for i in range(0, len(a.files), 3):
            kinds = {}
            for f in a.files[i:i + 3]:
                k, d = _load(f)
                if k in kinds:
                    raise InputError(f"{kinds[k][0]} and {f} are both {k} files; each triple needs one ground truth, "
                                     f"one result.json and one app file")
                kinds[k] = (f, d)
            files = {k: v[0] for k, v in kinds.items()}
            cases.append((kinds["gt"][1], kinds["result"][1], kinds["app"][1], files))
        rep = head_to_head(cases)
    except InputError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    command = "python bench/head_to_head.py " + " ".join(Path(f).as_posix() for f in a.files)
    if a.out != ap.get_default("out"):
        command += f" --out {Path(a.out).as_posix()}"
    md = to_markdown(rep, command)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".md").write_text(md, encoding="utf-8", newline="\n")
    out.with_suffix(".json").write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(md)
    print(f"wrote {out.with_suffix('.md')} and {out.with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
