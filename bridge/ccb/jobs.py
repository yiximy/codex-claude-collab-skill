"""Job-state I/O + in-process registries.

A `job` is a JSON file under ~/.ai-bridge/jobs/<job_id>.json tracking one
codex spawn (window / background / live). The corresponding event stream is
~/.ai-bridge/streams/<job_id>.jsonl.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import JOBS_DIR

# In-process registries (volatile; lost on MCP server restart, but
# detached/window jobs survive via on-disk job files)
BG_JOB_TASKS: dict[str, asyncio.Task] = {}
BG_JOB_PROCS: dict[str, "asyncio.subprocess.Process"] = {}
RUNNING_CODEX_PROCS: dict[str, "asyncio.subprocess.Process"] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def load_job(job_id: str) -> dict | None:
    p = job_path(job_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_job(job: dict) -> None:
    try:
        job_path(job["job_id"]).write_text(
            json.dumps(job, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def update_job(job_id: str, **kwargs: Any) -> dict | None:
    job = load_job(job_id)
    if job is None:
        return None
    job.update(kwargs)
    write_job(job)
    return job
