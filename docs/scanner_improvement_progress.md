# Scanner improvements and remaining validation

This is an implementation checkpoint, not a claim that the app is perfect or ready for precision surveying.
Raw recordings remain in `/home/jai/Documents/yc_proj`; the independent working app is in
`/home/jai/AndroidStudioProjects/roomscan`. Nothing was added to TrackIt.

## Implemented

### 1. Diagnose and reduce large wall failures

`bench/wall_diagnostics.py` exposes reference/predicted wall-count mismatches, unpaired predicted
segments, missing rooms and large individual errors. It retains the existing benchmark score;
it does not improve metrics by discarding failed walls.

The layout repair fills an inward furniture recess only when continuous inward-facing upper-wall
evidence closes it, with a measured ceiling and no overlap into a neighbouring room. Synthetic
regressions preserve true recesses, missing-ceiling cases and neighbouring rooms.

On the four existing laser-reference development rooms:

| Room | Previous maximum wall error | Updated maximum | Area error before → after |
|---|---:|---:|---:|
| 42446532 | 227.73 cm | 5.88 cm | −7.14% → about −2.40% |
| 44358446 | 193.63 cm | 3.37 cm | −2.49% → about −1.73% |
| 47332890 | 134.64 cm | unchanged | +0.56%, unchanged |
| 47331988 | 4.83 cm | unchanged | −1.99%, unchanged |

These four rooms informed diagnosis and are development data. They are no longer evidence of
unseen-data generalization. The strict configured wall gate still fails. No global correction
factor was fitted to their reference dimensions.

Outputs retain the outline before a supported repair and its before/after areas in
`capture.meta.outline_repairs`; `quality_review` calls for boundary review.

### 2. Recover scans and support offline capture

The native library stores captured room geometry, frame references, scan session identifiers,
property associations, ZIP paths and measurement references. It writes atomically with a backup
of the previous readable revision. It attempts backup recovery; if neither file is readable,
it preserves the files and disables new capture rather than replacing the library with an empty one.

Saved properties can be selected from Home. Creating a new property or replacing an expired
server project no longer discards saved rooms. Local capture and sharing do not require a server
project. Automatic connection does not block the capture button. ZIP packing interrupted by a
relaunch is retried for saved rooms without an export path. Session folders contain UUIDs to
avoid media collisions across fast relaunches.

Capture review stays open if saving room geometry fails. Full-storage and interruption behavior
still require physical-device fault testing. Raw media files remain separate from the JSON library;
manually deleting those files can prevent complete media exports.

### 3. Make capture review actionable

Review checks an unclosed wall boundary even when a floor polygon exists. It identifies weak
walls by number, highlights medium/low confidence walls and flags a missing floor or invalid
dimensions. Users can rescan before saving. These checks remain heuristics, not accuracy guarantees.

### 4. Collect independent reference lengths

Saved-room detail lets users record a tape/laser wall length and measurement uncertainty, then
view the signed difference from the captured length. References persist locally and are included
in a newly shared whole-property ZIP. The backend validates and retains them, and warns about
large differences. An unknown wall ID is reported rather than matched to a different wall silently.

References remain separate from geometry. This is measurement recording/comparison, **not** a
corner editor, a wall-moving solver or automatic calibration. Editing references after an automatic
room upload does not replace that earlier server ZIP; share a fresh whole-property ZIP to include
the latest reference metadata.

### 5. Expose incomplete or uncertain results

Backend output includes `capture.meta.quality_review`: model-estimated scale, low tracking retention,
weak wall evidence, wide wall intervals, unobserved ceilings and adjusted outlines are review reasons.
Even a result without these issues has `accuracy_verified: false`.

The iOS results view shows review reasons and existing capture notes. Completion is not treated as
proof of accuracy. The hosted backend must be updated separately before it emits the new review
metadata; older servers' existing warnings are also shown by the updated app.

### 6. Expand native validation

The existing macOS workflow now builds the unsigned iPhone/iPad app and runs unit and synthetic
preview UI tests separately on iPhone and iPad simulators, including an iPad landscape preview.
Tests exercise atomic persistence, corrupt-file recovery, schema validation, review warnings and
reference-measurement export. Simulator selection fails explicitly when its device family is absent.

