# Optional printed scale marker

The video and photo tiers have no depth sensor: their metric scale comes from a
monocular depth model, which is 0.4x-3.4x off on a single image. One sheet of A4
paper per room fixes that. The marker is a black-and-white square of known size.
Wherever it appears in a frame, roomscan measures how far away it is from its size
in pixels, and compares that distance with the model's depth at the same pixels.

The marker is optional. Without it, the pipeline still uses typed-in room sizes or the
model's own scale. A typed length or width takes priority over the marker (see "In the
pipeline" below).

## Print it

1. Make the page: `python scripts/make_marker.py --out-dir .` writes
   `scale_marker.pdf` and `scale_marker.png`, both A4.
2. Print the PDF at **100 % / "actual size"**. Do not use "fit to page" or "shrink to
   printable area". Black and white is fine. Plain office paper is fine, but matt is
   better than glossy because it reflects less glare.
3. **Check the size with a ruler.** The 10 cm ruler under the marker must measure
   exactly 10 cm, and the black square must measure **180 mm** on each side.
   - If it is off by more than 1 mm, fix the printer's scaling and print again.
   - If you cannot fix the printer, measure the black square's side and pass it on,
     for example `side_m=0.176`. Every 1 % of size error becomes 1 % of scale error.
4. Do not fold or crease the marker area. A bent sheet is not a flat square.

## Put it up

- Stick it **flat on a wall** with tape at all four corners. It must not bulge or curl.
  Place it at **chest height**, roughly 1.2-1.5 m above the floor, on a wall the camera
  will see.
- Use **one marker per room**. If you print several, every copy is the same id 0.
  Only one copy should be visible in a room at a time.
- Do not put it behind glass, on a door that moves, or in direct sunlight with glare.

## Capture

- **Photos:** make sure the marker is visible in **a few photos (3 or more)**, taken
  from 1-3 m away and from different positions. Hold the camera roughly facing it:
  less than about 45 degrees off square is ideal, and more than 60 degrees is rejected.
- **Video:** let the marker be in view for **a few seconds** while you walk past at
  1-3 m. Move slowly while it is in view, because motion blur makes frames get rejected.
- The marker must look at least about 40 px wide in the image. On a phone's main camera
  that is reached up to about 4-5 m away; closer is better.

## How it is used (roomscan.markers)

- `detect(image, K)` finds the marker and works out its pose with OpenCV ArUco
  (DICT_4X4_50, id 0) and `solvePnP(IPPE_SQUARE)`. That gives its distance in metres
  and the direction its surface faces. A detection is **rejected** when:
  - its side is under 40 px,
  - it is seen more than 60 degrees off square,
  - it is blurred (the edge width is over 6 % of the side), or
  - its corners are not a flat square.
- `marker_scale(images, Ks, depths)` works out, for every accepted detection, the ratio
  **true depth / model depth**. It takes the median of that ratio over the central
  70 % of the marker. It then combines all the detections:
  - the result is `{"scale", "spread", "n", "quality", ...}`;
  - `scale` is the median of the ratios, and `spread` is their relative MAD;
  - `quality` is "good" when there are 3 or more detections within 3 % of each other;
  - it returns `None` when no usable marker was seen.

Measured accuracy is in `tests/test_markers.py`:

- On synthetic renders, the distance to the marker is within 1 % at 1-2 m. With
  1-3.5 px of blur and noise it is within 1.3 % up to 4 m.
- Pasted onto real walls of the LiDAR sample scan, the marker's scale against the LiDAR
  depth is 1.002 (0.998-1.006 over 11 frames).

The marker measures the depth model's scale *at the marker* to within 1 %. The model's
scale also changes from one frame to the next, and within one frame. So a scale from
one frame only fully applies to depth that has already been made consistent across
frames. The tiers below take care of that.

## In the pipeline

The marker is used automatically when it is seen. Nothing needs to be switched on. When
no photo or frame shows it, nothing changes: detection is a fast ArUco search on the
images already in memory (a few ms per image) and nothing else runs.

- **Photo tier, per room** (`frontends/photos.py`, `apply_scales`). Each room's sweep
  photos are searched, at the working resolution (960 px) with their depth maps. The
  look-back photo is not used, because it shows the previous room. Each photo's depth is
  taken after the room fit's camera-height normalisation, so one ratio from any of the
  room's photos is the room's scale. The result multiplies the fitted room: its
  rectangle, levels, camera height and photo depths. This is the same point where a typed
  length applies, before stitching.
  - A marker only scales **its own room**. Rooms are fitted on their own, and stitching
    does not share a scale, so nothing is passed on to other rooms.
- **Video tier, per clip** (`frontends/video.py`, `clip_marker_scale`). After pose
  solving, the keyframes' depth is consistent along the clip (each keyframe has its
  factor to the map). All detections over the clip are pooled into one scale, metres per
  map unit. That scale replaces the depth model's bias-corrected median scale.
- **Priority:**
  1. a typed length or width (`measurements.yaml`);
  2. the marker;
  3. photo tier only: the median of the rooms with a typed number;
  4. the depth model's own scale.

  A typed height alone is still only compared. When both a typed number and a marker
  exist, the typed number is used and the warnings give the marker's scale for comparison.
- **Intervals** (`uncertainty/intervals.py`). With a marker scale, the tier's relative
  (scale) term `rel` is replaced by `max(spread, 2 %)`:
  - this applies to lengths, heights and opening sizes, and twice that to areas;
  - the floor is `markers.MARKER_REL_FLOOR`, which covers the model's scale changing
    within a frame;
  - the absolute term, inflation and the calibrated per-tier multiplier stay as they are
    (they have not been refitted for marker captures).
- **Output.** `capture.meta.scale` records it:
  - photo: `{"source": "marker" | "known_sizes" | "model" | "mixed", "rooms": {room:
    source}, "marker": {room: {scale, spread, n, quality, rel_sigma}}}`;
  - video: `{"source": ..., "model_scale": ..., "marker": {scale, spread, n, quality,
    rel_sigma}}`;
  - there is also a warning line, for example `room 02_kitchen: scale from A4 marker (n=4,
    spread 1.2 %, good): x0.812 applied to the depth model's scale`.

  For the photo tier, `scale` is the factor applied to the fitted room. For video it is
  metres per map unit, which is the new `metric_scale`.
