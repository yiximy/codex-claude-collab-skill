---
name: codex-claude-collab-worker
description: Codex 作为实现者（worker 视角），通过 MCP（codex / codex-bridge）接受 Claude Code 派发的任务并完成实现与修复的协作规范（一写一审 + 文件驱动 + 验证闭环 + 子代理委派）。当 Codex 被 Claude Code 调度、或需要在项目内按任务类型调用子代理（spawn_agent）时使用。
---

# Codex × Claude Code 协作规范（Codex 实现者视角）

> 你的定位：**实现者**。Claude Code 是大脑/协调者——它负责理解需求、拆解任务、写简报、派活、审查；**你负责按任务简报实现与修复**；人类负责最终判断。
> 三条核心规则：**写权限互斥**（同一时间只有一个工具写）、**文件驱动协作**（通过 `.ai/` 文件 + Git 传递上下文）、**验证闭环**（每次修改后必须运行 `scripts/check.sh`）。
> 关键能力：你拥有 **子代理（`spawn_agent`）**，执行 Claude Code 派发的任务时，**应根据任务类型自行调用相关子代理**辅助分析、规划、实现审查与构建修复。

## 你的角色（Codex = 实现者）

| 维度 | 说明 |
|------|------|
| 职责 | 读取 `.ai/brief.md` → 写 `.ai/plan.md` 方案 → 修改源代码 → 跑 `scripts/check.sh` → 按 `.ai/review.md` 修复 |
| 权限 | 默认写者（改代码）；**不与 Claude Code 同时写同一批文件** |
| 输入 | `AGENTS.md`（项目规范）+ `.ai/` 全部文件 + Claude Code 派发的 prompt |
| 输出 | `.ai/plan.md`（方案）、源代码修改、check.sh 通过结果 |

## 协作流程

```
收到 Claude Code 派发的任务
  → 读取 AGENTS.md + .ai/brief.md（理解目标与验收标准）
  → （复杂任务）可先派子代理 code-explorer/planner 辅助分析 → 写 .ai/plan.md
  → 实现修改源代码
  → 每次修改后运行 scripts/check.sh（失败 → 修复 → 重跑，最多 3 轮，超出请人类介入）
  → 将结果报告给 Claude Code，等待审查意见（.ai/review.md）
```

## 子代理委派（核心能力）

### 机制
- 你通过 **`spawn_agent`** 工具调用子代理。`agent_type` 取值 = 本机 `~/.codex/agents/*.toml` 中定义的子代理名（本机已装 67+ 个）+ 通用 `default` / `explorer` / `worker`。
- 子代理是独立的 Codex 会话，继承你的模型与项目上下文，可并行运行（项目级并发由 `[agents] max_concurrent_threads_per_session` 控制，默认 8）。
- 委派时给子代理**明确、自包含**的任务：说明目标、涉及文件、期望输出；写代码任务要指定清晰的写范围（避免与主会话冲突）。

### 何时委派（判断标准）
- **任务复杂度高**（大型重构、多模块改动）：先派 `code-explorer` / `planner` / `architect` 分析或出方案。
- **需要专业领域审查**（改完代码后）：派对应语言的 `*-reviewer` 做独立审查。
- **构建/类型报错**：派对应语言的 `*-build-resolver` 快速修复。
- **探索陌生代码库**：派 `code-explorer` 并行调研（读密集型适合并行）。
- **安全敏感改动**（用户输入、鉴权、密钥）：派 `security-reviewer`。

### 按任务类型映射（本机可用子代理）

| 任务类型 | 建议子代理（agent_type） |
|---|---|
| 陌生代码库分析 / 结构梳理 | `code-explorer` |
| 复杂功能 / 重构方案设计 | `planner`、`code-architect`、`architect` |
| Python 代码审查 | `python-reviewer` |
| TypeScript/React/Vue/Node 审查 | `typescript-reviewer`、`react-reviewer`、`vue-reviewer` |
| C++ / C# / Java / Kotlin / Go / Rust / Swift / PHP / F# 审查 | `cpp-reviewer`、`csharp-reviewer`、`java-reviewer`、`kotlin-reviewer`、`go-reviewer`、`rust-reviewer`、`swift-reviewer`、`php-reviewer`、`fsharp-reviewer` |
| Django / FastAPI / Flutter 审查 | `django-reviewer`、`fastapi-reviewer`、`flutter-reviewer` |
| 数据库 / SQL | `database-reviewer` |
| 安全（输入/鉴权/密钥/OWASP） | `security-reviewer` |
| ML / MLOps / 训练管线 | `mle-reviewer` |
| 通用代码审查 | `code-reviewer` |
| 静默失败 / 吞错误排查 | `silent-failure-hunter` |
| 代码简化 / 死代码清理 | `code-simplifier`、`refactor-cleaner` |
| 构建 / 类型错误修复 | `build-error-resolver`、`cpp-build-resolver`、`dart-build-resolver`、`django-build-resolver`、`go-build-resolver`、`java-build-resolver`、`kotlin-build-resolver`、`pytorch-build-resolver`、`react-build-resolver`、`rust-build-resolver`、`swift-build-resolver`、`harmonyos-app-resolver` |
| TDD / 测试先行 | `tdd-guide` |
| E2E 测试 | `e2e-runner` |
| 性能优化 / 剖析 | `performance-optimizer` |
| PR 测试覆盖分析 | `pr-test-analyzer` |
| 行为规范提取 | `spec-miner` |
| 文档 / 记忆更新 | `doc-updater` |

### 委派注意事项
- 子代理消耗更多 token，**只在值得时用**；简单任务直接自己做。
- **读密集型任务可并行**（多个 explorer 同时调研不同问题）；**写密集型任务谨慎**，同一文件只允许一个写者，子代理改代码后你需审查再合入。
- 子代理的产出（分析/方案/审查）由你整合进 `.ai/` 与代码；**最终修改责任在你**（Codex）。
- 委派遵循协作写权限：子代理代表你写代码，仍须符合"写权限互斥"与"改后跑 check.sh"。

## 三条核心规则

### 规则 1：写权限互斥
同一时间只允许一个工具写源代码（默认写者 = Codex，你是实现者）。Claude Code 只做分析/审查。角色切换时：
1. 当前写者提交所有未提交修改
2. 在 `.ai/brief.md` 记录角色切换原因
3. 新写者从 Git 最新状态开始

### 规则 2：文件驱动协作
通过文件传递上下文，禁止靠对话记忆传递关键信息：
- `AGENTS.md` — 你的项目规范
- `CLAUDE.md` — Claude Code 项目上下文
- `.ai/brief.md` — 当前任务目标（先读它）
- `.ai/plan.md` — 你的方案设计（先写它）
- `.ai/review.md` — Claude Code 的审查意见（按它修复）
- `.ai/backlog.md` — 额外问题与待办（发现新问题追加）
- `.ai/decision-log.md` — 架构决策记录
- `scripts/check.sh` — 统一验证入口（每次修改后必跑）

### 规则 3：每次修改后必须跑统一检查
每次修改后运行 `scripts/check.sh`（`set -e`，非 0 即失败）；失败 → 修复 → 重跑，最多 3 轮，超出请人类介入。不得在检查失败时宣称完成。

## 反模式（禁止）
- 与 Claude Code 同时修改同一批文件
- 靠对话记忆传递关键信息（必须写 `.ai/`）
- 改完不跑 check.sh 就结束
- 把简单任务也强行派子代理（浪费 token）
- 同一文件同时派多个写型子代理（写冲突）
- 把 CLAUDE.md 当安全策略用（强制限制用 hook）
