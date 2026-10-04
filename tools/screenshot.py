"""Grab a screen region to PNG (verification helper).

    python tools/screenshot.py <x> <y> <w> <h> <out.png>
"""

from __future__ import annotations

import sys

from PIL import ImageGrab


def main() -> None:
    x, y, w, h, out = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    img.save(out)
    print(f"saved {out} {img.size}")


if __name__ == "__main__":
    main()
