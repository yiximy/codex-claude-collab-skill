# claude-codex-bridge

> Spawn Codex (and Gemini) from Claude Code as real interactive sessions in their own terminal windows.

<sub>[简体中文](README.zh-CN.md)</sub>

The default tool opens a terminal tab and runs `codex --yolo "<prompt>"` with a real TTY. What you see is codex's native TUI — apply_patch blocks, inline diffs, command output, reasoning summaries. Parallel spawns share one terminal window via tabs. Claude tracks progress and completion without touching the codex process.

## Quick start

```bash
npm i -g @openai/codex                  # or @google/gemini-cli, or both
git clone https://github.com/uuz495/claude-codex-bridge
```

Register with Claude Code — add to `~/.claude.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "claude-codex-bridge": {
      "command": "python",
      "args": ["/path/to/claude-codex-bridge/run.py"]
    }
  }
}
```

Restart Claude Code. 8 tools appear under the `mcp__claude-codex-bridge__` prefix (9 with multi-account rotation enabled).

## Example

Ask Claude:

> Use `spawn_codex` to have Codex write a binary-search Fibonacci to `fib.py` and run 10 tests.

Claude gets a `job_id` back immediately and a terminal tab opens running codex's TUI. `peek_codex(job_id)` returns an activity snapshot; `wait_for_codex(job_id)` blocks until codex finishes.

## Tools

| Tool | Purpose |
|---|---|
| `spawn_codex(prompt, wait=False, with_window=True, session_id, account, timeout_sec)` | Run codex. **Default** opens a terminal tab and returns `job_id` immediately. `wait=True, with_window=False` for a short blocking call that returns the final message text. `wait=True, with_window=True` opens a window and blocks. |
| `peek_codex(job_id)` | Activity snapshot — tool calls, file changes, agent messages, completion, final_message. |
| `wait_for_codex(job_id, timeout_sec)` | Block until codex completes, or timeout. |
| `cancel_codex_job(job_id)` | Mark a job cancelled. Window-mode codex must be stopped by closing the terminal — the bridge holds no process handle. |
| `list_codex_jobs(limit=20)` | Recent jobs newest-first. |
| `spawn_gemini(prompt)` | One-shot Gemini CLI call. |
| `spawn_parallel(tasks)` | Run N codex/gemini tasks concurrently. |
| `list_logs(n)` | Recent subprocess log paths. |

<details>
<summary><b>Multi-account rotation</b> — optional, off by default</summary>

Set `CCB_ENABLE_ROTATION=1` to expose `manage_codex_accounts(action, ...)`:

| Action | Purpose |
|---|---|
| `list` | Rotation order + per-account state. |
| `get_login_cmd(name)` | Returns the `CODEX_HOME=... codex login` shell command for `name`. |
| `add(name)` | Register `name` (pre-login to its `CODEX_HOME` first). |
| `reset(name, status)` | Set status: `active` / `quota_exhausted` / `banned` / `auth_invalid` / `dead`. |
| `probe(timeout_sec=45)` | Run a trivial codex call per account to detect quota / ban state. |
| `remove(name)` | Drop from rotation. |

Pass `account=<name>` to `spawn_codex` to pick a specific account; otherwise the current `CODEX_HOME` is used.

> Using multiple ChatGPT Plus/Pro accounts to extend rate limits may violate OpenAI's Terms of Service. Check your provider's terms before enabling.

</details>

## Recommended patterns

**Auto-poll with `/loop`** — `/loop 10m peek codex job j-xxxxxxxxxx and tell me what changed`. Claude wakes every 10 minutes and reports progress.

**Block until done** — `Spawn codex with this HANDOFF, then wait_for_codex with timeout 5400s. When it returns, review the final message.`

**Self-verify** — Ask codex to end with `STATUS: PASS` or `STATUS: FAIL: <reason>`. `peek_codex(...)["final_message"]` carries that line.

**Chain phases** — Pass the previous `session_id` into the next `spawn_codex` to inherit reasoning + tool history.

## Configuration

Override via env var or `~/.ai-bridge/config.json`. Env wins over file wins over default.

| Setting | Env var | Default |
|---|---|---|
| Codex model | `CCB_CODEX_MODEL` | `gpt-5.5` |
| Follow Codex config (CC Switch) | `CCB_FOLLOW_CODEX_CONFIG` | `1` |
| Reasoning effort | `CCB_REASONING_EFFORT` | `high` |
| Reasoning summary | `CCB_REASONING_SUMMARY` | `auto` |
| Fast mode | `CCB_FAST_MODE` | `1` |
| Default timeout (s) | `CCB_DEFAULT_TIMEOUT` | `1800` |
| Multi-account | `CCB_ENABLE_ROTATION` | `0` |
| Summary tail | `CCB_SUMMARY_TAIL` | `1` |
| Quota TTL (h) | `CCB_QUOTA_TTL_HOURS` | `5` |
| Ban TTL (h) | `CCB_BAN_TTL_HOURS` | `24` |
| Non-ASCII cwd remap | `CCB_CWD_REMAPS` | `""` |

## Caveats

- **Windows-only at the moment.** Linux/macOS code paths exist but have not been tested end-to-end.
- **The terminal window stays open after `task_complete`** — codex sits at the next-input prompt. Close it manually.
- **`cancel_codex_job` does not kill detached codex** — close the terminal window to actually stop it.
- **Non-ASCII working directory** triggers a codex CLI bug. Use `CCB_CWD_REMAPS` to map to an ASCII junction (Windows: `mklink /J C:\ascii-alias D:\real-path`).
- **`wait=True, with_window=False`** uses `codex exec --json` and inherits its quirks. Prefer the default window mode for non-trivial work.

## Development

```bash
git clone https://github.com/uuz495/claude-codex-bridge
cd claude-codex-bridge
pip install -e .[dev]
ruff check .
```

## License

MIT — see [LICENSE](LICENSE).

When enabled (default), the Codex model automatically follows the top-level `model` in `~/.codex/config.toml` (rewritten by CC Switch / Codex Desktop on every provider switch), so switching the default model in CC Switch (e.g. DeepSeek V4 Pro / DeepSeek V4 Flash) takes effect without manually editing `codex_model` in `~/.ai-bridge/config.json`. When the TOML has no `model`, or following is disabled via `CCB_FOLLOW_CODEX_CONFIG=0`, the bridge falls back to env `CCB_CODEX_MODEL` > `~/.ai-bridge/config.json` `codex_model` > built-in default.
