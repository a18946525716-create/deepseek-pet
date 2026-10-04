"""DeepSeek 桌宠 — a transparent, always-on-top desktop character for Windows.

The skin is an eye-state x mouth-state frame matrix (assets/skins/<key>/skin.json),
so blinking, talking and the mood can be combined instead of fighting each other:

    frames      "eyes|mouth" -> file        e.g. "lid70|frown": "e-lid70_m-frown.png"
    blink       eye states played in order  ["lid35","lid70","closed"]
    talk        mouth states cycled         ["talk1","talk2","talk3"]
    expressions mood -> [eyes, mouth(, blush)]

Conversation is driven directly by DeepSeek Harness (`dsh headless`, see
dsh_link.py), so the pet talks to the same agent runtime as the GUI, with
persistent multi-turn context. Her expression follows the conversation.

Run:  pythonw pet.py        (or 启动桌宠.bat)
"""

from __future__ import annotations

import argparse
import json
import math
import queue
import random
import threading
import time
import tkinter as tk
from tkinter import ttk
from pathlib import Path

from PIL import Image, ImageTk

import expressions

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
SKINS_DIR = ASSETS / "skins"
CONFIG_PATH = ROOT / "pet_config.json"

KEY = "#ff00ff"       # colour key that becomes fully transparent
TICK_MS = 33          # ~30 fps
TALK_STEP = 0.115     # seconds per talk mouth shape
EMOTION_HOLD = 45.0   # seconds before the mood drifts back to neutral

MIN_SCALE, MAX_SCALE = 0.4, 2.6

DEFAULT_CWD = Path.home() / "Documents" / "deepseek-harness" / "default-workspace"

