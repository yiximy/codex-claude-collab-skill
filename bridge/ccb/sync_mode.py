"""Synchronous `codex exec --json` helper.

No MCP tools are registered here — these are pure helpers called by
window_mode._spawn_sync_impl when the unified spawn_codex is invoked with
`wait=True, with_window=False`.
"""
from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path

from . import accounts, config
from .classify import classify_codex_result, parse_quota_reset
from .cli import codex_safe_cwd, resolve_cli, resolve_node_cli
from .spawn import (
    codex_pinned_flags,
    extract_session_id_from_jsonl,
    run_subprocess,
)

DEFAULT_TIMEOUT = 30 * 60
_AUTH_SWAP_LOCK = asyncio.Lock()


async def _spawn_codex_once(
    prompt: str,
    session_id: str | None,
    timeout_sec: int,
    codex_prefix: list[str],
    account: str | None = None,
) -> dict:
    """Sync exec: returns {rc, stdout, stderr, result, session_id}."""
    with tempfile.TemporaryDirectory(prefix="codex_out_") as tmp:
        out_file = Path(tmp) / "last.md"
        out_file.touch()
        flags = [
            *codex_pinned_flags(),
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--output-last-message", str(out_file),
        ]
        if session_id:
            cmd = [*codex_prefix, "exec", "resume", session_id, *flags, prompt]
        else:
            cmd = [*codex_prefix, "exec", "--cd", codex_safe_cwd(), *flags, prompt]
        extra_env: dict = {}
        if account:
            extra_env["CODEX_HOME"] = str(accounts.account_home(account))
        rc, stdout, stderr = await run_subprocess(
            cmd, timeout_sec, f"codex_{uuid.uuid4().hex[:8]}", extra_env=extra_env
        )
        try:
            result_text = out_file.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            result_text = ""
        if not result_text and stdout:
            result_text = stdout.strip()
        sid = ""
        try:
            for line in stdout.splitlines():
                sid = extract_session_id_from_jsonl(line) or ""
                if sid:
                    break
        except Exception:
            pass
        return {"rc": rc, "stdout": stdout, "stderr": stderr,
                "result": result_text, "session_id": sid}


async def _rotate_and_spawn(
    prompt: str,
    session_id: str | None,
    timeout_sec: int,
    spawner,
    account: str | None,
    auto_rotate: bool,
) -> dict:
    """Shared rotation loop for sync exec."""
    codex_prefix = resolve_node_cli("codex") or (
        [resolve_cli("codex")] if resolve_cli("codex") else None
    )
    if not codex_prefix:
        return {"error": "[FAIL] codex not in PATH. `npm i -g @openai/codex` first."}

    rotation_on = accounts.is_rotation_enabled()
    tried: set[str] = set()

    while True:
        chosen: str | None = None
        if account:
            chosen = account
        elif rotation_on:
            chosen = accounts.pick_next_eligible_account(tried)
            if not chosen:
                return {"error": "[FAIL] all accounts exhausted/banned",
                        "tried_accounts": sorted(tried)}

        async with _AUTH_SWAP_LOCK:
            if chosen:
                ok, err = accounts.activate_account(chosen)
                if not ok:
                    tried.add(chosen)
                    if account or not auto_rotate:
                        return {"error": f"[FAIL] activate {chosen}: {err}",
                                "account_used": chosen}
                    continue
            res = await spawner(prompt, session_id, timeout_sec, codex_prefix, account=chosen)

        combined = (res.get("stderr") or "") + "\n" + (res.get("stdout") or "")
        classification = classify_codex_result(res["rc"], combined, res["result"])

        if chosen:
            if classification == "ok":
                accounts.mark_account(chosen, "active")
            elif classification == "quota_exhausted":
                reset_at = parse_quota_reset(combined + "\n" + (res.get("result") or ""))
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

        if res["rc"] != 0:
            return {
                "session_id": res["session_id"],
                "error": f"[FAIL] codex rc={res['rc']} ({classification})\n"
                         f"stderr: {res['stderr'][-1500:]}\noutput: {res['result'][-500:]}",
                "account_used": chosen,
                "tried_accounts": sorted(tried),
            }
        out = {
            "session_id": res["session_id"],
            "output": res["result"] or "[WARN] codex exited OK but produced no output",
        }
        if chosen:
            out["account_used"] = chosen
        return out
