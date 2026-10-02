"""Speed-ups that must not change results, low-light handling, and the mirror test.

Speed: the LiDAR colour video is decoded once (VideoReader.prefetch), and the opening and
surface-assignment geometry evaluates only the (sample, wall) pairs that can matter. Each
is checked here against the straightforward computation it replaced: same frames, same
grids, same assignments, bit for bit.

Mirror: a rendered room whose wall carries a mirror (LiDAR sees the reflected room behind
the wall plane) next to a real doorway into a second room: the doorway is an opening, the
mirror is not.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from roomscan.capture import Frame, PosedCapture
from roomscan.geometry import openings as O
from roomscan.geometry.layout import extract_layout
from roomscan.geometry.pointcloud import backproject, fuse_capture

from synth import box_room

# ---------------------------------------------------------------- a rendered capture

ROOM_A = np.array([[0.0, 0.0, 0.0], [4.0, 2.5, 3.0]])  # x, y (up), z; metres
MIRROR = dict(x=(1.5, 2.5), y=(0.5, 2.0))  # on the wall z = 3
DOOR = dict(z=(1.0, 1.9), y=(0.0, 2.0))  # in the wall x = 4, into room B
ROOM_B = np.array([[4.0, 0.0, 0.5], [6.5, 2.5, 2.5]])
K_D = np.array([[1600.0, 0, 960], [0, 1600.0, 720], [0, 0, 1]]) * np.array([[256 / 1920], [192 / 1440], [1]])


def _rot(yaw: float, pitch: float) -> np.ndarray:
    cy, sy, cp, sp = np.cos(yaw), np.sin(yaw), np.cos(pitch), np.sin(pitch)
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    return Ry @ Rx @ np.diag([1.0, -1.0, -1.0])  # OpenCV camera -> world, looking along -Z


def _exit(box: np.ndarray, P: np.ndarray, D: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Ray parameter where rays P + t D leave an axis-aligned box from inside, and the axis."""
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(D > 0, (box[1] - P) / D, (box[0] - P) / D)
    t = np.where(np.isfinite(t) & (t > 1e-9), t, np.inf)
    return t.min(-1), t.argmin(-1)


def render(C: np.ndarray, R: np.ndarray, mirror: bool = True, door: bool = True) -> np.ndarray:
    """z-depth as the LiDAR reports it: through the door the second room; on the mirror the
    length of the reflected path, so the point appears behind the wall plane."""
    u, v = np.meshgrid(np.arange(256) + 0.5, np.arange(192) + 0.5)
    rays = np.stack([(u - K_D[0, 2]) / K_D[0, 0], (v - K_D[1, 2]) / K_D[1, 1], np.ones_like(u)], -1).reshape(-1, 3)
    D = rays @ R.T  # camera z = 1, so the ray parameter is the z-depth
    P0 = np.broadcast_to(C, D.shape)
    t, ax = _exit(ROOM_A, P0, D)
    H = P0 + t[:, None] * D
    if mirror:
        m = (ax == 2) & (D[:, 2] > 0) & (H[:, 0] > MIRROR["x"][0]) & (H[:, 0] < MIRROR["x"][1]) \
            & (H[:, 1] > MIRROR["y"][0]) & (H[:, 1] < MIRROR["y"][1])
        Dm = D[m] * np.array([1.0, 1.0, -1.0])
        t[m] += _exit(ROOM_A, H[m], Dm)[0]
    if door:
        m = (ax == 0) & (D[:, 0] > 0) & (H[:, 2] > DOOR["z"][0]) & (H[:, 2] < DOOR["z"][1]) & (H[:, 1] < DOOR["y"][1])
        t[m] += _exit(ROOM_B, H[m], D[m])[0]
    return t.reshape(192, 256).astype(np.float32)


