"""Unified codex spawn + progress tools.

The user-facing surface here is intentionally small:
  - spawn_codex(prompt, wait=False, with_window=True, ...)
  - peek_codex(job_id)
  - wait_for_codex(job_id, timeout_sec)
Job-level tools (cancel_codex_job / list_codex_jobs) live in jobs_tools.py.

Default mode (wait=False, with_window=True) opens codex as a native TUI in
a new terminal tab (wezterm / Windows Terminal / etc.), then tracks via
codex's own rollout file at ~/.codex/sessions/.../rollout-<ts>-<sid>.jsonl.
The user sees codex itself render — apply_patch, inline diffs, command
output, reasoning summaries — not a re-rendering layer.

wait=True, with_window=False falls back to the legacy `codex exec --json`
synchronous wrapper for short Q&A that doesn't merit a window.

wait=False, with_window=False is rejected: no way to observe progress and
nothing to return.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from . import accounts, config
from .activity import (
    parse_codex_activity,
    parse_rollout_activity,
    peek_last_jsonl_event,
)
from .cli import codex_safe_cwd, ensure_cwd_trusted, resolve_cli, resolve_node_cli
from .jobs import load_job, now_iso, update_job, write_job
from .paths import IS_WINDOWS, JOBS_DIR
from .spawn import with_summary_tail

DEFAULT_TIMEOUT = 30 * 60

_SID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)

_AUTH_SWAP_LOCK = asyncio.Lock()


# ─── rollout / session helpers ────────────────────────────────────────────


def _rollout_root() -> Path:
    return Path.home() / ".codex" / "sessions"


def _snapshot_rollouts() -> set[Path]:
    """Return current set of rollout-*.jsonl paths under ~/.codex/sessions."""
    root = _rollout_root()
    if not root.exists():
        return set()
    try:
        return set(root.rglob("rollout-*.jsonl"))
    except Exception:
        return set()


async def _wait_for_new_rollout(
    pre_snapshot: set[Path], timeout_sec: float
) -> Path | None:
    """Poll for a rollout file that appeared after `pre_snapshot` was taken."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        current = _snapshot_rollouts()
        new = current - pre_snapshot
        if new:
            return max(new, key=lambda p: p.stat().st_mtime)
        await asyncio.sleep(0.1)
    return None


def _extract_sid_from_rollout_name(path: Path) -> str:
    m = _SID_RE.search(path.name)
    return m.group(1) if m else ""


def _adopt_latest_rollout(job: dict) -> str:
    """Self-healing: if a job never captured a rollout_file (slow start,
    trust prompt delay, etc.), adopt the newest session file created after
    the job started so peek/wait_for_codex can report completion instead of
    spinning forever.
    """
    existing = job.get("rollout_file") or ""
    if existing and Path(existing).exists():
        return existing
    started = job.get("started_at", "")
    start_ts = 0.0
    if started:
        try:
            from datetime import datetime
            start_ts = datetime.fromisoformat(started).timestamp()
        except Exception:
            start_ts = 0.0
    root = _rollout_root()
    best, best_diff = "", float("inf")
    if root.exists():
        try:
            for p in root.rglob("rollout-*.jsonl"):
                try:
                    st = p.stat()
                    ctime = getattr(st, "st_ctime", st.st_mtime)
                except Exception:
                    continue
                # 允许 5 秒容差：任务开始后（或略早）创建的会话都算候选，
                # 选 ctime 最接近任务开始时间的一个（并发场景更安全）。
                if ctime >= start_ts - 5.0:
                    diff = abs(ctime - start_ts)
                    if diff < best_diff:
                        best_diff, best = diff, str(p)
        except Exception:
            pass
    if best:
        try:
            update_job(job["job_id"], rollout_file=best,
                       session_id=_extract_sid_from_rollout_name(best) or job.get("session_id", ""))
        except Exception:
            pass
        return best
    return existing


