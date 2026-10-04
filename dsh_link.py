"""Drive the DeepSeek Harness from Python — the pet's chat backend.

The harness CLI (`dsh headless`) is an Electron binary running in Node mode:

    "<app root>\\DeepSeek Harness.exe" --expose-internals \\
        "<app root>\\resources\\app.asar\\dsh\\node_modules\\@deepseek-ai\\dsh-desktop-host\\lib\\cli.js"

The CLI *script* lives inside `app.asar`, so it has no real filesystem path and
cannot be probed with `Path.exists()`. We therefore locate the app root (which
does exist on disk) and build the virtual path from it.

`dsh headless --json` streams newline-delimited JSON events on stdout; model
reasoning and diagnostics go to stderr. Passing the user's text as a single
argv element keeps Unicode intact (CreateProcessW), which a `cmd.exe /c`
round-trip through the console codepage would not.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

CREATE_NO_WINDOW = 0x08000000
CLI_REL = Path("resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js")
EXE_NAME = "DeepSeek Harness.exe"


def candidate_app_roots() -> Iterable[Path]:
    env = os.environ.get("DSH_APP_ROOT")
    if env:
        yield Path(env)
    yield Path(r"D:\大肥鱼")
    for var in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        if base:
            yield Path(base) / "Programs" / "DeepSeek Harness"
            yield Path(base) / "DeepSeek Harness"
    # shallow sweep of drive roots (finds portable installs like D:\大肥鱼)
    for drive in ("C:\\", "D:\\", "E:\\", "F:\\"):
        try:
            with os.scandir(drive) as it:
                for entry in it:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    if entry.name.startswith(("$", "Windows", "Program", "System")):
                        continue
                    yield Path(entry.path)
        except OSError:
            continue


def find_cli(app_root: str | os.PathLike | None = None) -> tuple[Path, Path]:
    """Return (electron exe, cli js). The cli path is virtual (inside app.asar)."""
    roots = [Path(app_root)] if app_root else list(candidate_app_roots())
    tried = []
    for root in roots:
        exe = root / EXE_NAME
        asar = root / "resources" / "app.asar"
        tried.append(str(root))
        if exe.is_file() and asar.is_file():
            return exe, root / CLI_REL
    raise FileNotFoundError(
        "DeepSeek Harness installation not found. Looked in:\n  " + "\n  ".join(tried[:20])
    )


class DshSession:
    """One persistent harness conversation, driven turn by turn."""

    def __init__(self, cwd: str, app_root: str | None = None, session_id: str | None = None,
                 timeout: float = 300.0):
        self.exe, self.cli = find_cli(app_root)
        self.cwd = cwd
        self.session_id = session_id
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    @property
    def busy(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def cancel(self) -> None:
        with self._lock:
            proc = self._proc
        if proc and proc.poll() is None:
            proc.kill()

    def reset(self) -> None:
        """Forget the conversation; the next turn starts a fresh session."""
        self.cancel()
        self.session_id = None

    # -- one turn ----------------------------------------------------------
    def turn(self, message: str, emit: Callable[[str, object], None]) -> str:
        """Run one turn, streaming events to `emit(kind, payload)`. Returns final text.

        kinds: session | phase | delta | thinking | stderr | done
        The calling thread blocks; run it in a worker thread for a responsive UI.
        """
        argv = [str(self.exe), "--expose-internals", str(self.cli), "headless", "--json"]
        if self.session_id:
            argv += ["--session-id", self.session_id]
        argv.append(message)

        env = {**os.environ, "ELECTRON_RUN_AS_NODE": "1"}
        if not env.get("DSH_HOME"):
            env["DSH_HOME"] = str(Path.home() / ".dsh")

        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # merged: avoids a full stderr pipe deadlocking the read loop
            stdin=subprocess.DEVNULL,
            cwd=self.cwd,
            env=env,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        with self._lock:
            self._proc = proc

        final_text = ""
        streamed: list[str] = []
        deadline = time.monotonic() + self.timeout
        try:
            assert proc.stdout is not None
            for raw in iter(proc.stdout.readline, b""):
                if time.monotonic() > deadline:
                    proc.kill()
                    emit("stderr", f"[timeout after {self.timeout:.0f}s]")
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if not line.startswith("{"):
                    emit("stderr", line)
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    emit("stderr", line)
                    continue
                kind = event.get("type")
                if kind == "session":
                    sid = event.get("sessionId")
                    if sid:
                        self.session_id = sid
                        emit("session", sid)
                elif kind == "text":
                    chunk = event.get("text") or ""
                    if chunk:
                        streamed.append(chunk)
                        emit("delta", chunk)
                elif kind == "thinking":
                    chunk = event.get("text") or ""
                    if chunk:
                        emit("thinking", chunk)
                elif kind == "status":
                    emit("phase", event.get("phase") or "")
                elif kind == "final":
                    final_text = event.get("text") or ""
                elif kind == "error":
                    emit("stderr", str(event.get("message") or event))
        finally:
            rc = proc.wait()
            with self._lock:
                if self._proc is proc:
                    self._proc = None
            emit("done", rc)

        return final_text or "".join(streamed)