def rendered_capture(mirror: bool = True, door: bool = True) -> PosedCapture:
    frames = []
    for C in ([2.0, 1.4, 1.2], [1.2, 1.4, 0.9], [2.9, 1.4, 1.6]):
        for pitch in (0.0, -0.35, 0.35):
            for yaw in np.linspace(0, 2 * np.pi, 12, endpoint=False):
                R = _rot(yaw, pitch)
                T = np.eye(4)
                T[:3, :3], T[:3, 3] = R, C
                d = render(np.array(C), R, mirror, door)
                frames.append(Frame(index=len(frames), timestamp=0.1 * len(frames), T_wc=T, K=K_D.copy(),
                                    depth_fn=(lambda d=d: d), K_rgb=np.array([[1600.0, 0, 960], [0, 1600.0, 720],
                                                                            [0, 0, 1]])))
    return PosedCapture("lidar", "rendered", frames)


@pytest.fixture(scope="module")
def scene():
    cap = rendered_capture()
    # plan from a clean cloud of room A with the doorway cut out (wall 1 is the x = 4 side)
    layout = extract_layout(box_room(4.0, 3.0, 2.5, origin=(0.0, 0.0), floor_y=0.0,
                                     door=(1, DOOR["z"][0], DOOR["z"][1] - DOOR["z"][0], DOOR["y"][1])))
    # what the scan saw, reflections included (sparse sampling: only 6 cm voxels are looked up)
    cloud = fuse_capture(cap, voxel=0.02, pixel_stride=4, progress=False)
    return cap, layout, cloud


# ---------------------------------------------------------------- mirror

def test_mirror_is_not_an_opening_but_the_doorway_is(scene):
    cap, layout, cloud = scene
    assert len(layout.rooms) == 1
    walls = {w.id: w for w in layout.rooms[0].walls}
    fr = layout.frame

    def wall_at(axis: int, value: float) -> str:  # the wall lying on world x = value (0) or z = value (2)
        for wid, w in walls.items():
            ends = fr.to_world(np.stack([w.start, w.end]))[:, 0 if axis == 0 else 1]
            if np.allclose(ends, value, atol=0.05):
                return wid
        raise AssertionError(f"no wall at {'xz'[axis // 2]} = {value}")

    mirror_wall, door_wall = wall_at(2, 3.0), wall_at(0, 4.0)
    mirrors: list = []
    ops = O.detect_openings(cap, layout, cloud=cloud, mirrors=mirrors)
    # without the fused cloud there is no mirror test: the mirror shows up as a window
    unchecked = O.detect_openings(cap, layout)
    assert any(o.wall_id == mirror_wall and o.kind == "window" and abs(o.width - 1.0) < 0.1 for o in unchecked)
    assert [m["wall_id"] for m in mirrors] == [mirror_wall] and abs(mirrors[0]["width"] - 1.0) < 0.1
    assert not any(o.wall_id == mirror_wall for o in ops)
    door = [o for o in ops if o.wall_id == door_wall]
    assert len(door) == 1 and door[0].kind == "door" and abs(door[0].width - 0.9) < 0.1


# ---------------------------------------------------------------- same results, less work

def test_prefetched_frames_are_the_frames_a_direct_decode_returns(tmp_path, monkeypatch):
    from roomscan.damage.detect import colour_prefetched
    from roomscan.frontends.lidar_stray import VideoReader
    rng = np.random.default_rng(0)
    clip = tmp_path / "rgb.mp4"
    vw = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 30, (160, 120))
    for _ in range(40):
        vw.write(rng.integers(0, 255, (120, 160, 3), dtype=np.uint8))
    vw.release()
    order = [12, 3, 30, 12, 7, 39, 3, 20]  # forward, backward and repeated requests
    plain = VideoReader(clip)
    want = [plain.get(i) for i in order]  # decodes from the start at every step back
    reader = VideoReader(clip)
    opens = []
    real_open = reader._open
    monkeypatch.setattr(reader, "_open", lambda: opens.append(1) or real_open())
    frames = [Frame(index=i, timestamp=float(i), T_wc=np.eye(4), K=np.eye(3), depth_fn=lambda: None,
                    rgb_fn=reader.frame(i)) for i in range(40)]
    with colour_prefetched([frames[i] for i in order]):
        assert sorted(reader.ahead) == sorted(set(order))
        got = [frames[i].rgb_fn() for i in order]
    assert len(opens) == 1  # one forward pass served every request
    assert all(a is not None and np.array_equal(a, b) for a, b in zip(want, got))
    assert not reader.ahead  # nothing held after the block


