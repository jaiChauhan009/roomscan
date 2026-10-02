"""Depth for a photo set that mixes portrait and landscape (a phone's normal output)."""
import numpy as np

import roomscan.ml.depth as depth


class _Proc:
    def __call__(self, images, return_tensors):
        import torch
        shapes = {im.shape for im in images}
        assert len(shapes) == 1, f"mixed shapes in one batch: {shapes}"  # what the real processor fails on
        return {"pixel_values": torch.tensor(np.stack([im.mean(-1) for im in images]))}


class _Model:
    def __call__(self, pixel_values):
        class R:
            predicted_depth = pixel_values + 1.0
        return R()


def test_mixed_orientations_keep_their_order(monkeypatch):
    monkeypatch.setattr(depth, "_model", lambda: (_Proc(), _Model()))
    rng = np.random.default_rng(0)
    shapes = [(8, 6), (8, 6), (6, 8), (8, 6), (6, 8), (8, 6), (8, 6)]  # portrait, landscape mixed
    imgs = [rng.integers(0, 255, s + (3,)).astype(np.uint8) for s in shapes]
    out = depth.predict_depth(imgs, batch=2)
    assert [o.shape for o in out] == shapes
    for im, o in zip(imgs, out):
        assert np.allclose(o, im.mean(-1) + 1.0)
