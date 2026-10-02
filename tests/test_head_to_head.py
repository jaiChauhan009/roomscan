"""Head-to-head table: pairing with ground truth, verdicts, missing values on either side, the gate."""
import copy
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "bench"))

from head_to_head import TIE_TOL_M, head_to_head, main, verdict  # noqa: E402


def _m(v, half=0.01, lower_bound=None):
    if v is None:
        return {"value": None, "ci90": None, "sigma": None, "unit": "m", "lower_bound": lower_bound,
                "note": "ceiling not captured"}
    return {"value": v, "ci90": [v - half, v + half], "sigma": half / 1.645, "unit": "m", "lower_bound": None, "note": None}


def _room(rid, walls, ceiling, openings=()):
    return {"id": rid, "label": "room", "floor_area": _m(walls[0] * walls[1]),
            "ceiling_height": _m(ceiling, lower_bound=2.1),
            "walls": [{"id": f"{rid}_w{i + 1}", "length": _m(w)} for i, w in enumerate(walls)],
            "openings": [{"id": f"{rid}_op{i + 1}", "type": t, "width": _m(w), "connects_to": c}
                         for i, (t, w, c) in enumerate(openings)]}


GT = {"capture": "synthetic_flat", "source": "laser", "rooms": [
    {"name": "living", "ceiling_height": 2.70, "walls": [4.20, 3.50, 4.25, 3.45],
     "openings": [{"type": "door", "width": 0.90}, {"type": "window", "width": 1.40}]},
    {"name": "bedroom", "ceiling_height": 2.70, "walls": [3.00, 2.80, 3.05, 2.75],
     "openings": [{"type": "door", "width": 0.80}, {"type": "window", "width": 1.20}]},
    {"name": "hall", "ceiling_height": 2.70, "walls": [1.10, 3.00, 1.10, 3.00], "openings": []},
]}

OURS = {"capture": {"id": "c0ffee", "tier": "lidar"}, "property": {}, "rooms": [
    # living: walls +0.5, -0.2, +3.0, 0.0 cm; window not detected
    _room("room_1", [4.205, 3.498, 4.280, 3.450], 2.705, [("door", 0.905, "room_3")]),
    # bedroom: walls listed from another corner (cyclic shift), one +1 cm; ceiling not observed;
    # its door is reported on the hall, as a doorway between two rooms is reported once
    _room("room_2", [2.81, 3.05, 2.75, 3.00], None),
    _room("room_3", [1.10, 3.00, 1.10, 3.00], 2.70, [("door", 0.80, "room_2")]),
]}

APP = {"app": "TestScan", "version": "1.2.3", "plan": "free", "export_file": "testscan.pdf", "rooms": [
    # living: walls +2, +2, +1, -1 cm; an extra window the ground truth does not have
    {"name": "living", "walls": [4.22, 3.52, 4.26, 3.44], "ceiling_height": 2.68,
     "openings": [{"type": "door", "width": 0.88}, {"type": "window", "width": 1.42},
                  {"type": "window", "width": 0.60}]},
    # bedroom: one wall short (3 of 4), no openings
    {"name": "bedroom", "walls": [3.03, 2.79, 3.06], "ceiling_height": 2.71, "openings": []},
]}


def _verdicts(rep):
    return {(r["room"], r["dimension"]): r["verdict"] for r in rep["rows"]}


def test_verdict_rule_and_tie_tolerance():
    assert TIE_TOL_M == 0.01
    assert verdict(0.0, 0.01) == "tie"  # within the tolerance, boundary included
    assert verdict(-0.004, 0.012) == "tie"  # absolute errors compared, not signed ones
    assert verdict(0.0, 0.0102) == "beat"
    assert verdict(0.0202, 0.01) == "lose"
    assert verdict(None, 0.01) == "lose"  # we report nothing where the app does
    assert verdict(0.01, None) == "not shared"  # the app reports nothing: not counted
    assert verdict(None, None) == "not shared"


