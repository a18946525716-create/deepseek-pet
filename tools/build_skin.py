"""Build the chubby-fish skin: an eye-state x mouth-state frame matrix.

Everything is derived from assets/source/character_cutout.png.

Why a matrix: the pet needs to combine things independently — blink (eyes) while
idle (mouth), or talk (mouth) while happy (eyes). Baking every combination beats
swapping between whole face images that would fight each other.

Blink correctness: the eye's own outline is found by flood filling its bright
interior from the centre, then filling that region's holes (the pupil) and
dilating over the line art. Every eyelid/lash pixel is masked by that region, so
the bangs hanging over the eye are never overwritten — that was the "穿模".

    python tools/build_skin.py [--height 360]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_character import border_seeds, scanline_flood  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "assets", "source", "character_cutout.png")
OUT = os.path.join(ROOT, "assets", "skins", "fish")
ICON_DIR = os.path.join(ROOT, "assets")

# --- regions, measured on the *untrimmed* 922x1245 cutout -------------------
LEFT_EYE = (243, 505, 358, 645)
RIGHT_EYE = (505, 505, 615, 640)
EYE_CENTRES = ((300, 578), (560, 572))
MOUTH_BOX = (415, 645, 452, 678)
MOUTH_CENTRE = (433, 660)
CHEEKS = ((300, 662, 62, 34), (566, 662, 62, 34))  # x, y, rx, ry
SKIN_PROBE = (300, 658, 382, 698)
BLUSH = (247, 146, 162, 150)

LID_STEPS = {"lid35": 0.35, "lid70": 0.72, "closed": 1.0}
EYES = ["open", "lid35", "lid70", "closed", "happy"]
MOUTHS = ["neutral", "talk1", "talk2", "talk3", "smile", "o", "frown"]
BLUSH_FRAMES = [("happy", "smile")]

TALK_SHAPES = {"talk1": 15, "talk2": 24, "talk3": 33}


def shift(region, offset):
    x0, y0, x1, y1 = region
    return (x0 - offset[0], y0 - offset[1], x1 - offset[0], y1 - offset[1])


def shift_point(point, offset):
    return (point[0] - offset[0], point[1] - offset[1])


# Trimmed-space geometry, filled in by main() so every drawing helper agrees.
MOUTH_C = MOUTH_CENTRE
CHEEK_BOXES: tuple = CHEEKS


def dilate(mask: np.ndarray, n: int) -> np.ndarray:
    for _ in range(n):
        mask = (mask | np.roll(mask, 1, 0) | np.roll(mask, -1, 0)
                | np.roll(mask, 1, 1) | np.roll(mask, -1, 1))
    return mask


def eye_region(arr: np.ndarray, box: tuple[int, int, int, int],
               centre: tuple[int, int], grow: int = 3) -> np.ndarray:
    """Mask of one eye: its bright interior, the pupil, and the surrounding line art."""
    x0, y0, x1, y1 = box
    sub = arr[y0:y1, x0:x1, :3].astype(np.int16)
    bright = (sub.max(axis=2) > 95).astype(np.uint8) * 255

    h, w = bright.shape
    sx, sy = centre[0] - x0, centre[1] - y0
    filled = scanline_flood(bright, [(sx, sy)]) == 128
    not_filled = ~filled
    outside = scanline_flood(not_filled.astype(np.uint8) * 255,
                             border_seeds(w, h)) == 128
    holes = not_filled & ~outside          # the pupil, enclosed by the iris
    region = dilate(filled | holes, grow)  # swallow the eye's own outline
    return region


def erase_mouth(arr: np.ndarray, box: tuple[int, int, int, int], skin) -> None:
    x0, y0, x1, y1 = box
    arr[y0:y1, x0:x1, :3] = skin


def draw_mouth(arr: np.ndarray, kind: str, skin) -> None:
    img = Image.fromarray(arr, mode="RGBA")
    d = ImageDraw.Draw(img)
    cx, cy = MOUTH_C
    if kind in TALK_SHAPES:
        h = TALK_SHAPES[kind]
        w = int(h * 1.05)
        d.ellipse((cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2),
                  fill=(122, 54, 68, 255), outline=(48, 22, 34, 255), width=4)
        d.ellipse((cx - w // 5, cy, cx + w // 5, cy + h // 2), fill=(206, 108, 124, 255))
    elif kind == "smile":
        d.arc((cx - 26, cy - 16, cx + 26, cy + 18), start=18, end=162,
              fill=(70, 36, 44, 255), width=5)
    elif kind == "frown":
        d.arc((cx - 20, cy + 2, cx + 20, cy + 34), start=198, end=342,
              fill=(70, 36, 44, 255), width=5)
    elif kind == "o":
        d.ellipse((cx - 12, cy - 12, cx + 12, cy + 12),
                  fill=(122, 54, 68, 255), outline=(48, 22, 34, 255), width=4)
    arr[:] = np.asarray(img)


def apply_eyelids(arr: np.ndarray, box, region: np.ndarray, frac: float,
                  happy: bool) -> None:
    """Close the eye by `frac` (0 open .. 1 shut), lid edge drawn as a shallow arc.

    At frac=1 the whole eye is skin-filled and only the lash stays, which is what
    a closed anime eye looks like. Every pixel written is masked by `region`
    (the eye's real outline), so the bangs hanging over the eye survive intact.
    """
    x0, y0, x1, y1 = box
    h, w = y1 - y0, x1 - x0
    yy, xx = np.mgrid[0:h, 0:w]
    u = (xx - w / 2) / (w / 2)
    bow = 26.0 if happy else 13.0
    rest = h * (0.58 if happy else 0.55)          # where the lash ends up when shut
    edge = frac * rest - bow * (0.35 + 0.65 * (1.0 - min(frac, 1.0))) * (1.0 - u ** 2)

    lid = region if frac >= 0.999 else (region & (yy <= edge))
    arr[y0:y1, x0:x1, :3][lid] = SKIN_RGB

    lash = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(lash).line([(float(x), float(edge[0, x])) for x in range(w)],
                              fill=(42, 28, 46, 255), width=11, joint="curve")
    lash_arr = np.asarray(lash)
    mask = region & (lash_arr[:, :, 3] > 0)
    arr[y0:y1, x0:x1, :3][mask] = lash_arr[:, :, :3][mask]


def apply_blush(arr: np.ndarray, strength: int) -> None:
    layer = Image.new("RGBA", (arr.shape[1], arr.shape[0]), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for (x, y, rx, ry) in CHEEK_BOXES:
        d.ellipse((x - rx, y - ry, x + rx, y + ry), fill=(BLUSH[0], BLUSH[1], BLUSH[2], strength))
    base = Image.fromarray(arr, mode="RGBA")
    base.alpha_composite(layer)
    arr[:] = np.asarray(base)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--height", type=int, default=360)
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    full = Image.open(SRC).convert("RGBA")
    box = full.getchannel("A").getbbox()
    off = (box[0], box[1])
    base_img = full.crop(box)
    base = np.asarray(base_img).copy()
    print(f"cutout trimmed to {base_img.size[0]}x{base_img.size[1]} (offset {off})")

    global SKIN_RGB, MOUTH_C, CHEEK_BOXES
    probe = shift(SKIN_PROBE, off)
    SKIN_RGB = np.median(base[probe[1]:probe[3], probe[0]:probe[2], :3].reshape(-1, 3), axis=0).astype(np.uint8)
    MOUTH_C = shift_point(MOUTH_CENTRE, off)
    CHEEK_BOXES = tuple(shift_point((c[0], c[1]), off) + (c[2], c[3]) for c in CHEEKS)
    print("skin colour:", tuple(int(v) for v in SKIN_RGB), " mouth:", MOUTH_C)

    eyes = {}
    for name, ebox, centre in (("L", LEFT_EYE, EYE_CENTRES[0]), ("R", RIGHT_EYE, EYE_CENTRES[1])):
        sbox = shift(ebox, off)
        eyes[name] = (sbox, eye_region(base, sbox, (centre[0] - off[0], centre[1] - off[1])))
        print(f"  eye {name}: {eyes[name][1].sum()} px masked")

    target = (round(base_img.size[0] * args.height / base_img.size[1]), args.height)

    def render(kind_eyes: str, kind_mouth: str, blush_strength: int) -> Image.Image:
        arr = base.copy()
        if kind_mouth != "neutral":
            erase_mouth(arr, shift(MOUTH_BOX, off), SKIN_RGB)
            draw_mouth(arr, kind_mouth, SKIN_RGB)
        if kind_eyes != "open":
            happy = kind_eyes == "happy"
            frac = 1.0 if happy else LID_STEPS.get(kind_eyes, 0.0)
            for sbox, region in eyes.values():
                apply_eyelids(arr, sbox, region, frac, happy)
        if blush_strength:
            apply_blush(arr, blush_strength)
        img = Image.fromarray(arr, mode="RGBA")
        small = img.resize(target, Image.LANCZOS)
        small.putalpha(small.getchannel("A").point(lambda v: 255 if v >= 128 else 0))
        return small

    frames: dict[str, str] = {}
    made: dict[tuple[str, str], Image.Image] = {}
    for e in EYES:
        for m in MOUTHS:
            img = render(e, m, 0)
            name = f"e-{e}_m-{m}.png"
            img.save(os.path.join(OUT, name))
            frames[f"{e}|{m}"] = name
            made[(e, m)] = img
    for (e, m) in BLUSH_FRAMES:
        img = render(e, m, BLUSH[3])
        name = f"e-{e}_m-{m}_blush.png"
        img.save(os.path.join(OUT, name))
        frames[f"{e}|{m}|blush"] = name

    # expression -> [eyes, mouth(, blush)]
    expressions = {
        "neutral": ["open", "neutral"],
        "happy": ["happy", "smile"],
        "laugh": ["happy", "talk3"],
        "smile": ["open", "smile"],
        "surprised": ["open", "o"],
        "sad": ["lid70", "frown"],
        "angry": ["lid35", "frown"],
        "shy": ["happy", "smile", "blush"],
        "think": ["lid35", "neutral"],
        "talk": ["open", "talk2"],
    }

    # contact sheet: eyes down the rows, mouths across the columns
    cw, ch = target
    sheet = Image.new("RGB", (cw * len(MOUTHS), ch * len(EYES)), (255, 255, 255))
    for r, e in enumerate(EYES):
        for c, m in enumerate(MOUTHS):
            sheet.paste(made[(e, m)], (c * cw, r * ch), made[(e, m)])
    sheet.save(os.path.join(OUT, "preview.png"))

    with open(os.path.join(OUT, "skin.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "name": "大肥鱼",
            "size": list(target),
            "frames": frames,
            "blink": ["lid35", "lid70", "closed"],
            "talk": ["talk1", "talk2", "talk3"],
            "blush_suffix": True,
            "expressions": expressions,
        }, fh, ensure_ascii=False, indent=2)

    # icon: her head, squared off
    head = made[("open", "neutral")].crop((int(cw * 0.10), 0, int(cw * 0.90), int(cw * 0.80)))
    icon = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    icon.paste(head.resize((256, int(256 * head.height / head.width)), Image.LANCZOS), (0, 0))
    icon.save(os.path.join(ICON_DIR, "icon.ico"),
              sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
    icon.save(os.path.join(ICON_DIR, "icon.png"))

    print(f"wrote {len(frames)} frames + preview.png + skin.json + icon to {OUT}")


if __name__ == "__main__":
    main()
