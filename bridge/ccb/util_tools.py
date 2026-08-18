"""Misc utility tools: log listing, etc."""
from __future__ import annotations

from .paths import LOG_DIR


def register(mcp) -> None:
    @mcp.tool()
    async def list_logs(n: int = 10) -> list[str]:
        """List N most-recent subprocess log files."""
        try:
            logs = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            return [str(p) for p in logs[:n]]
        except Exception as e:
            return [f"[FAIL] {type(e).__name__}: {e}"]
