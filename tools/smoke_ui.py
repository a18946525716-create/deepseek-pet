"""Exercise the pet's UI and animation logic without a human clicking.

    python tools/smoke_ui.py

Builds the real app and drives moods, blink stages, talk cycle, scaling and the
windows, asserting the frame matrix actually resolves for every combination.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import expressions  # noqa: E402
import pet  # noqa: E402

FAILURES: list[str] = []


def step(name, fn):
    try:
        fn()
        print(f"  ok   {name}")
    except Exception:
        FAILURES.append(name)
        print(f"  FAIL {name}")
        traceback.print_exc()


def check_matrix(app) -> None:
    """Every mood, blink stage and talk shape must resolve to a real image."""
    skin = app.skin
    scale = app.pet.scale
    for mood in skin.expressions:
        eyes, mouth, blush = skin.expression(mood)
        assert skin.face(eyes, mouth, blush, scale) is not None, f"{mood}: no frame"
    for stage in skin.blink:
        assert skin.face(stage, "neutral", "", scale) is not None, f"blink {stage}: no frame"
    for mouth in skin.talk:
        assert skin.face("open", mouth, "", scale) is not None, f"talk {mouth}: no frame"
    print(f"       {len(skin.expressions)} moods, {len(skin.blink)} blink stages, "
          f"{len(skin.talk)} talk shapes, {len(skin.base)} frames total")


def check_moods(app) -> None:
    petw = app.pet
    for mood in app.skin.expressions:
        petw.set_mood(mood)
        eyes, mouth, blush = petw.current_face()
        want = app.skin.expression(mood)
        assert (eyes, mouth, blush) == tuple(want), f"{mood}: {eyes},{mouth},{blush} != {want}"
    petw.set_mood("neutral")
    print(f"       moods cycle cleanly ({len(app.skin.expressions)})")


def check_no_blink(app) -> None:
    """Blinking was removed on request: only sleep may shut her eyes."""
    petw = app.pet
    petw.asleep = False
    petw.set_mood("neutral")
    for i in range(40):
        eyes, _, _ = petw.current_face(time.monotonic() + i * 0.033)
        assert eyes == "open", f"frame {i}: eyes became {eyes!r} while awake"
    petw.asleep = True
    assert petw.current_face()[0] == "closed", "sleeping should close the eyes"
    petw.asleep = False
    print("       no blinking while awake; sleep still closes the eyes")


def check_talk(app) -> None:
    petw = app.pet
    petw.set_speaking(True)
    mouths = {petw.current_face(time.monotonic() + i * pet.TALK_STEP)[1] for i in range(3)}
    assert mouths == set(app.skin.talk), mouths
    petw.set_speaking(False)
    petw.set_mood("neutral")
    assert petw.current_face()[1] == "neutral"
    print("       talk cycle covers all mouth shapes")


def check_scaling(app) -> None:
    petw = app.pet
    for scale in (0.4, 0.75, 1.0, 1.35, 2.6):
        petw.set_scale(scale)
        assert abs(petw.scale - scale) < 0.01, petw.scale
        assert petw._w() == int(app.skin.size[0] * scale), petw._w()
    petw.set_scale(1.0)
    print("       scale 40%..260% applies to geometry and frames")


def check_classifier() -> None:
    cases = {
        "谢谢你，今天真开心": "happy",
        "哈哈哈哈哈笑死我了": "laugh",
        "我有点难过，想哭": "sad",
        "这也太过分了吧，气死": "angry",
        "哇，居然是真的": "surprised",
        "你好害羞啊": "shy",
        "这个怎么做？": "think",
        "今天天气不错": "neutral",
    }
    for text, want in cases.items():
        got = expressions.classify(text)
        assert got == want, f"{text!r} -> {got}, want {want}"
    print(f"       classifier: {len(cases)} cases correct")


def main() -> None:
    args = argparse.Namespace(chat=False, say="", reset=True, timeout=30.0, skin="", scale=0.0)
    cfg = pet.load_config()
    skins = pet.load_skins()
    print("skins:", ", ".join(f"{k} ({v.name}, {v.size[0]}x{v.size[1]}, {len(v.base)} frames)"
                              for k, v in skins.items()))

    print("building app …")
    app = pet.PetApp(cfg, args, skins)
    win = app.pet.win

    def run_steps() -> None:
        print("exercising …")
        step("frame matrix resolves", lambda: check_matrix(app))
        step("moods map to frames", lambda: check_moods(app))
        step("blink removed, sleep closes eyes", lambda: check_no_blink(app))
        step("talk cycle", lambda: check_talk(app))
        step("scaling", lambda: check_scaling(app))
        step("mood classifier", check_classifier)
        step("topmost off/on", lambda: (app.pet.set_topmost(False), app.pet.set_topmost(True)))
        step("reset position", app.pet.reset_position)
        step("save position", app.pet.save_position)
        step("bubble show", lambda: app.bubble.show("冒个泡测试一下～", 2))
        step("bubble hide", app.bubble.hide)
        step("size dialog open/close", lambda: pet.SizeDialog(app).cancel())
        step("chat show", app.chat.show)
        step("chat system note", lambda: app.chat.system("测试系统提示"))
        step("chat begin_reply", app.chat.begin_reply)
        step("chat push_delta", lambda: app.chat.push_delta("流式文字"))
        step("chat end_reply", lambda: app.chat.end_reply(True))
        step("chat hide", app.chat.hide)
        step("new_session", app.new_session)
        step("pet click toggles chat", app.on_pet_click)
        step("spawn bubbles", lambda: [app.pet._spawn_bubble() for _ in range(5)])
        win.after(1200, finish)

    def finish() -> None:
        try:
            app.quit()
        except Exception:
            traceback.print_exc()
            FAILURES.append("quit")

    win.after(600, run_steps)
    win.mainloop()

    print()
    if FAILURES:
        print("FAILED:", ", ".join(FAILURES))
        raise SystemExit(1)
    print("ALL UI STEPS OK")


if __name__ == "__main__":
    main()
