"""Shared helpers for round 4: the laser rooms and guard captures, and their fused clouds.

The cloud is the one the pipeline fuses (same loader, drift correction and cache key as
roomscan.pipeline.run_posed), so with a warm .cache it loads in seconds. One cloud at a time.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(ROOT / "bench")]
os.chdir(ROOT)  # the pipeline's cache is ./.cache

LASER = ["arkit_42446532", "arkit_44358446", "arkit_47332890", "arkit_47331988"]
GUARDS = ["apt_lidar_a", "apt_lidar_b", "room_lidar", "own_lidar_1", "own_lidar_2", "own_lidar_3"]


def manifest() -> dict:
    return {c["name"]: c for c in yaml.safe_load((ROOT / "bench" / "manifest.yaml").read_text())["captures"]}


def data_root() -> Path:
    man = yaml.safe_load((ROOT / "bench" / "manifest.yaml").read_text())
    return Path(os.environ.get("ROOMSCAN_DATA", ROOT / man["data_root"]))


def capture_path(name: str) -> Path:
    from roomscan.pipeline import prepare_input
    return prepare_input(data_root() / manifest()[name]["path"])


def truth(name: str) -> dict | None:
    f = ROOT / "bench" / "ground_truth" / f"{name}.yaml"
    return yaml.safe_load(f.read_text()) if f.exists() else None


def load_cloud(name: str, stride: int = 5, drift: str = "loop"):
    from roomscan.frontends.lidar_stray import load_stray
    from roomscan.geometry.drift import correct_drift
    from roomscan.pipeline import DRIFT_MODES, cached_fuse
    path = capture_path(name)
    cap = load_stray(path, stride=stride)
    key = (str(path.resolve()), "lidar", stride)
    cap, _ = correct_drift(cap, use_cache=True, key=key, progress=False, **DRIFT_MODES[drift])
    return cached_fuse(cap, key + (drift,), use_cache=True, progress=False)
