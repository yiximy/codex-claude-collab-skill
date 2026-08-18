# claude-codex-bridge

> 在 Claude Code 里把任务派给 Codex 和 Gemini —— codex 在它自己的终端窗口里以真实交互 session 运行。

<sub>[English](README.md)</sub>

默认工具开一个终端 tab，跑 `codex --yolo "<prompt>"`，TTY 是真的。你看到的是 codex 自己的 TUI —— apply_patch 块、inline diff、命令输出、reasoning summary。并行 spawn 共享同一个终端窗口的 tab。Claude 追踪进度和完成状态不需要碰 codex 进程。

## 快速开始

```bash
npm i -g @openai/codex                  # 或 @google/gemini-cli，或都装
git clone https://github.com/uuz495/claude-codex-bridge
```

注册到 Claude Code —— 在 `~/.claude.json` 的 `mcpServers` 下加：

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

重启 Claude Code。8 个工具以 `mcp__claude-codex-bridge__` 前缀注册进来（多账号开启后 9 个）。

## 示例

跟 Claude 说：

> 用 `spawn_codex` 让 codex 写一个二分 Fibonacci 到 `fib.py` 然后跑 10 个测试。

Claude 瞬间拿到 `job_id`，一个终端 tab 弹出来跑 codex TUI。`peek_codex(job_id)` 返回活动快照，`wait_for_codex(job_id)` 阻塞等 codex 完成。

## 工具

| 工具 | 用途 |
|---|---|
| `spawn_codex(prompt, wait=False, with_window=True, session_id, account, timeout_sec)` | 跑 codex。**默认**开终端 tab + 立刻返回 `job_id`。`wait=True, with_window=False` 短阻塞调用返回 final message 文本。`wait=True, with_window=True` 开窗口 + 阻塞。 |
| `peek_codex(job_id)` | 活动快照 —— tool call、文件改动、agent message、完成状态、final_message。 |
| `wait_for_codex(job_id, timeout_sec)` | 阻塞等 codex 完成，或超时。 |
| `cancel_codex_job(job_id)` | 标记 job 为 cancelled。Window 模式 codex 需要关终端窗口才真停 —— bridge 不持进程 handle。 |
| `list_codex_jobs(limit=20)` | 最近 jobs，最新在前。 |
| `spawn_gemini(prompt)` | 一次性 Gemini CLI 调用。 |
| `spawn_parallel(tasks)` | 并发跑 N 个 codex/gemini 任务。 |
| `list_logs(n)` | 最近 N 条子进程日志路径。 |

<details>
<summary><b>多账号轮换</b> —— 可选，默认关</summary>

设 `CCB_ENABLE_ROTATION=1` 才暴露 `manage_codex_accounts(action, ...)`：

| Action | 用途 |
|---|---|
| `list` | 轮换次序 + 每个账号状态。 |
| `get_login_cmd(name)` | 返回 `CODEX_HOME=... codex login` 命令串。 |
| `add(name)` | 注册 `name`（要求先 pre-login 到它的 `CODEX_HOME`）。 |
| `reset(name, status)` | 设状态：`active` / `quota_exhausted` / `banned` / `auth_invalid` / `dead`。 |
| `probe(timeout_sec=45)` | 每个账号跑一次 trivial codex 探活。 |
| `remove(name)` | 从轮换里删除。 |

传 `account=<name>` 给 `spawn_codex` 指定账号，否则用当前 `CODEX_HOME`。

> 用多个 ChatGPT Plus/Pro 账号绕速率限制可能违反 OpenAI 服务条款。启用前查清楚。

</details>

## 推荐用法

**`/loop` 自动轮询** —— `/loop 10m peek codex job j-xxxxxxxxxx and tell me what changed`。Claude 每 10 分钟唤醒一次报进度。

**阻塞等完成** —— `用这个 HANDOFF spawn codex，然后 wait_for_codex timeout 5400s，等返回后 review final message`。

**自验证** —— 让 codex 在 final message 末尾加 `STATUS: PASS` 或 `STATUS: FAIL: <reason>`。`peek_codex(...)["final_message"]` 直接拿这一行。

**串接多步** —— 把上一步的 `session_id` 传给下一个 `spawn_codex`，继承推理 + 工具调用历史。

## 配置

通过环境变量或 `~/.ai-bridge/config.json` 覆盖。优先级：env > config 文件 > 默认。

| 设置 | 环境变量 | 默认 |
|---|---|---|
| Codex 模型 | `CCB_CODEX_MODEL` | `gpt-5.5` |
| 跟随 Codex 配置（CC Switch） | `CCB_FOLLOW_CODEX_CONFIG` | `1` |
| 推理强度 | `CCB_REASONING_EFFORT` | `high` |
| 推理摘要 | `CCB_REASONING_SUMMARY` | `auto` |
| Fast mode | `CCB_FAST_MODE` | `1` |
| 默认超时（秒） | `CCB_DEFAULT_TIMEOUT` | `1800` |
| 多账号 | `CCB_ENABLE_ROTATION` | `0` |
| Summary tail | `CCB_SUMMARY_TAIL` | `1` |
| Quota TTL（小时） | `CCB_QUOTA_TTL_HOURS` | `5` |
| Ban TTL（小时） | `CCB_BAN_TTL_HOURS` | `24` |
| 非 ASCII cwd 重映射 | `CCB_CWD_REMAPS` | `""` |

## Caveats

- **目前只在 Windows 上跑过**。Linux/macOS 代码分支存在但没端到端测过。
- **终端窗口在 `task_complete` 后不会自动关** —— codex 停在等下一条用户输入的状态。手动关。
- **`cancel_codex_job` 不会杀 detached codex** —— 关终端窗口才真停。
- **非 ASCII 工作目录**会触发 codex CLI bug。用 `CCB_CWD_REMAPS` 映射到 ASCII junction（Windows：`mklink /J C:\ascii-alias D:\real-path`）。
- **`wait=True, with_window=False`** 走 `codex exec --json` 继承它的局限。非平凡任务用默认 window 模式。

## 开发

```bash
git clone https://github.com/uuz495/claude-codex-bridge
cd claude-codex-bridge
pip install -e .[dev]
ruff check .
```

## License

MIT —— 见 [LICENSE](LICENSE)。

默认开启时，Codex 模型自动跟随 `~/.codex/config.toml` 顶层的 `model` 字段（该文件由 CC Switch / Codex Desktop 在切换 provider 时重写）。因此在 CC Switch 中切换 Codex 默认模型（如 DeepSeek V4 Pro / DeepSeek V4 Flash）即可生效，无需再手动修改 `~/.ai-bridge/config.json` 的 `codex_model`。当 `~/.codex/config.toml` 中没有 `model`，或通过 `CCB_FOLLOW_CODEX_CONFIG=0` 关闭跟随时，回退到 `CCB_CODEX_MODEL` 环境变量 > `~/.ai-bridge/config.json` 的 `codex_model` > 内置默认。
