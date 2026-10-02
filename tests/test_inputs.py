"""Every input a reviewer's iPhone 15+ produces is accepted, or rejected with a clear error.

All inputs are generated here (no binaries in the repository):
  * HEIC photos with pillow_heif (its x265 encoder), JPEG/PNG with Pillow
  * HEVC clips: x265-coded stills from pillow_heif put into a minimal MOV/MP4 container,
    so HEVC decoding, the rotation flag and variable frame rate are tested without ffmpeg
  * Stray Scanner exports: depth rendered from a box room, odometry, confidence, video
"""
from __future__ import annotations

import io
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from roomscan.frontends import lidar_stray, photos, video
from roomscan.pipeline import InputError, detect_tier, prepare_input

SRC = Path(__file__).resolve().parent.parent / "src"
NON_ASCII = "Wohnung Küche łazienka 日本"  # spaces, Latin-1, Latin Extended, CJK
FAST_X265 = {"enc_params": {"preset": "ultrafast"}}  # test files only: 6x faster to encode


# ---------------------------------------------------------------- photos

def pattern(w: int, h: int) -> np.ndarray:
    """Upright test picture: red block top-left, blue block bottom-right."""
    a = np.full((h, w, 3), 200, np.uint8)
    a[: h // 4, : w // 4] = (255, 0, 0)
    a[-h // 4:, -w // 4:] = (0, 0, 255)
    return a


def upright(img: np.ndarray) -> bool:
    h, w = img.shape[:2]
    tl = img[: h // 8, : w // 8].reshape(-1, 3).mean(0)
    br = img[-h // 8:, -w // 8:].reshape(-1, 3).mean(0)
    return tl[0] > 200 > 60 > tl[2] and br[2] > 200 > 60 > br[0]


def exif(orientation: int = 1, f35: int = 26) -> bytes:
    ex = Image.Exif()
    ex[0x0112] = orientation
    ex[0x010F], ex[0x0110] = "Apple", "iPhone 15 Pro"
    ex.get_ifd(0x8769)[0xA405] = f35
    return ex.tobytes()


def stored(up: np.ndarray, orientation: int) -> Image.Image:
    """Pixels as a camera stores them when `orientation` must be applied to show `up`."""
    im = Image.fromarray(up)
    return {6: im.transpose(Image.Transpose.ROTATE_90), 8: im.transpose(Image.Transpose.ROTATE_270),
            3: im.transpose(Image.Transpose.ROTATE_180)}.get(orientation, im)


def save_photo(p: Path, orientation: int = 1, size=(300, 400), fmt: str | None = None) -> Path:
    import pillow_heif
    pillow_heif.register_heif_opener()
    p.parent.mkdir(parents=True, exist_ok=True)
    fmt = fmt or {".heic": "HEIF", ".heif": "HEIF", ".png": "PNG"}.get(p.suffix.lower(), "JPEG")
    im = stored(pattern(*size), orientation)
    kw = {} if fmt == "PNG" else {"exif": exif(orientation), "quality": 85}
    im.save(p, format=fmt, **kw, **(FAST_X265 if fmt == "HEIF" else {}))
    return p


@pytest.mark.parametrize("name,orientation", [("IMG_0001.HEIC", 6), ("IMG_0002.heic", 8), ("IMG_0003.HEIF", 1),
                                              ("IMG_0004.JPG", 6), ("IMG_0005.JPEG", 8), ("IMG_0006.jpg", 3)])
def test_photo_formats_and_exif_orientation(tmp_path, name, orientation):
    p = save_photo(tmp_path / name, orientation)
    img, K, meta = photos.read_photo(p)
    assert img.shape == (960, 720, 3) and upright(img)
    assert meta == {"f35": 26.0, "focal_source": "exif"}
    assert K[0, 2] == 360 and K[1, 2] == 480


def test_tiled_heic_as_an_iphone_writes_it(tmp_path, monkeypatch):
    """iPhone HEIC: a grid of tiles, an `irot` rotation property and EXIF orientation 6 in
    the file. libheif applies irot and pillow_heif reports orientation 1: rotated once."""
    import pillow_heif
    monkeypatch.setattr(pillow_heif.options, "GRID_TILE_SIZE", 256)
    p = save_photo(tmp_path / "IMG_0001.HEIC", 6, size=(600, 800))
    data = p.read_bytes()
    assert b"irot" in data and b"grid" in data
    img, _, meta = photos.read_photo(p)
    assert img.shape == (960, 720, 3) and upright(img) and meta["focal_source"] == "exif"


def test_png_screenshot_with_alpha(tmp_path):
    p = tmp_path / "Screenshot.PNG"
    Image.fromarray(np.dstack([pattern(300, 400), np.full((400, 300), 255, np.uint8)])).save(p)
    img, _, meta = photos.read_photo(p)
    assert img.shape == (960, 720, 3) and upright(img) and meta["focal_source"] == "default"


def test_heic_named_jpg_in_a_fresh_process(tmp_path):
    """A HEIC renamed .JPG (or the first photo of a run being HEIC) must still open."""
    p = save_photo(tmp_path / "IMG_0001.JPG", 6, fmt="HEIF")
    code = ("import sys; from pathlib import Path; from roomscan.frontends.photos import read_photo; "
            "img, K, m = read_photo(Path(sys.argv[1])); print(img.shape)")
    r = subprocess.run([sys.executable, "-c", code, str(p)], capture_output=True, text=True,
                       env=dict(os.environ, PYTHONPATH=str(SRC)), timeout=120)
    assert r.returncode == 0, r.stderr
    assert "(960, 720, 3)" in r.stdout


@pytest.mark.parametrize("w,h,fmt", [(8064, 6048, "JPEG"), (4032, 3024, "HEIF")])  # 48 MP JPEG, 12 MP HEIC
def test_large_photos_are_shrunk_while_decoding(tmp_path, w, h, fmt):
    import pillow_heif
    pillow_heif.register_heif_opener()
    up = np.empty((w, h, 3), np.uint8)  # upright portrait: w rows, h columns
    up[..., 0] = np.linspace(0, 255, h).astype(np.uint8)[None, :]
    up[..., 1] = np.linspace(0, 255, w).astype(np.uint8)[:, None]
    up[..., 2] = 128
    up[: w // 4, : h // 4] = (255, 0, 0)
    up[-w // 4:, -h // 4:] = (0, 0, 255)
    p = tmp_path / f"IMG_0001.{'JPG' if fmt == 'JPEG' else 'HEIC'}"
    stored(up, 6).save(p, format=fmt, exif=exif(6), quality=30, **(FAST_X265 if fmt == "HEIF" else {}))
    del up
    t = time.time()
    img, _, meta = photos.read_photo(p)
    dt = time.time() - t
    assert img.shape == (960, 720, 3) and upright(img) and meta["focal_source"] == "exif"
    assert dt < 15.0  # unloaded laptop: 48 MP JPEG 0.6 s (draft decode), 12 MP HEIC < 1 s


def test_unreadable_photos_raise_input_error(tmp_path):
    bad = tmp_path / "IMG_0001.JPG"
    bad.write_bytes(b"not a jpeg at all" * 10)
    trunc = save_photo(tmp_path / "IMG_0002.JPG", size=(600, 800))
    trunc.write_bytes(trunc.read_bytes()[:2000])
    empty = tmp_path / "IMG_0003.HEIC"
    empty.write_bytes(b"")
    for p in (bad, trunc, empty):
        with pytest.raises(InputError, match="cannot read photo"):
            photos.read_photo(p)
    # a capture whose photos are all unreadable stops with one clear message, before any model runs
    with pytest.raises(InputError, match="none of the photos"):
        photos.run_photo_tier(tmp_path, tmp_path / "out", use_cache=False, progress=False, damage=False)


def iphone_room(d: Path, n: int = 3, start: int = 1) -> list[Path]:
    """Photos as they arrive from an iPhone: HEIC + Live Photo .MOV + .AAE, plus OS clutter."""
    real = []
    for i in range(start, start + n):
        real.append(save_photo(d / f"IMG_{i:04d}.HEIC", 6))
        (d / f"IMG_{i:04d}.MOV").write_bytes(b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 64)  # Live Photo part
        (d / f"IMG_{i:04d}.AAE").write_text("<?xml version='1.0'?><plist/>")
        (d / f"._IMG_{i:04d}.HEIC").write_bytes(b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X" + b"\x00" * 64)
    for junk in (".DS_Store", "Thumbs.db", "desktop.ini"):
        (d / junk).write_bytes(b"\x00junk")
    return real


def test_photo_folders_with_live_photos_sidecars_and_os_files(tmp_path):
    root = tmp_path / "flat"
    hall = iphone_room(root / "01_hall")
    kitchen = iphone_room(root / "02_kitchen", start=10)
    save_photo(root / "02_kitchen" / "IMG_E0010.JPG")  # edited copy exported next to the original
    save_photo(root / "02_kitchen" / "IMG_0011.JPG")  # same photo in a second format
    (root / "__MACOSX" / "01_hall").mkdir(parents=True)
    (root / "__MACOSX" / "01_hall" / "._IMG_0001.HEIC").write_bytes(b"\x00\x05\x16\x07")
    (root / ".DS_Store").write_bytes(b"\x00")
    assert detect_tier(root) == "photo"
    assert detect_tier(root / "01_hall") == "photo"  # Live Photo .MOV next to photos: still photos
    warnings: list[str] = []
    rooms = photos.find_rooms(root, warnings)
    assert rooms == {"01_hall": hall, "02_kitchen": kitchen}
    assert any("IMG_E0010.JPG" in w and "IMG_0011.JPG" in w for w in warnings)
    for f in hall + kitchen:
        assert upright(photos.read_photo(f)[0])


def test_rooms_and_photos_in_natural_order(tmp_path):
    for room in ("room 10", "Room 2", "room 1"):
        for name in ("10.jpg", "2.JPG", "1.jpg"):
            save_photo(tmp_path / room / name)
    rooms = photos.find_rooms(tmp_path)
    assert list(rooms) == ["room 1", "Room 2", "room 10"]
    assert [p.name for p in rooms["room 1"]] == ["1.jpg", "2.JPG", "10.jpg"]


def test_wrapped_folders_and_single_photo(tmp_path):
    # Explorer "Extract all" of flat.zip gives flat/flat/<rooms>
    iphone_room(tmp_path / "flat" / "flat" / "01_hall", n=2)
    assert detect_tier(tmp_path / "flat") == "photo"
    assert list(photos.find_rooms(tmp_path / "flat")) == ["01_hall"]
    one = save_photo(tmp_path / "one" / "IMG_0042.HEIC", 6)
    assert detect_tier(one) == "photo"
    assert photos.find_rooms(one) == {"IMG_0042": [one]}


def test_proraw_dng_is_rejected_clearly(tmp_path):
    dng = tmp_path / "raw" / "01_hall" / "IMG_0001.DNG"
    dng.parent.mkdir(parents=True)
    dng.write_bytes(b"II*\x00\x08\x00\x00\x00" + b"\x00" * 256)  # TIFF header, as a DNG starts
    for p in (dng, tmp_path / "raw", tmp_path / "raw" / "01_hall"):
        with pytest.raises(InputError, match="ProRAW"):
            detect_tier(p)
    with pytest.raises(InputError, match="ProRAW"):
        photos.find_rooms(tmp_path / "raw")
    save_photo(dng.parent / "IMG_0002.JPG")  # mixed: the JPEG is used, the DNG reported
    warnings: list[str] = []
    assert [p.name for p in photos.find_rooms(tmp_path / "raw", warnings)["01_hall"]] == ["IMG_0002.JPG"]
    assert any(".dng" in w for w in warnings)


# ---------------------------------------------------------------- video (HEVC without ffmpeg)

def _box(typ: bytes, *parts: bytes) -> bytes:
    body = b"".join(parts)
    return struct.pack(">I", 8 + len(body)) + typ + body


def _full(typ: bytes, version: int, flags: int, *parts: bytes) -> bytes:
    return _box(typ, struct.pack(">I", (version << 24) | flags), *parts)


# tkhd matrices; 90 = what an iPhone writes for a clip filmed upright (ffmpeg: rotation -90)
_MATRIX = {0: (0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000), 90: (0, 0x10000, 0, -0x10000, 0, 0, 0, 0, 0x40000000)}


def hevc_still(img: np.ndarray) -> tuple[bytes, bytes]:
    """x265-coded picture via pillow_heif: (hvcC box, length-prefixed NAL units)."""
    import pillow_heif
    b = io.BytesIO()
    pillow_heif.from_pillow(Image.fromarray(img)).save(b, quality=60, **FAST_X265)
    data = b.getvalue()
    k = data.find(b"hvcC")
    hvcc = data[k - 4: k - 4 + struct.unpack(">I", data[k - 4: k])[0]]
    i = 0
    while i + 8 <= len(data):
        size, typ = struct.unpack(">I4s", data[i: i + 8])
        if typ == b"mdat":
            return hvcc, data[i + 8: i + size]
        i += size
    raise AssertionError("no mdat in HEIC")


def write_hevc_clip(path: Path, frames: list[np.ndarray], deltas: list[int], timescale: int = 600,
                    rotation: int = 0, n: int | None = None) -> Path:
    """MOV (.mov: QuickTime brand, as an iPhone writes) or MP4 with an hvc1 track. `frames`
    are the stored pictures (all intra coded), repeated cyclically up to n samples."""
    coded = [hevc_still(f) for f in frames]
    hvcc = coded[0][0]
    assert all(c[0] == hvcc for c in coded)  # same parameter sets for every picture
    n = n or len(deltas)
    samples = [coded[i % len(coded)][1] for i in range(n)]
    h, w = frames[0].shape[:2]
    qt = path.suffix.lower() == ".mov"
    ftyp = _box(b"ftyp", b"qt  ", struct.pack(">I", 0x200), b"qt  ") if qt else \
        _box(b"ftyp", b"isom", struct.pack(">I", 0x200), b"isom", b"iso2", b"mp41")
    mdat = _box(b"mdat", *samples)
    dur = sum(deltas)
    runs: list[list[int]] = []
    for d in deltas:
        if runs and runs[-1][1] == d:
            runs[-1][0] += 1
        else:
            runs.append([1, d])
    entry = _box(b"hvc1", b"\0" * 6, struct.pack(">H", 1), b"\0" * 16, struct.pack(">HH", w, h),
                 struct.pack(">III", 0x480000, 0x480000, 0), struct.pack(">H", 1), b"\0" * 32,
                 struct.pack(">Hh", 0x18, -1), hvcc)
    stbl = _box(b"stbl", _full(b"stsd", 0, 0, struct.pack(">I", 1), entry),
                _full(b"stts", 0, 0, struct.pack(">I", len(runs)), *(struct.pack(">II", c, d) for c, d in runs)),
                _full(b"stsc", 0, 0, struct.pack(">IIII", 1, 1, n, 1)),
                _full(b"stsz", 0, 0, struct.pack(">II", 0, n), *(struct.pack(">I", len(s)) for s in samples)),
                _full(b"stco", 0, 0, struct.pack(">II", 1, len(ftyp) + 8)))
    minf = _box(b"minf", _full(b"vmhd", 0, 1, b"\0" * 8),
                _box(b"dinf", _full(b"dref", 0, 0, struct.pack(">I", 1), _full(b"url ", 0, 1))), stbl)
    mdia = _box(b"mdia", _full(b"mdhd", 0, 0, struct.pack(">IIII", 0, 0, timescale, dur), struct.pack(">HH", 0x55C4, 0)),
                _full(b"hdlr", 0, 0, b"\0" * 4, b"vide", b"\0" * 12, b"VideoHandler\0"), minf)
    tkhd = _full(b"tkhd", 0, 7, struct.pack(">IIIII", 0, 0, 1, 0, dur), b"\0" * 8, b"\0" * 8,
                 struct.pack(">9i", *_MATRIX[rotation]), struct.pack(">II", w << 16, h << 16))
    mvhd = _full(b"mvhd", 0, 0, struct.pack(">IIII", 0, 0, timescale, dur), struct.pack(">IH", 0x10000, 0x100),
                 b"\0" * 10, struct.pack(">9i", *_MATRIX[0]), b"\0" * 24, struct.pack(">I", 2))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ftyp + mdat + _box(b"moov", mvhd, _box(b"trak", tkhd, mdia)))
    return path


@pytest.fixture(scope="module")
def portrait_frames() -> list[np.ndarray]:
    """Upright 240x320 pictures stored the way an iPhone stores them: landscape, turned."""
    rng = np.random.default_rng(0)
    tex = cv2.resize(rng.integers(0, 255, (60, 80), dtype=np.uint8), (640, 480), interpolation=cv2.INTER_CUBIC)
    out = []
    for i in range(4):  # a textured scene panning sideways, so tracking has corners to follow
        up = np.repeat(tex[80: 400, 40 + 12 * i: 280 + 12 * i, None], 3, axis=2).copy()
        up[:40, :60] = (255, 0, 0)
        out.append(np.ascontiguousarray(np.rot90(up, 1)))  # stored 320x240
    return out


def test_iphone_hevc_portrait_mov_is_decoded_upright(tmp_path, portrait_frames):
    clip = write_hevc_clip(tmp_path / "IMG_0001.MOV", portrait_frames, [20] * 45, rotation=90)  # 30 fps
    info = video.probe_video(clip)
    assert info["codec"] == "hevc" and info["rotation"] == 90 and info["size"] == (240, 320)
    tr = video.track_video(clip)
    assert tr["n_frames"] == 45 and tr["kfs"]
    img = cv2.imdecode(tr["kfs"][0]["jpg"], cv2.IMREAD_COLOR)[:, :, ::-1]
    assert img.shape[0] > img.shape[1]  # portrait
    assert img[:10, :10, 0].mean() > 200 and img[:10, :10, 2].mean() < 60  # red corner top-left: upright
    assert tr["duration_s"] == pytest.approx(44 / 30, abs=1e-3)


def test_hevc_mp4_variable_and_high_frame_rate(tmp_path, portrait_frames):
    # 60 fps with every third frame held twice as long (variable frame rate)
    deltas = [20 if i % 3 == 0 else 10 for i in range(60)]
    clip = write_hevc_clip(tmp_path / "clip.MP4", portrait_frames, deltas)
    pts = np.concatenate([[0], np.cumsum(deltas)[:-1]]) / 600
    tr = video.track_video(clip)
    assert tr["n_frames"] == 60
    t = np.array([k["t"] for k in tr["kfs"]])
    assert np.all(np.diff(t) > 0)
    assert np.allclose(t, pts[[k["frame"] for k in tr["kfs"]]], atol=1e-3)  # real times, not i / fps


def test_video_folder_and_unusable_videos(tmp_path, portrait_frames):
    # a folder holding one clip is the video tier; two clips cannot be guessed between
    one = tmp_path / "one"
    write_hevc_clip(one / "IMG_0100.MOV", portrait_frames[:1], [20] * 3)
    (one / ".DS_Store").write_bytes(b"\x00")
    assert detect_tier(one) == "video" and video.find_video(one).name == "IMG_0100.MOV"
    write_hevc_clip(one / "IMG_0101.MP4", portrait_frames[:1], [20] * 3)
    with pytest.raises(InputError, match="holds 2 videos"):
        video.find_video(one)
    # too short to show a room
    with pytest.raises(InputError, match="too short"):
        video.load_video(one / "IMG_0100.MOV", use_cache=False, progress=False)
    # damaged files
    garbage = tmp_path / "bad.MOV"
    garbage.write_bytes(os.urandom(4096))
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    good = write_hevc_clip(tmp_path / "cut.mov", portrait_frames[:1], [20] * 10)
    good.write_bytes(good.read_bytes()[:-200])  # copy interrupted: index (moov) at the end is cut
    for p, msg in [(garbage, "cannot open video"), (empty, "empty"), (good, "cannot")]:
        with pytest.raises(InputError, match=msg):
            video.probe_video(p)


def _ffmpeg() -> str | None:
    """A real ffmpeg with libx265, if one is around (ROOMSCAN_FFMPEG, imageio-ffmpeg, PATH)."""
    exe = os.environ.get("ROOMSCAN_FFMPEG")
    if not exe:
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            exe = shutil.which("ffmpeg")
    return exe if exe and Path(exe).exists() else None


@pytest.mark.skipif(_ffmpeg() is None, reason="no ffmpeg with libx265 (set ROOMSCAN_FFMPEG to run)")
def test_ffmpeg_iphone_like_hdr_hevc_4k60_portrait(tmp_path):
    """HEVC Main 10, HLG / BT.2020 (iPhone HDR video default), 4K 60 fps, rotation flag."""
    up = np.full((3840, 2160, 3), 128, np.uint8)
    up[:480, :480] = (0, 0, 255)  # BGR red, top-left of the upright picture
    raw = tmp_path / "raw.mov"
    st = np.ascontiguousarray(np.rot90(up, 1))
    cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "3840x2160", "-r", "60",
           "-i", "-", "-c:v", "libx265", "-tag:v", "hvc1", "-x265-params", "log-level=error", "-pix_fmt", "yuv420p10le",
           "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc", str(raw)]
    subprocess.run(cmd, input=st.tobytes() * 6, check=True, timeout=300)
    clip = tmp_path / "IMG_0200.MOV"
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-display_rotation", "-90", "-i", str(raw), "-c", "copy",
                    str(clip)], check=True, timeout=120)
    info = video.probe_video(clip)
    assert info["codec"] == "hevc" and info["rotation"] == 90 and info["size"] == (2160, 3840)
    assert info["fps"] == pytest.approx(60)


# ---------------------------------------------------------------- LiDAR (Stray Scanner)

ROOM = np.array([[-2.0, 0.0, -1.5], [2.0, 2.5, 1.5]])  # box room, +Y up, metres
K_RGB = np.array([[1600.0, 0, 960], [0, 1600.0, 720], [0, 0, 1]])  # at 1920 x 1440


def _rot(yaw: float, pitch: float) -> np.ndarray:
    cy, sy, cp, sp = np.cos(yaw), np.sin(yaw), np.cos(pitch), np.sin(pitch)
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    return Ry @ Rx @ np.diag([1.0, -1.0, -1.0])  # OpenCV camera -> world, looking along -Z


def _quat(R: np.ndarray) -> tuple[float, float, float, float]:
    from scipy.spatial.transform import Rotation
    return tuple(Rotation.from_matrix(R).as_quat())  # x, y, z, w


def _render_depth(R: np.ndarray, C: np.ndarray, K: np.ndarray, rng, w: int = 256, h: int = 192) -> np.ndarray:
    u, v = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    rays = np.stack([(u - K[0, 2]) / K[0, 0], (v - K[1, 2]) / K[1, 1], np.ones_like(u)], -1)
    D = rays @ R.T
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(D > 0, (ROOM[1] - C) / D, (ROOM[0] - C) / D)
    t = np.where(np.isfinite(t) & (t > 0), t, np.inf).min(-1)
    t = t * (1 + rng.normal(0, 0.005, t.shape))  # LiDAR-like noise, 0.5 % of range
    return np.clip(t * 1000, 0, 65535).astype(np.uint16)  # z depth in mm (rays have z = 1)


def make_stray(root: Path, n_yaw: int = 16, confidence: bool = True, imu: bool = True, rgb: bool = True,
               old_odometry: bool = False) -> Path:
    """A small Stray Scanner export of the box room: the camera turns around twice in the
    middle, once level and once tilted down (floor), once tilted up (ceiling)."""
    (root / "depth").mkdir(parents=True)
    if confidence:
        (root / "confidence").mkdir()
    Kd = K_RGB.copy()
    Kd[:2] *= 256 / 1920
    C = np.array([0.3, 1.4, 0.2])
    rng = np.random.default_rng(0)
    rows = []
    poses = [(y, p) for p in (0.0, -0.6, 0.6) for y in np.linspace(0, 2 * np.pi, n_yaw, endpoint=False)]
    for i, (yaw, pitch) in enumerate(poses):
        R = _rot(yaw, pitch)
        d = _render_depth(R, C, Kd, rng)
        cv2.imencode(".png", d)[1].tofile(str(root / "depth" / f"{i:06d}.png"))
        if confidence:
            cv2.imencode(".png", np.full(d.shape, 2, np.uint8))[1].tofile(str(root / "confidence" / f"{i:06d}.png"))
        qx, qy, qz, qw = _quat(R)
        pose = f"{1000 + i / 10:.6f}, {i:06d}, {C[0]}, {C[1]}, {C[2]}, {qx}, {qy}, {qz}, {qw}"
        rows.append(pose if old_odometry else pose + f", 1600.0, 1600.0, 960.0, 720.0, , ")
    head = "timestamp, frame, x, y, z, qx, qy, qz, qw" + ("" if old_odometry else
                                                          ", fx, fy, cx, cy, distortion_center_x, distortion_center_y")
    (root / "odometry.csv").write_text("\n".join([head] + rows) + "\n")
    (root / "camera_matrix.csv").write_text("1600.0, 0.0, 960.0\n0.0, 1600.0, 720.0\n0.0, 0.0, 1.0")
    if imu:
        (root / "imu.csv").write_text("timestamp, a_x, a_y, a_z, alpha_x, alpha_y, alpha_z\n1000.0, 0, -1, 0, 0, 0, 0\n")
    if rgb:  # few frames are enough: colour is looked up by frame number and may be missing
        vw = cv2.VideoWriter(str(root / "rgb.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 10, (1920, 1440))
        for _ in range(3):
            vw.write(np.full((1440, 1920, 3), 120, np.uint8))
        vw.release()
    return root


def _zip(src: Path, dest: Path, top: bool = True) -> Path:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in src.rglob("*"):
            z.write(p, p.relative_to(src.parent if top else src))
        z.writestr("__MACOSX/._x", b"\x00\x05\x16\x07")
    return dest


def test_stray_export_layouts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # zips are extracted under ./.cache
    scan = make_stray(tmp_path / "exports" / "c7d28f72c6")
    shutil.copytree(scan, tmp_path / "dl" / "c7d28f72c6" / "c7d28f72c6")  # Explorer "Extract all" doubles it
    zipped = _zip(scan, tmp_path / "c7d28f72c6.zip")
    (tmp_path / "handed over").mkdir()
    shutil.copy(zipped, tmp_path / "handed over" / zipped.name)
    variants = {
        "folder": scan,
        "parent": tmp_path / "exports",
        "two levels down": tmp_path / "dl",
        "zip": prepare_input(zipped),
        "zip without top folder": prepare_input(_zip(scan, tmp_path / "flat.ZIP", top=False)),
        "folder holding the zip": prepare_input(tmp_path / "handed over"),
    }
    ref = lidar_stray.load_stray(scan)
    for name, p in variants.items():
        assert detect_tier(p) == "lidar", name
        cap = lidar_stray.load_stray(p)
        assert len(cap.frames) == 48 and cap.meta["input_warnings"] == [], name
        assert np.allclose(cap.frames[5].K, ref.frames[5].K) and np.allclose(cap.frames[5].T_wc, ref.frames[5].T_wc)
        assert np.array_equal(cap.frames[5].depth_fn(), ref.frames[5].depth_fn()), name
    # a reused extraction: same folder, nothing extracted twice
    assert prepare_input(zipped) == variants["zip"]
    two = tmp_path / "two"
    shutil.copytree(scan, two / "a")
    shutil.copytree(scan, two / "b")
    with pytest.raises(InputError, match="2 Stray Scanner recordings"):
        detect_tier(two)
    shutil.rmtree(two / "a" / "depth")
    shutil.rmtree(two / "b")
    with pytest.raises(InputError, match="without its depth/"):
        detect_tier(two)
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"PK\x03\x04 this is not really a zip")
    with pytest.raises(InputError, match="not a readable zip"):
        prepare_input(bad)


def test_stray_export_missing_optional_parts(tmp_path):
    scan = make_stray(tmp_path / f"{NON_ASCII}" / "scan", confidence=False, imu=False, rgb=False, old_odometry=True)
    cap = lidar_stray.load_stray(scan.parent)
    assert len(cap.frames) == 48
    f = cap.frames[0]
    assert f.conf_fn is None and f.rgb_fn is None
    assert np.allclose(f.K, K_RGB * np.array([[256 / 1920], [192 / 1440], [1]]))  # from camera_matrix.csv
    d = f.depth_fn()
    assert d.shape == (192, 256) and 0.5 < np.median(d) < 4.0  # read from a non-ASCII path
    w = " ".join(cap.meta["input_warnings"])
    assert "confidence" in w and "rgb.mp4" in w


def test_lidar_zip_end_to_end_from_the_cli(tmp_path, monkeypatch):
    """`roomscan run <export>.zip` in a folder with spaces and non-ASCII characters, no
    confidence/ and no imu.csv: one room of the right size, UTF-8 result.json."""
    from typer.testing import CliRunner

    from roomscan.cli import app
    monkeypatch.chdir(tmp_path)
    scan = make_stray(tmp_path / "src" / "c7d28f72c6", n_yaw=12, confidence=False, imu=False)
    (tmp_path / NON_ASCII).mkdir()
    z = _zip(scan, tmp_path / NON_ASCII / "c7d28f72c6.zip")
    out = tmp_path / NON_ASCII / "out"
    r = CliRunner().invoke(app, ["run", str(z), "--out", str(out), "--stride", "1", "--no-damage", "--no-cache", "-q"])
    assert r.exit_code == 0, r.output
    res = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert res["capture"]["tier"] == "lidar"
    assert any("confidence" in w for w in res["warnings"])
    assert len(res["rooms"]) == 1
    assert res["property"]["footprint_area"]["value"] == pytest.approx(12.0, abs=0.6)
    assert (out / "plan.png").exists()


# ---------------------------------------------------------------- CLI: unusable input

def _cli(args):
    from typer.testing import CliRunner

    from roomscan.cli import app
    return CliRunner().invoke(app, ["run", *map(str, args)])


def _one_line_error(r) -> str:
    lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
    assert len(lines) == 1 and lines[0].startswith("error: "), r.output
    assert "Traceback" not in r.output
    return lines[0]


def test_cli_unusable_input_is_one_clear_line(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "empty").mkdir()
    (tmp_path / "notes.txt").write_text("hello")
    (tmp_path / "IMG_0001.MOV").write_bytes(os.urandom(2048))
    (tmp_path / "scan.zip").write_bytes(b"garbage")
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "IMG_0001.DNG").write_bytes(b"II*\x00" + b"\x00" * 64)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.pdf").write_bytes(b"%PDF-1.4")
    cases = [([tmp_path / "missing"], "does not exist"), ([tmp_path / "empty"], "empty"),
             ([tmp_path / "notes.txt"], "unsupported file type .txt"), ([tmp_path / "IMG_0001.MOV"], "cannot open video"),
             ([tmp_path / "scan.zip"], "not a readable zip"), ([tmp_path / "raw"], "ProRAW"),
             ([tmp_path / "docs"], "1 .pdf"), ([tmp_path / "empty", "--tier", "foo"], "unknown tier")]
    for args, msg in cases:
        r = _cli(args)
        assert r.exit_code == 2, (args, r.output)
        assert msg in _one_line_error(r), (args, r.output)


def test_cli_internal_failure_is_one_line_with_saved_traceback(tmp_path, monkeypatch):
    import roomscan.pipeline

    def boom(*a, **k):
        raise RuntimeError("something\nbroke")
    monkeypatch.setattr(roomscan.pipeline, "run", boom)
    r = _cli([tmp_path, "--out", tmp_path / "o"])
    assert r.exit_code == 1
    assert "RuntimeError: something broke" in _one_line_error(r)
    assert "Traceback" in (tmp_path / "o" / "error.log").read_text(encoding="utf-8")


def test_cli_non_ascii_path_with_redirected_cp1252_output(tmp_path):
    """Windows: output piped to a file is cp1252; a name it cannot encode must not crash
    the run, on stderr (errors) or stdout (the summary line)."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    env.update(PYTHONPATH=str(SRC), PYTHONIOENCODING="cp1252")
    cli = [sys.executable, "-m", "roomscan.cli"]
    r = subprocess.run(cli + ["run", str(tmp_path / NON_ASCII / "missing.MOV")], capture_output=True, env=env, timeout=120)
    err = r.stderr.decode("cp1252", errors="replace")
    assert r.returncode == 2, err
    assert "error:" in err and "does not exist" in err and "Traceback" not in err
    out = tmp_path / NON_ASCII / "schema.json"
    r = subprocess.run(cli + ["schema", "--out", str(out)], capture_output=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr.decode("cp1252", errors="replace")
    assert out.exists()
