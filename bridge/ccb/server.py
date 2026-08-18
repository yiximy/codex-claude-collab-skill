"""claude-codex-bridge MCP server entry point.

Usage:
    python -m ccb.server
or via the installed entry point:
    claude-codex-bridge

Public tool surface (9 tools, 10 with multi-account rotation):
    spawn_codex / peek_codex / wait_for_codex     ← codex spawn + tracking
    cancel_codex_job / list_codex_jobs            ← job management
    spawn_gemini / spawn_parallel                 ← gemini + cross-provider
    list_logs                                     ← utility
    manage_codex_accounts                         ← multi-account (gated)
"""
from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from . import (
    account_tools,
    accounts,
    gemini,
    jobs_tools,
    util_tools,
    window_mode,
)


def build_server() -> FastMCP:
    mcp = FastMCP("claude-codex-bridge")

    # Always-on tools
    window_mode.register(mcp)
    jobs_tools.register(mcp)
    gemini.register(mcp)
    util_tools.register(mcp)

    # Multi-account tools: gated by config
    if accounts.is_rotation_enabled():
        account_tools.register(mcp)
        print(
            "[ccb] multi-account rotation: ENABLED "
            "(disable: unset CCB_ENABLE_ROTATION or set enable_multi_account=false)",
            file=sys.stderr,
        )
    else:
        print(
            "[ccb] multi-account rotation: disabled (default). "
            "Enable: CCB_ENABLE_ROTATION=1",
            file=sys.stderr,
        )

    return mcp


def main() -> None:
    mcp = build_server()
    mcp.run()


if __name__ == "__main__":
    main()
