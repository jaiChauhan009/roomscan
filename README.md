# roomscan

Phone capture (photos / video / LiDAR) → dimensioned, stitched whole-property floor plan
with damage regions, concealed-damage flags, scope line items and a confidence interval on
every measurement.

> Work in progress. Setup and usage instructions will be completed as stages land.

## Quick start (dev)

```bash
pip install uv
uv sync --extra dev
uv run roomscan run <capture_path> --out runs/<name>
```
