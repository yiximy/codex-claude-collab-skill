"""Codex CLI subprocess primitives.

Three flavors:
  - run_subprocess: await-able sync exec, captures stdout/stderr to a log file.
  - spawn_codex_live_once: live-streaming exec, writes JSONL to <stream>.jsonl.
  - spawn_codex_detached: fire-and-forget for window-mode (Windows DETACHED_PROCESS).

All flavors share:
  - stdin=DEVNULL (codex hangs if stdin is a TTY)
  - --json + --output-last-message <file> for downstream parsing
  - --dangerously-bypass-approvals-and-sandbox (caller's prompt is trusted)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

from . import config
from .cli import codex_safe_cwd
from .paths import IS_WINDOWS, LOG_DIR, RUNNING_DIR, STREAM_DIR

_SESSION_UUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")

SUMMARY_TAIL = (
    "\n\n---\n"
    "Finally, write a short paragraph (<= 250 words) summarizing this run:\n"
    "- Files changed (path + 1-line description each)\n"
    "- Key commands / tests run (verdict, not full stdout)\n"
    "- Open issues or unresolved questions\n"
    "- Spots the caller should review carefully\n"
    "If the task was purely advisory (no files / no commands), a brief conclusion is enough."
)


def with_summary_tail(prompt: str) -> str:
    if not prompt or not config.get("summary_tail_enabled"):
        return prompt
    if "summar" in prompt[-300:].lower() or "总结" in prompt[-300:]:
        return prompt
    return prompt + SUMMARY_TAIL


def extract_session_id_from_jsonl(line: str) -> str | None:
    """Walk a JSONL event looking for any UUID inside session-named keys."""

    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if "session" in str(k).lower():
                    found = walk(v)
                    if found:
                        return found
            for v in value.values():
                found = walk(v)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = walk(item)
                if found:
                    return found
        elif isinstance(value, str):
            m = _SESSION_UUID_RE.search(value)
            return m.group(1) if m else None
        return None

    try:
        parsed = json.loads(line)
    except Exception:
        m = _SESSION_UUID_RE.search(line)
        return m.group(1) if m else None
    return walk(parsed)


def codex_pinned_flags() -> list[str]:
    """Model / reasoning / fast-mode flags from config.

    `model_reasoning_summary=auto` (default) makes codex emit a textual
    reasoning summary inside `reasoning` items in --json mode, so the viewer
    has an opening "what I'm about to do" block to render. The user's own
    ~/.codex/config.toml may pin `summary=none` for TUI runs — we override
    on each spawn so bridge windows always have visible commentary.
    """
    flags = ["-m", str(config.resolve_codex_model()),
             "-c", "model_reasoning_effort=" + str(config.get("codex_reasoning_effort")),
             "-c", "model_reasoning_summary=" + str(config.get("codex_reasoning_summary"))]
    if config.get("codex_fast_mode"):
        flags += ["--enable", "fast_mode"]
    return flags


async def run_subprocess(
    cmd: list[str],
    timeout_sec: int,
    log_name: str,
    extra_env: dict | None = None,
) -> tuple[int, str, str]:
    """Run cmd, capture (rc, stdout, stderr). Time out and kill on overflow.

    Errors are swallowed so the MCP server doesn't crash on subprocess hiccups.
    """
    log_path = LOG_DIR / f"{log_name}.log"
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except Exception as e:
            return 127, "", f"spawn failed: {type(e).__name__}: {e}"
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            await proc.wait()
            return 124, "", f"timeout after {timeout_sec}s"
        stdout = (stdout_b or b"").decode("utf-8", errors="replace")
        stderr = (stderr_b or b"").decode("utf-8", errors="replace")
        try:
            log_path.write_text(
                f"cmd: {cmd}\nrc: {proc.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}",
                encoding="utf-8",
            )
        except Exception:
            pass
        return proc.returncode, stdout, stderr
    except Exception as e:
        return 1, "", f"unexpected: {type(e).__name__}: {e}"


def spawn_codex_detached(
    cmd: list[str],
    stream_path: Path,
    cwd: str,
    extra_env: dict,
) -> subprocess.Popen:
    """Fire-and-forget: codex stdout/stderr redirected to stream file.

    Windows: DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP, child outlives parent.
    Caller may keep Popen for a short viability check, then drop it.
    """
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    stream_fd = open(stream_path, "ab", buffering=0)
    creationflags = 0
    if IS_WINDOWS:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=stream_fd,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=env,
            creationflags=creationflags,
            close_fds=True,
        )
    finally:
        try:
            stream_fd.close()
        except Exception:
            pass
    return proc


async def wait_pid_exit(pid: int, timeout_sec: int) -> bool:
    """Return True if pid exits within timeout."""
    if IS_WINDOWS:
        return await asyncio.to_thread(_wait_pid_exit_windows, pid, timeout_sec)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_sec
    while loop.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        await asyncio.sleep(0.2)
    return False


def _wait_pid_exit_windows(pid: int, timeout_sec: int) -> bool:
    import ctypes
    SYNCHRONIZE = 0x00100000
    WAIT_OBJECT_0 = 0
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return True
    try:
        return kernel32.WaitForSingleObject(handle, int(timeout_sec * 1000)) == WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)
