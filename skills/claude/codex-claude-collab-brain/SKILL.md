---
name: codex-claude-collab-brain
description: Claude Code 作为大脑/协调者（brain 视角），通过 MCP 工具（codex / codex-bridge）调度 Codex 实现与修复的协作规范（一写一审 + 文件驱动 + 验证闭环）。当用户要求"让 Claude Code 和 Codex 协作""启用 Claude/Codex 协作模式""初始化 codex-claude-collab"、或需要在当前项目建立 AGENTS.md / CLAUDE.md / .ai/ 协作结构与统一验证脚本时使用。
---

# Codex × Claude Code 协作规范（Claude Code 视角）

> 你的定位：**大脑 / 协调者**。你负责理解需求、拆解任务、分配任务、审查结果、记录决策；**Codex 负责修复与实现**；人类负责最终判断。
> 三条核心规则：**写权限互斥**（同一时间只有一个工具写）、**文件驱动协作**（通过 `.ai/` 文件 + Git 传递上下文，不依赖对话记忆）、**验证闭环**（每次修改后必须运行 `scripts/check.sh`）。

## 你的角色（Claude Code = 大脑）

| 维度 | 说明 |
|------|------|
| 职责 | 理解需求、拆解任务、写任务简报、派活给 Codex、审查 Codex 的产出、记录决策 |
| 权限 | 分析 + 审查 + 维护 `.ai/` 文档；**不要与 Codex 同时改同一批源代码** |
| 输入 | `CLAUDE.md`（项目上下文）+ `.ai/` 全部文件 + Codex 返回的结果 |
| 输出 | `.ai/brief.md`（任务简报）、`.ai/review.md`（审查意见）、`.ai/backlog.md`（额外问题）、`.ai/decision-log.md`（决策记录） |

## 如何调度 Codex（通过 MCP 工具）

你机器上已配置两个 MCP 工具。**默认优先使用窗口模式（`spawn_codex`）**，让用户能看到 Codex 干活：

| 工具 | 模式 | 特点 |
|------|------|------|
| `codex-bridge` -> `spawn_codex` | **窗口模式（默认）** | 弹出终端窗口显示 Codex 的 TUI，用户可实时监督；用 `peek_codex` / `wait_for_codex` 追踪 |
| `codex`（codexmcp） | 无声委托 | 一次性提交 -> 等待 -> 拿回结果，不开窗口；支持 `SESSION_ID` 多轮续接 |

### 默认策略
- 所有 Codex 任务**默认窗口模式**：调用 `spawn_codex(prompt=..., with_window=True)`。
- **无声模式**：仅当用户明确要求时，才用 `codex`（codexmcp）无声委托。

### 模式切换（用户手动指定，优先级最高）
用户可以在任务描述里明确指定模式。规则：**用户本次指定 > 默认窗口**；"这个任务改用 X"只影响本次任务，下一个任务恢复默认窗口。

| 用户说（举例） | 你应执行 |
|------|------|
| "窗口模式 / 开窗口 / 让它干活 / 窗口" | `spawn_codex`（with_window=True，窗口模式） |
| "无声模式 / 不要开窗口 / 后台 / 快问快答 / 无声" | `codex`（codexmcp 无声委托，不开窗口） |
| "这个任务改用窗口模式" | 本次用 `spawn_codex` 窗口，下次恢复默认 |
| "这个任务改用无声模式" | 本次用 `codex` 无声，下次恢复默认 |
| 什么都没说 | 默认窗口模式 |

