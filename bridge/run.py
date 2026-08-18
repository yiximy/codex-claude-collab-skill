#!/usr/bin/env python
"""Standalone entry point for running the MCP server without pip install.

Use this when registering with Claude Code if you'd rather skip
`pip install claude-codex-bridge`:

    "claude-codex-bridge": {
      "command": "python",
      "args": ["/path/to/claude-codex-bridge/run.py"]
    }
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ccb.server import main  # noqa: E402

if __name__ == "__main__":
    main()
