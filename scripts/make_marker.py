"""Write the printable roomscan scale marker (A4, PDF + PNG at 600 dpi).

One ArUco marker (DICT_4X4_50, id 0) whose black square is exactly --side-mm wide
(default 180 mm, roomscan.markers.MARKER_SIDE_M), a 10 cm ruler to check the print
scale, and the instruction line. See docs/scale_marker.md.

    python scripts/make_marker.py                 # -> scale_marker.pdf, scale_marker.png
    python scripts/make_marker.py --out-dir out --side-mm 150
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from roomscan.markers import MARKER_DICT, MARKER_ID, MARKER_SIDE_M

A4_MM = (210.0, 297.0)
TEXT = "roomscan scale marker - print at 100 %, check the 10 cm ruler"


def _font(px: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=px)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def make_page(side_mm: float = MARKER_SIDE_M * 1000, marker_id: int = MARKER_ID, dpi: int = 600) -> Image.Image:
    if not 50 <= side_mm <= 190:
        raise ValueError("side must be 50-190 mm to fit A4 with a white margin")
    mm = dpi / 25.4
    W, H = round(A4_MM[0] * mm), round(A4_MM[1] * mm)
    page = Image.new("L", (W, H), 255)
    # marker: 6 x 6 cells (4 x 4 code + 1-cell black border), scaled up with nearest
    # neighbour so every cell edge lands within half a pixel (0.02 mm) of its place
    cells = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(MARKER_DICT), marker_id, 6, borderBits=1)
    side_px = round(side_mm * mm)
    marker = cv2.resize(cells, (side_px, side_px), interpolation=cv2.INTER_NEAREST)
    x0 = (W - side_px) // 2
    y0 = round(25 * mm)
    page.paste(Image.fromarray(marker), (x0, y0))

    d = ImageDraw.Draw(page)
    line = max(1, round(0.25 * mm))
    # 10 cm ruler with mm ticks, centred under the marker
    ry = y0 + side_px + round(22 * mm)
    rx0 = (W - round(100 * mm)) // 2
    for i in range(101):
        x = rx0 + round(i * mm)
        L = 8 if i % 10 == 0 else (5 if i % 5 == 0 else 3)
        d.line([(x, ry), (x, ry - round(L * mm))], fill=0, width=line)
    d.line([(rx0, ry), (rx0 + round(100 * mm), ry)], fill=0, width=line)
    small = _font(round(3.5 * mm))
    for cm in range(11):
        x = rx0 + round(cm * 10 * mm)
        d.text((x, ry + round(1.5 * mm)), str(cm), fill=0, font=small, anchor="mt")
    d.text((rx0 + round(100 * mm) + round(3 * mm), ry), "cm", fill=0, font=small, anchor="lm")

    big = _font(round(4.5 * mm))
    d.text((W // 2, ry + round(14 * mm)), TEXT, fill=0, font=big, anchor="mt")
    info = (f"ArUco DICT_4X4_50 id {marker_id} - black square {side_mm:g} mm wide - "
            f"stick flat on a wall at chest height, one per room")
    d.text((W // 2, ry + round(22 * mm)), info, fill=0, font=small, anchor="mt")
    # side check: a dimension line over the marker
    ty = y0 - round(6 * mm)
    d.line([(x0, ty), (x0 + side_px, ty)], fill=0, width=line)
    for x in (x0, x0 + side_px):
        d.line([(x, ty - round(2 * mm)), (x, ty + round(2 * mm))], fill=0, width=line)
    d.text((W // 2, ty - round(1.5 * mm)), f"{side_mm:g} mm", fill=0, font=small, anchor="mb")
    return page


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", type=Path, default=Path("."))
    ap.add_argument("--side-mm", type=float, default=MARKER_SIDE_M * 1000)
    ap.add_argument("--id", type=int, default=MARKER_ID)
    ap.add_argument("--dpi", type=int, default=600)
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)
    page = make_page(a.side_mm, a.id, a.dpi).convert("1", dither=Image.Dither.NONE)
    png, pdf = a.out_dir / "scale_marker.png", a.out_dir / "scale_marker.pdf"
    page.save(png, dpi=(a.dpi, a.dpi), optimize=True)
    page.save(pdf, resolution=a.dpi)  # page size = pixels / dpi = exactly A4
    print(f"wrote {pdf} and {png} (A4, {a.dpi} dpi, marker side {a.side_mm:g} mm)")
    if a.side_mm != MARKER_SIDE_M * 1000:
        print(f"note: pass side_m={a.side_mm / 1000:g} to roomscan.markers for this print")


if __name__ == "__main__":
    main()
