"""Legacy background job mode.

Window mode (window_mode.py) is recommended. Background mode kept for
backward compatibility:
  - codex runs in an asyncio child of the MCP server (NOT detached)
  - if MCP server restarts, the job is orphaned and status freezes at 'running'
  - poll_codex_job checks the on-disk JSON, which may grow stale on orphan
  - Use this only if you want the MCP server to hold the codex handle
    (e.g. for guaranteed cancel-via-handle in cancel_codex_job).
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path

from . import accounts, config
from .activity import parse_codex_activity
from .classify import classify_codex_result, parse_quota_reset
from .cli import codex_safe_cwd, resolve_cli, resolve_node_cli
from .jobs import (
    BG_JOB_PROCS,
    BG_JOB_TASKS,
    job_path,
    load_job,
    now_iso,
    update_job,
    write_job,
)
from .paths import JOBS_DIR, STREAM_DIR
from .spawn import codex_pinned_flags, extract_session_id_from_jsonl, with_summary_tail
from .viewer import open_tail_window

DEFAULT_TIMEOUT = 30 * 60
_AUTH_SWAP_LOCK = asyncio.Lock()


async def _run_codex_job_bg(
    job_id: str,
    prompt: str,
    session_id: str | None,
    timeout_sec: int,
    account: str | None,
    auto_rotate: bool,
) -> None:
    codex_prefix = resolve_node_cli("codex") or (
        [resolve_cli("codex")] if resolve_cli("codex") else None
    )
    if not codex_prefix:
        update_job(job_id, status="error", error="codex not in PATH", finished_at=now_iso())
        return

    rotation_on = accounts.is_rotation_enabled()
    tried: set[str] = set()
    attempts: list[dict] = []
    stream_path = STREAM_DIR / f"{job_id}.jsonl"

    while True:
        chosen: str | None = None
        if account:
            chosen = account
        elif rotation_on:
            chosen = accounts.pick_next_eligible_account(tried)
            if not chosen:
                update_job(
                    job_id, status="error",
                    error="all accounts exhausted/banned",
                    tried_accounts=sorted(tried),
                    attempts=attempts,
                    finished_at=now_iso(),
                )
                return

        async with _AUTH_SWAP_LOCK:
            if chosen:
                ok, err = accounts.activate_account(chosen)
                if not ok:
                    tried.add(chosen)
                    attempts.append({"account": chosen, "classification": "activate_failed", "error": err})
                    if account or not auto_rotate:
                        update_job(
                            job_id, status="error",
                            error=f"activate {chosen}: {err}",
                            account_used=chosen, attempts=attempts, finished_at=now_iso(),
                        )
                        return
                    continue
                update_job(job_id, account_used=chosen)

            with tempfile.TemporaryDirectory(prefix="codex_bg_out_") as tmp:
                out_file = Path(tmp) / "last.md"
                out_file.touch()
                flags = [
                    *codex_pinned_flags(),
                    "--skip-git-repo-check",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "--json",
                    "--output-last-message", str(out_file),
                ]
                if session_id:
                    cmd = [*codex_prefix, "exec", "resume", session_id, *flags, prompt]
                else:
                    cmd = [*codex_prefix, "exec", "--cd", codex_safe_cwd(), *flags, prompt]

                env = os.environ.copy()
                if chosen:
                    env["CODEX_HOME"] = str(accounts.account_home(chosen))

                if chosen and tried:
                    # Mark account rotation in the stream so viewers can split sections.
                    try:
                        with stream_path.open("ab") as f:
                            f.write(json.dumps({"type": "bridge.rotated", "account": chosen}).encode() + b"\n")
                    except Exception:
                        pass

                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=env,
                    )
                except Exception as e:
                    err_msg = f"spawn: {type(e).__name__}: {e}"
                    attempts.append({"account": chosen, "classification": "spawn_failed", "error": err_msg})
                    if account or not auto_rotate:
                        update_job(job_id, status="error", error=err_msg, attempts=attempts, finished_at=now_iso())
                        return
                    if chosen:
                        tried.add(chosen)
                    continue

                BG_JOB_PROCS[job_id] = proc
                actual_sid = ""

                async def _drain_stdout():
                    nonlocal actual_sid
                    assert proc.stdout is not None
                    with stream_path.open("ab") as f:
                        while True:
                            line = await proc.stdout.readline()
                            if not line:
                                break
                            f.write(line)
                            f.flush()
                            if not actual_sid:
                                try:
                                    sid = extract_session_id_from_jsonl(line.decode("utf-8", errors="replace"))
                                    if sid:
                                        actual_sid = sid
                                        update_job(job_id, session_id=actual_sid)
                                except Exception:
                                    pass

                async def _drain_stderr() -> str:
                    assert proc.stderr is not None
                    parts: list[bytes] = []
                    while True:
                        line = await proc.stderr.readline()
                        if not line:
                            break
                        parts.append(line)
                    return b"".join(parts).decode("utf-8", errors="replace")

                try:
                    stderr_task = asyncio.create_task(_drain_stderr())
                    stdout_task = asyncio.create_task(_drain_stdout())
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=timeout_sec)
                    except asyncio.TimeoutError:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        await proc.wait()
                    await stdout_task
                    stderr = await stderr_task
                finally:
                    BG_JOB_PROCS.pop(job_id, None)

                try:
                    result_text = out_file.read_text(encoding="utf-8", errors="replace").strip()
                except Exception:
                    result_text = ""

                classification = classify_codex_result(proc.returncode or 0, stderr, result_text)
                attempts.append({
                    "account": chosen,
                    "rc": proc.returncode,
                    "classification": classification,
                })

                if chosen:
                    if classification == "ok":
                        accounts.mark_account(chosen, "active")
                    elif classification == "quota_exhausted":
                        reset_at = parse_quota_reset(stderr + "\n" + result_text)
                        accounts.mark_account(
                            chosen, "quota_exhausted",
                            blocked_until=reset_at,
                            ttl_hours=config.get("default_quota_ttl_hours") if reset_at is None else None,
                        )
                        tried.add(chosen)
                        if auto_rotate and not account:
                            continue
                    elif classification == "banned":
                        accounts.mark_account(chosen, "banned", ttl_hours=config.get("default_ban_ttl_hours"))
                        tried.add(chosen)
                        if auto_rotate and not account:
                            continue
                    elif classification == "auth_invalid":
                        accounts.mark_account(chosen, "auth_invalid")
                        tried.add(chosen)
                        if auto_rotate and not account:
                            continue

                if proc.returncode != 0:
                    update_job(
                        job_id, status="error",
                        error=f"codex rc={proc.returncode} ({classification})",
                        result=result_text, stderr_tail=stderr[-1500:],
                        attempts=attempts, finished_at=now_iso(),
                    )
                    return

                update_job(
                    job_id, status="done",
                    result=result_text,
                    attempts=attempts,
                    finished_at=now_iso(),
                )
                return


def register(mcp) -> None:
    @mcp.tool()
    async def spawn_codex_background(
        prompt: str,
        session_id: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT,
        account: str | None = None,
        auto_rotate: bool = True,
        auto_tail: bool = True,
    ) -> dict:
        """Background spawn (legacy). codex held in MCP server's asyncio tree.

        Returns immediately; use poll_codex_job(job_id) for status.
        ⚠ If MCP server restarts, job is orphaned with status stuck at 'running'.
        Prefer spawn_codex_window for production use.
        """
        if not prompt or not prompt.strip():
            return {"error": "[FAIL] empty prompt"}
        prompt = with_summary_tail(prompt)

        job_id = "j-" + uuid.uuid4().hex[:10]
        stream_path = STREAM_DIR / f"{job_id}.jsonl"
        stream_path.touch()

        job = {
            "job_id": job_id,
            "status": "starting",
            "mode": "background",
            "prompt": prompt,
            "session_id": session_id or "",
            "stream_file": str(stream_path),
            "cwd": os.getcwd(),
            "account_requested": account,
            "auto_rotate": auto_rotate,
            "started_at": now_iso(),
        }
        write_job(job)
        update_job(job_id, status="running")

        task = asyncio.create_task(
            _run_codex_job_bg(job_id, prompt, session_id, timeout_sec, account, auto_rotate)
        )
        BG_JOB_TASKS[job_id] = task

        tail_opened = False
        tail_terminal = ""
        if auto_tail:
            tail_opened, tail_terminal = open_tail_window(stream_path)

        return {
            "job_id": job_id,
            "status": "running",
            "stream_file": str(stream_path),
            "tail_opened": tail_opened,
            "tail_terminal": tail_terminal,
        }

    @mcp.tool()
    async def poll_codex_job(
        job_id: str,
        wait_sec: int = 0,
        include_activity: bool = False,
    ) -> dict:
        """Query background job status. wait_sec > 0 blocks until done or timeout."""
        job = load_job(job_id)
        if job is None:
            return {"error": f"[FAIL] no such job '{job_id}'"}

        done_statuses = ("done", "error", "cancelled")
        if job["status"] not in done_statuses and wait_sec > 0:
            task = BG_JOB_TASKS.get(job_id)
            if task is not None and not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=wait_sec)
                except asyncio.TimeoutError:
                    pass
            job = load_job(job_id) or job

        if include_activity and job.get("stream_file"):
            try:
                job["activity"] = parse_codex_activity(Path(job["stream_file"]))
            except Exception as e:
                job["activity"] = {"error": f"parse failed: {type(e).__name__}: {e}"}
        return job

    @mcp.tool()
    async def list_codex_jobs(limit: int = 20) -> list[dict]:
        """List N most-recent jobs (background or window mode), newest first."""
        files = sorted(JOBS_DIR.glob("j-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        out: list[dict] = []
        for f in files:
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
                out.append({
                    "job_id": j.get("job_id"),
                    "status": j.get("status"),
                    "mode": j.get("mode", ""),
                    "session_id": j.get("session_id", ""),
                    "account_used": j.get("account_used"),
                    "started_at": j.get("started_at"),
                    "finished_at": j.get("finished_at"),
                    "stream_file": j.get("stream_file"),
                })
            except Exception:
                continue
        return out

    @mcp.tool()
    async def cancel_codex_job(job_id: str) -> dict:
        """Kill background job's codex subprocess + cancel its asyncio task.

        ⚠ Known issue: this may hang for a long time waiting on the stream
        monitor; see README "Caveats / known issues". Window mode jobs cannot
        be cancelled this way (the codex process is detached) — close the
        viewer window or kill the PID externally.
        """
        job = load_job(job_id)
        if job is None:
            return {"error": f"[FAIL] no such job '{job_id}'"}
        if job["status"] in ("done", "error", "cancelled"):
            return {"job_id": job_id, "already": job["status"]}

        proc = BG_JOB_PROCS.pop(job_id, None)
        if proc and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass

        task = BG_JOB_TASKS.pop(job_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        update_job(job_id, status="cancelled", finished_at=now_iso())
        return {"job_id": job_id, "status": "cancelled"}
