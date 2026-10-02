# Damage stage on synthetic staged damage

Capture `single_scan_with_ceiling` (LiDAR tier, stride 5, loop closure). Regenerate with `uv run python bench/synth_damage.py`; painted damage is a proxy (see the script's docstring).

## Painted

| class | wall | left m | bottom m | width m | height m | bare wall under it | frames showing it |
|---|---|---|---|---|---|---|---|
| water_stain | room_4_w21 | 1.50 | 1.04 | 0.520 | 0.552 | 100% | 4 |
| crack | room_1_w1 | 1.70 | 1.18 | 0.579 | 0.437 | 100% | 2 |

Frames the damage stage read: 88, of which 6 show painted damage.

## Clean capture (nothing painted)

No region reported (the flat is undamaged, so anything reported would be a false positive).


## Painted capture, scored as staged damage

| painted | status | reported | width m: true / ours (rel. err) | height m: true / ours (rel. err) | centre height err m |
|---|---|---|---|---|---|
| water_stain | found | water_stain | 0.520 / 0.456 (-12%) | 0.552 / 0.482 (-13%) | -0.022 |
| crack | found | crack | 0.579 / 0.499 (-14%) | 0.437 / 0.368 (-16%) | 0.035 |

Phantoms: 0.
Concealed-damage flags: CD-04; scope items: 7.

Gate (gates.yaml, assumed: every staged region found with its class, extents within 30%, at most 0 phantoms): found 2/2, wrong class 0, missed 0, phantoms 0, median extent error 0.132 -> **PASS**.

Run time 439 s.
