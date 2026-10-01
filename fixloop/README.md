# Fix loop

| Step | Where |
|---|---|
| Declaration (committed before the fix) | [declaration.md](declaration.md), commit `c9255e9` |
| Root-cause experiment | [evidence_height_cut.py](evidence_height_cut.py) |
| Before run | [before/benchmark.md](before/benchmark.md), code at tag `fixloop-before` (`0a9c23c`) |
| Fix | commit `1e8c490`, diff in [fix.diff](fix.diff) |
| After run | [after/benchmark.md](after/benchmark.md) |

## Regenerate both runs

```bash
git checkout fixloop-before && uv run python bench/run_all.py --out fixloop/before
git checkout 1e8c490       && uv run python bench/run_all.py --out fixloop/after
```

## The fix in one paragraph

A grid cell counted as wall only if wall points covered 0.8 m of a fixed height band
reaching up to 1.9 m. Now the band ends where the capture's own wall evidence ends (90th
percentile of wall-point height, capped at 1.9 m), and a wall must cover half of it,
never less than 0.5 m. For a full-height scan that is the same rule as before. In
`src/roomscan/geometry/layout.py`: new `observed_band_top()`, the band passed into
`_coverage_grids()`, `WALL_BAND_FRAC = 0.5`, `extract_layout(..., adaptive_band=True)`.

## Result

| | before | after | declared prediction | met? |
|---|---|---|---|---|
| footprint B / A | 27.7 / 50.1 m² (−45 %) | 41.2 / 50.2 m² (−18 %) | within ±10 % | no |
| rooms B / A | 6 / 9 | 7 / 10 | B 8 or 9 | no |
| rooms paired | 0 | 1 | 6 or more | no |
| median wall difference of paired rooms | n/a | 24 cm | ≤ 5 cm | no |
| walls within 1 cm / 0.5 % | 0 | 0 of 4 | 10-30 % | no |
| **gate** | **fail** | **fail** | fail expected | yes |

The fix moved the right quantity in the right direction: the footprint gap closed from 22.4
to 9.0 m² and scan B's bedrooms reappeared. The prediction was nevertheless badly wrong on
everything downstream of the footprint.

## Post-mortem

**What the hypothesis got right.** Scan B lost walls because the test demanded evidence
above the height the scan ever looked at. Making the band relative recovered 13.4 m² of
B's floor and both bedrooms that were missing (see the two plans in `after/runs/`).

**What it missed. A second cause, of similar size, was hidden behind the first.**
Scan B sees walls only up to ~1.6 m. In furnished rooms the lower part of most walls is
behind beds, wardrobes and sofas, so for many wall segments the only vertical surface B
saw was the furniture front. The outline snaps to it. B's two bedrooms come out at
2.41 × 2.54-2.81 m and 2.53 × 1.57-1.68 m against A's 2.73 × 3.07-3.36 m and
2.29 × 1.29-1.04 m: sides off by 30-50 cm. The repeatability check then refuses to pair
rooms whose shapes differ that much, so 0 → 1 paired rooms.

That second cause could not be seen before the fix: rooms that do not exist cannot
have the wrong size.

**Why the prediction was so far off.** It assumed that once B's walls were accepted, the
same walls would be found in both scans. The evidence experiment (cutting A at 1.4 m)
removed points but kept A's camera paths. A's paths saw the walls above the furniture
before the cut, so wall positions there were still right. It did not reproduce B's
occlusion. A faithful experiment would have dropped A's frames that point upward, not A's
points above a height.

**Side effect on scan A.** A went from 9 to 10 rooms. The old code truncated the band
edges (0.3 / 0.1 → bin 2), so the "fixed 0.3-1.9 m" band was really 0.2-1.8 m. The fix
rounds them, which changes which cells count as wall in A. Small, but the before / after
of A is not purely the declared change.

**What would close the gate (not shipped).**
1. Snap wall edges to planes with evidence *above* furniture height when there is any,
   and to the floor-wall junction line otherwise. The floor is visible right up to the
   wall in front of a sofa or bed.
2. The protocol already asks for one upward sweep per room. Scan B did not follow it; a
   capture that does avoids the problem at the source.
3. The 1 cm per-wall gate then needs both scans to fit each wall on comparable evidence.
   That is a second, separate step.
