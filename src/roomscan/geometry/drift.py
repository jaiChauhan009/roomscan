"""Accumulated-drift correction for multi-room captures.

ARKit visual-inertial odometry drifts slowly (a few cm per 10 m, mostly in heading).
Two complementary corrections, both switchable for the ablation:

1. Pose-graph loop closure. The trajectory is cut into submaps (~3 s each). Consecutive
   submaps are linked by the ARKit relative pose; submaps that revisit the same place
   are linked by point-to-plane ICP. The graph is optimised (Open3D, Levenberg-Marquardt
   with robust edge pruning) and every frame is moved with its submap. Roll/pitch
   changes are discarded afterwards: ARKit gravity is reliable, drift is in yaw + xyz.
2. Plane-anchored heading correction (optional, off by default: on the sample scans
   3 s submaps hold too little wall evidence and it blurs walls; see ablation). Indoor walls are mostly mutually orthogonal; each
   submap's dominant wall direction is compared with the global one and small heading
   errors (< 4 degrees) are removed, smoothed along the trajectory.
"""
from __future__ import annotations

import copy

import numpy as np
import open3d as o3d

from roomscan.capture import Frame, PosedCapture
from roomscan.geometry.pointcloud import frame_points, voxel_fuse

SUBMAP_S = 3.0  # seconds per submap
LOOP_RADIUS = 2.0  # m, centroid distance for loop candidates
MIN_GAP = 4  # submaps; skip near-consecutive pairs


def _yaw_of(R: np.ndarray) -> float:
    # heading of the camera's forward (z) axis projected on the floor
    f = R[:, 2]
    return float(np.arctan2(f[0], f[2]))


def _rot_y(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _submaps(cap: PosedCapture) -> list[list[int]]:
    ts = np.array([f.timestamp for f in cap.frames])
    groups, cur, t0 = [], [], ts[0]
    for i, t in enumerate(ts):
        if t - t0 > SUBMAP_S and cur:
            groups.append(cur)
            cur, t0 = [], t
        cur.append(i)
    if cur:
        groups.append(cur)
    return groups


def _submap_cloud(cap: PosedCapture, idx: list[int], T_anchor_inv: np.ndarray):
    ps, ns, ws = [], [], []
    for i in idx[::2]:
        p, n, d = frame_points(cap.frames[i], pixel_stride=3, max_depth=4.0)
        ps.append(p); ns.append(n); ws.append(np.ones(len(p), np.float32))
    if not ps:
        return None
    c = voxel_fuse(np.concatenate(ps), np.concatenate(ns), np.concatenate(ws), 0.04)
    pts = c.points @ T_anchor_inv[:3, :3].T + T_anchor_inv[:3, 3]
    nrm = c.normals @ T_anchor_inv[:3, :3].T
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts.astype(np.float64)))
    pc.normals = o3d.utility.Vector3dVector(nrm.astype(np.float64))
    return pc, c


def correct_drift(cap: PosedCapture, use_cache: bool = True, key: tuple = (), progress: bool = True,
                  loop_closure: bool = True, heading: bool = False):
    """Return (corrected capture, info dict). See module docstring."""
    groups = _submaps(cap)
    method = "+".join(m for m, on in [("pose_graph_loop_closure", loop_closure),
                                       ("plane_anchored_heading", heading)] if on)
    info = {"enabled": True, "method": method,
            "n_submaps": len(groups), "loop_edges": 0, "heading_corrections_deg": []}
    if len(groups) < 2 * MIN_GAP:
        info.update(method="none (capture too short for drift to matter)", enabled=False)
        return cap, info
    anchors = [cap.frames[g[0]].T_wc for g in groups]
    clouds = []
    for g, Ta in zip(groups, anchors):
        clouds.append(_submap_cloud(cap, g, np.linalg.inv(Ta)))
    centroids = np.array([(Ta[:3, :3] @ np.asarray(c[0].points).mean(0) + Ta[:3, 3]) if c else Ta[:3, 3]
                          for c, Ta in zip(clouds, anchors)])

    new_anchors = [a.copy() for a in anchors]
    if loop_closure:
        new_anchors, n_loops, residuals = _pose_graph(anchors, clouds, centroids)
        info["loop_edges"] = n_loops
        info["loop_residual_m_before"] = residuals[0]
        info["loop_residual_m_after"] = residuals[1]

    if heading:
        new_anchors, corr = _heading_snap(new_anchors, clouds, centroids)
        info["heading_corrections_deg"] = [round(float(np.degrees(c)), 3) for c in corr]

    # apply per-submap correction to all frames in it
    frames: list[Frame] = []
    shifts = []
    for g, Ta, Tn in zip(groups, anchors, new_anchors):
        corr = Tn @ np.linalg.inv(Ta)
        shifts.append(float(np.linalg.norm(corr[:3, 3] + corr[:3, :3] @ Ta[:3, 3] - Ta[:3, 3])))
        for i in g:
            f = copy.copy(cap.frames[i])
            f.T_wc = corr @ f.T_wc
            frames.append(f)
    info["max_submap_shift_m"] = round(max(shifts), 4)
    info["mean_submap_shift_m"] = round(float(np.mean(shifts)), 4)
    return PosedCapture(tier=cap.tier, name=cap.name, frames=frames, meta=dict(cap.meta, drift=info)), info


