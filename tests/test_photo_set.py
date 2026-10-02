"""make_photo_set.py: the benchmark photo set is rebuilt from the scan's video by recipe, byte for byte."""
import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml
from PIL import Image

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import make_photo_set as mps  # noqa: E402


def test_benchmark_recipe_is_the_recorded_set(tmp_path):
    """7 rooms and 28 photos, as in the photo rows of bench/reports; names, frames and truth agree."""
    r = mps.load_recipe()
    photos = r["photos"]
    assert len(photos) == 7 and sum(map(len, photos.values())) == 28
    for folder, ps in photos.items():
        kinds = [p["file"].split("_")[1] for p in ps]
        assert [p["file"] for p in ps] == [f"{k:02d}_{kind}_frame{p['frame']:06d}.jpg"
                                           for k, (kind, p) in enumerate(zip(kinds, ps), start=1)]
        assert "lookback" not in kinds[:-1]
        t = r["truth"][folder]
        assert t["n_sweep"] == kinds.count("sweep") and t["has_look_back"] == (kinds[-1] == "lookback")
        assert all(p["rotate_cw"] in mps.ROTATE_CW and len(p["sha256"]) == 64 for p in ps)
    mps.write_truth(tmp_path / "truth.json", r["truth"])
    assert mps.sha256(tmp_path / "truth.json") == r["truth_sha256"]


def _video(path: Path, n: int = 24) -> None:
    """Frame k is flat grey 10 * k with a white block in the top-left corner."""
    path.parent.mkdir(parents=True)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
    for k in range(n):
        img = np.full((48, 64, 3), 10 * k, np.uint8)
        img[:12, :12] = 255
        vw.write(img)
    vw.release()


def _photo(p: Path):
    im = Image.open(p)
    f35 = im.getexif().get_ifd(0x8769).get(0xA405)
    a = np.asarray(im.convert("L")).astype(float)
    corners = {"tl": a[:4, :4], "tr": a[:4, -4:], "bl": a[-4:, :4], "br": a[-4:, -4:]}
    white = max(corners, key=lambda c: corners[c].mean())
    h, w = a.shape
    return a.shape, f35, white, float(np.median(a[h // 3: 2 * h // 3, w // 3: 2 * w // 3]))


@pytest.mark.filterwarnings("ignore:Corrupt EXIF data")  # PIL on piexif's EXIF block; the focal length reads fine
def test_rebuild_from_recipe(tmp_path):
    video = tmp_path / "scan" / "export" / "rgb.mp4"
    _video(video)
    photo = lambda f, k, rot, f35: {"file": f, "frame": 0, "video_frame": k, "rotate_cw": rot, "focal_35mm": f35,
                                    "sha256": "0" * 64}
    recipe = {"scan": "scan", "jpeg_quality": 93,
              "video": {"path": "export/rgb.mp4", "bytes": video.stat().st_size, "sha256": mps.sha256(video)},
              "photos": {"01_a": [photo("01_sweep.jpg", 7, 90, 29), photo("02_sweep.jpg", 3, 0, 26)],
                         "03_b": [photo("01_sweep.jpg", 20, 270, 29), photo("02_lookback.jpg", 7, 180, 30)]},
              "truth": {"01_a": {"lidar_room": "room_2", "parent": None, "n_sweep": 2, "has_look_back": False,
                                 "sweep_angles_deg": [-7.0, 0.0]},
                        "02_gone": {"lidar_room": "room_1", "parent": "room_2", "n_sweep": 1,
                                    "has_look_back": False, "sweep_angles_deg": [3.3]}},
              "truth_sha256": "0" * 64}
    rfile = tmp_path / "recipe.yaml"
    rfile.write_text(yaml.safe_dump(recipe, sort_keys=False))
    scan = str(tmp_path / "scan")

    out1 = tmp_path / "set1"
    assert mps.main([scan, str(out1), "--recipe", str(rfile)]) == 0  # wrong hashes: a warning, not a failure
    assert not (tmp_path / "set1.part").exists()
    # the right frame by position, rotated clockwise, focal length in EXIF
    assert _photo(out1 / "01_a" / "01_sweep.jpg")[:3] == ((64, 48), 29, "tr")
    assert _photo(out1 / "01_a" / "02_sweep.jpg")[:3] == ((48, 64), 26, "tl")
    assert _photo(out1 / "03_b" / "01_sweep.jpg")[:3] == ((64, 48), 29, "bl")
    assert _photo(out1 / "03_b" / "02_lookback.jpg")[:3] == ((48, 64), 30, "br")
    for f, k in [("01_a/01_sweep.jpg", 7), ("01_a/02_sweep.jpg", 3), ("03_b/01_sweep.jpg", 20),
                 ("03_b/02_lookback.jpg", 7)]:
        assert abs(_photo(out1 / f)[3] - 10 * k) <= 3, f
    truth = (out1 / "truth.json").read_bytes()
    assert json.loads(truth) == recipe["truth"] and truth.count(b"\r\n") == truth.count(b"\n")

    # record the hashes; a second build is byte-identical and passes the check
    for folder, ps in recipe["photos"].items():
        for p in ps:
            p["sha256"] = mps.sha256(out1 / folder / p["file"])
    recipe["truth_sha256"] = hashlib.sha256(truth).hexdigest()
    rfile.write_text(yaml.safe_dump(recipe, sort_keys=False))
    out2 = tmp_path / "set2"
    out2.mkdir()  # an empty folder may exist
    assert mps.main([str(video.parent), str(out2), "--recipe", str(rfile)]) == 0  # export folder given directly
    assert mps.main(["--check", str(out2), "--recipe", str(rfile)]) == 0
    assert mps.check(out1, recipe) == []

    (out2 / "03_b" / "02_lookback.jpg").write_bytes(b"x")
    (out2 / "Thumbs.db").write_bytes(b"x")
    (out2 / "01_a" / "01_sweep.jpg").unlink()
    assert mps.check(out2, recipe) == ["missing: 01_a/01_sweep.jpg", "differs: 03_b/02_lookback.jpg",
                                       "not in the recipe: Thumbs.db"]
    assert mps.main(["--check", str(out2), "--recipe", str(rfile)]) == 1

    with pytest.raises(SystemExit, match="already exists"):  # never written into an existing set
        mps.main([scan, str(out2), "--recipe", str(rfile)])
    b = bytearray(video.read_bytes())
    b[-1] ^= 0xFF  # same size, other content
    video.write_bytes(bytes(b))
    with pytest.raises(SystemExit, match="not the video"):
        mps.main([scan, str(tmp_path / "set3"), "--recipe", str(rfile)])
    assert not (tmp_path / "set3").exists() and not (tmp_path / "set3.part").exists()
