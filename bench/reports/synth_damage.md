# Damage stage on synthetic staged damage

Capture `single_scan_with_ceiling` (LiDAR tier, stride 5, loop closure). Regenerate with `uv run python bench/synth_damage.py`; painted damage is a proxy (see the script's docstring).

## Painted

| class | wall | left m | bottom m | width m | height m | bare wall under it | frames showing it |
|---|---|---|---|---|---|---|---|
| water_stain | room_1_w5 | 1.50 | 1.04 | 0.520 | 0.552 | 100% | 2 |
| crack | room_3_w12 | 0.80 | 1.18 | 0.579 | 0.437 | 100% | 4 |

Frames the damage stage read: 88, of which 8 show painted damage.

## Clean capture (nothing painted)

0 region(s) reported, all false positives.


## Painted capture, scored as staged damage

| painted | status | reported | width m: true / ours (rel. err) | height m: true / ours (rel. err) | centre height err m |
|---|---|---|---|---|---|
| water_stain | missed | - | 0.520 / - | 0.552 / - | - |
| crack | missed | - | 0.579 / - | 0.437 / - | - |

Phantoms: 0.
Concealed-damage flags: none; scope items: 0.

Gate (gates.yaml, assumed: every staged region found with its class, extents within 30%, at most 0 phantoms): found 0/2, wrong class 0, missed 2, phantoms 0, median extent error None -> **FAIL**.

Run time 993 s.