Final CI run and exact counts are recorded in the saved validation report under `runs/release-validation/`.
No signing, TestFlight publication, AWS allocation or physical LiDAR testing is included.

## Remaining work, in order

1. **Independent measured rooms.** Capture 10–20 rooms using our native app, not only Stray Scanner.
   Cover rectangular/L-shaped rooms, furniture, glass, low light and connected/open-plan rooms.
   Measure every straight wall, ceiling and opening; record instrument accuracy and room identity.
   Repeat the route three times. Keep original media rather than chat-app copies.
2. **Freeze a holdout set.** Split by property/device session, not by frames from the same room.
   Keep the four current laser rooms in the development set. Do not tune thresholds or refit
   uncertainty using held-out results. More device generations and iPad orientations are needed.
3. **Resolve remaining geometry and segmentation failures.** Investigate 47332890's residual step
   against its raw depth and laser shape; preserve ambiguity when evidence is insufficient.
   The sample-flat scans still split into 9 versus 7 regions. A one-room input still splits into two.
4. **Build a reversible plan editor.** Add explicit corner movement, opening corrections, room
   split/merge and deliberate room joining. Preserve the original capture, validate polygon topology,
   and recompute affected areas, openings and adjacency. The reference-length feature is not this editor.
5. **Improve photo/video reconstruction.** The new review reasons expose failures; they do not fix
   the models. Tracking recovery across segments, coverage estimation, scale-reference capture and
   independently measured evaluation remain necessary. Re-score against tape/laser truth, not just
   this app's LiDAR output. Existing percentage errors remain failures.
6. **Measure device performance.** Profile memory, frame retention, CPU, battery and thermal behavior
   with Instruments on real devices, including long scans, full storage, backgrounding and interruptions.
   Optimize measured bottlenecks, then rerun correctness tests. Linux replay times are not device speed.
7. **Validate damage separately.** Record real staged stains/cracks with dimensions and clean controls.
   The earlier painted-data test is not real-world validation; ceiling-light false positives remain open.
8. **Compare products fairly.** Scan the same measured rooms with the same route in competitor apps,
   then score dimensions, coverage, failures and editing effort. No superiority claim is supported yet.

## Initial acceptance targets

These are development targets, not measured achievements or a certification standard:

- No unexplained metre-scale wall failures.
- At least 95% of independently measured walls within 5 cm on held-out rooms.
- At least 90% of room areas within 3% on that same set.
- Correct room count and connections for predefined test layouts.
- Saved rooms survive app restart; failed writes never report a successful room save.
- Device performance reports include failure counts, peak memory and sustained thermal behavior.

Strict existing benchmark targets are retained separately in `bench/gates.yaml`.

## Reproduce

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_outline.py tests/test_wall_diagnostics.py \
  tests/test_geometry.py tests/test_drift.py tests/test_roomplan.py tests/test_roomplan_randomized.py \
  tests/test_review.py tests/test_known_sizes.py tests/test_marker_wiring.py tests/test_stages.py \
  tests/test_intervals_evidence.py tests/test_contract.py tests/test_simulator_selection.py tests/test_inputs.py -q

PYTHONPATH=src .venv/bin/python bench/wall_diagnostics.py \
  runs/release-validation/arkit_42446532/result.json \
  bench/ground_truth/arkit_42446532.yaml --out runs/release-validation/wall-diagnostics-42446532.json

ROOMSCAN_DATA=/home/jai/Documents/yc_proj/data PYTHONPATH=src .venv/bin/python bench/run_all.py \
  --only apt_lidar_a apt_lidar_b room_lidar arkit_42446532 arkit_44358446 arkit_47332890 \
  arkit_47331988 own_lidar_1 own_lidar_2 own_lidar_3 \
  --no-damage --no-cache --skip-ablation --out runs/geometry-benchmark
```

The local validation environment uses Python 3.12, whereas `pyproject.toml` specifies Python 3.11.
The raw replay disables damage analysis and caches. Full ML pipelines and their runtime costs are
not established by the geometry replay or the lightweight processing tests.
