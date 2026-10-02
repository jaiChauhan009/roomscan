# Device matrix

Which tier runs on which hardware, and what accuracy each tier delivers on the evidence
we have. "Measured" means a number from `bench/` or `tests/`; everything else is labelled.

## Capture devices

| Device | LiDAR tier | Video tier | Photo tier | Capture tool |
|---|---|---|---|---|
| iPhone 15 Pro / Pro Max, 16 Pro / Pro Max, 17 Pro / Pro Max | yes | yes | yes | Stray Scanner (LiDAR); Camera app (video, photo) |
| iPad Pro (2020 or later, LiDAR) | yes | yes | yes | same |
| iPhone 15 / 15 Plus, 16 / 16 Plus / 16e, 17, Air (no LiDAR) | no | yes | yes | Camera app |
| any older iPhone or Android phone | no | runs, untested | runs, untested | camera app; photos need EXIF focal length or the 26 mm default is used |

The tier is chosen by what is handed over, not by the device (see `docs/capture_protocol.md`).

## Processing machine

| Machine | Status |
|---|---|
| Windows 11 laptop, AMD Ryzen 7, 16 GB RAM, no GPU | development and all benchmark runs |
| macOS / Linux, Python 3.11 | expected to work (pure Python + wheels); not run yet |
| GPU | not needed; not used |

Times on the development laptop (CPU only), from `fixloop/round2/after/benchmark.md`:

| Capture | Tier | Time |
|---|---|---|
| whole flat, 215 s scan, 9 rooms | LiDAR | 226 s (damage ~100 s) |
| whole flat, 115 s floor-only scan, 7 rooms | LiDAR | 131 s |
| single room, 37 s scan | LiDAR | 41 s |
| whole flat, 115 s clip | video | ~8 min first run, ~115 s with cached depth |
| 7 rooms, 34 photos | photo | ~3 min |

## Accuracy each tier delivers

| | LiDAR | video | photo |
|---|---|---|---|
| Wall length, synthetic room (exact truth) | < 1 mm | not tested | ±4 cm on a noisy synthetic box |
| Wall length, real capture | **no ground truth yet** | vs LiDAR reference: median error 40 % | vs LiDAR reference: median error 103 % |
| Ceiling height | synthetic < 5 mm; real: no ground truth | not recovered on sample | vs LiDAR reference: up to 0.83 m off |
| Footprint, real capture | two scans of one flat: −0.9 % (sum of rooms), −6.3 % (union) | −92 % vs reference | +136 % vs reference |
| Repeatability, two scans of one flat | point clouds agree to 7 mm median; rooms 9 vs 7, none paired (`fixloop/round2/`) | not measured | not measured |
| Interval coverage (should be ~90 %) | not measured (no truth) | 77 % vs reference | 28 % vs reference |
| Brief's gate | 2 cm openings, 1.5 cm ceiling, 1 cm repeat | ±3 % walls | ±8 % walls, stitched footprint |
| Honest status | geometry is precise; segmentation not yet repeatable | far from gate | far from gate |

### Why the thin tiers are far off on this evidence

- **Input is a stand-in.** The video input is the LiDAR scan's own RGB stream, a fast
  sweep with heavy motion blur. The photos are frames cut from that stream, not stills
  taken from a doorway as the protocol asks. Both make our numbers pessimistic, but we
  have no protocol-following captures to show by how much.
- **Scale comes from a depth model.** Its per-frame scale ranges 0.4×-3.4× against
  LiDAR; the median over many frames is stable (1.09-1.18×) and is bias-corrected. That
  stability is not enough for ±3 %.
- **Intervals are too narrow.** Coverage of 30-54 % means the video and photo terms in
  `src/roomscan/uncertainty/calibration.yaml` must be widened. They are to be refitted on
  real ground truth (`bench/` has the harness; the data is missing).