def _codex_tui_flags() -> list[str]:
    """Flags for `codex` (TUI mode, not `exec`).

    --yolo is the short form of --dangerously-bypass-approvals-and-sandbox.
    `--skip-git-repo-check` is `codex exec`-only and rejected by the TUI
    entry point with "unexpected argument" → exit 2 → window flashes and
    closes; do NOT add it here.
    """
    flags = [
        "--yolo",
        "-m", str(config.resolve_codex_model()),
        "-c", "model_reasoning_effort=" + str(config.get("codex_reasoning_effort")),
        "-c", "model_reasoning_summary=" + str(config.get("codex_reasoning_summary")),
    ]
    if config.get("codex_fast_mode"):
        flags += ["--enable", "fast_mode"]
    return flags


def _sanitize_prompt(prompt: str) -> str:
    """Sanitize a prompt for safe Windows command-line passing.

    `wt new-tab` re-serializes argv into a command line; line breaks and ASCII
    double quotes inside the prompt can break that serialization and make codex
    misparse the prompt (e.g. `error: unexpected argument`). Replace them with
    safe equivalents so the prompt always survives as a single argument.
    """
    return (
        (prompt or "")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\t", " ")
        .replace('"', "\u201c")
    )


def _wezterm_gui_pids() -> list[int]:
    """Return PIDs of running wezterm-gui processes, newest first."""
    try:
        if IS_WINDOWS:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Process wezterm-gui -ErrorAction SilentlyContinue | "
                 "Sort-Object StartTime -Descending | Select-Object -ExpandProperty Id"],
                capture_output=True, text=True, timeout=4,
            )
        else:
            r = subprocess.run(
                ["pgrep", "-x", "wezterm-gui"],
                capture_output=True, text=True, timeout=4,
            )
        if r.returncode != 0:
            return []
        return [int(p) for p in r.stdout.split() if p.strip().isdigit()]
    except Exception:
        return []


def _try_wezterm_cli_spawn(
    wezterm: str, codex_argv: list[str], cwd: str, env: dict,
) -> bool:
    """Try to attach as a tab to ANY running wezterm-gui via its IPC socket.

    Multiple wezterm-gui processes can coexist (each with its own socket).
    `wezterm cli` only consults one socket at a time, defaulting to whichever
    name is baked into the binary's runtime config — often a stale PID. We
    iterate through every live wezterm-gui PID and try its socket explicitly
    via WEZTERM_UNIX_SOCKET until one succeeds.
    """
    pids = _wezterm_gui_pids()
    if not pids:
        return False
    # Socket location: %TEMP%/gui-sock-<pid> on Windows; runtime dir on Unix.
    sock_dir_candidates = []
    if IS_WINDOWS:
        for var in ("TEMP", "TMP"):
            if env.get(var):
                sock_dir_candidates.append(Path(env[var]))
        sock_dir_candidates.append(Path.home() / "AppData/Local/Temp")
    else:
        if env.get("XDG_RUNTIME_DIR"):
            sock_dir_candidates.append(Path(env["XDG_RUNTIME_DIR"]))
        sock_dir_candidates.append(Path("/tmp"))
    for pid in pids:
        for sock_dir in sock_dir_candidates:
            sock_path = sock_dir / f"gui-sock-{pid}"
            if not sock_path.exists():
                continue
            child_env = dict(env)
            child_env["WEZTERM_UNIX_SOCKET"] = str(sock_path)
            try:
                r = subprocess.run(
                    [wezterm, "cli", "spawn", "--cwd", cwd, "--", *codex_argv],
                    env=child_env, capture_output=True, timeout=5,
                )
                if r.returncode == 0:
                    return True
            except Exception:
                continue
    return False


