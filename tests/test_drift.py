"""Pose-graph drift correction on a synthetic loop with known drift.

The closure pattern is the one scan B of the sample flat produces: few loop closures, the
first one tying the start of the capture to its end. Closures are exact, so after the
correction every closure should hold to about a centimetre.
"""
import numpy as np

from roomscan.geometry import drift

PAIRS = [(3, 33), (8, 27), (9, 27), (10, 15), (15, 27), (21, 25)]
INFO = np.diag([7500.0] * 3 + [2500.0] * 3)  # about what 2,500 ICP correspondences give


def _loop(end_drift_m: float, n: int = 38):
    """Submap anchors around an elliptic loop, true and with drift growing linearly along it."""
    true, drifted = [], []
    for k in range(n):
        a = 2 * np.pi * k / n
        T = np.eye(4)
        T[:3, :3] = drift._rot_y(-a)
        T[:3, 3] = [3 * np.cos(a), 0.0, 2 * np.sin(a)]
        f = k / (n - 1)
        E = np.eye(4)
        E[:3, :3] = drift._rot_y(np.deg2rad(4 * end_drift_m * f))
        E[:3, 3] = [end_drift_m * f, 0.0, 0.4 * end_drift_m * f]
        true.append(T)
        drifted.append(E @ T)
    return true, drifted


def _closure(true, i, j, error=None):
    T = np.linalg.inv(true[j]) @ true[i]
    return {"i": i, "j": j, "T": T if error is None else error @ T, "info": INFO, "shift": 0.0}


def _worst_closure_error(new, true, pairs):
    return max(np.linalg.norm((np.linalg.inv(np.linalg.inv(new[j]) @ new[i]) @ (np.linalg.inv(true[j]) @ true[i]))[:3, 3])
               for i, j in pairs)


def test_half_metre_of_drift_is_corrected():
    # scan B: 0.5 m between the start and the end of the loop. The weights shipped before
    # fix-loop round 2 pruned 5 of these 6 closures and moved nothing.
    true, drifted = _loop(0.5)
    new, kept = drift._optimise(drifted, [_closure(true, i, j) for i, j in PAIRS])
    assert len(kept) == len(PAIRS)
    assert _worst_closure_error(new, true, PAIRS) < 0.02
    assert max(np.linalg.norm(a[:3, 3] - b[:3, 3]) for a, b in zip(new, drifted)) > 0.35


def test_a_wrong_loop_closure_is_pruned():
    true, drifted = _loop(0.5)
    wrong = np.eye(4)
    wrong[:3, 3] = [0.3, 0.0, 0.0]  # a closure that slid 30 cm along a corridor
    loops = [_closure(true, i, j) for i, j in PAIRS] + [_closure(true, 12, 30, error=wrong)]
    new, kept = drift._optimise(drifted, loops)
    assert len(kept) == len(PAIRS)
    assert _worst_closure_error(new, true, PAIRS) < 0.02