DEFAULT_CONFIG = {
    "x": None,
    "y": None,
    "scale": 1.0,
    "topmost": True,
    "skin": "fish",
    "session_id": None,
    "app_root": None,
    "cwd": str(DEFAULT_CWD if DEFAULT_CWD.is_dir() else ROOT),
    "bubble_seconds": 10,
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


class Skin:
    """One character: its frame matrix, at whatever scale is being displayed."""

    def __init__(self, directory: Path):
        meta = json.loads((directory / "skin.json").read_text(encoding="utf-8"))
        self.dir = directory
        self.key = directory.name
        self.name = meta.get("name", directory.name)
        self.size = (int(meta["size"][0]), int(meta["size"][1]))
        self.files: dict[str, str] = meta.get("frames") or {}
        self.blink: list[str] = meta.get("blink") or []
        self.talk: list[str] = meta.get("talk") or []
        self.expressions: dict[str, list[str]] = meta.get("expressions") or {}
        self.base: dict[str, Image.Image] = {}
        self._tk: dict[tuple[str, float], ImageTk.PhotoImage] = {}

    def load(self) -> None:
        for key, filename in self.files.items():
            img = Image.open(self.dir / filename).convert("RGBA")
            if img.size != self.size:
                img = img.resize(self.size, Image.LANCZOS)
            # Tk's -transparentcolor is a colour key: a half-transparent edge pixel
            # would blend with the key colour and leave a magenta fringe.
            img.putalpha(img.getchannel("A").point(lambda v: 255 if v >= 128 else 0))
            self.base[key] = img

    def scaled(self, key: str, scale: float) -> ImageTk.PhotoImage | None:
        if key not in self.base:
            return None
        ck = (key, round(scale, 3))
        cached = self._tk.get(ck)
        if cached is None:
            img = self.base[key]
            w = max(1, int(round(self.size[0] * scale)))
            h = max(1, int(round(self.size[1] * scale)))
            if (w, h) != img.size:
                img = img.resize((w, h), Image.LANCZOS)
                img.putalpha(img.getchannel("A").point(lambda v: 255 if v >= 128 else 0))
            cached = ImageTk.PhotoImage(img)
            self._tk[ck] = cached
        return cached

    def drop_scaled(self) -> None:
        self._tk.clear()

    def expression(self, mood: str) -> tuple[str, str, str]:
        spec = self.expressions.get(mood) or self.expressions.get("neutral") or ["open", "neutral"]
        spec = list(spec)
        eyes = spec[0] if len(spec) > 0 else "open"
        mouth = spec[1] if len(spec) > 1 else "neutral"
        blush = spec[2] if len(spec) > 2 else ""
        return eyes, mouth, blush

    def face(self, eyes: str, mouth: str, blush: str, scale: float) -> ImageTk.PhotoImage | None:
        tail = f"|{blush}" if blush else ""
        for key in (f"{eyes}|{mouth}{tail}", f"{eyes}|{mouth}", f"{eyes}|neutral",
                    f"open|{mouth}", "open|neutral"):
            img = self.scaled(key, scale)
            if img is not None:
                return img
        return None


def load_skins() -> dict[str, Skin]:
    skins: dict[str, Skin] = {}
    if not SKINS_DIR.is_dir():
        return skins
    for child in sorted(SKINS_DIR.iterdir()):
        if (child / "skin.json").is_file():
            try:
                skin = Skin(child)
                skin.load()
                skins[child.name] = skin
            except (OSError, ValueError, KeyError):
                continue
    return skins


class SizeDialog:
    """Pick any size, with live preview; cancelling restores the old scale."""

    def __init__(self, app: "PetApp"):
        self.app = app
        self.original = app.pet.scale
        self.win = tk.Toplevel()
        self.win.title("自定义大小")
        self.win.configure(bg="#eef2ff")
        self.win.resizable(False, False)
        self.win.transient(app.chat.win)
        try:
            self.win.iconbitmap(str(ASSETS / "icon.ico"))
        except tk.TclError:
            pass
        body = tk.Frame(self.win, bg="#eef2ff")
        body.pack(padx=16, pady=14)

        self.value = tk.DoubleVar(value=app.pet.scale)
        self.label = tk.Label(body, bg="#eef2ff", fg="#1b2440", font=("Microsoft YaHei UI", 11, "bold"))
        self.label.pack(anchor="w")
        self.slider = ttk.Scale(body, from_=MIN_SCALE, to=MAX_SCALE, orient="horizontal",
                                variable=self.value, command=self._changed, length=320)
        self.slider.pack(pady=(6, 2))
        row = tk.Frame(body, bg="#eef2ff")
        row.pack(fill="x", pady=(6, 0))
        tk.Label(row, text=f"{int(MIN_SCALE * 100)}%", bg="#eef2ff", fg="#8a93b5",
                 font=("Microsoft YaHei UI", 8)).pack(side="left")
        tk.Label(row, text=f"{int(MAX_SCALE * 100)}%", bg="#eef2ff", fg="#8a93b5",
                 font=("Microsoft YaHei UI", 8)).pack(side="right")

        buttons = tk.Frame(body, bg="#eef2ff")
        buttons.pack(fill="x", pady=(14, 0))
        tk.Button(buttons, text="重置 100%", command=lambda: self._set(1.0), bg="#dbe3ff",
                  fg="#1b2440", relief="flat", bd=0, padx=12, pady=5,
                  font=("Microsoft YaHei UI", 9)).pack(side="left")
        tk.Button(buttons, text="确定", command=self.ok, bg="#4d6bfe", fg="white", relief="flat",
                  bd=0, padx=18, pady=5, font=("Microsoft YaHei UI", 9, "bold")).pack(side="right")
        tk.Button(buttons, text="取消", command=self.cancel, bg="#dbe3ff", fg="#1b2440",
                  relief="flat", bd=0, padx=14, pady=5,
                  font=("Microsoft YaHei UI", 9)).pack(side="right", padx=(0, 8))
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self._changed()
        self.win.update_idletasks()
        px, py = app.pet.pos()
        self.win.geometry(f"+{max(8, px - 180)}+{max(8, py + 40)}")

    def _set(self, value: float) -> None:
        self.value.set(value)
        self._changed(str(value))

    def _changed(self, _=None) -> None:
        scale = round(float(self.value.get()), 2)
        self.label.configure(text=f"当前大小：{int(round(scale * 100))}%")
        self.app.pet.set_scale(scale)

    def ok(self) -> None:
        self.app.pet.set_scale(round(float(self.value.get()), 2), save=True)
        self.win.destroy()

    def cancel(self) -> None:
        self.app.pet.set_scale(self.original, save=True)
        self.win.destroy()


class PetWindow:
    """Frameless, transparent, always-on-top sprite window.

    She never moves on her own — only the user drags her — but the sprite inside
    the window floats, sways, blinks and talks.
    """

    def __init__(self, app: "PetApp", cfg: dict, skin: Skin):
        self.app = app
        self.cfg = cfg
        self.skin = skin
        self.scale = float(cfg.get("scale") or 1.0)
        self.topmost = bool(cfg.get("topmost", True))

        self.win = tk.Tk()
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.configure(bg=KEY)
        try:
            self.win.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        self.win.attributes("-topmost", self.topmost)

        self.margin = max(6, int(10 * self.scale))
        w, h = self._w(), self._h()
        x, y = self._initial_pos(w, h)
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.canvas = tk.Canvas(self.win, width=w, height=h, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.sprite = self.canvas.create_image(w // 2, h // 2, image=None)
        self.bubbles: list[int] = []
        self.win.deiconify()

        # animation state
        self.phase = 0.0
        self.mood = "neutral"
        self.mood_at = time.monotonic()
        self.speaking = False
        self.asleep = False
        self.idle_since = time.monotonic()
        self.drag_origin: tuple[int, int, int, int] | None = None
        self.drag_moved = False
        self.next_idle_bubble = time.monotonic() + random.uniform(4.0, 9.0)
        self._shown_key: tuple | None = None

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", self._menu)
        self.canvas.bind("<Double-Button-1>", lambda _e: self.app.toggle_chat())
        self.canvas.bind("<Control-MouseWheel>", self._wheel)
        self.win.bind("<Escape>", lambda _e: self.app.quit())
        self.win.after(TICK_MS, self._tick)

    # -- geometry ----------------------------------------------------------
    def _w(self) -> int:
        return int(self.skin.size[0] * self.scale)

    def _h(self) -> int:
        return int(self.skin.size[1] * self.scale) + 2 * self.margin

    def _initial_pos(self, w: int, h: int) -> tuple[int, int]:
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        x = self.cfg.get("x")
        y = self.cfg.get("y")
        if x is None or y is None or not (0 <= x <= sw - w) or not (0 <= y <= sh - h):
            x, y = sw - w - 80, sh - h - 120
        return int(x), int(y)

    def pos(self) -> tuple[int, int]:
        return self.win.winfo_x(), self.win.winfo_y()

    def move_to(self, x: int, y: int) -> None:
        self.win.geometry(f"+{int(x)}+{int(y)}")

    # -- scale -------------------------------------------------------------
    def set_scale(self, scale: float, save: bool = False) -> None:
        scale = max(MIN_SCALE, min(MAX_SCALE, float(scale)))
        if abs(scale - self.scale) < 0.001 and not save:
            return
        old_x, old_y, old_h = *self.pos(), self._h()
        self.scale = scale
        self.cfg["scale"] = round(scale, 2)
        self.margin = max(6, int(10 * self.scale))
        self.skin.drop_scaled()
        w, h = self._w(), self._h()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        x = max(0, min(sw - w, old_x))
        y = max(0, min(sh - h, old_y + old_h - h))   # keep her feet anchored
        self.win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
        self.canvas.configure(width=w, height=h)
        self._shown_key = None
        if save:
            save_config(self.cfg)

    def _wheel(self, event) -> None:
        self.set_scale(round(self.scale + (0.05 if event.delta > 0 else -0.05), 2), save=True)

    def set_topmost(self, on: bool) -> None:
        self.topmost = on
        self.cfg["topmost"] = on
        self.win.attributes("-topmost", on)
        save_config(self.cfg)

    # -- interaction -------------------------------------------------------
    def _press(self, event) -> None:
        self.drag_origin = (event.x_root, event.y_root, self.win.winfo_x(), self.win.winfo_y())
        self.drag_moved = False
        self.wake()

    def _motion(self, event) -> None:
        if not self.drag_origin:
            return
        sx, sy, wx, wy = self.drag_origin
        dx, dy = event.x_root - sx, event.y_root - sy
        if abs(dx) + abs(dy) > 4:
            self.drag_moved = True
        self.move_to(wx + dx, wy + dy)

    def _release(self, event) -> None:
        if self.drag_origin and not self.drag_moved:
            self.app.on_pet_click()
        if self.drag_moved:
            self.save_position()
        self.drag_origin = None

    def _menu(self, event) -> None:
        self.wake()
        menu = tk.Menu(self.win, tearoff=0)
        menu.add_command(label="打开 / 收起对话", command=self.app.toggle_chat)
        menu.add_command(label="新建会话（清空上下文）", command=self.app.new_session)
        menu.add_separator()
        mood = tk.Menu(menu, tearoff=0)
        for name in self.skin.expressions:
            mood.add_command(label=("● " if name == self.mood else "   ")
                             + expressions.MOOD_LABELS.get(name, name),
                             command=lambda m=name: self.set_mood(m))
        menu.add_cascade(label="表情", menu=mood)
        size = tk.Menu(menu, tearoff=0)
        for label, value in (("小 75%", 0.75), ("中 100%", 1.0), ("大 135%", 1.35)):
            size.add_command(label=label, command=lambda v=value: self.set_scale(v, save=True))
        size.add_separator()
        size.add_command(label="自定义大小…", command=lambda: SizeDialog(self.app))
        menu.add_cascade(label="大小", menu=size)
        var = tk.BooleanVar(value=self.topmost)
        menu.add_checkbutton(label="窗口置顶", variable=var, command=lambda: self.set_topmost(var.get()))
        menu.add_command(label="回到右下角", command=self.reset_position)
        menu.add_separator()
        menu.add_command(label="退出桌宠", command=self.app.quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def save_position(self) -> None:
        x, y = self.pos()
        self.cfg["x"], self.cfg["y"] = x, y
        save_config(self.cfg)

    def reset_position(self) -> None:
        w, h = self._w(), self._h()
        self.move_to(self.win.winfo_screenwidth() - w - 80, self.win.winfo_screenheight() - h - 120)
        self.save_position()

    # -- mood / animation --------------------------------------------------
    def wake(self) -> None:
        self.asleep = False
        self.idle_since = time.monotonic()

    def set_speaking(self, speaking: bool) -> None:
        self.speaking = speaking
        if speaking:
            self.wake()

    def set_mood(self, mood: str) -> None:
        if mood not in self.skin.expressions:
            mood = "neutral"
        self.mood = mood
        self.mood_at = time.monotonic()

    def current_face(self, now: float | None = None) -> tuple[str, str, str]:
        """(eyes, mouth, blush) shown right now — also used by the tests.

        There is deliberately no blink animation: she only shuts her eyes when
        asleep. The skin still ships the half-lidded stages, so re-enabling a
        blink is just a matter of alternating `eyes` through `skin.blink` here.
        """
        now = time.monotonic() if now is None else now
        eyes, mouth, blush = self.skin.expression(self.mood)
        if self.asleep:
            eyes = "closed"
        if self.speaking and self.skin.talk:
            mouth = self.skin.talk[int(now / TALK_STEP) % len(self.skin.talk)]
        return eyes, mouth, blush

    def _tick(self) -> None:
        now = time.monotonic()
        self.phase += 0.06
        if not self.speaking and now - self.idle_since > 210:
            self.asleep = True
        if self.mood != "neutral" and now - self.mood_at > EMOTION_HOLD:
            self.mood = "neutral"

        eyes, mouth, blush = self.current_face(now)
        amp = 4.5 if self.speaking else 2.6
        bob = math.sin(self.phase * 1.15) * amp * self.scale
        sway = math.sin(self.phase * 0.5) * 1.8 * self.scale

        if not self.asleep and now >= self.next_idle_bubble:
            self.next_idle_bubble = now + random.uniform(5.0, 11.0)
            self._spawn_bubble()
        self._step_bubbles()

        key = (eyes, mouth, blush)
        if key != self._shown_key:
            image = self.skin.face(eyes, mouth, blush, self.scale)
            if image is not None:
                self.canvas.itemconfig(self.sprite, image=image)
            self._shown_key = key
        self.canvas.coords(self.sprite, self._w() // 2 + sway, self._h() // 2 + bob)

        self.win.after(TICK_MS, self._tick)

    def _spawn_bubble(self) -> None:
        x = self._w() // 2 + random.randint(-int(self._w() * 0.2), int(self._w() * 0.2))
        r = random.randint(2, int(4 * self.scale) + 2)
        item = self.canvas.create_oval(x, self.margin, x + 2 * r, self.margin + 2 * r,
                                       outline="#bfd0ff", width=1)
        self.bubbles.append(item)

    def _step_bubbles(self) -> None:
        for item in list(self.bubbles):
            self.canvas.move(item, 0, -2)
            if self.canvas.coords(item)[1] < 0:
                self.canvas.delete(item)
                self.bubbles.remove(item)


class Bubble:
    """Small speech bubble floating above the pet."""

    def __init__(self, app: "PetApp", cfg: dict):
        self.app = app
        self.cfg = cfg
        self.win = tk.Toplevel()
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", 0.97)
        except tk.TclError:
            pass
        self.outer = tk.Frame(self.win, bg="#4d6bfe", bd=0)
        self.outer.pack()
        self.inner = tk.Frame(self.outer, bg="#ffffff", bd=0)
        self.inner.pack(padx=2, pady=2)
        self.label = tk.Label(self.inner, text="", bg="#ffffff", fg="#12236c",
                              font=("Microsoft YaHei UI", 10), justify="left",
                              wraplength=300, padx=10, pady=7)
        self.label.pack()
        for w in (self.label, self.inner, self.outer):
            w.bind("<Button-1>", lambda _e: self.app.toggle_chat())
        self.hide_at = 0.0

    def show(self, text: str, seconds: float | None = None) -> None:
        self.label.configure(text=text[:400])
        self.win.update_idletasks()
        pet = self.app.pet
        px, py = pet.pos()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        x = max(8, min(pet.win.winfo_screenwidth() - w - 8, px + pet._w() // 2 - w // 2))
        y = py - h - 6
        if y < 8:
            y = py + pet._h() + 6
        self.win.geometry(f"+{int(x)}+{int(y)}")
        self.win.deiconify()
        self.win.attributes("-topmost", pet.topmost)
        self.hide_at = time.monotonic() + (seconds if seconds is not None
                                           else float(self.cfg.get("bubble_seconds", 10)))

    def tick(self) -> None:
        if self.hide_at and time.monotonic() > self.hide_at and self.win.winfo_viewable():
            self.hide()

    def hide(self) -> None:
        self.hide_at = 0.0
        self.win.withdraw()


class ChatWindow:
    """Transcript + input, streaming the harness reply as it arrives."""

    def __init__(self, app: "PetApp"):
        self.app = app
        self.win = tk.Toplevel()
        self.win.title("DeepSeek 大肥鱼")
        self.win.configure(bg="#eef2ff")
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        try:
            self.win.iconbitmap(str(ASSETS / "icon.ico"))
        except tk.TclError:
            pass
        self.win.geometry("460x560")

        header = tk.Frame(self.win, bg="#4d6bfe")
        header.pack(fill="x")
        self.title = tk.Label(header, text="DeepSeek 大肥鱼", bg="#4d6bfe", fg="white",
                              font=("Microsoft YaHei UI", 11, "bold"), padx=12, pady=7)
        self.title.pack(side="left")
        self.status = tk.Label(header, text="空闲", bg="#4d6bfe", fg="#dce4ff",
                               font=("Microsoft YaHei UI", 9), padx=12)
        self.status.pack(side="right")

        body = tk.Frame(self.win, bg="#eef2ff")
        body.pack(fill="both", expand=True, padx=10, pady=(8, 4))
        self.text = tk.Text(body, wrap="word", bg="white", fg="#1b2440", bd=0,
                            font=("Microsoft YaHei UI", 10), padx=10, pady=8,
                            state="disabled", cursor="arrow")
        scroll = tk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("user", foreground="#1b3fd8", font=("Microsoft YaHei UI", 10, "bold"),
                                spacing1=8, spacing3=2, justify="right")
        self.text.tag_configure("pet", foreground="#243052", spacing1=2, spacing3=8)
        self.text.tag_configure("meta", foreground="#8a93b5", font=("Microsoft YaHei UI", 8), spacing3=4)
        self.text.tag_configure("err", foreground="#c02b45", font=("Microsoft YaHei UI", 9))

        bar = tk.Frame(self.win, bg="#eef2ff")
        bar.pack(fill="x", padx=10, pady=(0, 10))
        self.entry = tk.Entry(bar, font=("Microsoft YaHei UI", 10), relief="flat", bd=0,
                              bg="white", fg="#1b2440")
        self.entry.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 8))
        self.entry.bind("<Return>", lambda _e: self.send())
        self.button = tk.Button(bar, text="发送", command=self.send, bg="#4d6bfe", fg="white",
                                activebackground="#3a55d9", activeforeground="white",
                                relief="flat", bd=0, font=("Microsoft YaHei UI", 10, "bold"),
                                padx=18, cursor="hand2")
        self.button.pack(side="right", ipady=4)
        self.win.withdraw()

        self.streaming = False
        self.pet_started = False
        self.set_pet_name()

    def set_pet_name(self, name: str | None = None) -> None:
        self.title.configure(text=f"🐟  {name or self.app.skin.name}")

    # -- visibility --------------------------------------------------------
    def show(self) -> None:
        self.set_pet_name()
        self.win.deiconify()
        self.win.lift()
        self._place_near_pet()
        self.entry.focus_set()

    def hide(self) -> None:
        self.win.withdraw()

    def toggle(self) -> None:
        self.hide() if self.win.winfo_viewable() else self.show()

    def _place_near_pet(self) -> None:
        pet = self.app.pet
        px, py = pet.pos()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        w, h = 460, 560
        x = px - w - 12 if px - w - 12 > 0 else px + pet._w() + 12
        x = max(8, min(sw - w - 8, x))
        y = max(8, min(sh - h - 48, py + pet._h() // 2 - h // 2))
        self.win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

    # -- transcript --------------------------------------------------------
    def _append(self, chunk: str, tag: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", chunk, tag)
        self.text.configure(state="disabled")
        self.text.see("end")

    def system(self, text: str, tag: str = "meta") -> None:
        self._append(text + "\n", tag)

    def set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def begin_reply(self) -> None:
        self.streaming = True
        self.pet_started = False
        self.set_status("思考中…")
        self.button.configure(state="disabled")
        self._append("🐟 ", "pet")

    def push_delta(self, chunk: str) -> None:
        if not self.pet_started:
            self.pet_started = True
            self.set_status("回复中…")
        self._append(chunk, "pet")

    def end_reply(self, ok: bool, note: str = "") -> None:
        self.streaming = False
        self.button.configure(state="normal")
        self.set_status("空闲")
        if not self.pet_started:
            self._append("（没有返回内容）", "meta")
        self._append("\n", "pet")
        if note:
            self.system(note, "err" if not ok else "meta")

    # -- sending -----------------------------------------------------------
    def send(self) -> None:
        message = self.entry.get().strip()
        if not message:
            return
        if self.streaming:
            self.system("上一条还在回复，请稍等…")
            return
        self.entry.delete(0, "end")
        self._append(message + "\n", "user")
        self.app.ask(message)


class PetApp:
    def __init__(self, cfg: dict, args, skins: dict[str, Skin]):
        self.cfg = cfg
        self.args = args
        self.skins = skins
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.session = None
        self.session_error: str | None = None
        self.model_reply = ""
        self.user_mood = "neutral"
        self.reply_mood = "neutral"
        self.last_mood_switch = 0.0

        key = cfg.get("skin") if cfg.get("skin") in skins else None
        if key is None:
            key = "fish" if "fish" in skins else next(iter(skins))
            cfg["skin"] = key
        self.pet = PetWindow(self, cfg, skins[key])
        self.bubble = Bubble(self, cfg)
        self.chat = ChatWindow(self)
        self._init_harness()

        self.pet.win.after(60, self._pump)
        self.pet.win.after(400, self._bubble_tick)

        if args.chat:
            self.chat.show()
            self.bubble.show(f"你好呀，我是{self.skin.name}～ 点我聊天吧！", 6)

    @property
    def skin(self) -> Skin:
        return self.pet.skin

    # -- harness -----------------------------------------------------------
    def _init_harness(self) -> None:
        def worker() -> None:
            try:
                import dsh_link

                self.session = dsh_link.DshSession(
                    cwd=self.cfg.get("cwd") or str(ROOT),
                    app_root=self.cfg.get("app_root") or None,
                    session_id=None if self.args.reset else self.cfg.get("session_id"),
                    timeout=float(self.args.timeout),
                )
                self.events.put(("ready", f"已连接 DeepSeek Harness\n{self.session.exe}"))
            except Exception as exc:
                self.session_error = str(exc)
                self.events.put(("harness_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def ask(self, message: str) -> None:
        if self.session is None:
            if self.session_error:
                self.chat.system("还没连上 DeepSeek Harness：\n" + self.session_error, "err")
                self.bubble.show("我连不上 Harness …", 6)
            else:
                self.chat.system("正在连接 DeepSeek Harness，请等一下…")
            return
        if self.session.busy:
            self.chat.system("上一条还在回复，请稍等…")
            return
        self.model_reply = ""
        self.user_mood = expressions.classify(message)
        self.reply_mood = "neutral"
        self.pet.set_mood("think" if self.user_mood == "neutral" else self.user_mood)
        self.chat.begin_reply()
        self.pet.set_speaking(True)
        self.bubble.show("…", 600)
        self.chat.win.after(80, self.chat._place_near_pet)

        def worker() -> None:
            def emit(kind: str, payload: object) -> None:
                self.events.put((kind, payload))

            try:
                text = self.session.turn(message, emit)
                self.events.put(("reply_final", text))
            except Exception as exc:
                self.events.put(("harness_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _mood_from_reply(self, text: str, force: bool = False) -> None:
        """Let her face follow what the conversation is actually about."""
        mood = expressions.classify(text, allow_think=False)
        if mood == "neutral" and not force:
            return
        self.reply_mood = mood
        if mood == "neutral":
            mood = self.user_mood
        now = time.monotonic()
        if force or (mood != self.pet.mood and now - self.last_mood_switch > 1.5):
            self.pet.set_mood(mood)
            self.last_mood_switch = now

    def on_pet_click(self) -> None:
        self.toggle_chat()

    def toggle_chat(self) -> None:
        self.chat.toggle()
        self.bubble.hide()

    def new_session(self) -> None:
        if self.session:
            self.session.reset()
        self.cfg["session_id"] = None
        save_config(self.cfg)
        self.chat.system("—— 已新建会话，上下文已清空 ——")

    # -- event pump (main thread) -----------------------------------------
    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        self.pet.win.after(60, self._pump)

    def _handle(self, kind: str, payload: object) -> None:
        if kind == "ready":
            self.chat.system(f"—— {payload} ——")
        elif kind == "harness_error":
            self.session_error = str(payload)
            self.pet.set_speaking(False)
            self.pet.set_mood("sad")
            if self.chat.streaming:
                self.chat.end_reply(False, str(payload)[:400])
            else:
                self.chat.system("⚠ 连接 DeepSeek Harness 失败：" + str(payload)[:400], "err")
            self.bubble.show("我连不上 Harness…", 6)
        elif kind == "session":
            self.cfg["session_id"] = payload
            save_config(self.cfg)
        elif kind == "delta":
            self.chat.push_delta(str(payload))
            self.model_reply += str(payload)
            self.bubble.show(self.model_reply[-160:] or "…", 600)
            if len(self.model_reply) >= 6:
                self._mood_from_reply(self.model_reply)
        elif kind == "thinking":
            self.chat.set_status("思考中…")
        elif kind == "phase":
            if payload == "step_end":
                self.chat.set_status("整理中…")
        elif kind == "done":
            self.pet.set_speaking(False)
            rc = int(payload)
            note = "" if rc == 0 else f"（Harness 退出码 {rc}：可能是网络、额度或权限问题）"
            self.chat.end_reply(rc == 0, note)
            if self.model_reply:
                self._mood_from_reply(self.model_reply, force=True)
                self.bubble.show(self.model_reply, float(self.cfg.get("bubble_seconds", 10)))
            else:
                self.bubble.hide()
        elif kind == "reply_final":
            if payload and not self.model_reply:
                self.model_reply = str(payload)
                self.chat.push_delta(str(payload))
                self._mood_from_reply(self.model_reply)
        elif kind == "stderr":
            pass  # model reasoning / diagnostics: intentionally hidden

    def _bubble_tick(self) -> None:
        self.bubble.tick()
        self.pet.win.after(400, self._bubble_tick)

    # -- lifecycle ---------------------------------------------------------
    def run(self) -> None:
        if self.args.say:
            self.chat.show()

            def auto() -> None:
                self.chat.entry.insert(0, self.args.say)
                self.chat.send()

            self.pet.win.after(1500, auto)
        self.pet.win.mainloop()

    def quit(self) -> None:
        try:
            if self.session:
                self.session.cancel()
            self.pet.save_position()
        finally:
            self.pet.win.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepSeek 大肥鱼桌宠")
    parser.add_argument("--chat", action="store_true", help="启动时打开聊天窗口")
    parser.add_argument("--say", default="", help="启动后自动发送一条消息（自测用）")
    parser.add_argument("--skin", default="", help="指定形象目录名")
    parser.add_argument("--scale", type=float, default=0.0, help="覆盖显示比例")
    parser.add_argument("--reset", action="store_true", help="忽略已保存的会话")
    parser.add_argument("--timeout", type=float, default=300.0, help="单轮超时秒数")
    args = parser.parse_args()

    skins = load_skins()
    if not skins:
        raise SystemExit(f"没有找到任何形象，请检查 {SKINS_DIR}")

    cfg = load_config()
    if args.skin and args.skin in skins:
        cfg["skin"] = args.skin
    if args.scale:
        cfg["scale"] = args.scale
    app = PetApp(cfg, args, skins)
    app.run()


if __name__ == "__main__":
    main()
