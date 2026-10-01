"""Monocular metric depth (Depth Anything V2, metric indoor, ViT-S; Apache-2.0 weights).

Disclosure: pretrained weights `depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf`
from the Hugging Face hub, fetched by scripts/fetch_weights.py into the local HF cache.
Runs on CPU (~1 s per frame at 518 px on a laptop).

Measured on the sample LiDAR scans: per-frame absolute scale is unreliable (0.4x-3.4x),
but the median over many frames is stable at ~1.14x LiDAR depth. Callers therefore only
use relative shape per frame and a many-frame median for scale, corrected by
DEPTH_SCALE_BIAS (refit by bench/calibrate.py).
"""
from __future__ import annotations

import os
from functools import lru_cache

import numpy as np

MODEL_ID = "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf"
DEPTH_SCALE_BIAS = 1.137  # median(pred / lidar) over the sample scans
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


@lru_cache(maxsize=1)
def _model():
    import torch
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation

    torch.set_num_threads(max(1, os.cpu_count() or 4))
    # 392 px short side instead of 518: ~2x faster on CPU; walls are large smooth surfaces
    proc = AutoImageProcessor.from_pretrained(MODEL_ID, size={"height": 392, "width": 392})
    model = AutoModelForDepthEstimation.from_pretrained(MODEL_ID).eval()
    return proc, model


def predict_depth(images: list[np.ndarray], batch: int = 4, progress: bool = False) -> list[np.ndarray]:
    """RGB uint8 images -> metric depth maps (m) at the model's output resolution."""
    import torch
    from tqdm import tqdm

    proc, model = _model()
    out = []
    for i in tqdm(range(0, len(images), batch), desc="depth", disable=not progress):
        chunk = images[i:i + batch]
        with torch.no_grad():
            inp = proc(images=chunk, return_tensors="pt")
            pd = model(**inp).predicted_depth
        out += [d.numpy().astype(np.float32) for d in pd]
    return out