def _spawn_codex_in_new_terminal(
    codex_argv: list[str],
    cwd: str,
    env: dict,
) -> tuple[bool, str]:
    """Open a terminal tab running `codex_argv` with a real TTY.

    Prefers attaching as a new tab in an existing wezterm / Windows Terminal
    so parallel codex spawns share one window instead of scattering across
    several. If no existing terminal exists, a new one is started.
    """
    if IS_WINDOWS:
        CREATE_NEW_CONSOLE = 0x00000010

        # 首选 CREATE_NEW_CONSOLE：由 CreateProcess 直接执行，Python 的
        # list2cmdline 会正确转义所有特殊字符（引号/换行/& 等），避免
        # `wt new-tab` 对 argv 二次拼接导致的参数拆分（unexpected argument）。
        try:
            subprocess.Popen(
                codex_argv, cwd=cwd, env=env,
                creationflags=CREATE_NEW_CONSOLE,
            )
            return True, "new_console"
        except Exception:
            pass

        # 备选：Windows Terminal 标签页（体验更好，若 conhost 不可用时使用）
        wt = shutil.which("wt") or shutil.which("wt.exe")
        if wt:
            try:
                subprocess.Popen(
                    [wt, "new-tab", "--title", "codex",
                     "--startingDirectory", cwd, *codex_argv],
                    env=env,
                )
                return True, "wt"
            except Exception:
                pass

        wezterm = shutil.which("wezterm") or shutil.which("wezterm.exe")
        if wezterm:
            if _try_wezterm_cli_spawn(wezterm, codex_argv, cwd, env):
                return True, "wezterm-tab"
            try:
                subprocess.Popen(
                    [wezterm, "start", "--new-tab", "--cwd", cwd, "--", *codex_argv],
                    env=env,
                )
                return True, "wezterm"
            except Exception:
                pass

        return False, "no terminal available"

    for term in ("x-terminal-emulator", "gnome-terminal", "xterm",
                 "alacritty", "kitty", "wezterm"):
        path = shutil.which(term)
        if not path:
            continue
        try:
            if term == "gnome-terminal":
                subprocess.Popen([path, "--working-directory", cwd, "--tab",
                                  "--", *codex_argv], env=env)
            elif term == "wezterm":
                if _try_wezterm_cli_spawn(path, codex_argv, cwd, env):
                    return True, "wezterm-tab"
                subprocess.Popen([path, "start", "--new-tab", "--cwd", cwd,
                                  "--", *codex_argv], env=env)
            else:
                subprocess.Popen([path, "-e", *codex_argv], cwd=cwd, env=env)
            return True, term
        except Exception:
            continue

    return False, "no terminal found"


# ─── module-level impls (callable from dispatch + tool wrappers) ──────────


async def _spawn_window_impl(
    prompt: str,
    session_id: str | None,
    account: str | None,
    viability_check_sec: float,
) -> dict:
    """Native TUI in a new terminal tab. Fire-and-forget."""
    codex_prefix = resolve_node_cli("codex")
    if not codex_prefix:
        bin_path = resolve_cli("codex")
        codex_prefix = [bin_path] if bin_path else None
    if not codex_prefix:
        return {"error": "[FAIL] codex not in PATH. `npm i -g @openai/codex` first."}

    chosen: str | None = None
    env = os.environ.copy()
    if account:
        async with _AUTH_SWAP_LOCK:
            ok, err = accounts.activate_account(account)
            if not ok:
                return {"error": f"[FAIL] activate {account}: {err}"}
            chosen = account
            env["CODEX_HOME"] = str(accounts.account_home(account))

    cwd = codex_safe_cwd()
    if config.get("auto_trust_cwd"):
        ensure_cwd_trusted(cwd)
    flags = _codex_tui_flags()
    prompt = _sanitize_prompt(prompt)
    if session_id:
        codex_argv = [*codex_prefix, "resume", session_id, *flags, prompt]
    else:
        codex_argv = [*codex_prefix, *flags, "--cd", cwd, prompt]

    pre_snapshot = _snapshot_rollouts()

    window_opened, window_terminal = _spawn_codex_in_new_terminal(
        codex_argv, cwd, env
    )
    if not window_opened:
        return {"error": f"[FAIL] could not open terminal: {window_terminal}"}

    rollout_path = await _wait_for_new_rollout(pre_snapshot, viability_check_sec)

    actual_sid = ""
    if rollout_path:
        actual_sid = _extract_sid_from_rollout_name(rollout_path)

    job_id = "j-" + uuid.uuid4().hex[:10]
    job = {
        "job_id": job_id,
        "status": "running",
        "mode": "native_tui",
        "prompt": prompt,
        "session_id": actual_sid or (session_id or ""),
        "rollout_file": str(rollout_path) if rollout_path else "",
        "cwd": cwd,
        "account_used": chosen,
        "started_at": now_iso(),
        "window_terminal": window_terminal,
    }
    write_job(job)

    if chosen:
        accounts.mark_account(chosen, "active")

    return {
        "job_id": job_id,
        "session_id": actual_sid,
        "rollout_file": str(rollout_path) if rollout_path else "",
        "window_opened": window_opened,
        "window_terminal": window_terminal,
        "account_used": chosen,
        "note": (
            "Native TUI in a real terminal — what you see is codex itself, "
            "not a viewer. Use peek_codex(job_id) for activity, "
            "wait_for_codex(job_id, timeout_sec) to block until task_complete. "
            "Window stays open after task complete — close it manually."
            + ("" if rollout_path else "  ⚠ rollout file not found within "
               f"{viability_check_sec}s; codex may have failed to start.")
        ),
    }


