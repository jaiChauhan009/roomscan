"""The damage outline inside a positive region: a faint stain's body joins its tide mark,
against a wall with a lighting gradient (detect._outline, detect._background)."""
import cv2
import numpy as np

from roomscan.damage.detect import _background, _outline


def _wall_with_stain(cx=480, cy=330, r=60):
    H, W = 720, 960
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    shade = 0.75 + 0.25 * xx / W  # light falling off across the wall
    img = np.dstack([215 * shade, 200 * shade, 180 * shade])
    ang = np.arctan2(yy - cy, xx - cx)
    rr = r * (1 + 0.12 * np.sin(3 * ang + 1.0) + 0.08 * np.sin(5 * ang + 2.0))
    rel = np.hypot(xx - cx, yy - cy) / rr
    a = np.clip(1.2 - rel, 0, 1) * 0.35 * (rel < 1)  # faint body
    a = np.maximum(a, 0.6 * ((np.abs(rel - 0.9) < 0.08) & (rel < 1)))  # tide mark
    a = cv2.GaussianBlur(a.astype(np.float32), (0, 0), 1.5)[..., None]
    img = img * (1 - a) + np.array([150, 105, 60], np.float32) * a
    box = xx[rel < 1].min(), xx[rel < 1].max(), yy[rel < 1].min(), yy[rel < 1].max()
    return np.clip(img, 0, 255).astype(np.uint8), box


def test_faint_stain_body_joins_its_tide_mark():
    img, (x0, x1, y0, y1) = _wall_with_stain()
    lab = cv2.cvtColor(cv2.GaussianBlur(img, (0, 0), 2), cv2.COLOR_RGB2LAB).astype(np.float32)
    region = np.zeros(img.shape[:2], bool)
    region[218:442, 368:592] = True  # the positive 224 tile around the stain
    mask = _outline(lab, region, _background(img))
    n, cc, st, _ = cv2.connectedComponentsWithStats(mask)
    assert n == 2  # one outline
    w, h = st[1, cv2.CC_STAT_WIDTH], st[1, cv2.CC_STAT_HEIGHT]
    assert abs(w / (x1 - x0 + 1) - 1) < 0.15 and abs(h / (y1 - y0 + 1) - 1) < 0.15
    assert mask[int((y0 + y1) / 2), int((x0 + x1) / 2)]  # the body inside the ring is filled


def test_plain_wall_with_a_lighting_gradient_has_no_outline():
    img, _ = _wall_with_stain(r=1e-3)
    lab = cv2.cvtColor(cv2.GaussianBlur(img, (0, 0), 2), cv2.COLOR_RGB2LAB).astype(np.float32)
    region = np.zeros(img.shape[:2], bool)
    region[100:600, 100:900] = True
    assert not _outline(lab, region, _background(img)).any()
