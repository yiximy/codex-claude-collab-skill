"""Job-level tools: cancel + list.

These are mode-agnostic — they work on any job spawned via spawn_codex
regardless of the underlying execution path (native TUI / legacy sync).
"""
from __future__ import annotations

import json

from .jobs import BG_JOB_PROCS, BG_JOB_TASKS, load_job, now_iso, update_job
from .paths import JOBS_DIR


def register(mcp) -> None:
    @mcp.tool()
    async def cancel_codex_job(job_id: str) -> dict:
        """Best-effort cancel.

        - Legacy (mode=`background`): kills the asyncio task + subprocess
          held by the MCP server. May hang briefly waiting on stream
          drains.
        - Native TUI (mode=`native_tui`): the codex process is detached
          and the bridge has no handle on it. Only the on-disk job
          status is flipped to `cancelled`; you must close the terminal
          window manually to actually stop codex.
        """
        job = load_job(job_id)
        if job is None:
            return {"error": f"[FAIL] no such job '{job_id}'"}
        if job.get("status") in ("done", "error", "cancelled"):
            return {"job_id": job_id, "already": job["status"]}

        mode = job.get("mode", "")

        if mode == "background":
            import asyncio
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

        # Native TUI / window mode: detached, no handle. Mark only.
        update_job(job_id, status="cancelled", finished_at=now_iso())
        return {
            "job_id": job_id,
            "status": "cancelled_metadata_only",
            "note": (
                "Job status updated to cancelled, but the codex process is "
                "detached from the bridge — close the terminal window to "
                "actually stop codex."
            ),
        }

    @mcp.tool()
    async def list_codex_jobs(limit: int = 20) -> list[dict]:
        """List the N most-recent jobs newest-first, any mode."""
        files = sorted(
            JOBS_DIR.glob("j-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]
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
                    "rollout_file": j.get("rollout_file"),
                    "stream_file": j.get("stream_file"),
                })
            except Exception:
                continue
        return out
