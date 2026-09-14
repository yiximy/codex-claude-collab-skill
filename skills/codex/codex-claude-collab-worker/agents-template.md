# Codex Project Rules

> 由 codex-claude-collab skill 生成。Codex 在项目内协作时必须遵守。

## Write Permission
- 当前写者：Codex
- 禁止与 Claude Code 同时写同一文件
- 同一时间只允许一个工具拥有写权限

## Workflow
- 开始任务前：读取 .ai/brief.md 了解当前目标
- 设计方案：写入 .ai/plan.md
- 实现修改：修改源代码
- 每次修改后：运行 scripts/check.sh
- 修复循环：check.sh 失败 → 修复 → 重跑，直到全部通过（最多 3 轮，超出请求人类介入）

## Subagents（子代理委派）
- 你有 spawn_agent 工具，**可根据任务类型自行调用相关子代理**辅助分析、规划、审查与构建修复。
- 本项目常用子代理（按任务类型）：
  - 陌生代码库分析 / 结构梳理：`code-explorer`
  - 复杂功能 / 重构方案：`planner`、`code-architect`
  - 代码审查（按语言）：{{REVIEWER_AGENTS}}（如 Python → `python-reviewer`，TypeScript/React → `typescript-reviewer`/`react-reviewer`，通用 → `code-reviewer`）
  - 构建 / 类型错误修复（按语言）：{{BUILD_RESOLVER_AGENTS}}（如 `build-error-resolver` 或对应语言 resolver）
  - 安全敏感改动（输入/鉴权/密钥）：`security-reviewer`
  - 性能优化：`performance-optimizer`
- 委派原则：
  - 读密集型任务可并行（多个 `code-explorer` 并行调研）；写密集型任务谨慎，**同一文件只允许一个写者**
  - 子代理产出由你（Codex）整合进代码与 .ai/ 文档，**最终修改责任在你**
  - 简单任务直接自己做，不要强行走子代理浪费 token
  - 每次修改后仍须运行 scripts/check.sh

## Verification
- 必须通过 scripts/check.sh 的所有检查
- 不得在检查失败时结束任务

## Collaboration
- 审查意见在 .ai/review.md，按其修复
- 发现新问题追加到 .ai/backlog.md
- 架构决策记录到 .ai/decision-log.md
- 角色切换：提交当前修改 → 在 .ai/brief.md 记录 → 从 Git 最新状态开始
- **无 Git 降级模式**（项目未初始化 Git 且人类拒绝 git init）：角色切换改为把改动文件复制到 .ai/snapshots/<时间戳>/，接手方从最新快照 + 工作区现状开始；无 diff 可看时按「本次改动文件清单 + 每个文件改了什么」交付（写进 .ai/plan.md），审查方逐文件重读
