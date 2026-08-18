#!/usr/bin/env bash
# 统一验证入口 —— 由 codex-claude-collab skill 生成，按项目类型调整命令。
set -e

echo "=== Running unified checks ==="

# 1. 代码风格 / Lint
echo "--- Lint ---"
# <lint-command>   # 例如：ruff check . / pnpm lint / cargo clippy

# 2. 测试
echo "--- Test ---"
# <test-command>   # 例如：pytest / pnpm test / go test ./...

# 3. 构建
echo "--- Build ---"
# <build-command>  # 例如：pnpm build / go build ./... / cargo build

# 4. 类型检查（如适用）
echo "--- Type Check ---"
# <typecheck-command>  # 例如：mypy . / tsc --noEmit

echo "=== All checks passed ==="
