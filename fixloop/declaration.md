# Fix declaration

Written and committed before the fix. Before run: commit `0a9c23c` (tag `fixloop-before`),
report in `fixloop/before/benchmark.md`.

## 1. Worst-performing gate

**Repeatability, LiDAR tier.** Two captures of the same flat (`single_scan_with_ceiling` =
A, `single_scan_floor_only` = B) should give the same plan.

| | A | B |
|---|---|---|
| rooms | 9 | 6 |
| footprint | 50.1 m² | 27.7 m² (−45 %) |
| rooms paired by the repeatability check | 0 | |
| walls within 1 cm / 0.5 % | 0 | |

Chosen over the video and photo gates, which fail by more, because those are scored
against LiDAR output: until LiDAR is repeatable, no other number can be trusted.

## 2. Root cause and evidence

**Hypothesis.** A grid cell counts as wall only if wall points cover at least 0.8 m of
the fixed 0.3-1.9 m height band (`layout.py`, `wall = low_cov >= 8 | ...`). Scan B was
shot with the phone pointing down: its wall points have median height 0.86 m and 90th
percentile 1.56 m. Many of B's real walls therefore fail the test, rooms are not sealed,
and they are dropped or merged.

**Evidence** (`fixloop/evidence_height_cut.py`): scan A with its points above a cut
height removed, same code.

| A, points removed above | rooms | footprint m² |
|---|---|---|
| nothing | 9 | 50.1 |
| 1.8 m (no lintels left) | 9 | 55.1 |
| 1.4 m | 7 | 32.2 |
| 1.2 m | 4 | 20.1 |

- **Ruled out:** missing door lintels, the first hypothesis. Without lintels (cut 1.8 m)
  A still gives 9 rooms.
- **Supported:** A collapses towards B's numbers only once wall evidence ends below about
  1.4 m, which is where B's wall evidence ends.

## 3. Fix and prediction

**Fix.** Make the wall test relative to the height range the capture actually observed:

- **Band top:** take it from the capture's own wall-point heights (90th percentile, capped
  at 1.9 m) instead of a fixed 1.9 m.
- **Coverage:** require 60 % of that band instead of a fixed 0.8 m.
- **Unchanged:** the rule that furniture (low, short evidence) is not wall still applies,
  because the band bottom stays at 0.3 m and the minimum span stays at 0.5 m.

**Predicted numbers after the fix:**

| | before | predicted |
|---|---|---|
| rooms in B | 6 | 8 or 9 |
| footprint B vs A | −45 % | within ±10 % |
| rooms paired | 0 | 6 or more |
| median wall difference of paired rooms | n/a | ≤ 5 cm |
| walls within the 1 cm / 0.5 % gate | 0 | 10-30 % |

**Expected outcome: the gate will still fail.** Agreeing within 1 cm per wall also needs
both scans to see every wall well. B never sees upper walls or ceilings, so its wall
planes are fitted on less data. The fix targets the structural failure (different room
sets), which is the precondition for the per-wall gate.
