"""Single MCP tool for multi-account management.

Only registered when CCB_ENABLE_ROTATION=1 (env) or enable_multi_account=true
(config.json). When rotation is disabled, codex spawns use the system-default
auth from ~/.codex/auth.json with no rotation logic.
"""
from __future__ import annotations

import asyncio
import shutil

from . import accounts
from .cli import resolve_cli, resolve_node_cli
from .paths import CODEX_AUTH_PATH
from .spawn import run_subprocess


_VALID_STATUSES = ("active", "quota_exhausted", "banned", "auth_invalid", "dead")


async def _action_list() -> dict:
    return accounts.load_accounts()


async def _action_get_login_cmd(name: str) -> dict:
    if not accounts.valid_account_name(name):
        return {"error": f"[FAIL] invalid name '{name}'"}
    home = accounts.account_home(name)
    return {
        "account": name,
        "powershell": f'$env:CODEX_HOME = "{home}"; codex login',
        "bash": f"CODEX_HOME='{home}' codex login",
        "home": str(home),
    }


async def _action_add(name: str, overwrite: bool = False) -> dict:
    if not accounts.valid_account_name(name):
        return {"error": f"[FAIL] invalid account name '{name}' (allow A-Za-z0-9_.-)"}
    home = accounts.account_home(name)
    target_auth = home / "auth.json"
    home.mkdir(parents=True, exist_ok=True)

    auth_source = "missing"
    if target_auth.exists() and not overwrite:
        auth_source = "existing"
    elif CODEX_AUTH_PATH.exists():
        try:
            shutil.copy2(CODEX_AUTH_PATH, target_auth)
            auth_source = "copied_from_global"
        except Exception as e:
            return {"error": f"[FAIL] copy legacy auth failed: {type(e).__name__}: {e}"}
    else:
        return {
            "error": (
                f"[FAIL] no auth found for '{name}'. Run first:\n"
                f"  PowerShell: $env:CODEX_HOME = \"{home}\"; codex login\n"
                f"  Bash:       CODEX_HOME='{home}' codex login\n"
                f"then call manage_codex_accounts(action='add', name='{name}') again."
            )
        }

    ok, err = accounts.ensure_account_home(name)
    if not ok:
        return {"error": f"[FAIL] sessions junction: {err}", "auth_source": auth_source}

    data = accounts.load_accounts()
    if name not in data["rotation"]:
        data["rotation"].append(name)
    data["states"].setdefault(name, {})["status"] = "active"
    if hasattr(accounts, "now_iso"):
        data["states"][name]["updated_at"] = accounts.now_iso()
    data["states"][name].pop("blocked_until", None)
    if data.get("current") is None:
        data["current"] = name
    accounts.save_accounts(data)

    return {
        "account": name,
        "saved": True,
        "auth_source": auth_source,
        "rotation": data["rotation"],
        "current": data["current"],
        "home": str(home),
    }


async def _action_reset(name: str, status: str = "active") -> dict:
    if not accounts.valid_account_name(name):
        return {"error": f"[FAIL] invalid name '{name}'"}
    if status not in _VALID_STATUSES:
        return {"error": f"[FAIL] unknown status '{status}'; pick one of {_VALID_STATUSES}"}
    accounts.mark_account(name, status)
    return {"account": name, "status": status}


async def _action_probe(timeout_sec: int = 45) -> dict:
    codex_prefix = resolve_node_cli("codex") or (
        [resolve_cli("codex")] if resolve_cli("codex") else None
    )
    if not codex_prefix:
        return {"error": "[FAIL] codex not in PATH"}

    data = accounts.load_accounts()
    results: dict[str, dict] = {}

    async def _probe(name: str) -> tuple[str, dict]:
        ok, err = accounts.activate_account(name)
        if not ok:
            return name, {"activate_failed": err}
        env = {"CODEX_HOME": str(accounts.account_home(name))}
        cmd = [*codex_prefix, "exec", "--skip-git-repo-check",
               "--dangerously-bypass-approvals-and-sandbox",
               "Reply with just OK."]
        rc, stdout, stderr = await run_subprocess(
            cmd, timeout_sec, f"probe_{name}", extra_env=env
        )
        return name, {"rc": rc, "stdout_tail": stdout[-400:], "stderr_tail": stderr[-400:]}

    rotation = data.get("rotation") or []
    outs = await asyncio.gather(*[_probe(n) for n in rotation])
    for name, info in outs:
        results[name] = info
    return results


async def _action_remove(name: str, delete_files: bool = False) -> dict:
    if not accounts.valid_account_name(name):
        return {"error": f"[FAIL] invalid name '{name}'"}
    data = accounts.load_accounts()
    if name in data["rotation"]:
        data["rotation"].remove(name)
    data["states"].pop(name, None)
    if data.get("current") == name:
        data["current"] = data["rotation"][0] if data["rotation"] else None
    accounts.save_accounts(data)

    if delete_files:
        home = accounts.account_home(name)
        if home.exists():
            try:
                shutil.rmtree(home, ignore_errors=True)
            except Exception as e:
                return {"removed": True, "files_deleted": False, "error": str(e)}
    return {
        "removed": True,
        "rotation": data["rotation"],
        "current": data["current"],
        "files_deleted": delete_files,
    }


def register(mcp) -> None:
    @mcp.tool()
    async def manage_codex_accounts(
        action: str,
        name: str | None = None,
        status: str | None = None,
        overwrite: bool = False,
        delete_files: bool = False,
        timeout_sec: int = 45,
    ) -> dict:
        """Multi-account management. Pick `action`:

        - `list`           — return rotation order + per-account state. No
                             other args needed.
        - `get_login_cmd`  — return the `CODEX_HOME=... codex login` shell
                             commands for `name`.
        - `add`            — register `name`. Requires that you have already
                             pre-logged-in to its CODEX_HOME (see the
                             `get_login_cmd` output), OR have a legacy
                             `~/.codex/auth.json` to copy. `overwrite=True`
                             replaces an existing `auth.json`.
        - `reset`          — set `name`'s `status` (active / quota_exhausted /
                             banned / auth_invalid / dead). Use after fixing
                             a deactivated workspace.
        - `probe`          — run a trivial `codex exec` per account to detect
                             quota / ban / auth state. `timeout_sec` per
                             account.
        - `remove`         — drop `name` from rotation. `delete_files=True`
                             also wipes accounts/<name>/.
        """
        action = (action or "").lower().strip()
        if action == "list":
            return await _action_list()
        if action == "get_login_cmd":
            if not name:
                return {"error": "[FAIL] action='get_login_cmd' requires name"}
            return await _action_get_login_cmd(name)
        if action == "add":
            if not name:
                return {"error": "[FAIL] action='add' requires name"}
            return await _action_add(name, overwrite=overwrite)
        if action == "reset":
            if not name:
                return {"error": "[FAIL] action='reset' requires name"}
            return await _action_reset(name, status=status or "active")
        if action == "probe":
            return await _action_probe(timeout_sec=timeout_sec)
        if action == "remove":
            if not name:
                return {"error": "[FAIL] action='remove' requires name"}
            return await _action_remove(name, delete_files=delete_files)
        return {
            "error": (
                f"[FAIL] unknown action '{action}'. Valid: list / "
                "get_login_cmd / add / reset / probe / remove."
            )
        }
