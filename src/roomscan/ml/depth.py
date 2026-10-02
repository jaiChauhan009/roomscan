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


DEPTH_PX = 392  # model input short side (the patch size 14 divides it)


@lru_cache(maxsize=2)
def _model(px: int = DEPTH_PX):
    import torch
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation

    from roomscan.ml.hub import pretrained

    torch.set_num_threads(max(1, os.cpu_count() or 4))
    # 392 px short side instead of 518: ~2x faster on CPU; walls are large smooth surfaces
    proc = pretrained(AutoImageProcessor, MODEL_ID, size={"height": px, "width": px})
    model = pretrained(AutoModelForDepthEstimation, MODEL_ID).eval()
    return proc, model


def predict_depth(images: list[np.ndarray], batch: int = 4, progress: bool = False,
                  px: int | None = None) -> list[np.ndarray]:
    """RGB uint8 images -> metric depth maps (m) at the model's output resolution.
    px: model input short side (default DEPTH_PX); a multiple of 14."""
    import torch
    from tqdm import tqdm

    proc, model = _model() if px in (None, DEPTH_PX) else _model(px)
    # a batch must hold one image shape: a phone's photos mix portrait and landscape, and the
    # processor keeps the aspect ratio, so mixed shapes cannot be stacked into one tensor
    groups: dict[tuple, list[int]] = {}
    for i, im in enumerate(images):
        groups.setdefault(tuple(im.shape[:2]), []).append(i)
    batches = [idx[k:k + batch] for idx in groups.values() for k in range(0, len(idx), batch)]
    out: list = [None] * len(images)
    for sel in tqdm(batches, desc="depth", disable=not progress):
        with torch.inference_mode():
            inp = proc(images=[images[j] for j in sel], return_tensors="pt")
            pd = model(**inp).predicted_depth
        for j, d in zip(sel, pd):
            out[j] = d.numpy().astype(np.float32)
    return out
