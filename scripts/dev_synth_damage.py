"""Functional test of the damage stage with synthetic, multi-view-consistent damage.

The sample apartment is undamaged, so a brown stain of known size is painted onto one
wall in world space and re-projected into every RGB frame (via the frame's depth and
pose). The detector then has to find it, put it on the right surface and measure it.
Also reports what is detected with no damage painted (false positives).

usage: python scripts/dev_synth_damage.py <stray scan> [radius_m]
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from roomscan.capture import PosedCapture
from roomscan.damage.pipeline import assess_damage
from roomscan.frontends.lidar_stray import load_stray
from roomscan.geometry.layout import extract_layout
from roomscan.geometry.openings import detect_openings
from roomscan.geometry.pointcloud import backproject
from roomscan.pipeline import cached_fuse


def paint(cap: PosedCapture, layout, wall, room, u0: float, v0: float, radius: float) -> PosedCapture:
    fr = layout.frame
    d_w = (wall.end - wall.start) / wall.length
    rng = np.random.default_rng(0)
    phase = rng.uniform(0, 2 * np.pi, 4)

    def wrap(f):
        orig = f.rgb_fn

        def fn():
            img = orig()
            if img is None:
                return None
            d = f.depth_fn()
            h, w = img.shape[:2]
            z = cv2.resize(d, (w // 2, h // 2), interpolation=cv2.INTER_LINEAR)
            K = f.K_rgb.copy()
            K[:2] *= 0.5
            Pw = backproject(z, K).reshape(-1, 3) @ f.T_wc[:3, :3].T + f.T_wc[:3, 3]
            ab = fr.to_plan(Pw[:, [0, 2]])
            s = (ab - wall.start) @ wall.inward
            u = (ab - wall.start) @ d_w
            v = Pw[:, 1] - room.floor.value
            ang = np.arctan2(v - v0, u - u0)
            r = radius * (1 + 0.12 * np.sin(3 * ang + phase[0]) + 0.08 * np.sin(5 * ang + phase[1]))
            dist = np.hypot(u - u0, v - v0)
            m = (np.abs(s) < 0.05) & (dist < r) & (z.reshape(-1) > 0.2)
            if not m.any():
                return img
            alpha = (np.clip(1.2 - dist / r, 0, 1) * 0.75 * m).reshape(h // 2, w // 2).astype(np.float32)
            ring = (np.abs(dist / r - 0.9) < 0.08) & m  # darker tide mark at the edge
            alpha = np.maximum(alpha, 0.8 * ring.reshape(h // 2, w // 2))
            alpha = cv2.GaussianBlur(cv2.resize(alpha, (w, h)), (0, 0), 3)[..., None]
            stain = np.array([150, 105, 60], np.float32)
            return (img * (1 - alpha) + stain * alpha).astype(np.uint8)

        g = type(f)(**{**f.__dict__})
        g.rgb_fn = fn
        return g

    return PosedCapture(cap.tier, cap.name, [wrap(f) for f in cap.frames], dict(cap.meta))


def main(scan: str, radius: float = 0.25):
    cap = load_stray(Path(scan), stride=5)
    cloud = cached_fuse(cap, (str(Path(scan).resolve()), "lidar", 5, "off"), progress=False)
    layout = extract_layout(cloud)
    ops = detect_openings(cap, layout)
    t = time.time()
    dmg, flags, scope, warn = assess_damage(cap, layout, ops)
    print(f"no damage painted: {len(dmg)} regions (false positives) in {time.time() - t:.1f}s")
    for d in dmg:
        print("   FP", d.damage_class, d.surface_id, d.score, d.area.value)
    # widest well-observed wall
    room, wall = max(((r, w) for r in layout.rooms for w in r.walls if w.coverage > 0.8),
                     key=lambda rw: rw[1].length)
    u0, v0 = wall.length / 2, 1.3
    truth = np.pi * radius ** 2
    cap2 = paint(cap, layout, wall, room, u0, v0, radius)
    dmg, flags, scope, warn = assess_damage(cap2, layout, ops)
    print(f"painted stain on {wall.id}: true area {truth:.3f} m2, extent {2 * radius:.2f} m")
    hit = [d for d in dmg if d.surface_id == wall.id]
    for d in dmg:
        tag = "HIT" if d in hit else "other"
        print(f"   {tag} {d.damage_class} on {d.surface_id} score {d.score} area {d.area.value} "
              f"ci90 {d.area.ci90} extent {d.extent_u.value} x {d.extent_v.value} frames {len(d.evidence_frames)}")
    for f in flags:
        print("   FLAG", f.rule_id, f.risk, "-", f.rule)
    for s in scope:
        print("   SCOPE", s.code, s.surface_id, s.quantity.value, s.unit, "<-", s.source)
    out = Path("runs/dev_damage")
    out.mkdir(parents=True, exist_ok=True)
    for f in cap2.frames:
        if hit and f.index in hit[0].evidence_frames:
            cv2.imwrite(str(out / "painted_frame.jpg"), cv2.resize(f.rgb_fn(), (960, 720))[:, :, ::-1])
            break


if __name__ == "__main__":
    main(sys.argv[1], *(float(a) for a in sys.argv[2:]))