async def _peek_impl(job_id: str, tail_n: int = 20) -> dict:
    job = load_job(job_id)
    if job is None:
        return {"error": f"[FAIL] no such job '{job_id}'"}

    rollout_file = job.get("rollout_file")
    if not rollout_file or not Path(rollout_file).exists():
        rollout_file = _adopt_latest_rollout(job)
    stream_file = job.get("stream_file")
    source_file = rollout_file or stream_file
    is_rollout = bool(rollout_file)

    result: dict = {
        "job_id": job_id,
        "session_id": job.get("session_id", ""),
        "account_used": job.get("account_used"),
        "started_at": job.get("started_at"),
        "rollout_file": rollout_file,
        "stream_file": stream_file,
        "mode": job.get("mode", ""),
        "window_terminal": job.get("window_terminal"),
    }

    if not source_file or not Path(source_file).exists():
        result["completed"] = False
        result["status_summary"] = "no rollout/stream file yet"
        return result

    sp = Path(source_file)
    try:
        mtime = sp.stat().st_mtime
        result["stream_static_for_sec"] = round(time.time() - mtime, 1)
    except Exception:
        pass

    try:
        if is_rollout:
            activity = parse_rollout_activity(sp)
        else:
            activity = parse_codex_activity(sp)
        result["tool_calls_count"] = len(activity["tool_calls"])
        result["file_changes_count"] = len(activity["file_changes"])
        result["agent_messages_count"] = len(activity["agent_messages"])
        result["retry_count"] = activity["retry_count"]
        result["errors"] = activity["errors"][-5:]
        result["completed"] = activity.get("completed", False)
        if activity.get("final_message"):
            result["final_message"] = activity["final_message"]

        recent: list[dict] = []
        for tc in activity["tool_calls"][-tail_n:]:
            recent.append({
                "kind": "tool",
                "name": tc.get("name", ""),
                "summary": tc.get("summary", "")[:200],
            })
        for am in activity["agent_messages"][-3:]:
            recent.append({"kind": "msg", "summary": am[:300]})
        result["recent_events"] = recent
    except Exception as e:
        result["activity_parse_error"] = f"{type(e).__name__}: {e}"

    if not is_rollout:
        try:
            result.update(peek_last_jsonl_event(sp))
        except Exception:
            pass

    silent = result.get("stream_static_for_sec", 0) or 0
    if result.get("completed"):
        result["status_summary"] = "completed"
    elif silent > 300:
        result["status_summary"] = (
            f"rollout silent for {int(silent)}s — codex may be in long "
            "reasoning / stuck on a tool / network blip / window closed"
        )
    elif silent > 60:
        result["status_summary"] = f"rollout silent for {int(silent)}s"
    else:
        result["status_summary"] = "active"

    return result


async def _wait_impl(
    job_id: str,
    timeout_sec: int,
    poll_interval_sec: float = 1.0,
) -> dict:
    job = load_job(job_id)
    if job is None:
        return {"error": f"[FAIL] no such job '{job_id}'"}

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        snapshot = await _peek_impl(job_id)
        if snapshot.get("completed"):
            snapshot["timed_out"] = False
            return snapshot
        await asyncio.sleep(poll_interval_sec)

    snapshot = await _peek_impl(job_id)
    snapshot["timed_out"] = True
    return snapshot


