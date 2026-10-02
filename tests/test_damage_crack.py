"""A crack's outline follows the thin dark line, not the colour blob around it."""
import cv2
import numpy as np

from roomscan.damage.detect import _crack_outline, crack_lines


def _wall(seed=0, h=240, w=320):
    rng = np.random.default_rng(seed)
    g = 180 + rng.normal(0, 3, (h, w)) + cv2.resize(rng.normal(0, 4, (h // 20, w // 20)), (w, h))  # plaster
    return g


def _crack(img, seed=1):
    rng = np.random.default_rng(seed)
    t = np.linspace(0, 1, 24)
    x = 60 + 200 * t
    y = 180 - 120 * t + np.cumsum(rng.normal(0, 3, 24))
    pts = np.stack([x, y], 1).astype(np.int32)
    line = np.zeros(img.shape, np.uint8)
    cv2.polylines(line, [pts], False, 1, 2)
    return np.where(line > 0, 70.0, img), line.astype(bool)


def _u8(g):
    return np.clip(cv2.GaussianBlur(g, (0, 0), 0.7), 0, 255).astype(np.uint8)


def test_drawn_crack_on_textured_wall_is_found():
    g, line = _crack(_wall())
    m = crack_lines(_u8(g), np.ones(g.shape, bool))
    near = cv2.dilate(line.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    assert (m & near).sum() >= 0.6 * line.sum()  # most of the crack
    assert (m & ~near).sum() < 0.002 * m.size  # hardly anything on the bare plaster


def test_edges_and_bands_are_not_lines():
    on = np.ones((240, 320), bool)
    door = _wall()
    door[:, 160:] -= 40  # a door frame's edge: a step, not a valley
    assert crack_lines(_u8(door), on).sum() < 30
    skirting = _wall()
    skirting[200:, :] -= 35  # a skirting board, 40 px tall
    assert crack_lines(_u8(skirting), on).sum() < 30


def test_outline_is_cut_to_the_line():
    g, line = _crack(_wall())
    g[150:200, 230:280] -= 60  # a dark fixture at the crack's end, inside the colour outline
    mask = (cv2.dilate(line.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0)
    mask[150:200, 230:280] = True
    z = np.full(g.shape, 2.0, np.float32)
    out = _crack_outline(mask.astype(np.uint8), _u8(g), np.ones(g.shape, bool), z, fx=300.0)
    assert out[150:200, 230:280].sum() < 0.1 * 50 * 50  # most of the fixture is gone
    near = cv2.dilate(line.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    assert out[near].sum() >= 0.8 * line.sum()
    # no thin line in an outline (a stain-like blob): left as it is
    blob = np.zeros(g.shape, np.uint8)
    cv2.circle(blob, (160, 120), 40, 1, -1)
    flat = _wall(2)
    flat[blob > 0] -= 30
    out = _crack_outline(blob, _u8(flat), np.ones(g.shape, bool), z, fx=300.0)
    assert (out == blob).all()