def _dense_wall_evidence(cap, layout, frame_stride=2, pixel_stride=3, max_depth=10.0):
    """The opening evidence as computed before: every (sample, wall) pair in full (N, W)
    arrays. Returns per wall (hit grid, pass grid, mirror-test samples)."""
    fr = layout.frame
    accs = [O._WallAcc(r, w, min(r.height if r.height else 2.4, 3.5)) for r in layout.rooms for w in r.walls
            if w.length >= 0.5]
    S, D, NO = (np.array([a.wall.start for a in accs]), np.array([a.d for a in accs]),
                np.array([a.n_out for a in accs]))
    L, F, H = (np.array([a.wall.length for a in accs]), np.array([a.room.floor.value for a in accs]),
               np.array([a.height for a in accs]))
    for f in cap.frames[::frame_stride]:
        d = f.depth_fn()
        P = backproject(d, f.K)[::pixel_stride, ::pixel_stride].reshape(-1, 3)
        dd = d[::pixel_stride, ::pixel_stride].reshape(-1)
        ok = (dd > 0.2) & (dd < max_depth)
        if ok.sum() < 50:
            continue
        Pw = P[ok] @ f.T_wc[:3, :3].T + f.T_wc[:3, 3]
        C = f.T_wc[:3, 3]
        pab, cab = fr.to_plan(Pw[:, [0, 2]]), fr.to_plan(C[None, [0, 2]])[0]
        sP = ((pab[:, None, :] - S[None]) * NO[None]).sum(-1)
        sC = ((cab[None, :] - S) * NO).sum(-1)
        uP = ((pab[:, None, :] - S[None]) * D[None]).sum(-1)
        hP = Pw[:, 1:2] - F[None]
        hitm = (np.abs(sP) < 0.04) & (uP > 0) & (uP < L[None]) & (hP > 0) & (hP < H[None])
        crossm = (sC < -0.2)[None] & (sP > 0.15)
        t = np.where(crossm, sC[None] / np.where(crossm, sC[None] - sP, 1), 0)
        X = cab[None, None, :] + t[..., None] * (pab[:, None, :] - cab[None, None, :])
        uX = ((X - S[None]) * D[None]).sum(-1)
        hX = (C[1] + t * (Pw[:, 1:2] - C[1])) - F[None]
        crossm &= (uX > 0) & (uX < L[None]) & (hX > 0) & (hX < H[None])
        for wi, a in enumerate(accs):
            m = hitm[:, wi]
            np.add.at(a.hit, (np.clip((hP[m, wi] / O.HB).astype(int), 0, a.nh - 1),
                              np.clip((uP[m, wi] / O.UB).astype(int), 0, a.nu - 1)), 1)
            m = crossm[:, wi]
            np.add.at(a.pas, (np.clip((hX[m, wi] / O.HB).astype(int), 0, a.nh - 1),
                              np.clip((uX[m, wi] / O.UB).astype(int), 0, a.nu - 1)), 1)
            if m.any():
                sel = np.where(m)[0][::7]
                a.beyond.append(np.concatenate([np.stack([uX[sel, wi], hX[sel, wi]], 1), Pw[sel]], 1))
    return [(a.hit, a.pas, np.concatenate(a.beyond) if a.beyond else np.zeros((0, 5))) for a in accs]


def test_opening_evidence_is_bit_identical_to_the_dense_computation(scene, monkeypatch):
    cap, layout, cloud = scene
    seen = []
    extract = O._extract

    def spy(a, start):
        seen.append((a.hit.copy(), a.pas.copy(), np.concatenate(a.beyond) if a.beyond else np.zeros((0, 5))))
        return extract(a, start)

    monkeypatch.setattr(O, "_extract", spy)
    O.detect_openings(cap, layout, cloud=cloud)
    ref = _dense_wall_evidence(cap, layout)
    assert len(seen) == len(ref) == 4
    for (h, p, b), (h0, p0, b0) in zip(seen, ref):
        assert h.dtype == h0.dtype == np.float32 and np.array_equal(h, h0) and np.array_equal(p, p0)
        assert np.array_equal(b, b0)
    assert sum(float(p.sum()) for _, p, _ in ref) > 1000  # the rays through door and mirror were counted


