"""Common in-memory representation every input tier is converted into.

All front ends (LiDAR, video, photos) produce a ``PosedCapture``: a list of frames,
each with an RGB loader, a metric depth map, intrinsics at depth resolution and a
camera-to-world pose. World frame convention: +Y is up (gravity aligned), metres.
Camera frame convention: OpenCV (x right, y down, z forward).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np


@dataclass
class Frame:
    index: int
    timestamp: float
    T_wc: np.ndarray  # 4x4 camera-to-world
    K: np.ndarray  # 3x3 intrinsics at depth resolution
    depth_fn: Callable[[], np.ndarray]  # HxW float32 metres (0 = invalid)
    rgb_fn: Callable[[], np.ndarray] | None = None  # HxWx3 uint8 RGB (any resolution)
    K_rgb: np.ndarray | None = None  # 3x3 intrinsics at RGB resolution
    conf_fn: Callable[[], np.ndarray] | None = None  # HxW uint8, 0..2 (2 = high)
    depth_sigma_rel: float = 0.01  # 1-sigma relative depth noise for this source


@dataclass
class PosedCapture:
    tier: str  # "lidar" | "video" | "photo"
    name: str
    frames: list[Frame]
    meta: dict = field(default_factory=dict)

    def positions(self) -> np.ndarray:
        return np.array([f.T_wc[:3, 3] for f in self.frames])
