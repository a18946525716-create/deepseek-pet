"""Diagnostic: locate the harness, then run one real headless turn.

    python tools/probe_dsh.py ["optional prompt"]
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dsh_link  # noqa: E402


def main() -> None:
    exe, cli = dsh_link.find_cli()
    print("electron :", exe)
    print("cli      :", cli)
    try:
        from PIL import ImageTk  # noqa: F401

        print("ImageTk  : OK")
    except Exception as exc:
        print("ImageTk  : MISSING", exc)

    prompt = " ".join(sys.argv[1:]) or "只回复六个字：蓝色大肥鱼好"
    session = dsh_link.DshSession(cwd=os.getcwd(), timeout=180)
    print(f"\n>>> {prompt}")

    def emit(kind: str, payload: object) -> None:
        if kind == "delta":
            print(payload, end="", flush=True)
        elif kind in ("session", "phase"):
            print(f"\n[{kind}: {payload}]", end="")
        elif kind == "stderr":
            print(f"\n[stderr] {str(payload)[:160]}", end="")
        elif kind == "done":
            print(f"\n[done rc={payload}] session={session.session_id}")

    text = session.turn(prompt, emit)
    print("\n--- final ---")
    print(text)


if __name__ == "__main__":
    main()