async def _spawn_sync_impl(
    prompt: str,
    session_id: str | None,
    account: str | None,
    timeout_sec: int,
) -> dict:
    """Legacy `codex exec --json` synchronous wrapper. Returns final message.

    Useful when Claude needs codex's answer in this turn for short Q&A and
    doesn't want to open a window. Inherits the codex exec --json quirks
    (no rich rendering, the known parallel-command race) — use sparingly.
    """
    from .sync_mode import _rotate_and_spawn, _spawn_codex_once
    return await _rotate_and_spawn(
        prompt, session_id, timeout_sec,
        _spawn_codex_once, account, auto_rotate=True,
    )


async def spawn_codex_dispatch(
    prompt: str,
    *,
    wait: bool = False,
    with_window: bool = True,
    session_id: str | None = None,
    account: str | None = None,
    timeout_sec: int = DEFAULT_TIMEOUT,
    viability_check_sec: float = 5.0,
) -> dict:
    """Unified spawn entry point. Exposed as the `spawn_codex` mcp tool and
    also called internally by gemini.spawn_parallel for the codex leg.
    """
    if not prompt or not prompt.strip():
        return {"error": "[FAIL] empty prompt"}
    if not with_window and not wait:
        return {"error": (
            "[FAIL] invalid combo: wait=False with_window=False has no way "
            "to observe progress or return a result. Use wait=True or "
            "with_window=True."
        )}

    prompt = with_summary_tail(prompt)

    if with_window:
        spawn_res = await _spawn_window_impl(
            prompt, session_id, account, viability_check_sec,
        )
        if "error" in spawn_res or not wait:
            return spawn_res
        job_id = spawn_res["job_id"]
        wait_res = await _wait_impl(job_id, timeout_sec)
        wait_res["window_opened"] = spawn_res.get("window_opened")
        wait_res["window_terminal"] = spawn_res.get("window_terminal")
        return wait_res

    return await _spawn_sync_impl(prompt, session_id, account, timeout_sec)


# ─── MCP tool registration ────────────────────────────────────────────────


def register(mcp) -> None:
    @mcp.tool()
    async def spawn_codex(
        prompt: str,
        wait: bool = False,
        with_window: bool = True,
        session_id: str | None = None,
        account: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT,
        viability_check_sec: float = 5.0,
    ) -> dict:
        """Run codex with `prompt`. Returns differently based on mode:

        - `wait=False, with_window=True` (default): open codex as a native TUI
          in a new terminal tab (wezterm / Windows Terminal / etc.), return
          job_id + rollout_file immediately. Track progress via peek_codex /
          wait_for_codex. Codex outlives the MCP server (no pid retained).
        - `wait=True, with_window=False`: legacy `codex exec --json` blocking
          call, returns final message text. Use for short Q&A where you don't
          want a terminal window.
        - `wait=True, with_window=True`: open the window AND block here until
          codex emits `task_complete`. Useful when Claude has follow-up work
          on the result.
        - `wait=False, with_window=False`: rejected — nothing to observe.

        `session_id` resumes a previous codex session by id. `account` picks
        a specific account (otherwise current `CODEX_HOME`). `timeout_sec`
        applies to the blocking modes.
        """
        return await spawn_codex_dispatch(
            prompt,
            wait=wait,
            with_window=with_window,
            session_id=session_id,
            account=account,
            timeout_sec=timeout_sec,
            viability_check_sec=viability_check_sec,
        )

    @mcp.tool()
    async def peek_codex(job_id: str, tail_n: int = 20) -> dict:
        """Snapshot of codex job progress from the rollout (or legacy --json)
        file. Returns activity counts (tool_calls / file_changes /
        agent_messages), recent events, completion state, final_message if
        done. Does not infer process liveness.
        """
        return await _peek_impl(job_id, tail_n)

    @mcp.tool()
    async def wait_for_codex(
        job_id: str,
        timeout_sec: int = 600,
        poll_interval_sec: float = 1.0,
    ) -> dict:
        """Block until codex job emits task_complete, or timeout. Returns the
        final peek snapshot with `timed_out` set accordingly.
        """
        return await _wait_impl(job_id, timeout_sec, poll_interval_sec)
