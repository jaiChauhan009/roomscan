# Raw benchmark data (deliverable 8)

The repository holds code, ground truth and checksums. The data is too large for git and is
collected from three places:

| Data | Size | How to get it | Truth |
|---|---|---|---|
| Sample flat: two whole-flat LiDAR scans, one room scan, the upright clip and the photo set derived from them | ~1 GB | `uv run python scripts/fetch_data.py` (public Google Drive folder); the photo set and clip are rebuilt by the `prepare:` steps in `bench/manifest.yaml` | none: the video and photo tiers are scored against our LiDAR output |
| ARKitScenes: 4 rooms (iPad Pro LiDAR) with Faro laser scans | 1.8 GB | `uv run python scripts/fetch_arkitscenes.py` | `bench/ground_truth/arkit_*.yaml`, made by `bench/make_laser_truth.py` |
| **Our own captures**: iPhone 16 Pro LiDAR scans, iPhone and moto g45 videos and photos | 1.3 GB | `roomscan_own_captures.zip`, shared by link (made by `scripts/pack_raw_data.py`) | tape truth pending (`docs/tape_form_own_flat.md`) |

## Our own captures

| Folder in the zip | Device | Benchmark name |
|---|---|---|
| `own/lidar_1/609c9be5d1` | iPhone 16 Pro, Stray Scanner, whole flat (147 s) | `own_lidar_1` |
| `own/lidar_2/51b6138385` | same, part of the flat (55 s) | `own_lidar_2` |
| `own/lidar_3/84d7fdb836` | same, room 4 alone (31 s): repeat of `own_lidar_1` | `own_lidar_3` |
| `own/video_1` | moto g45, 84 s, 1080p | `own_video_1` |
| `own/photos_1` | moto g45, one folder per room | `own_photos_1` |
| `own/iphone_video_1` | iPhone 16 Pro, received through WhatsApp | `own_video_iphone` |
| `own/iphone_photos_1` | iPhone 16 Pro, received through WhatsApp (EXIF stripped) | `own_photos_iphone` |
| `iphone/` | copy log of the iPhone session | |

## Make, share and check the zip

```
uv run python scripts/pack_raw_data.py                 # writes ../submission/roomscan_own_captures.zip
uv run python scripts/pack_raw_data.py --verify <zip>  # checks every file against bench/raw_data.sha256
```

- Upload the zip to Google Drive (or similar), set "anyone with the link can view", and put the link in the submission.
- `bench/raw_data.sha256` is committed: a download that verifies is byte-identical to the captures we scored.

## Reproducing the numbers

1. Unzip next to the repository as `data/`, so that `data/own/...` exists.
2. Fetch the rest with `fetch_data.py` and `fetch_arkitscenes.py`.
3. Run `uv run python bench/run_all.py`.

The manifest paths are relative to `data/`.

## Not included yet

- Consumer-app exports for the head-to-head (needs an iPhone).
- Tape measurements of our flat.
- Real staged damage.