def test_voxel_lookup_matches_a_set_of_neighbourhood_keys():
    rng = np.random.default_rng(1)
    pts = rng.uniform(-1, 1, (3000, 3)).astype(np.float32)
    keys = np.floor(pts / 0.06).astype(np.int64)
    old = set(map(tuple, np.concatenate([keys + o for o in O._NEIGHBOURS])))  # the former set
    q = np.floor(rng.uniform(-1.3, 1.3, (5000, 3)) / 0.06).astype(np.int64)
    got = O._VoxelSet(pts, 0.06).contains(q)
    assert np.array_equal(got, np.array([tuple(k) in old for k in q]))
    assert 0.1 < got.mean() < 0.9
    assert not O._VoxelSet(np.zeros((0, 3), np.float32), 0.06).contains(q).any()


def _dense_assign(sidx, Pw, cam):
    """SurfaceIndex.assign as it was: the wall choice from full (N, W) arrays."""
    fr = sidx.layout.frame
    ab, cab = fr.to_plan(Pw[:, [0, 2]]), fr.to_plan(cam[None, [0, 2]])[0]
    r, c = fr.to_cell(ab)
    ok = (r >= 0) & (r < fr.shape[0]) & (c >= 0) & (c < fr.shape[1])
    lab = np.zeros(len(Pw), int)
    lab[ok] = sidx.layout.labels[r[ok], c[ok]]
    idx, table = np.full(len(Pw), -1), []
    facing = ((cab[None] - sidx.S) * sidx.IN).sum(1) > 0.05
    s = ((ab[:, None, :] - sidx.S[None]) * sidx.IN[None]).sum(-1)
    u = ((ab[:, None, :] - sidx.S[None]) * sidx.D[None]).sum(-1)
    hit = (np.abs(s) < sidx.tol) & (u > 0) & (u < sidx.L[None]) & facing[None]
    cost = np.where(hit, np.abs(s), np.inf)
    best, has = cost.argmin(1), np.isfinite(cost.min(1))
    for wi in np.unique(best[has]):
        room, w = sidx.walls[wi]
        h = Pw[:, 1] - room.floor.value
        m = has & (best == wi) & (h > 0.08) & (h < (room.height if room.height else 3.0) - 0.05)
        if m.any():
            idx[m] = len(table)
            table.append((w.id, room.id, "wall"))
    for lid, room in sidx.rooms.items():
        m = (lab == lid) & (idx < 0)
        h = Pw[:, 1] - room.floor.value
        if (m & (np.abs(h) < sidx.tol)).any():
            idx[m & (np.abs(h) < sidx.tol)] = len(table)
            table.append((f"{room.id}_floor", room.id, "floor"))
        if room.height and room.ceiling_source == "ceiling_plane" and (m & (np.abs(h - room.height) < sidx.tol)).any():
            idx[m & (np.abs(h - room.height) < sidx.tol)] = len(table)
            table.append((f"{room.id}_ceiling", room.id, "ceiling"))
    return idx, table


def test_surface_assignment_matches_the_dense_computation(scene):
    from roomscan.damage.detect import SurfaceIndex
    cap, layout, _ = scene
    n_wall = 0
    for tol in (0.06, 0.20):  # LiDAR, video / photos
        sidx = SurfaceIndex(layout, tol)
        for f in cap.frames[::7]:
            d = f.depth_fn()
            Pw = (backproject(d, f.K).reshape(-1, 3) @ f.T_wc[:3, :3].T + f.T_wc[:3, 3])[d.reshape(-1) > 0.2]
            idx, table, _ = sidx.assign(Pw, f.T_wc[:3, 3])
            idx0, table0 = _dense_assign(sidx, Pw, f.T_wc[:3, 3])
            assert np.array_equal(idx, idx0) and [row[:3] for row in table] == table0
            n_wall += sum(row[2] == "wall" for row in table)
    assert n_wall > 20
