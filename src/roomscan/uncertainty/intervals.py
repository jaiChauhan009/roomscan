"""Per-tier uncertainty model.

Raw sigmas come from the geometry (plane-fit standard errors, edge sharpness). They only
describe noise, not bias, so each tier adds an absolute and a relative systematic term and
an inflation factor (priors from sensor specs). A per-tier `scale` on top of that is fitted
from the benchmark by bench/calibrate.py so the 90 % intervals cover about 90 % of the truth.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

from roomscan.schema import Measurement

Z90 = 1.6449
CAL_PATH = Path(__file__).with_name("calibration.yaml")


@lru_cache
def calibration() -> dict:
    return yaml.safe_load(CAL_PATH.read_text())


def _terms(tier: str, kind: str) -> tuple[float, float, float, float]:
    c = calibration()[tier][kind]
    return float(c["inflate"]), float(c["abs"]), float(c["rel"]), float(c.get("scale", 1.0))


def measure(value: float | None, raw_sigma: float, tier: str, kind: str, unit: str = "m",
            lower_bound: float | None = None, note: str | None = None, ndigits: int = 4) -> Measurement:
    """kind: wall_length | ceiling_height | opening_width | opening_height | area | damage."""
    if value is None or not np.isfinite(value):
        return Measurement(value=None, ci90=None, sigma=None, unit=unit,
                           lower_bound=None if lower_bound is None else round(float(lower_bound), ndigits),
                           note=note)
    k, a, r, scale = _terms(tier, kind)
    s = scale * float(np.sqrt((k * raw_sigma) ** 2 + a ** 2 + (r * abs(value)) ** 2))
    lo, hi = value - Z90 * s, value + Z90 * s
    if unit == "m2" or kind in ("wall_length", "ceiling_height", "opening_width", "opening_height"):
        lo = max(lo, 0.0)
    return Measurement(value=round(float(value), ndigits), ci90=(round(lo, ndigits), round(hi, ndigits)),
                       sigma=round(s, ndigits + 1), unit=unit, note=note,
                       lower_bound=None if lower_bound is None else round(float(lower_bound), ndigits))
