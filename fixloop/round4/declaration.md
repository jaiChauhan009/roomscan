# Fix declaration, round 4

Written and committed before the fix (commit message: `fixloop4-before`; the coordinator tags
it on merge). Before run: [before/summary.md](before/summary.md), made by
`python fixloop/round4/run_laser.py before` on this commit (it matches `bench/reports/` exactly).
Evidence: `python fixloop/round4/evidence_ceiling.py <captures>`, output in
[evidence_output.txt](evidence_output.txt).

## 1. Worst-performing gate

**Ceiling height <= 1.5 cm per room, on the four ARKitScenes laser rooms** (the only ceiling
truth that does not come from our own pipeline). 1 of 4 rooms passes, and all four read low:

| capture | laser (m) | ours (m) | error |
|---|---|---|---|
| arkit_42446532 | 2.337 | 2.2822 | **-5.48 cm** |
| arkit_44358446 | 2.387 | 2.3699 | -1.71 cm |
| arkit_47332890 | 2.705 | 2.6830 | -2.20 cm |
| arkit_47331988 | 2.614 | 2.6038 | -1.02 cm (pass) |

## 2. Root cause and evidence

Two causes, one in our level estimator and one upstream of it.

**(a) The level is the most-*looked-at* patch, not the room's surface.** `planes._mode_refine`
takes the peak of a histogram of every point, weighted by observation count, then the median
within 3 cm of it. In our fused LiDAR cloud a floor or ceiling is not one sheet: it is 1.2-2.7
cm thick (rms of a trimmed plane) and parts of it sit a few cm apart (drift between passes,
depth noise at grazing angles). The peak lands on whichever patch the camera dwelt on longest.
Counting each 25 cm cell of surface once (median within the cell, then median over cells)
shows where the rest of the surface is (`evidence_output.txt`):

| capture | floor cells > 1 cm below / above the peak | ceiling cells > 1 cm above / below the peak |
|---|---|---|
| arkit_42446532 | **0.76** / 0.01 | **0.52** / 0.00 |
| arkit_44358446 | 0.01 / 0.18 | 0.42 / 0.06 |
| arkit_47332890 | 0.05 / 0.27 | 0.31 / 0.12 |
| arkit_47331988 | 0.14 / 0.02 | 0.15 / 0.18 |

In arkit_42446532, three quarters of the floor lies below the chosen floor and half the
ceiling above the chosen ceiling: both errors shrink the height, which is why that room is
off by 5.5 cm. The laser truth (`bench/make_laser_truth.py`) is a trimmed plane over the
*whole* ceiling and floor, read at the room's centre, so it weights by area, not by dwell.

Ruled out: tilt (our trimmed planes tilt 0.1-0.7 deg; reading them at the room centre moves
the height by < 0.4 cm except where (a) applies); light fixtures and rugs (would be
compact patches; the cell maps show broad layers, not islands); bin width (1 cm bins, then a
median ±3 cm: the peak choice, not the bin, decides).

**(b) A scale bias of the ARKitScenes cloud of about -0.5 to -1 %, which no level code can
remove.** Even the area-weighted height stays low in every room (-0.4 to -1.3 %), and the
walls that the benchmark matched within 10 cm of the laser are short by the same amount
(signed relative errors -0.46 to -1.73 %, median about -1.0 %, last line of each capture in
`evidence_output.txt`). Heights and lengths short by the same fraction point at the
depth/intrinsics of the converted ARKitScenes capture, not at plane estimation. It is
outside the files this round may change (`planes.py`, `layout.py` level code) and four rooms
are too few to fit a correction factor without fitting the test set.

## 3. Fix and prediction

**Area-weighted room levels.** In `layout._per_room_levels`, after the existing peak finds a
room's floor and ceiling, refine each to the median over 25 cm cells of the cells' median
heights, using points within 6 cm of the peak (so a 10 cm step or a low table top is not
floor). Fall back to the peak with fewer than 8 cells. Sigma: the cell medians' MAD over
sqrt(cells). New function in `planes.py`, unit tested on a synthetic room whose ceiling is
2 cm higher over 60 % of its area but densely observed over the other 40 %. The whole-capture
floor and ceiling (used to cut the plan) do not change.

Predicted from `evidence_output.txt` ("cellmed"), each within ±0.3 cm:

| capture | before | predicted after |
|---|---|---|
| arkit_42446532 | -5.48 cm | -2.95 cm (fail) |
| arkit_44358446 | -1.71 cm | -1.03 cm (**pass**) |
| arkit_47332890 | -2.20 cm | -1.70 cm (fail, by 0.2 cm) |
| arkit_47331988 | -1.02 cm | -0.92 cm (pass) |
| rooms within the gate | 1 / 4 | **2 / 4** |
| **gate** | **fail** | **fail** |

The gate still fails because of (b): every room keeps a -0.4 to -1.3 % residual.

Guards (no truth; room heights from `extract_layout`):

| capture | predicted move |
|---|---|
| apt_lidar_a (9 rooms) | every room within 0.6 cm |
| apt_lidar_b, room_lidar | none (no ceiling seen: wall-top heights, untouched) |
| own_lidar_1 | within 1.3 cm (room_3 +1.3, room_1 -1.25) |
| own_lidar_2 | room_3 **+1.7 cm** (the same failure as arkit_42446532: 72 % of its ceiling cells lie > 1 cm above the peak), room_4 -1.3 cm (a 2 m² room; 81 % of its floor cells lie above the peak), the rest within 0.1 cm |
| own_lidar_3 | +0.25 cm |