def _pose_graph(anchors, clouds, centroids):
    reg = o3d.pipelines.registration
    pg = reg.PoseGraph()
    for Ta in anchors:
        pg.nodes.append(reg.PoseGraphNode(Ta))
    n = len(anchors)
    info_odo = np.diag([4e4, 4e4, 4e4, 1e4, 1e4, 1e4])  # ARKit relative pose: very trusted short-term
    for i in range(n - 1):
        rel = np.linalg.inv(anchors[i + 1]) @ anchors[i]  # maps i -> i+1 frame
        pg.edges.append(reg.PoseGraphEdge(i, i + 1, rel, info_odo, uncertain=False))

    loops, before, after = 0, [], []
    est = reg.TransformationEstimationPointToPlane()
    crit = reg.ICPConvergenceCriteria(max_iteration=30)
    for i in range(n):
        if clouds[i] is None:
            continue
        for j in range(i + MIN_GAP, n):
            if clouds[j] is None or np.linalg.norm(centroids[i] - centroids[j]) > LOOP_RADIUS:
                continue
            init = np.linalg.inv(anchors[j]) @ anchors[i]  # source i into target j
            src, tgt = clouds[i][0], clouds[j][0]
            r1 = reg.registration_icp(src, tgt, 0.10, init, est, crit)
            r2 = reg.registration_icp(src, tgt, 0.03, r1.transformation, est, crit)
            if r2.fitness < 0.35 or r2.inlier_rmse > 0.02:
                continue
            delta = np.linalg.inv(init) @ r2.transformation
            dt = np.linalg.norm(delta[:3, 3])
            da = np.degrees(np.arccos(np.clip((np.trace(delta[:3, :3]) - 1) / 2, -1, 1)))
            if dt > 0.5 or da > 8:
                continue
            inf = reg.get_information_matrix_from_point_clouds(src, tgt, 0.03, r2.transformation)
            pg.edges.append(reg.PoseGraphEdge(i, j, r2.transformation, inf, uncertain=True))
            before.append(dt)
            loops += 1
    if loops == 0:
        return [a.copy() for a in anchors], 0, (0.0, 0.0)
    opt = reg.GlobalOptimizationOption(max_correspondence_distance=0.03, edge_prune_threshold=0.25,
                                       preference_loop_closure=1.0, reference_node=0)
    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
    reg.global_optimization(pg, reg.GlobalOptimizationLevenbergMarquardt(),
                            reg.GlobalOptimizationConvergenceCriteria(), opt)
    new = []
    for Ta, node in zip(anchors, pg.nodes):
        T = np.asarray(node.pose).copy()
        # keep ARKit gravity: only accept the heading part of the rotation change
        dR = T[:3, :3] @ Ta[:3, :3].T
        yaw = float(np.arctan2(dR[0, 2], dR[2, 2]))
        T[:3, :3] = _rot_y(yaw) @ Ta[:3, :3]
        new.append(T)
    for e in pg.edges:
        if e.uncertain:
            Ti, Tj = new[e.source_node_id], new[e.target_node_id]
            pred = np.linalg.inv(Tj) @ Ti
            after.append(float(np.linalg.norm((np.linalg.inv(pred) @ e.transformation)[:3, 3])))
    return new, loops, (round(float(np.median(before)), 4), round(float(np.median(after)) if after else 0.0, 4))


def _heading_snap(anchors, clouds, centroids, max_deg: float = 4.0):
    """Remove small per-submap heading errors relative to the global Manhattan frame."""
    th_all, w_all, per = [], [], []
    for Ta, c in zip(anchors, clouds):
        if c is None:
            per.append(None)
            continue
        n = np.asarray(c[0].normals) @ Ta[:3, :3].T
        m = np.abs(n[:, 1]) < 0.2
        if m.sum() < 300:
            per.append(None)
            continue
        th = np.arctan2(n[m, 2], n[m, 0])
        z = np.exp(1j * 4 * th)
        per.append(z.mean())
        th_all.append(th)
    if not th_all:
        return anchors, []
    g = np.angle(np.exp(1j * 4 * np.concatenate(th_all)).mean()) / 4
    deltas = np.array([np.nan if p is None or abs(p) < 0.3 else np.angle(p * np.exp(-1j * 4 * g)) / 4
                       for p in per])
    deltas[np.abs(deltas) > np.deg2rad(max_deg)] = np.nan
    # smooth along the trajectory (heading drift is slow); fill gaps by interpolation
    idx = np.arange(len(deltas))
    good = np.isfinite(deltas)
    if good.sum() < 3:
        return anchors, []
    d = np.interp(idx, idx[good], deltas[good])
    k = np.ones(5) / 5
    d = np.convolve(np.pad(d, 2, mode="edge"), k, mode="valid")
    d -= np.median(d)  # global rotation is free; only remove relative heading drift
    out = []
    for Ta, a, c in zip(anchors, d, centroids):
        # rotate the submap about its own centroid: fixes wall orientation without moving it
        T = Ta.copy()
        R = _rot_y(-a)
        T[:3, :3] = R @ Ta[:3, :3]
        T[:3, 3] = R @ (Ta[:3, 3] - c) + c
        out.append(T)
    return out, list(d)
