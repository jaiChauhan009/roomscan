"""Turn a Stray Scanner recording's rgb.mp4 into the clip an iPhone held upright would give.

usage: python scripts/make_iphone_clip.py <stray export folder> <out folder>

Stray stores the camera's frames as the sensor delivers them: landscape, with no rotation
flag, even though the phone was held upright. A clip from the Camera app holds the same
landscape pictures but flags them "rotate 90", and the video tier (like any player) turns
them upright. Fed the raw rgb.mp4, the video tier sees every frame sideways, which no
real capture does.

This copies rgb.mp4 to <out>/rgb_upright.mp4 and writes the iPhone's rotation matrix
into the video track's header (tkhd). Nothing is re-encoded: the frames are bit-identical,
only the 36-byte matrix differs. camera_matrix.csv is copied next to it; the video tier
turns that calibration with the frames.
"""
from __future__ import annotations

import shutil
import struct
import sys
from pathlib import Path

# tkhd matrix an iPhone writes for a clip filmed upright (OpenCV reports rotation 90)
IPHONE_UPRIGHT = (0, 0x10000, 0, -0x10000, 0, 0, 0, 0, 0x40000000)


def _boxes(f, start: int, end: int):
    """(type, payload start, box end) of the boxes between start and end."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        size, typ = struct.unpack(">I4s", f.read(8))
        head = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            head = 16
        elif size == 0:
            size = end - pos
        if size < head:
            raise ValueError(f"corrupt box {typ!r} at {pos}")
        yield typ, pos + head, pos + size
        pos += size


def _child(f, start: int, end: int, want: bytes):
    return next(((s, e) for t, s, e in _boxes(f, start, end) if t == want), None)


def video_tkhd_matrix_offset(path: Path) -> int:
    """File offset of the video track's tkhd matrix."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        moov = _child(f, 0, f.tell(), b"moov")
        if moov is None:
            raise ValueError(f"{path}: no moov box")
        for typ, s, e in _boxes(f, *moov):
            if typ != b"trak":
                continue
            mdia = _child(f, s, e, b"mdia")
            hdlr = mdia and _child(f, *mdia, b"hdlr")
            if not hdlr:
                continue
            f.seek(hdlr[0] + 8)  # version/flags, pre_defined
            if f.read(4) != b"vide":
                continue
            tkhd = _child(f, s, e, b"tkhd")
            f.seek(tkhd[0])
            version = f.read(1)[0]
            return tkhd[0] + (52 if version == 1 else 40)
    raise ValueError(f"{path}: no video track")


def make(src_dir: Path, out_dir: Path) -> Path:
    src_dir, out_dir = Path(src_dir), Path(out_dir)
    clip = src_dir / "rgb.mp4"
    if not clip.exists():
        raise SystemExit(f"error: no rgb.mp4 in {src_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "rgb_upright.mp4"
    tmp = out.with_suffix(".tmp")
    shutil.copyfile(clip, tmp)
    off = video_tkhd_matrix_offset(tmp)
    with open(tmp, "r+b") as f:
        f.seek(off)
        f.write(struct.pack(">9i", *IPHONE_UPRIGHT))
    tmp.replace(out)  # only a finished file ever has the final name
    if (src_dir / "camera_matrix.csv").exists():
        shutil.copyfile(src_dir / "camera_matrix.csv", out_dir / "camera_matrix.csv")
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    print("wrote", make(Path(sys.argv[1]), Path(sys.argv[2])))
