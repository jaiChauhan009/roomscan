"""Interval calibration (bench/calibrate.py) and its use in measure()."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
import calibrate  # noqa: E402

from roomscan.uncertainty import intervals  # noqa: E402


def test_conformal_scale_gives_ninety_percent_on_new_samples():
    rng = np.random.default_rng(0)
    true_scale = 2.5  # errors are 2.5x larger than the reported sigma
    z = np.abs(rng.normal(0, true_scale, 200))
    q, exact = calibrate.conformal_q(z)
    assert exact
    fresh = np.abs(rng.normal(0, true_scale, 20000))
    assert np.mean(fresh <= q) == pytest.approx(0.90, abs=0.03)
    assert q / calibrate.Z90 == pytest.approx(true_scale, rel=0.15)


def test_refitting_a_run_does_not_compound_the_scale():
    # the run was made with scale 3, so its sigmas are 3x the base sigma
    report = {"calibration_used": {"photo": {"wall_length": {"scale": 3.0}}},
              "evaluations": {"cap": {"tier": "photo", "gt_source": "x", "interval_samples": [
                  {"kind": "wall_length", "room": "r1", "pred": 3.2, "truth": 3.0, "sigma": 0.3}]}}}
    s = calibrate.collect(report)["photo"][0]
    assert s["base_sigma"] == pytest.approx(0.1)
    assert s["z"] == pytest.approx(2.0)


def test_thinner_data_never_gets_a_narrower_interval():
    t = {"inflate": 2.0, "abs": 0.05, "rel": 0.05}
    cal = {tier: {k: dict(t, scale=1.0) for k in calibrate.REFERENCE} for tier in calibrate.TIERS}
    fits = {"video": {"length": {"scale": 4.0}}, "photo": {"length": {"scale": 2.0}}}  # photo narrower: wrong
    new, notes = calibrate.apply(cal, fits)
    for kind in calibrate.REFERENCE:
        assert calibrate.half_width(new["photo"], kind) >= calibrate.half_width(new["video"], kind) - 1e-9
        assert calibrate.half_width(new["video"], kind) >= calibrate.half_width(new["lidar"], kind) - 1e-9
    assert any("photo wall_length" in n for n in notes)


def test_measure_applies_the_fitted_scale():
    c = intervals.calibration()["lidar"]["wall_length"]
    m = intervals.measure(3.0, 0.005, "lidar", "wall_length")
    base = np.sqrt((c["inflate"] * 0.005) ** 2 + c["abs"] ** 2 + (c["rel"] * 3.0) ** 2)
    assert m.sigma == pytest.approx(c.get("scale", 1.0) * base, rel=1e-3)
    assert m.ci90[1] - 3.0 == pytest.approx(intervals.Z90 * m.sigma, abs=2e-4)
