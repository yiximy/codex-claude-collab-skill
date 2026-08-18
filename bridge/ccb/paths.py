"""Filesystem paths for ai-bridge state directory."""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path.home() / ".ai-bridge"
LOG_DIR = BASE_DIR / "logs"
STREAM_DIR = BASE_DIR / "streams"
RUNNING_DIR = BASE_DIR / "running"
JOBS_DIR = BASE_DIR / "jobs"
ACCOUNTS_DIR = BASE_DIR / "accounts"
SHARED_SESSIONS_DIR = BASE_DIR / "shared-sessions"
ACCOUNTS_FILE = BASE_DIR / "accounts.json"
CONFIG_FILE = BASE_DIR / "config.json"
CODEX_AUTH_PATH = Path.home() / ".codex" / "auth.json"

IS_WINDOWS = sys.platform == "win32"


def ensure_dirs() -> None:
    """Create state directories on first run."""
    for d in (LOG_DIR, STREAM_DIR, RUNNING_DIR, JOBS_DIR, ACCOUNTS_DIR, SHARED_SESSIONS_DIR):
        d.mkdir(parents=True, exist_ok=True)


ensure_dirs()
