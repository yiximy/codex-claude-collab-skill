"""Standalone tail viewer for codex --json event streams.

Renders JSONL events from `~/.ai-bridge/streams/<job_id>.jsonl` to a terminal
with ANSI colors. Aims to match the detail level of codex's native exec output:
shell command stdout is rendered inline, file edits show a `+N -M` git stat,
todo lists re-render on updates.

Usage:
    python tail_viewer.py <stream.jsonl>

Set env CCB_SHOW_TRACE=1 to surface codex's internal tracing log noise.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


_GIT_ROOT_CACHE: dict[str, str | None] = {}
_PREV_FILE_CONTENT: dict[str, str] = {}


def _git_root_for(path: str) -> str | None:
    """Resolve repo root containing `path`. Cached by parent dir."""
    try:
        parent = str(Path(path).resolve().parent)
    except Exception:
        return None
    if parent in _GIT_ROOT_CACHE:
        return _GIT_ROOT_CACHE[parent]
    try:
        r = subprocess.run(
            ["git", "-C", parent, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2, errors="replace",
        )
        root = r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        root = None
    _GIT_ROOT_CACHE[parent] = root
    return root


def _file_diff_stat(path: str, kind: str) -> str:
    """Return '+N -M' / '+N (new)' / '' for a changed file.

    Sources of truth (in order):
      1. `git diff --numstat HEAD -- path` for tracked files with unstaged edits
      2. `git status --porcelain -- path` to detect untracked (codex-created) files
      3. Empty string if file is tracked but clean (edit was already committed)
    """
    try:
        p = Path(path)
        if not p.exists() and kind not in ("delete", "remove"):
            return ""
        root = _git_root_for(path)
        if not root:
            if kind in ("add", "create") and p.exists():
                try:
                    n = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
                    return f"+{n} (new)"
                except Exception:
                    pass
            return ""
        try:
            rel = str(p.resolve().relative_to(Path(root).resolve())).replace("\\", "/")
        except Exception:
            rel = path

        r = subprocess.run(
            ["git", "-C", root, "diff", "--numstat", "HEAD", "--", rel],
            capture_output=True, text=True, timeout=2, errors="replace",
        )
        line = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
        if line:
            parts = line.split("\t")
            if len(parts) >= 2:
                added, removed = parts[0], parts[1]
                if added == "-" or removed == "-":
                    return "(binary)"
                return f"+{added} -{removed}"

        r2 = subprocess.run(
            ["git", "-C", root, "status", "--porcelain", "--", rel],
            capture_output=True, text=True, timeout=2, errors="replace",
        )
        status = r2.stdout.strip()
        if status.startswith("??") and p.exists():
            try:
                n = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
                return f"+{n} (new)"
            except Exception:
                return "(new)"
        return ""
    except Exception:
        return ""


def _read_file_safe(path: str) -> str:
    """Read file content, return '' on any failure (missing / binary / locked)."""
    try:
        p = Path(path)
        if not p.exists():
            return ""
        # Skip large files to avoid blowing up memory / wedging the viewer
        if p.stat().st_size > 2 * 1024 * 1024:
            return ""
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _git_head_content(path: str) -> str:
    """Return file content at HEAD via `git show`, '' if not tracked."""
    try:
        root = _git_root_for(path)
        if not root:
            return ""
        try:
            rel = str(Path(path).resolve().relative_to(Path(root).resolve())).replace("\\", "/")
        except Exception:
            return ""
        r = subprocess.run(
            ["git", "-C", root, "show", f"HEAD:{rel}"],
            capture_output=True, text=True, timeout=3, errors="replace",
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _compute_unified_diff(prev: str, curr: str, path: str) -> list[str]:
    """Return raw unified-diff lines (no ANSI). Empty list if identical."""
    if prev == curr:
        return []
    prev_lines = prev.splitlines(keepends=False)
    curr_lines = curr.splitlines(keepends=False)
    return list(difflib.unified_diff(prev_lines, curr_lines, fromfile=path, tofile=path, n=3, lineterm=""))


_PWSH_PATTERN = re.compile(
    r"^[\"']?[A-Za-z]:\\\\?(?:Program Files\\\\?|Windows\\\\?).*?(?:pwsh|powershell)(?:\.exe)?[\"']?\s+",
    re.IGNORECASE,
)


def _shorten_command(cmd: str) -> tuple[str, str]:
    """Return (shell_tag, displayed_command).

    Strips pwsh.exe / powershell.exe / cmd.exe wrappers, keeping only the inner
    command for display. Returns the shell tag ('pwsh', 'cmd', or '') separately
    so the viewer can show it as a small prefix.
    """
    if not cmd:
        return "", ""
    s = cmd.strip()
    # pwsh.exe / powershell.exe -Command "..."
    m = re.match(
        r"^[\"']?[A-Za-z]:\\\\?[^\"']*?(pwsh|powershell)(?:\.exe)?[\"']?\s+(?:-(?:NoProfile|NoLogo|NonInteractive)\s+)*-Command\s+(.*)$",
        s, re.IGNORECASE | re.DOTALL,
    )
    if m:
        inner = m.group(2).strip()
        # Strip leading/trailing quote if it wraps the whole inner
        if len(inner) >= 2 and inner[0] in ("'", '"') and inner[-1] == inner[0]:
            inner = inner[1:-1]
        return "pwsh", inner
    # cmd.exe /c "..."
    m = re.match(r"^[\"']?[A-Za-z]:\\\\?[^\"']*?cmd(?:\.exe)?[\"']?\s+/[cC]\s+(.*)$", s, re.DOTALL)
    if m:
        inner = m.group(1).strip()
        if len(inner) >= 2 and inner[0] in ("'", '"') and inner[-1] == inner[0]:
            inner = inner[1:-1]
        return "cmd", inner
    # bash -c "..."
    m = re.match(r"^(?:bash|sh)\s+-c\s+(.*)$", s, re.DOTALL)
    if m:
        inner = m.group(1).strip()
        if len(inner) >= 2 and inner[0] in ("'", '"') and inner[-1] == inner[0]:
            inner = inner[1:-1]
        return "sh", inner
    return "", s


def _format_output_block(
    text: str,
    indent: str,
    max_head: int = 8,
    max_tail: int = 20,
    *,
    GRY: str = "",
    DIM: str = "",
    R: str = "",
) -> list[str]:
    """Render captured stdout/stderr with a vertical bar + head/tail truncation."""
    if not text:
        return []
    # Codex sometimes prefixes with shell prologue noise; just keep as-is
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Drop trailing empty lines
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return []
    n = len(lines)
    bar = GRY + indent + "│ " + R
    out: list[str] = []
    if n <= max_head + max_tail + 1:
        for ln in lines:
            out.append(bar + ln)
    else:
        for ln in lines[:max_head]:
            out.append(bar + ln)
        skipped = n - max_head - max_tail
        out.append(
            GRY + indent + "│ " + DIM + "··· (" + str(skipped) + " lines elided) ···" + R
        )
        for ln in lines[-max_tail:]:
            out.append(bar + ln)
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: tail_viewer.py <stream.jsonl>")
        return 1
    stream = sys.argv[1]

    # Force UTF-8 stdout (Windows parent process may inherit cp936/Latin-1)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # Windows: enable ANSI virtual terminal + UTF-8 codepage
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            for hid in (-11, -12):
                h = kernel32.GetStdHandle(hid)
                mode = ctypes.c_ulong()
                if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                    kernel32.SetConsoleMode(h, mode.value | 0x0004)
            kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass

    ESC = "\x1b"
    R = ESC + "[0m"
    B = ESC + "[1m"
    DIM = ESC + "[2m"
    ITAL = ESC + "[3m"
    RED = ESC + "[31m"
    GRN = ESC + "[32m"
    YEL = ESC + "[33m"
    BLU = ESC + "[34m"
    MAG = ESC + "[35m"
    CYA = ESC + "[36m"
    GRY = ESC + "[90m"

    try:
        WIDTH = max(40, os.get_terminal_size().columns)
    except Exception:
        WIDTH = 100

    SPINNER = "|/-\\"
    spin_state = {"i": 0}

    def spin() -> str:
        c = SPINNER[spin_state["i"] % len(SPINNER)]
        spin_state["i"] += 1
        return c

    def hr(ch: str = "─", color: str = GRY) -> str:
        return color + (ch * WIDTH) + R

    def banner(title: str = "") -> str:
        if not title:
            return hr()
        left = GRY + "── " + R + B + title + R + " "
        visible = 4 + len(title)
        return left + GRY + ("─" * max(0, WIDTH - visible)) + R

    def render_md(text: str) -> list[str]:
        out: list[str] = []
        in_code = False
        for line in text.splitlines():
            m = re.match(r"^```\s*(\w*)\s*$", line)
            if m:
                if not in_code:
                    in_code = True
                    lang = m.group(1) or ""
                    out.append(
                        "  " + GRY + "┌── " + R + CYA + (lang or "code") + R + " "
                        + GRY + ("─" * max(0, WIDTH - 11 - len(lang))) + R
                    )
                else:
                    out.append("  " + GRY + "└" + ("─" * max(0, WIDTH - 3)) + R)
                    in_code = False
                continue
            if in_code:
                out.append("  " + GRY + "│ " + R + line)
                continue
            line = re.sub(r"`([^`]+)`", lambda mm: CYA + mm.group(1) + R, line)
            line = re.sub(r"\*\*([^*]+)\*\*", lambda mm: B + mm.group(1) + R, line)
            out.append(line)
        return out

    def emit_agent_message(text: str) -> None:
        if not (text and text.strip()):
            print(DIM + "  (empty assistant message)" + R, flush=True)
            return
        print(flush=True)
        print(banner("codex"), flush=True)
        for ln in render_md(text):
            print(ln, flush=True)
        print(hr(), flush=True)
        print(flush=True)

    def emit_reasoning(text: str) -> None:
        if not (text and text.strip()):
            return
        print(flush=True)
        print(GRY + "── reasoning " + ("─" * max(0, WIDTH - 13)) + R, flush=True)
        for ln in text.splitlines():
            print(GRY + "  " + ITAL + ln + R, flush=True)

    def _render_cmd_header(item: dict) -> None:
        """Print `· Ran [shell] inner_cmd` line for a command_execution item."""
        cmd = item.get("command") or ""
        if isinstance(cmd, list):
            cmd = " ".join(str(x) for x in cmd)
        cmd = str(cmd)
        shell, inner = _shorten_command(cmd)
        tag = (GRY + "[" + shell + "]" + R + " ") if shell else ""
        disp = inner if inner else cmd
        disp = disp if len(disp) <= 600 else disp[:600] + DIM + " …(truncated)" + R
        print(CYA + "· " + R + B + "Ran " + R + tag + disp, flush=True)

    def emit_tool_start(item: dict) -> None:
        """Emit on item.started for non-command tools only.

        Shell commands are rendered as a single block on item.completed (header
        + output + footer together) so parallel command_execution events do not
        interleave: when codex starts cmd1 and cmd2 back-to-back, the JSONL is
        started1/started2/completed1/completed2 — emitting on started would
        place cmd1's output under cmd2's header in the linear scrollback.
        """
        it = item.get("type", "")
        if it == "command_execution":
            return
        cmd = item.get("command") or item.get("arguments") or item.get("args") or ""
        if isinstance(cmd, list):
            cmd = " ".join(str(x) for x in cmd)
        cmd = str(cmd)
        name = item.get("name") or item.get("tool") or it
        disp = cmd if len(cmd) <= 600 else cmd[:600] + DIM + " …(truncated)" + R
        print(BLU + "· " + R + B + name + R + "  " + disp, flush=True)

    def emit_tool_done(item: dict) -> None:
        it = item.get("type", "")
        # For command_execution we deferred the header from item.started so
        # parallel commands don't interleave; print header+output+footer as a
        # single block now.
        if it == "command_execution":
            _render_cmd_header(item)

        rc = item.get("exit_code")
        dur_ms = item.get("duration_ms")
        dur_s = item.get("duration_sec")
        output = item.get("aggregated_output") or item.get("output") or ""
        if isinstance(output, dict):
            output = json.dumps(output, ensure_ascii=False)
        output = str(output)

        if output and output.strip():
            for ln in _format_output_block(output, "  ", GRY=GRY, DIM=DIM, R=R):
                print(ln, flush=True)

        parts: list[str] = []
        if rc is not None:
            sym = (GRN + "✓" + R) if rc == 0 else (RED + "✗" + R)
            parts.append(sym + " exit=" + str(rc))
        if dur_ms is not None:
            parts.append(DIM + str(int(dur_ms)) + "ms" + R)
        elif dur_s is not None:
            parts.append(DIM + str(dur_s) + "s" + R)
        if parts:
            print("  " + DIM + "└─ " + R + "  ".join(parts), flush=True)

    show_diff = os.environ.get("CCB_SHOW_DIFF", "1") != "0"
    diff_max_head = int(os.environ.get("CCB_DIFF_HEAD", "12"))
    diff_max_tail = int(os.environ.get("CCB_DIFF_TAIL", "40"))

    def _render_diff_lines(diff_lines: list[str]) -> None:
        """Print unified diff with +/- coloring, hunks in cyan, headers dim."""
        if not diff_lines:
            return
        # Drop the file header lines (---/+++) since we already showed the path
        body: list[str] = []
        for ln in diff_lines:
            if ln.startswith("---") or ln.startswith("+++"):
                continue
            body.append(ln)
        if not body:
            return
        n = len(body)
        total_cap = diff_max_head + diff_max_tail
        if n <= total_cap + 1:
            view = body
            elided = 0
        else:
            view = body[:diff_max_head] + ["__ELIDED__"] + body[-diff_max_tail:]
            elided = n - diff_max_head - diff_max_tail

        for ln in view:
            if ln == "__ELIDED__":
                print("  " + DIM + "  ··· (" + str(elided) + " diff lines elided) ···" + R, flush=True)
                continue
            if ln.startswith("@@"):
                print("  " + CYA + ln + R, flush=True)
            elif ln.startswith("+"):
                print("  " + GRN + ln + R, flush=True)
            elif ln.startswith("-"):
                print("  " + RED + ln + R, flush=True)
            else:
                print("  " + DIM + ln + R, flush=True)

    def emit_file_change(item: dict) -> None:
        # Codex JSONL: item.changes = [{path, kind}, ...]
        changes = item.get("changes") or []
        if not changes and (item.get("path") or item.get("file")):
            changes = [{"path": item.get("path") or item.get("file"),
                        "kind": item.get("kind") or item.get("action") or "update"}]
        for ch in changes:
            path = ch.get("path") or ch.get("file") or "?"
            kind = (ch.get("kind") or ch.get("action") or "update").lower()
            verb = {
                "add": "Added", "create": "Added",
                "delete": "Deleted", "remove": "Deleted",
                "update": "Edited", "modify": "Edited",
                "rename": "Renamed",
            }.get(kind, "Edited")
            stat = _file_diff_stat(path, kind) if kind not in ("delete", "remove") else ""
            stat_suffix = (" " + DIM + stat + R) if stat else ""
            display_path = path
            root = _git_root_for(path)
            if root:
                try:
                    display_path = str(Path(path).resolve().relative_to(Path(root).resolve())).replace("\\", "/")
                except Exception:
                    pass
            print(MAG + "· " + R + B + verb + R + " " + display_path + stat_suffix, flush=True)

            if not show_diff:
                continue
            if kind in ("delete", "remove"):
                # Deleted: prev was last cached or HEAD; show as all minus
                prev = _PREV_FILE_CONTENT.pop(path, None)
                if prev is None:
                    prev = _git_head_content(path)
                diff = _compute_unified_diff(prev, "", display_path)
                _render_diff_lines(diff)
                continue
            # Add / update path: read current; baseline = last cached, else HEAD
            curr = _read_file_safe(path)
            if path in _PREV_FILE_CONTENT:
                prev = _PREV_FILE_CONTENT[path]
            elif kind in ("add", "create"):
                prev = ""
            else:
                prev = _git_head_content(path)
            diff = _compute_unified_diff(prev, curr, display_path)
            _render_diff_lines(diff)
            _PREV_FILE_CONTENT[path] = curr

    def emit_todo(item: dict, *, updated: bool = False) -> None:
        items = item.get("items") or []
        if not items:
            return
        print(flush=True)
        label = "updated todo" if updated else "todo"
        print(GRY + "── " + label + " " + ("─" * max(0, WIDTH - 5 - len(label))) + R, flush=True)
        for t in items:
            text = t.get("text", "")
            done = t.get("completed", False)
            mark = (GRN + "[x]" + R) if done else (DIM + "[ ]" + R)
            color = DIM if done else R
            print("  " + mark + " " + color + text + R, flush=True)

    state = {"retry_count": 0, "last_retry_print": 0.0,
             "tokens_in_total": 0, "tokens_out_total": 0}

    def handle_event(e: dict) -> None:
        t = e.get("type", "")

        if t == "error":
            msg = str(e.get("message", ""))
            if "Reconnecting" in msg and "stream disconnected" in msg:
                state["retry_count"] += 1
                now = time.time()
                if now - state["last_retry_print"] > 0.25:
                    sys.stdout.write(
                        "\r" + YEL + spin() + R + " " + DIM
                        + "codex network retry x" + str(state["retry_count"]) + R + "  "
                    )
                    sys.stdout.flush()
                    state["last_retry_print"] = now
                return
            print()
            print(RED + "[!] error" + R + " " + msg[:500], flush=True)
            return

        if t == "turn.failed":
            print()
            print(RED + "[!] turn.failed" + R + " " + str(e.get("message", ""))[:500], flush=True)
            return

        if state["retry_count"]:
            sys.stdout.write("\r" + " " * 60 + "\r")
            sys.stdout.flush()
            print(DIM + "(recovered after " + str(state["retry_count"]) + " retries)" + R, flush=True)
            state["retry_count"] = 0

        if t == "thread.started":
            sid = e.get("thread_id", "")
            print(banner("session " + sid[:8]), flush=True)
            return

        if t == "turn.started":
            print(GRY + "── turn start " + ("─" * max(0, WIDTH - 15)) + R, flush=True)
            return

        if t == "turn.completed":
            u = e.get("usage", {}) or {}
            tin = int(u.get("input_tokens", 0) or 0)
            tout = int(u.get("output_tokens", 0) or 0)
            state["tokens_in_total"] += tin
            state["tokens_out_total"] += tout
            print(
                GRY + "── turn done  in=" + str(tin)
                + " out=" + str(tout)
                + DIM + "  (cum in=" + str(state["tokens_in_total"])
                + " out=" + str(state["tokens_out_total"]) + ")" + R
                + " " + GRY + ("─" * max(0, WIDTH - 30 - len(str(tin)) - len(str(tout))
                                         - len(str(state["tokens_in_total"]))
                                         - len(str(state["tokens_out_total"])))) + R,
                flush=True,
            )
            return

        if t == "bridge.rotated":
            print(banner("ROTATED to account " + str(e.get("account", "?"))), flush=True)
            return

        if t.startswith("item."):
            item = e.get("item", {}) or {}
            it = item.get("type", "")
            if t == "item.started":
                if it in ("function_call", "tool_call", "command_execution"):
                    emit_tool_start(item)
                elif it == "todo_list":
                    emit_todo(item)
                elif it == "file_change":
                    # file_change item.started has no diff info yet; render on completed
                    pass
            elif t == "item.updated":
                if it == "todo_list":
                    emit_todo(item, updated=True)
            elif t == "item.completed":
                if it == "agent_message":
                    emit_agent_message(item.get("text") or item.get("message") or "")
                elif it == "reasoning":
                    emit_reasoning(item.get("text") or item.get("message") or "")
                elif it in ("function_call", "tool_call", "command_execution"):
                    emit_tool_done(item)
                elif it == "file_change":
                    emit_file_change(item)
            return

        reason = e.get("reason") or e.get("message") or ""
        if reason:
            print(GRY + "[" + t + "] " + str(reason)[:200] + R, flush=True)

    def is_codex_internal_noise(line: str) -> bool:
        if not line:
            return False
        if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s+(DEBUG|INFO|WARN|ERROR)\s+codex_", line):
            return True
        if line.startswith(("Wall time:", "Output:", "Exit code:", "Error:")):
            return True
        if "codex_core::" in line or "codex_cli::" in line:
            return True
        return False

    show_trace = os.environ.get("CCB_SHOW_TRACE") == "1"

    print(banner("claude-codex-bridge viewer"), flush=True)
    print(DIM + "  stream: " + stream + R, flush=True)
    print(DIM + "  Ctrl+C to close" + R, flush=True)
    print(flush=True)

    try:
        with open(stream, encoding="utf-8", errors="replace") as f:
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.05)
                    continue
                stripped = line.rstrip()
                try:
                    e = json.loads(stripped)
                except Exception:
                    if is_codex_internal_noise(stripped):
                        if show_trace:
                            print(DIM + stripped + R, flush=True)
                    else:
                        print(DIM + stripped + R, flush=True)
                    continue
                try:
                    handle_event(e)
                except Exception as exc:
                    print(RED + "[viewer error] " + type(exc).__name__ + ": " + str(exc) + R, flush=True)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