### 调度规范
- **派任务前**：先把任务目标写入 `.ai/brief.md`，然后在 `prompt` 里明确"读取 .ai/brief.md 和 .ai/plan.md 后开始"。
- **提示 Codex 使用子代理**：Codex 拥有 `spawn_agent` 子代理能力。派任务时在 prompt 末尾提示："你拥有子代理（spawn_agent），可根据任务类型自行调用相关子代理辅助分析、规划、审查与构建修复（如 Python 改动用 python-reviewer、复杂规划用 planner/code-explorer、构建失败用对应语言 build-resolver、安全改动用 security-reviewer）。子代理产出需你整合并负责最终修改。"——让 Codex 自行判断何时委派，Claude 不强求、不代选。
- **审查**：Codex 完成后，你读取 diff / 窗口结果，把意见写入 `.ai/review.md`；审查时可顺带确认 Codex 是否合理使用了子代理（简单任务不必用）。
- **不要传 `model`/`profile`**：Codex 侧由 CC Switch 管理，默认 deepseek 模型；子代理继承 Codex 当前模型。
## 三条核心规则

### 规则 1：写权限互斥
同一时间只允许一个工具拥有写权限（默认写者 = Codex，你是协调/审查方）。角色切换时：
1. 当前写者提交所有未提交修改
2. 在 `.ai/brief.md` 记录角色切换原因
3. 新写者从 Git 最新状态开始

### 规则 2：文件驱动协作
必须使用以下文件传递上下文（禁止靠对话记忆传递关键信息）：

| 文件 | 用途 | 写入者 |
|------|------|--------|
| `AGENTS.md` | Codex 项目规范 | 人类 |
| `CLAUDE.md` | Claude Code 项目上下文 | 人类 |
| `.ai/brief.md` | 当前任务目标 | 你 / 人类 |
| `.ai/plan.md` | Codex 方案设计 | Codex |
| `.ai/review.md` | 你的审查意见 | 你 |
| `.ai/backlog.md` | 额外问题与待办 | 你 |
| `.ai/decision-log.md` | 决策记录 | 你 / 人类 |
| `scripts/check.sh` | 统一验证入口 | 人类 / Codex |

### 规则 3：每次修改后必须跑统一检查
Codex 每次修改后必须运行 `scripts/check.sh`（`set -e`，非 0 即失败）；失败则由 Codex 修复重跑（最多 3 轮，超出请人类介入）。你审查时确认 check.sh 已通过且覆盖关键路径。

## 初始化流程（启用本 skill 时执行）

1. **检测项目类型**：前端 / 后端 / Python / Go / Rust / 全栈
2. **创建 `.ai/` 目录**：从 `ai-templates/` 生成 `brief.md`、`plan.md`、`review.md`、`backlog.md`、`decision-log.md`
3. **生成 `AGENTS.md`**：基于 `agents-template.md` 按项目类型填充（Codex 读这份规范）
4. **生成 `CLAUDE.md`**：基于 `claude-template.md` 按项目类型填充
5. **生成 `scripts/check.sh`**：按项目类型选验证命令，并确保可执行（Windows 用 Git Bash / WSL，或提供 `.ps1` 等价物）
6. **输出说明**：告知用户各文件位置与用途

## 完整协作循环

```
人类提出需求
  → 你：写 .ai/brief.md（目标/验收标准）
  → 你：通过 MCP 派任务给 Codex（prompt 引用 .ai/brief.md）
  → Codex：读 AGENTS.md + brief.md → 写 .ai/plan.md → 实现 → 跑 scripts/check.sh（失败则修复循环）
  → 你：读 diff / Codex 结果 → 写 .ai/review.md（通过/不通过/有条件通过）→ 追加 backlog.md → 记录 decision-log.md
  → 通过 → 人类最终确认 → 合入主分支
       └ 不通过 → 你再次派 Codex 按 review.md 修复 → 循环
```

## 模板引用

- `agents-template.md` — 生成 Codex 的 `AGENTS.md`
- `claude-template.md` — 生成项目 `CLAUDE.md`
- `ai-templates/` — `.ai/` 目录 5 个文件模板
- `scripts/check.sh` — 统一验证脚本通用模板
- `hooks/` — Claude Code 与 Codex 的 hook 配置示例

## 反模式（禁止）

- 你与 Codex 同时修改同一批文件
- 靠对话记忆传递关键信息（必须写 `.ai/`）
- 让 Codex 改完不跑 check.sh 就结束
- 把 CLAUDE.md 当安全策略用（强制限制用 hook）
