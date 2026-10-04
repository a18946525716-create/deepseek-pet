"""Analyse the reference screenshot: find the character and probe the background.

    python tools/analyse_reference.py
"""

from __future__ import annotations

import os
from collections import Counter

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "assets", "source", "reference.jpg")
OUT = os.path.join(ROOT, "assets", "source")


def main() -> None:
    img = Image.open(SRC).convert("RGB")
    a = np.asarray(img).astype(np.int16)
    h, w, _ = a.shape
    print(f"size: {w}x{h}")

    # most common colours overall, and along the middle band where the character sits
    print("\ntop colours overall:")
    for col, n in Counter(map(tuple, a.reshape(-1, 3)[::37])).most_common(6):
        print(f"  {col}  {n}")

    print("\nborder samples:")
    for name, sl in {
        "top row": a[0:4, :, :],
        "bottom row": a[h - 4 : h, :, :],
        "left col": a[:, 0:4, :],
        "right col": a[:, w - 4 : w, :],
    }.items():
        med = np.median(sl.reshape(-1, 3), axis=0)
        print(f"  {name}: median {tuple(int(v) for v in med)}")

    # rough character bbox: pixels far from the dominant dark background
    bg = np.array([16, 16, 32], dtype=np.int16)
    corner = np.median(a[0:40, 0:40].reshape(-1, 3), axis=0)
    print("\ncorner median:", tuple(int(v) for v in corner))
    dist = np.abs(a - corner.reshape(1, 1, 3)).sum(axis=2)
    mask = dist > 60
    ys, xs = np.nonzero(mask)
    if len(xs):
        print(f"content bbox: x {xs.min()}..{xs.max()}  y {ys.min()}..{ys.max()}")
        # column/row density helps spot the title text vs the character
        rows = mask.sum(axis=1)
        bands = []
        inside = False
        for y, v in enumerate(rows):
            if v > 6 and not inside:
                inside, start = True, y
            elif v <= 6 and inside:
                inside = False
                if y - start > 8:
                    bands.append((start, y))
        if inside:
            bands.append((start, h))
        print("dense row bands:", bands[:12])

    # what does the background look like right around the character?
    for (x, y) in [(60, 900), (1100, 900), (580, 700), (580, 1900), (580, 2400)]:
        if 0 <= x < w and 0 <= y < h:
            print(f"  sample ({x},{y}) = {tuple(int(v) for v in a[y, x])}")

    img.resize((w // 2, h // 2), Image.LANCZOS).save(os.path.join(OUT, "reference_half.png"))
    print("\nwrote reference_half.png")


if __name__ == "__main__":
    main()