def test_dimension_by_dimension_with_missing_values_on_either_side():
    rep = head_to_head([(GT, OURS, APP)])
    v = _verdicts(rep)
    assert {r["room"] for r in rep["rows"]} == {"living", "bedroom"}  # only the rooms in the app file
    assert v[("living", "wall 1")] == "beat"
    assert v[("living", "wall 2")] == "beat"
    assert v[("living", "wall 3")] == "lose"
    assert v[("living", "wall 4")] == "tie"
    assert v[("living", "ceiling height")] == "beat"
    assert v[("living", "door 1 width")] == "beat"
    assert v[("living", "window 1 width")] == "lose"  # ours missing, app has it
    assert v[("bedroom", "wall 1")] == "beat"  # paired across the cyclic shift
    assert v[("bedroom", "wall 2")] == "tie"
    assert v[("bedroom", "wall 3")] == "tie"
    assert v[("bedroom", "wall 4")] == "not shared"  # app missing, ours present
    assert v[("bedroom", "ceiling height")] == "lose"  # ours not observed, app present
    assert v[("bedroom", "door 1 width")] == "not shared"  # app missing; ours borrowed from the hall
    assert v[("bedroom", "window 1 width")] == "not shared"  # neither reports it
    rows = {(r["room"], r["dimension"]): r for r in rep["rows"]}
    assert rows[("bedroom", "door 1 width")]["ours"] == pytest.approx(0.80)
    assert rows[("bedroom", "ceiling height")]["ours_note"] == "not observed (at least 2.10 m)"
    assert rows[("bedroom", "wall 2")]["our_err"] == pytest.approx(0.01)
    s = rep["summary"]
    assert (s["beat"], s["tie"], s["lose"], s["not shared"], s["shared"]) == (5, 3, 3, 3, 11)
    assert s["win_or_tie_fraction"] == pytest.approx(8 / 11, abs=1e-4)
    assert s["gate"] == 0.70 and s["pass"]
    assert s["by_kind"]["ceiling"]["ours_missing"] == 1
    extra = {e["room"]: e for e in rep["captures"][0]["unscored_openings"]}
    assert extra["living"]["app"] == [{"type": "window", "width": 0.60}]
    assert extra["living"]["ours"] == [] and extra["bedroom"]["ours"] == []


def test_gate_fails_below_seventy_percent():
    ours = copy.deepcopy(OURS)
    ours["rooms"][0]["walls"][1]["length"]["value"] = 3.54  # living wall 2 now +4 cm against the app's +2 cm
    s = head_to_head([(GT, ours, APP)])["summary"]
    assert (s["beat"], s["tie"], s["lose"]) == (4, 3, 4)
    assert s["win_or_tie_fraction"] == pytest.approx(7 / 11, abs=1e-4) and not s["pass"]


def test_command_line_writes_markdown_and_json(tmp_path, capsys):
    gt, res, app = tmp_path / "gt.yaml", tmp_path / "run" / "result.json", tmp_path / "app.yaml"
    res.parent.mkdir()
    gt.write_text(yaml.safe_dump(GT), encoding="utf-8")
    res.write_text(json.dumps(OURS), encoding="utf-8")
    app.write_text(yaml.safe_dump(APP), encoding="utf-8")
    (tmp_path / "testscan.pdf").write_bytes(b"%PDF-1.4")
    out = tmp_path / "h2h"
    assert main([str(app), str(gt), str(res), "--out", str(out)]) == 0  # any order within the triple
    md = (out.with_suffix(".md")).read_text(encoding="utf-8")
    assert "| room | dimension | truth (m) | ours (m) | our error (cm) | app (m) | app error (cm) | verdict |" in md
    assert "8 of 11 shared dimensions = 72.7 % (gate >= 70 %): PASS" in md
    assert "| bedroom | ceiling height | 2.700 | not observed (at least 2.10 m) | n/a | 2.710 | +1.0 | lose |" in md
    assert "| bedroom | wall 4 | 2.750 | 2.750 | +0.0 | not reported | n/a | not shared |" in md
    assert "| living | wall 3 | 4.250 | 4.280 | +3.0 | 4.260 | +1.0 | lose |" in md
    rep = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert rep["summary"]["pass"] and rep["app"]["version"] == "1.2.3" and not rep["warnings"]

    bad = copy.deepcopy(APP)
    bad["rooms"][0]["name"] = "kitchen"
    app.write_text(yaml.safe_dump(bad), encoding="utf-8")
    assert main([str(gt), str(res), str(app), "--out", str(out)]) == 2
    err = capsys.readouterr().err.strip()
    assert err.startswith("error:") and "kitchen" in err and len(err.splitlines()) == 1


def test_runs_on_a_real_pipeline_result():
    res_file = ROOT / "bench" / "reports" / "runs" / "room_lidar" / "result.json"
    if not res_file.exists():
        pytest.skip("benchmark run not present")
    pred = json.loads(res_file.read_text(encoding="utf-8"))
    room = max(pred["rooms"], key=lambda r: r["floor_area"]["value"])
    walls = [w["length"]["value"] for w in room["walls"] if w["length"]["value"] >= 0.25]
    opens = [{"type": o["type"], "width": o["width"]["value"]} for o in room["openings"]]
    gt = {"rooms": [{"name": "main", "match": room["id"], "walls": walls, "ceiling_height": 2.5, "openings": opens}]}
    app = {"app": "TestScan", "version": "1", "rooms": [{"name": "main", "walls": [w + 0.02 for w in walls],
                                                        "ceiling_height": 2.52,
                                                        "openings": [dict(o, width=o["width"] + 0.02) for o in opens]}]}
    rep = head_to_head([(gt, pred, app)])
    v = [r["verdict"] for r in rep["rows"] if r["kind"] != "ceiling"]
    assert v == ["beat"] * (len(walls) + len(opens))  # ours equal the truth here, the app is 2 cm off
    ceiling = next(r for r in rep["rows"] if r["kind"] == "ceiling")
    if room["ceiling_height"]["value"] is None:  # this sample scan never saw its ceiling
        assert ceiling["verdict"] == "lose" and ceiling["ours_note"].startswith("not observed")
