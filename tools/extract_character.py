"""Cut the character out of the phone screenshot.

The background is a flat dark navy, but the character's outlines fade into it
through anti-aliased / JPEG pixels. A global colour threshold either eats her
dark dress or leaves a dark fringe, so we:

  1. classify each pixel as "background-ish" within a *tight* tolerance;
  2. flood fill that class inward from the crop border, so only pixels genuinely
     connected to the outside are dropped and enclosed dark regions survive;
  3. keep only the character blob (drops leftover specks);
  4. erode then dilate, shaving the one-pixel JPEG halo around her.

NB: Pillow 12.3's ImageDraw.floodfill is a no-op here and scipy is unavailable,
so the fill is our own scanline implementation.

    python tools/extract_character.py [--tol 12] [--debug]
"""

from __future__ import annotations

import argparse
import os

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "assets", "source", "reference.jpg")
OUT = os.path.join(ROOT, "assets", "source")

BAND = (560, 1900)  # y range containing the character (the title text ends at ~549)
MARGIN = 6


def scanline_flood(mask: np.ndarray, seeds: list[tuple[int, int]], fill: int = 128) -> np.ndarray:
    """Flood the 255-valued region of `mask` reachable from `seeds` with `fill`.

    Returns a new uint8 array. Four-connected scanline fill: O(pixels), and it
    touches only bytearrays so it stays quick on a ~1.1M pixel image.
    """
    m = [bytearray(row) for row in mask.astype(np.uint8)]
    h, w = mask.shape
    stack = [(x, y) for (x, y) in seeds if 0 <= x < w and 0 <= y < h and m[y][x] == 255]
    while stack:
        x, y = stack.pop()
        if m[y][x] != 255:
            continue
        row = m[y]
        x1 = x
        while x1 > 0 and row[x1 - 1] == 255:
            x1 -= 1
        x2 = x
        while x2 < w - 1 and row[x2 + 1] == 255:
            x2 += 1
        for i in range(x1, x2 + 1):
            row[i] = fill
        for ny in (y - 1, y + 1):
            if not 0 <= ny < h:
                continue
            nrow = m[ny]
            i = x1
            while i <= x2:
                if nrow[i] == 255:
                    stack.append((i, ny))
                    while i <= x2 and nrow[i] == 255:
                        i += 1
                i += 1
    return np.array(m, dtype=np.uint8)


def border_seeds(w: int, h: int) -> list[tuple[int, int]]:
    return (
        [(x, 0) for x in range(w)]
        + [(x, h - 1) for x in range(w)]
        + [(0, y) for y in range(h)]
        + [(w - 1, y) for y in range(h)]
    )


def interior_seed(mask: np.ndarray) -> tuple[int, int]:
    """A pixel guaranteed inside the character: middle of the widest run."""
    best = (0, 0, 0)
    for y in range(0, mask.shape[0], 2):
        idx = np.nonzero(mask[y])[0]
        if not len(idx):
            continue
        runs = np.split(idx, np.nonzero(np.diff(idx) > 1)[0] + 1)
        run = max(runs, key=len)
        if len(run) > best[0]:
            best = (len(run), int(run[len(run) // 2]), y)
    return best[1], best[2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=int, default=12, help="background colour tolerance")
    ap.add_argument("--erode", type=int, default=2)
    ap.add_argument("--dilate", type=int, default=1)
    ap.add_argument("--debug", action="store_true")
    # The halo ring above her head is drawn in the same dark navy as her hair, and
    # the screenshot's title text is painted straight over it — so the ring cannot
    # survive extraction intact. Erase it: inside this absolute-coordinate box,
    # anything that is not bright (i.e. not the white frilled headdress) is dropped.
    ap.add_argument("--kill-box", type=int, nargs=4, default=[330, 560, 645, 735],
                    metavar=("X0", "Y0", "X1", "Y1"))
    ap.add_argument("--kill-bright", type=int, default=165)
    args = ap.parse_args()

    full = np.asarray(Image.open(SRC).convert("RGB")).astype(np.int16)
    y0, y1 = BAND
    band = full[y0:y1]
    bg = np.median(np.concatenate([band[:, :40].reshape(-1, 3), band[:, -40:].reshape(-1, 3)]), axis=0)
    print("background:", tuple(int(v) for v in bg))

    bgish = (np.abs(band - bg.reshape(1, 1, 3)).max(axis=2) <= args.tol).astype(np.uint8) * 255

    kx0, ky0, kx1, ky1 = args.kill_box
    ry0, ry1 = max(0, ky0 - y0), min(band.shape[0], ky1 - y0)
    rx0, rx1 = max(0, kx0), min(band.shape[1], kx1)
    if ry1 > ry0 and rx1 > rx0:
        patch = band[ry0:ry1, rx0:rx1]
        dark = patch.max(axis=2) < args.kill_bright
        bgish[ry0:ry1, rx0:rx1][dark] = 255
        print(f"kill-box {kx0},{ky0}..{kx1},{ky1}: erased {int(dark.sum())} dark px")

    keep = bgish == 0
    ys, xs = np.nonzero(keep)
    bx0, by0 = max(0, int(xs.min()) - MARGIN), max(0, int(ys.min()) - MARGIN)
    bx1 = min(band.shape[1], int(xs.max()) + 1 + MARGIN)
    by1 = min(band.shape[0], int(ys.max()) + 1 + MARGIN)
    crop = band[by0:by1, bx0:bx1]
    cls = bgish[by0:by1, bx0:bx1]
    ch, cw = cls.shape
    print(f"crop: {cw}x{ch}   (absolute y {y0 + by0}..{y0 + by1})")

    # --- drop everything connected to the crop border -----------------------
    filled = scanline_flood(cls, border_seeds(cw, ch))
    outside = filled == 128
    print(f"outside: {outside.mean() * 100:.1f}%")
    solid = ~outside
    print(f"solid before cleanup: {solid.mean() * 100:.1f}%")

    # --- keep only the character blob ---------------------------------------
    sx, sy = interior_seed(solid)
    blob = scanline_flood(solid.astype(np.uint8) * 255, [(sx, sy)]) == 128
    leftover = int((solid & ~blob).sum())
    print(f"seed ({sx},{sy});  blob {blob.mean() * 100:.1f}% of crop;  "
          f"leftover outside blob: {leftover}px ({leftover / solid.size * 100:.2f}%)")

    m = blob
    for _ in range(args.erode):
        m = m & np.roll(m, 1, 0) & np.roll(m, -1, 0) & np.roll(m, 1, 1) & np.roll(m, -1, 1)
    for _ in range(args.dilate):
        m = m | np.roll(m, 1, 0) | np.roll(m, -1, 0) | np.roll(m, 1, 1) | np.roll(m, -1, 1)

    out = Image.fromarray(np.dstack([crop.astype(np.uint8), (m * 255).astype(np.uint8)]), mode="RGBA")
    out.save(os.path.join(OUT, "character_cutout.png"))
    print("wrote character_cutout.png", out.size)

    for name, colour in (("preview_white.png", (255, 255, 255)), ("preview_magenta.png", (255, 0, 255))):
        plate = Image.new("RGBA", out.size, colour + (255,))
        plate.alpha_composite(out)
        plate.convert("RGB").save(os.path.join(OUT, name))
    print("wrote previews")

    if args.debug:
        print("background-ish share of the crop:", f"{(filled == 255).mean() * 100:.1f}% still bgish")


if __name__ == "__main__":
    main()
