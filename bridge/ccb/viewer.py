"""Open a separate terminal window running tail_viewer against a stream file.

Tries wezterm > Windows Terminal (wt) > xterm/gnome-terminal > new-console fallback.
Returns (success, terminal_name) so callers can tell whether the window opened.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .paths import IS_WINDOWS

VIEWER_SCRIPT = Path(__file__).parent / "tail_viewer.py"


def open_tail_window(stream_path: Path) -> tuple[bool, str]:
    """Spawn a new terminal window running `python tail_viewer.py <stream>`."""
    viewer = str(VIEWER_SCRIPT)
    py = sys.executable

    if IS_WINDOWS:
        CREATE_NEW_CONSOLE = 0x00000010
        # 1. wezterm
        wezterm = shutil.which("wezterm") or shutil.which("wezterm.exe")
        if wezterm:
            try:
                subprocess.Popen(
                    [wezterm, "start", "--always-new-process", "--", py, viewer, str(stream_path)],
                )
                return True, "wezterm"
            except Exception:
                pass
        # 2. Windows Terminal
        wt = shutil.which("wt") or shutil.which("wt.exe")
        if wt:
            try:
                subprocess.Popen(
                    [wt, "new-tab", "--title", f"ccb:{stream_path.stem}", py, viewer, str(stream_path)],
                )
                return True, "wt"
            except Exception:
                pass
        # 3. Bare new console
        try:
            subprocess.Popen(
                [py, viewer, str(stream_path)],
                creationflags=CREATE_NEW_CONSOLE,
            )
            return True, "new_console"
        except Exception as e:
            return False, f"failed: {e}"

    # Unix
    for term in ("x-terminal-emulator", "gnome-terminal", "xterm", "alacritty", "kitty"):
        path = shutil.which(term)
        if not path:
            continue
        try:
            if term == "gnome-terminal":
                subprocess.Popen([path, "--", py, viewer, str(stream_path)])
            else:
                subprocess.Popen([path, "-e", py, viewer, str(stream_path)])
            return True, term
        except Exception:
            continue

    return False, "no terminal found"
