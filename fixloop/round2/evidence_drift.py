"""Root-cause evidence for fix-loop round 2 (LiDAR repeatability, scan A vs scan B).

Round 1's post-mortem blamed furniture fronts standing in for walls in scan B. This script
tests that, and the alternative: scan B's poses are not corrected for drift.

usage: python fixloop/round2/evidence_drift.py
           <section|walls|closures|odometry|weights|tolerance|synthetic> [option]

  walls     per-frame position of the big bedroom's walls in A and B, in A's plan frame
            (B aligned onto A by ICP on wall points). Shows when each wall was seen.
            Option: shipped | measured (weights below) | current (drift.py as checked out).
  section   strip across the bedroom: does A have any vertical surface where B puts the far
            wall? A furniture front would be in both scans.
  closures  loop-closure candidates, which ones the shipped pose graph keeps, and how far
            it moves the submaps.
  odometry  ARKit's relative-pose error between consecutive submaps, measured by ICP.
            This is the noise the pose graph's odometry edges should be weighted with.
  weights   wall crispness and submap shift with no correction, the shipped weights and
            weights from the measured noise.
  tolerance scan B with the measured odometry weights and the old vs new pruning distance.
  synthetic B's loop-closure pattern on a synthetic loop with known drift (no data needed):
            at which drift the shipped weights stop correcting, and whether a wrong
            closure is rejected.

Data: ROOMSCAN_DATA or ../data (single_scan_with_ceiling = A, single_scan_floor_only = B).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree

from roomscan.frontends.lidar_stray import load_stray
from roomscan.geometry import drift as dr
from roomscan.geometry.layout import PlanFrame, extract_layout
from roomscan.geometry.metrics import crispness
from roomscan.geometry.planes import floor_level, manhattan_yaw
from roomscan.geometry.pointcloud import fuse_capture, frame_points

DATA = Path(os.environ.get("ROOMSCAN_DATA", Path(__file__).resolve().parents[2] / ".." / "data"))
SCANS = {"A": DATA / "single_scan_with_ceiling", "B": DATA / "single_scan_floor_only"}
reg = o3d.pipelines.registration


def load(name: str):
    return load_stray(SCANS[name], stride=5)


def pose_graph(cap, sigma_m: float, sigma_deg: float, mcd: float):
    """drift.correct_drift with the odometry weights and line-process tolerance as parameters.

    Same submaps, candidates, ICP and acceptance rules as the shipped code at fixloop2-before;
    only info_odo and max_correspondence_distance differ. Returns (capture, info, kept, cands).
    """
    groups = dr._submaps(cap)
    anchors = [cap.frames[g[0]].T_wc for g in groups]
    clouds = [dr._submap_cloud(cap, g, np.linalg.inv(Ta)) for g, Ta in zip(groups, anchors)]
    centroids = np.array([(Ta[:3, :3] @ np.asarray(c[0].points).mean(0) + Ta[:3, 3]) if c else Ta[:3, 3]
                          for c, Ta in zip(clouds, anchors)])
    pg = reg.PoseGraph()
    for Ta in anchors:
        pg.nodes.append(reg.PoseGraphNode(Ta))
    sr = np.deg2rad(sigma_deg)
    info_odo = np.diag([1 / sr ** 2] * 3 + [1 / sigma_m ** 2] * 3)
    for i in range(len(anchors) - 1):
        pg.edges.append(reg.PoseGraphEdge(i, i + 1, np.linalg.inv(anchors[i + 1]) @ anchors[i], info_odo,
                                          uncertain=False))
    est = reg.TransformationEstimationPointToPlane()
    crit = reg.ICPConvergenceCriteria(max_iteration=30)
    cands = []
    for i in range(len(anchors)):
        for j in range(i + dr.MIN_GAP, len(anchors)):
            if clouds[i] is None or clouds[j] is None or np.linalg.norm(centroids[i] - centroids[j]) > dr.LOOP_RADIUS:
                continue
            init = np.linalg.inv(anchors[j]) @ anchors[i]
            src, tgt = clouds[i][0], clouds[j][0]
            r1 = reg.registration_icp(src, tgt, 0.10, init, est, crit)
            r2 = reg.registration_icp(src, tgt, 0.03, r1.transformation, est, crit)
            delta = np.linalg.inv(init) @ r2.transformation
            dt = float(np.linalg.norm(delta[:3, 3]))
            da = float(np.degrees(np.arccos(np.clip((np.trace(delta[:3, :3]) - 1) / 2, -1, 1))))
            row = dict(i=i, j=j, fit=round(r2.fitness, 2), rmse=round(r2.inlier_rmse, 4), dt=round(dt, 3),
                       da=round(da, 1), accepted=False)
            cands.append(row)
            if r2.fitness < 0.35 or r2.inlier_rmse > 0.02 or dt > 0.5 or da > 8:
                continue
            row["accepted"] = True
            inf = reg.get_information_matrix_from_point_clouds(src, tgt, 0.03, r2.transformation)
            pg.edges.append(reg.PoseGraphEdge(i, j, r2.transformation, inf, uncertain=True))
    opt = reg.GlobalOptimizationOption(max_correspondence_distance=mcd, edge_prune_threshold=0.25,
                                       preference_loop_closure=1.0, reference_node=0)
    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
    reg.global_optimization(pg, reg.GlobalOptimizationLevenbergMarquardt(),
                            reg.GlobalOptimizationConvergenceCriteria(), opt)
    kept = sorted((e.source_node_id, e.target_node_id) for e in pg.edges if e.uncertain)
    frames, shifts = [], []
    import copy
    for g, Ta, node in zip(groups, anchors, pg.nodes):
        T = np.asarray(node.pose).copy()
        dR = T[:3, :3] @ Ta[:3, :3].T
        T[:3, :3] = dr._rot_y(float(np.arctan2(dR[0, 2], dR[2, 2]))) @ Ta[:3, :3]
        corr = T @ np.linalg.inv(Ta)
        shifts.append(float(np.linalg.norm(corr[:3, 3] + corr[:3, :3] @ Ta[:3, 3] - Ta[:3, 3])))
        for i in g:
            f = copy.copy(cap.frames[i])
            f.T_wc = corr @ f.T_wc
            frames.append(f)
    from roomscan.capture import PosedCapture
    info = {"max_submap_shift_m": round(max(shifts), 3), "mean_submap_shift_m": round(float(np.mean(shifts)), 3),
            "accepted": sum(c["accepted"] for c in cands), "kept": len(kept)}
    return PosedCapture(cap.tier, cap.name, frames, dict(cap.meta)), info, kept, cands


SHIPPED = dict(sigma_m=0.01, sigma_deg=float(np.degrees(0.005)), mcd=0.03)  # drift.py at fixloop2-before
MEASURED = dict(sigma_m=0.02, sigma_deg=0.4, mcd=1.4 * 0.04)  # 'odometry' below; 1.4 x submap voxel


def wall_points_plan(cloud, frame, floor_y):
    h = cloud.points[:, 1] - floor_y
    m = (np.abs(cloud.normals[:, 1]) < 0.25) & (h > 0.3) & (h < 1.5)
    return frame.to_plan(cloud.points[m][:, [0, 2]].astype(np.float64))


def align_2d(src: np.ndarray, dst: np.ndarray):
    """Rigid 2D transform taking src onto dst: 90-degree / small-yaw search by FFT correlation, then ICP."""
    rng = np.random.default_rng(0)
    src = src[rng.choice(len(src), min(len(src), 150000), replace=False)]
    dst = dst[rng.choice(len(dst), min(len(dst), 150000), replace=False)]

    def rot(t):
        return np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])

    def raster(p, lo, shape, res=0.05):
        g = np.zeros(shape)
        r, c = ((p[:, 1] - lo[1]) / res).astype(int), ((p[:, 0] - lo[0]) / res).astype(int)
        ok = (r >= 0) & (r < shape[0]) & (c >= 0) & (c < shape[1])
        np.add.at(g, (r[ok], c[ok]), 1)
        return np.log1p(g)

    best = None
    for k in range(4):
        for d in np.deg2rad(np.arange(-3, 3.01, 0.5)):
            s = src @ rot(k * np.pi / 2 + d).T
            lo = np.minimum(dst.min(0), s.min(0)) - 2
            hi = np.maximum(dst.max(0), s.max(0)) + 2
            shape = (int((hi[1] - lo[1]) / 0.05) * 2, int((hi[0] - lo[0]) / 0.05) * 2)
            F = np.fft.irfft2(np.fft.rfft2(raster(dst, lo, shape)) * np.conj(np.fft.rfft2(raster(s, lo, shape))), s=shape)
            ij = np.unravel_index(np.argmax(F), F.shape)
            t = np.array([(ij[1] if ij[1] < shape[1] // 2 else ij[1] - shape[1]) * 0.05,
                          (ij[0] if ij[0] < shape[0] // 2 else ij[0] - shape[0]) * 0.05])
            if best is None or F[ij] > best[0]:
                best = (F[ij], rot(k * np.pi / 2 + d), t)
    _, R, t = best
    tree = cKDTree(dst)
    for _ in range(40):
        p = src @ R.T + t
        d, j = tree.query(p, distance_upper_bound=0.15)
        ok = np.isfinite(d)
        a, b = p[ok], dst[j[ok]]
        U, _, Vt = np.linalg.svd((a - a.mean(0)).T @ (b - b.mean(0)))
        dR = Vt.T @ U.T
        if np.linalg.det(dR) < 0:
            Vt[-1] *= -1
            dR = Vt.T @ U.T
        R, t = dR @ R, dR @ (t - a.mean(0)) + b.mean(0)
    d, _ = tree.query(src @ R.T + t, distance_upper_bound=0.15)
    ok = np.isfinite(d)
    return R, t, float(ok.mean()), float(np.median(d[ok]))


def b_onto_a(clouds):
    """A's layout, both floors, and the plan transform taking B onto A's plan frame.

    Each scan has its own ARKit world heading, so B is first put in its own wall-aligned
    frame (Manhattan yaw); the two frames then differ by a multiple of 90 degrees plus a
    small yaw and a shift, which align_2d finds.
    """
    LA = extract_layout(clouds["A"])
    fl = {"A": LA.floor.value, "B": floor_level(clouds["B"]).value}
    FB = PlanFrame(yaw=manhattan_yaw(clouds["B"]))
    R, t, inl, med = align_2d(wall_points_plan(clouds["B"], FB, fl["B"]),
                              wall_points_plan(clouds["A"], LA.frame, fl["A"]))
    frames = {"A": (LA.frame, np.eye(2), np.zeros(2)), "B": (FB, R, t)}
    return LA, fl, frames, inl, med


def to_a_plan(frames, name, xz, nxz=None):
    fr, R, t = frames[name]
    ab = fr.to_plan(xz.astype(np.float64)) @ R.T + t
    return ab if nxz is None else (ab, fr.to_plan(nxz.astype(np.float64)) @ R.T)


# the big bedroom's walls in A's plan frame (as extract_layout computes it at fixloop2-before):
# name, constant axis (0 = x), search range on that axis, range along the wall, facing sign
BEDROOM = [("left wall", 0, 5.25, 5.65, (0.9, 2.6), +1),
           ("right wall", 0, 8.0, 9.1, (0.5, 2.6), -1),
           ("top wall", 1, 2.5, 3.3, (6.2, 8.4), -1)]


def cmd_walls(weights: str = "shipped"):
    """weights: shipped | measured (this script's copy of the pose graph) | current (drift.py as it is now)."""
    caps, clouds = {}, {}
    for name in "AB":
        if weights == "current":
            caps[name], _ = dr.correct_drift(load(name), use_cache=False, progress=False)
        else:
            caps[name], _, _, _ = pose_graph(load(name), **(SHIPPED if weights == "shipped" else MEASURED))
        clouds[name] = fuse_capture(caps[name], progress=False)
    LA, fl, frames, inl, med = b_onto_a(clouds)
    print(f"weights={weights}: B onto A: {100 * inl:.1f} % of B wall points within 15 cm of A's, "
          f"median distance {100 * med:.2f} cm")
    print("wall | scan | frames | seen during (s) | position median (p10-p90) m")
    for name in "AB":
        t0 = caps[name].frames[0].timestamp
        rows = {b[0]: [] for b in BEDROOM}
        for f in caps[name].frames:
            p, n, _ = frame_points(f, pixel_stride=2)
            ab, nab = to_a_plan(frames, name, p[:, [0, 2]], n[:, [0, 2]])
            h = p[:, 1] - fl[name]
            wm = (np.abs(n[:, 1]) < 0.25) & (h > 0.3) & (h < 1.6)
            for wn, ax, lo, hi, (alo, ahi), sg in BEDROOM:
                m = (wm & (ab[:, ax] > lo) & (ab[:, ax] < hi) & (ab[:, 1 - ax] > alo) & (ab[:, 1 - ax] < ahi)
                     & (sg * nab[:, ax] > 0.8))
                if m.sum() >= 40:
                    hist, e = np.histogram(ab[m, ax], bins=np.arange(lo, hi + 0.01, 0.01))
                    rows[wn].append((f.timestamp - t0, e[np.argmax(np.convolve(hist, [1, 2, 1], "same"))] + 0.005))
        for wn, *_ in BEDROOM:
            r = np.array(rows[wn])
            if len(r):
                spans = np.split(r[:, 0], np.where(np.diff(r[:, 0]) > 5)[0] + 1)
                print(f"{wn} | {name} | {len(r)} | {', '.join(f'{s[0]:.0f}-{s[-1]:.0f}' for s in spans)} | "
                      f"{np.median(r[:, 1]):.3f} ({np.percentile(r[:, 1], 10):.3f}-{np.percentile(r[:, 1], 90):.3f})")


def cmd_section():
    """Is the surface B takes for the bedroom's far wall a piece of furniture?

    A saw the whole room, from every side and up to the ceiling. If B's far "wall" were a
    furniture front, A would have a vertical surface in the same place. If B's geometry is
    displaced, A has nothing there.
    """
    clouds = {}
    for name in "AB":
        cap, _, _, _ = pose_graph(load(name), **SHIPPED)
        clouds[name] = fuse_capture(cap, progress=False)
    LA, fl, frames, _, _ = b_onto_a(clouds)
    vert = {}
    for name in "AB":
        c = clouds[name]
        ab = to_a_plan(frames, name, c.points[:, [0, 2]])
        h = c.points[:, 1] - fl[name]
        strip = (ab[:, 1] > 1.0) & (ab[:, 1] < 2.0) & (ab[:, 0] > 7.2) & (ab[:, 0] < 9.3)
        m = strip & (np.abs(c.normals[:, 1]) < 0.25) & (h > 0.6)
        vert[name] = (ab[m, 0], h[m])
    xa, ha = vert["A"]
    xb, hb = vert["B"]
    hist, e = np.histogram(xb, bins=np.arange(8.0, 9.3, 0.01))
    wb = e[np.argmax(np.convolve(hist, [1, 2, 1], "same"))] + 0.005
    hist, e = np.histogram(xa, bins=np.arange(8.0, 9.3, 0.01))
    wa = e[np.argmax(np.convolve(hist, [1, 2, 1], "same"))] + 0.005
    print("Strip across the bedroom, A plan frame y 1.0-2.0 m, vertical surfaces more than 0.6 m above the floor.")
    print(f"B's far wall: x = {wb:.3f} m, seen {hb[np.abs(xb - wb) < 0.05].min():.2f}-{hb[np.abs(xb - wb) < 0.05].max():.2f} m "
          f"above the floor ({(np.abs(xb - wb) < 0.05).sum()} points)")
    print(f"A's far wall: x = {wa:.3f} m, seen {ha[np.abs(xa - wa) < 0.05].min():.2f}-{ha[np.abs(xa - wa) < 0.05].max():.2f} m "
          f"above the floor ({(np.abs(xa - wa) < 0.05).sum()} points)")
    print(f"A points within 5 cm of B's far wall position: {(np.abs(xa - wb) < 0.05).sum()}")


def cmd_closures(weights: str = "shipped"):
    w = SHIPPED if weights == "shipped" else MEASURED
    for name in "AB":
        _, info, kept, cands = pose_graph(load(name), **w)
        print(f"{name} weights={weights}: {info}")
        if name == "B":
            for c in cands:
                if c["fit"] >= 0.3:
                    print(f"   {c['i']:2d}-{c['j']:2d} fit {c['fit']:.2f} rmse {c['rmse']:.3f} correction {c['dt']:.3f} m "
                          f"{c['da']:.1f} deg | {'accepted' if c['accepted'] else 'rejected'}"
                          f"{' | kept after optimisation' if (c['i'], c['j']) in kept else ''}")


def cmd_odometry():
    est = reg.TransformationEstimationPointToPlane()
    crit = reg.ICPConvergenceCriteria(max_iteration=30)
    for name in "AB":
        cap = load(name)
        groups = dr._submaps(cap)
        anchors = [cap.frames[g[0]].T_wc for g in groups]
        clouds = [dr._submap_cloud(cap, g, np.linalg.inv(Ta)) for g, Ta in zip(groups, anchors)]
        hz, yw = [], []
        for i in range(len(groups) - 1):
            if clouds[i] is None or clouds[i + 1] is None:
                continue
            init = np.linalg.inv(anchors[i + 1]) @ anchors[i]
            src, tgt = clouds[i][0], clouds[i + 1][0]
            r = reg.registration_icp(src, tgt, 0.10, init, est, crit)
            r = reg.registration_icp(src, tgt, 0.03, r.transformation, est, crit)
            if r.fitness < 0.35 or r.inlier_rmse > 0.02:
                continue
            ev = np.linalg.eigvalsh(reg.get_information_matrix_from_point_clouds(src, tgt, 0.03, r.transformation)[3:, 3:])
            if ev[0] < 0.02 * ev[-1]:  # translation not constrained in every direction
                continue
            delta = r.transformation @ np.linalg.inv(init)
            Rw = anchors[i + 1][:3, :3]
            dtw, dRw = Rw @ delta[:3, 3], Rw @ delta[:3, :3] @ Rw.T
            hz.append(np.linalg.norm(dtw[[0, 2]]))
            yw.append(abs(np.degrees(np.arctan2(dRw[0, 2], dRw[2, 2]))))
        hz, yw = np.array(hz), np.array(yw)
        print(f"{name}: {len(hz)} of {len(groups) - 1} consecutive submap pairs well constrained; ARKit error per "
              f"3 s step: horizontal median {100 * np.median(hz):.1f} cm, p90 {100 * np.percentile(hz, 90):.1f} cm; "
              f"yaw median {np.median(yw):.2f} deg, rms {np.sqrt(np.mean(yw ** 2)):.2f} deg")
    print(f"shipped odometry weights: {100 * SHIPPED['sigma_m']:.0f} cm and {SHIPPED['sigma_deg']:.2f} deg per step")


def cmd_weights():
    for name in "AB":
        cap = load(name)
        print(f"{name}: no correction: crispness {crispness(fuse_capture(cap, progress=False)):.3f}")
        for label, w in (("shipped", SHIPPED), ("measured", MEASURED)):
            c2, info, kept, _ = pose_graph(cap, **w)
            print(f"{name}: {label} weights {w}: crispness {crispness(fuse_capture(c2, progress=False)):.3f}, "
                  f"closures accepted {info['accepted']}, kept {info['kept']}, max submap shift "
                  f"{info['max_submap_shift_m']} m", flush=True)


def cmd_tolerance():
    """Scan B with the measured odometry weights and the old vs the new pruning distance.

    Open3D's pruning tolerance is distance^2 x the mean information of all edges, so lowering
    the odometry information also tightens pruning unless the distance grows with it.
    """
    cap = load("B")
    for mcd in (0.03, MEASURED["mcd"]):
        _, info, _, _ = pose_graph(cap, sigma_m=MEASURED["sigma_m"], sigma_deg=MEASURED["sigma_deg"], mcd=mcd)
        print(f"B: odometry {100 * MEASURED['sigma_m']:.0f} cm / {MEASURED['sigma_deg']} deg, pruning distance "
              f"{100 * mcd:.1f} cm: closures accepted {info['accepted']}, kept {info['kept']}, "
              f"max submap shift {info['max_submap_shift_m']} m")


def cmd_synthetic():
    """B's loop-closure pattern on a synthetic loop with known drift; no capture data needed.

    38 submaps around a loop, ARKit-like drift growing linearly to D at the end, and loop
    closures between the same submap pairs scan B has, measured exactly. Optionally one
    wrong closure. Same optimisation as the shipped code, weights as parameters.
    """
    def optimise(anchors, loops, sigma_m, sigma_deg, mcd):
        pg = reg.PoseGraph()
        for Ta in anchors:
            pg.nodes.append(reg.PoseGraphNode(Ta))
        sr = np.deg2rad(sigma_deg)
        info_odo = np.diag([1 / sr ** 2] * 3 + [1 / sigma_m ** 2] * 3)
        for i in range(len(anchors) - 1):
            pg.edges.append(reg.PoseGraphEdge(i, i + 1, np.linalg.inv(anchors[i + 1]) @ anchors[i], info_odo,
                                              uncertain=False))
        for i, j, T in loops:
            pg.edges.append(reg.PoseGraphEdge(i, j, T, np.diag([7500.0] * 3 + [2500.0] * 3), uncertain=True))
        opt = reg.GlobalOptimizationOption(max_correspondence_distance=mcd, edge_prune_threshold=0.25,
                                           preference_loop_closure=1.0, reference_node=0)
        o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
        reg.global_optimization(pg, reg.GlobalOptimizationLevenbergMarquardt(),
                                reg.GlobalOptimizationConvergenceCriteria(), opt)
        kept = {(e.source_node_id, e.target_node_id) for e in pg.edges if e.uncertain}
        return [np.asarray(nd.pose) for nd in pg.nodes], kept

    n, pairs = 38, [(3, 33), (8, 27), (9, 27), (10, 15), (15, 27), (21, 25)]
    print("end drift | wrong closure | weights | closures kept | max error vs truth (correct closures) | max shift")
    for D, wrong in [(0.25, None), (0.35, None), (0.5, None), (0.5, (0.3, 0.0)), (0.5, (0.0, 5.0))]:
        true, drifted = [], []
        for k in range(n):
            a = 2 * np.pi * k / n
            T = np.eye(4)
            T[:3, :3] = dr._rot_y(-a)
            T[:3, 3] = [3 * np.cos(a), 0.0, 2 * np.sin(a)]
            f = k / (n - 1)
            E = np.eye(4)
            E[:3, :3] = dr._rot_y(np.deg2rad(4 * D * f))
            E[:3, 3] = [D * f, 0.0, 0.4 * D * f]
            true.append(T)
            drifted.append(E @ T)
        loops = [(i, j, np.linalg.inv(true[j]) @ true[i]) for i, j in pairs]
        if wrong:
            W = np.eye(4)
            W[:3, :3] = dr._rot_y(np.deg2rad(wrong[1]))
            W[:3, 3] = [wrong[0], 0, 0]
            loops.append((12, 30, W @ np.linalg.inv(true[30]) @ true[12]))
        for label, w in (("shipped", SHIPPED), ("measured", MEASURED)):
            new, kept = optimise(drifted, loops, **w)
            err = max(np.linalg.norm((np.linalg.inv(np.linalg.inv(new[j]) @ new[i]) @ (np.linalg.inv(true[j]) @ true[i]))[:3, 3])
                      for i, j in pairs)
            shift = max(np.linalg.norm(a[:3, 3] - b[:3, 3]) for a, b in zip(new, drifted))
            print(f"{D:.2f} m | {'none' if not wrong else f'{wrong[0]} m, {wrong[1]} deg'} | {label} | "
                  f"{len(kept)}/{len(loops)} | {100 * err:.1f} cm | {shift:.3f} m")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "walls"
    {"walls": cmd_walls, "section": cmd_section, "closures": cmd_closures, "odometry": cmd_odometry,
     "weights": cmd_weights, "tolerance": cmd_tolerance, "synthetic": cmd_synthetic}[cmd](*sys.argv[2:])
