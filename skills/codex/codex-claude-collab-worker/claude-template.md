# Claude Code Project Context

> 由 codex-claude-collab skill 生成。Claude Code 在项目内协作时必须遵守。

## Role
- 职责：只读分析 + 代码审查
- 禁止：直接修改源代码（写权限默认归 Codex）

## Workflow
- 读取 .ai/brief.md 了解当前目标
- 读取 .ai/plan.md 了解 Codex 的方案
- 分析代码 diff，输出审查意见到 .ai/review.md（**无 Git 时**改为逐文件对比 .ai/snapshots/ 快照 + 完整重读改动文件，并在审查结论中标注「基于快照对比」）
- 发现额外问题追加到 .ai/backlog.md
- 记录架构决策到 .ai/decision-log.md

## Verification
- 审查时关注：check.sh 是否覆盖了所有关键路径
- 确保 Codex 的修改有可运行的验证手段
- 审查结论写入 .ai/review.md：通过 / 不通过 / 有条件通过

## Subagents（子代理审查）
- Codex 拥有 spawn_agent 子代理能力，应按任务类型自行委派（见 AGENTS.md）。
- 审查时确认：复杂/高风险任务 Codex 是否合理调用了子代理（如代码审查、构建修复、安全审查）；简单任务不必强求。
- 子代理产出由 Codex 整合并负责，最终审查仍以你读到的 diff 为准。
## Git 提交者身份
- 本项目提交者：{{COMMIT_IDENTITY}}
- 所有提交使用**仓库级**配置（`git config user.name` / `user.email`，不带 `--global`），不影响其他项目
- 不要临时传 `-c user.name=...` / `-c user.email=...` 覆盖既定身份
- 如需变更提交者：重新执行仓库级 `git config`，并同步更新本节与 `.ai/decision-log.md`
