"""Resolve codex / gemini CLI entry points on Windows + Unix.

Windows npm CLIs ship as bash POSIX shims that asyncio.create_subprocess_exec
cannot execute. We parse the .cmd shim to extract `node <entry>.js` and call
node.exe directly — also bypasses cmd.exe's OEM codepage corruption of UTF-8
arguments (Chinese prompts).
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import config
from .paths import IS_WINDOWS


def resolve_cli(name: str) -> str | None:
    """Prefer .cmd/.bat/.exe on Windows; fall back to plain `which`."""
    if IS_WINDOWS:
        for ext in (".cmd", ".bat", ".exe", ".ps1"):
            p = shutil.which(name + ext)
            if p:
                return p
    return shutil.which(name)


def resolve_node_cli(name: str) -> list[str] | None:
    """Extract `node <entry>.js` (or `.mjs` / `.cjs`) from a Windows npm .cmd shim.

    Going through the .cmd → cmd.exe → node chain breaks stdout redirection on
    DETACHED_PROCESS spawns (the child Node inherits a console-bound stdout, not
    the file fd Python handed us). Calling `node <entry>` directly preserves the
    fd. Returns None on non-Windows; caller should fall back to resolve_cli().

    Matches both .js and .mjs/.cjs so user-installed shims (e.g. a model-rewrite
    wrapper) are still preferred over the raw .cmd.
    """
    if not IS_WINDOWS:
        return None
    cmd_shim = shutil.which(name + ".cmd")
    if not cmd_shim:
        return None
    try:
        text = Path(cmd_shim).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    m = re.search(r'"%dp0%\\([^"]+\.(?:m?js|cjs))"', text)
    if not m:
        return None
    js_rel = m.group(1).replace("/", "\\")
    js_abs = str(Path(cmd_shim).parent / js_rel)
    if not Path(js_abs).exists():
        return None
    node = shutil.which("node.exe") or shutil.which("node")
    if not node:
        return None
    return [node, js_abs]


def codex_safe_cwd(cwd: str | None = None) -> str:
    """Map non-ASCII cwd to a pre-created ASCII junction.

    Codex CLI puts cwd in HTTP headers; non-ASCII triggers an upstream encoding
    bug that retries 5 times then fails. Users mount a junction:
        mklink /J C:\\ascii-alias D:\\real-path-with-unicode
    Then export CCB_CWD_REMAPS="D:\\real-path-with-unicode=C:\\ascii-alias".
    """
    import os
    cwd = cwd or os.getcwd()
    if all(ord(c) < 128 for c in cwd):
        return cwd
    for src, dst in config.cwd_remaps():
        if cwd.startswith(src) and Path(dst).exists():
            remapped = dst + cwd[len(src):]
            if Path(remapped).exists():
                return remapped
    return cwd


def ensure_cwd_trusted(cwd: str | None = None) -> str:
    """Self-healing: make sure codex trusts the working directory.

    CC Switch / Codex Desktop rewrite ~/.codex/config.toml and wipe the
    [projects] trust table, so the TUI asks "Do you trust this directory?"
    on every window-mode spawn. This writes cwd into config.toml's trust
    table before spawn. Disable with CCB_AUTO_TRUST_CWD=0.
    """
    import os as _os
    cwd = (cwd or _os.getcwd()).lower()
    cfg = Path.home() / ".codex" / "config.toml"
    try:
        text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
    except Exception:
        return cwd
    key = "[projects.'" + cwd + "']"
    if key in text:
        return cwd
    block = "\n" + key + "\ntrust_level = \"trusted\"\n"
    try:
        with cfg.open("a", encoding="utf-8") as f:
            f.write(block)
    except Exception:
        pass
    return cwd
