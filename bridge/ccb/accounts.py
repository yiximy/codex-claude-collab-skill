"""Multi-account rotation (gated; disabled by default).

Enable via `CCB_ENABLE_ROTATION=1` env or `enable_multi_account: true` in
~/.ai-bridge/config.json. When disabled, codex spawns use the system-default
auth (~/.codex/auth.json) with no rotation logic in the hot path.

Account state machine:
  active           — eligible
  quota_exhausted  — temporary block (TTL or `try again in X` from server)
  banned           — temporary block (TTL)
  auth_invalid     — needs human re-login; permanently skipped
  dead             — needs human intervention; permanently skipped

Per-account home: ~/.ai-bridge/accounts/<name>/
   auth.json (codex CLI auth)
   sessions/ -> ~/.ai-bridge/shared-sessions/ (junction, so all accounts share session corpus)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config
from .jobs import now_iso
from .paths import (
    ACCOUNTS_DIR,
    ACCOUNTS_FILE,
    IS_WINDOWS,
    SHARED_SESSIONS_DIR,
)


def is_rotation_enabled() -> bool:
    return bool(config.get("enable_multi_account"))


def load_accounts() -> dict:
    if not ACCOUNTS_FILE.exists():
        return {"rotation": [], "current": None, "states": {}}
    try:
        data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        data.setdefault("rotation", [])
        data.setdefault("states", {})
        data.setdefault("current", None)
        return data
    except Exception:
        return {"rotation": [], "current": None, "states": {}}


def save_accounts(data: dict) -> None:
    try:
        ACCOUNTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def account_home(name: str) -> Path:
    return ACCOUNTS_DIR / name


def mark_account(
    name: str,
    status: str,
    ttl_hours: int | None = None,
    blocked_until: datetime | None = None,
) -> None:
    data = load_accounts()
    state = data["states"].setdefault(name, {})
    state["status"] = status
    state["updated_at"] = now_iso()
    if status == "active":
        state["last_ok"] = now_iso()
        state.pop("blocked_until", None)
    elif status in ("quota_exhausted", "banned"):
        if blocked_until is not None:
            state["blocked_until"] = blocked_until.isoformat()
        elif ttl_hours is not None:
            state["blocked_until"] = (
                datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
            ).isoformat()
        if status == "banned":
            state["ban_count"] = int(state.get("ban_count", 0)) + 1
    save_accounts(data)


def is_account_eligible(state: dict) -> bool:
    status = state.get("status", "active")
    if status in ("dead", "auth_invalid"):
        return False
    if status in ("banned", "quota_exhausted"):
        blocked_until = state.get("blocked_until")
        if not blocked_until:
            return True
        try:
            if datetime.fromisoformat(blocked_until) > datetime.now(timezone.utc):
                return False
        except Exception:
            return True
    return True


def pick_next_eligible_account(tried: set[str]) -> str | None:
    data = load_accounts()
    rotation = data.get("rotation", [])
    if not rotation:
        return None
    current = data.get("current")
    start = rotation.index(current) if current in rotation else 0
    order = rotation[start:] + rotation[:start]
    for name in order:
        if name in tried:
            continue
        state = data["states"].get(name, {})
        if is_account_eligible(state):
            return name
    return None


def is_junction_or_link(p: Path) -> bool:
    if not p.exists():
        return False
    try:
        if p.is_symlink():
            return True
        if IS_WINDOWS:
            import stat  # noqa: F401
            st = os.lstat(str(p))
            if st.st_file_attributes & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
                return True
    except Exception:
        pass
    return False


def ensure_account_home(name: str) -> tuple[bool, str]:
    """Create accounts/<name>/ + sessions junction → shared-sessions/."""
    home = account_home(name)
    home.mkdir(parents=True, exist_ok=True)
    sessions_link = home / "sessions"

    if is_junction_or_link(sessions_link):
        return True, ""

    if sessions_link.exists() and sessions_link.is_dir():
        bak = home / f"sessions.bak.{int(datetime.now().timestamp())}"
        try:
            sessions_link.rename(bak)
        except Exception as e:
            return False, f"rename existing sessions dir failed: {e}"

    if not IS_WINDOWS:
        try:
            sessions_link.symlink_to(SHARED_SESSIONS_DIR, target_is_directory=True)
            return True, ""
        except Exception as e:
            return False, f"symlink failed: {type(e).__name__}: {e}"

    try:
        r = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(sessions_link), str(SHARED_SESSIONS_DIR)],
            capture_output=True,
            check=False,
        )
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", errors="replace")[:200]
            return False, f"mklink rc={r.returncode}: {err}"
        return True, ""
    except Exception as e:
        return False, f"mklink error: {type(e).__name__}: {e}"


def activate_account(name: str) -> tuple[bool, str]:
    """Verify auth.json exists; ensure sessions junction; set current."""
    if not (account_home(name) / "auth.json").exists():
        return (
            False,
            f"auth.json not found in {account_home(name)}. "
            f"Run `CODEX_HOME={account_home(name)} codex login` first.",
        )
    ok, err = ensure_account_home(name)
    if not ok:
        return False, err
    try:
        data = load_accounts()
        data["current"] = name
        save_accounts(data)
        return True, ""
    except Exception as e:
        return False, f"update current failed: {type(e).__name__}: {e}"


def valid_account_name(name: str) -> bool:
    return bool(name and re.match(r"^[A-Za-z0-9_\-.]+$", name))
