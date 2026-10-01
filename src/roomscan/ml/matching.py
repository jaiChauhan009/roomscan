"""Learned image matching (EfficientLoFTR, Apache-2.0) for low-texture indoor pairs.

Disclosure: pretrained weights `zju-community/efficientloftr` from the Hugging Face hub.
Semi-dense matcher: finds correspondences on plain walls where SIFT finds none. About
2-4 s per pair on a laptop CPU, so callers keep the number of pairs small.
"""
from __future__ import annotations

import os
from functools import lru_cache

import numpy as np

MODEL_ID = "zju-community/efficientloftr"
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


@lru_cache(maxsize=1)
def _model():
    from transformers import AutoImageProcessor, AutoModelForKeypointMatching

    return AutoImageProcessor.from_pretrained(MODEL_ID), AutoModelForKeypointMatching.from_pretrained(MODEL_ID).eval()


def match_pair(img_a: np.ndarray, img_b: np.ndarray, threshold: float = 0.3,
               long_side: int = 640) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """RGB uint8 images -> matched pixel coordinates in each image and match scores."""
    import torch
    from PIL import Image

    proc, model = _model()
    a, b = Image.fromarray(img_a), Image.fromarray(img_b)
    portrait = a.height > a.width
    short = int(long_side * 0.75)
    size = {"height": long_side, "width": short} if portrait else {"height": short, "width": long_side}
    inp = proc([[a, b]], return_tensors="pt", size=size)
    with torch.no_grad():
        out = model(**inp)
    res = proc.post_process_keypoint_matching(out, [[(a.height, a.width), (b.height, b.width)]],
                                              threshold=threshold)[0]
    return (res["keypoints0"].numpy().astype(np.float32), res["keypoints1"].numpy().astype(np.float32),
            res["matching_scores"].numpy())
