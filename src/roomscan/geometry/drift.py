"""Accumulated-drift correction (placeholder: implemented in the next stage)."""
from __future__ import annotations

from roomscan.capture import PosedCapture


def correct_drift(cap: PosedCapture, use_cache: bool = True, key: tuple = (), progress: bool = True):
    return cap, {"method": "none", "enabled": False}
