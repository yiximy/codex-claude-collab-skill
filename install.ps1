# ============================================================
#  Claude Code x Codex 协作方案 —— 一键安装脚本（Windows）
#  用法： powershell -ExecutionPolicy Bypass -File install.ps1
#  说明： 合并式安装，不会覆盖现有 ~/.claude.json 配置
# ============================================================
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$home2 = $env:USERPROFILE

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  Claude Code x Codex 协作方案 - 安装脚本" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

function Check-Cmd($name, $hint) {
    $c = Get-Command $name -ErrorAction SilentlyContinue
    if ($c) { Write-Host "  [OK] $name -> $($c.Source)" -ForegroundColor Green }
    else { Write-Host "  [缺] $name。$hint" -ForegroundColor Yellow }
}
Write-Host "`n[1/6] 检查依赖..." -ForegroundColor Cyan
Check-Cmd node "请安装 Node.js 18+ (https://nodejs.org)"
Check-Cmd npm "请安装 Node.js (自带 npm)"
Check-Cmd git "请安装 Git (https://git-scm.com)"
Check-Cmd python "请安装 Python 3.12+"
Check-Cmd uv "可选：无声桥 codexmcp 需要；也可稍后安装"
Check-Cmd claude "请安装 Claude Code (npm install -g @anthropic-ai/claude-code)"

# 缺失 Claude Code 且 node 可用时自动安装
$claudeOk = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claudeOk) {
    if (Get-Command node -ErrorAction SilentlyContinue) {
        Write-Host "`n[1.5] 自动安装 Claude Code (npm install -g @anthropic-ai/claude-code)..." -ForegroundColor Cyan
        npm install -g @anthropic-ai/claude-code
        Write-Host "  [OK] Claude Code 已安装（首次运行还需登录 Anthropic）" -ForegroundColor Green
    } else {
        Write-Host "`n[1.5] 未检测到 Node.js，无法自动安装 Claude Code。" -ForegroundColor Yellow
        Write-Host "  请先安装 Node.js 18+：" -ForegroundColor Yellow
        Write-Host "     - 官网下载: https://nodejs.org" -ForegroundColor Yellow
        Write-Host "     - 或命令行:  winget install OpenJS.NodeJS.LTS" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [OK] Claude Code: $($claudeOk.Source)" -ForegroundColor Green
}

Write-Host "`n[2/6] 检查/安装 npm 版 Codex CLI..." -ForegroundColor Cyan
$codexOk = Get-Command codex -ErrorAction SilentlyContinue
if (-not $codexOk) {
    Write-Host "  安装 @openai/codex ..."
    npm install -g @openai/codex
    Write-Host "  [OK] Codex CLI 已安装" -ForegroundColor Green
} else {
    Write-Host "  [OK] 已存在: $($codexOk.Source)" -ForegroundColor Green
}

Write-Host "`n[3/6] 部署窗口桥 codex-bridge..." -ForegroundColor Cyan
$bridgeDir = Join-Path $home2 "claude-codex-bridge"
$srcBridge = Join-Path $scriptDir "bridge"
if (Test-Path $srcBridge) {
    if (-not (Test-Path (Join-Path $bridgeDir "run.py"))) {
        Copy-Item -LiteralPath $srcBridge -Destination $bridgeDir -Recurse -Force
        Write-Host "  已复制 bridge 源码到 $bridgeDir" -ForegroundColor Green
    } else {
        Write-Host "  bridge 已存在，跳过复制" -ForegroundColor Green
    }
    $venvPy = Join-Path $bridgeDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPy)) {
        Write-Host "  创建独立 venv..."
        python -m venv (Join-Path $bridgeDir ".venv")
    }
    Write-Host "  安装依赖 (mcp>=1.0.0,<2)..."
    & $venvPy -m pip install "mcp>=1.0.0,<2" | Out-Null
    & $venvPy -c "from mcp.server.fastmcp import FastMCP; print('  [OK] bridge venv 就绪')"
} else {
    Write-Host "  未找到 bridge/ 目录，跳过。请确认 install.ps1 与 bridge/ 在同一目录" -ForegroundColor Yellow
}

Write-Host "`n[4/6] 写入窗口桥配置 ~/.ai-bridge/config.json（首次）..." -ForegroundColor Cyan
$aiCfgDir = Join-Path $home2 ".ai-bridge"
$aiCfg = Join-Path $aiCfgDir "config.json"
if (-not (Test-Path $aiCfg)) {
    New-Item -ItemType Directory -Force -Path $aiCfgDir | Out-Null
    $defaultCfg = @{
        codex_model = "deepseek-v4-flash"
        codex_reasoning_effort = "high"
        codex_reasoning_summary = "auto"
        codex_fast_mode = $false
        default_timeout_sec = 1800
        cwd_remaps = @()
    }
    $defaultCfg | ConvertTo-Json -Depth 5 | Set-Content -Path $aiCfg -Encoding UTF8
    Write-Host "  已生成 $aiCfg（请按需修改 codex_model 和 cwd_remaps）" -ForegroundColor Green
} else {
    Write-Host "  已存在，跳过" -ForegroundColor Green
}

Write-Host "`n[5/6] 合并 MCP server 到 ~/.claude.json..." -ForegroundColor Cyan
$claudeJson = Join-Path $home2 ".claude.json"
$nodeExe = (Get-Command node).Source
if (Test-Path $claudeJson) {
    $mergeScript = @"
const fs = require('fs');
const path = require('path');
const cfgPath = path.join(process.env.USERPROFILE, '.claude.json');
const cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
cfg.mcpServers = cfg.mcpServers || {};
const venvPy = path.join(process.env.USERPROFILE, 'claude-codex-bridge', '.venv', 'Scripts', 'python.exe');
const runPy = path.join(process.env.USERPROFILE, 'claude-codex-bridge', 'run.py');
let changed = false;
if (!cfg.mcpServers['codex']) {
  cfg.mcpServers['codex'] = { type: 'stdio', command: 'uvx', args: ['--from', 'git+https://github.com/GuDaStudio/codexmcp.git', '--with', 'mcp<2', 'codexmcp'] };
  changed = true;
}
if (!cfg.mcpServers['codex-bridge']) {
  cfg.mcpServers['codex-bridge'] = { type: 'stdio', command: venvPy, args: [runPy] };
  changed = true;
}
fs.writeFileSync(cfgPath, JSON.stringify(cfg, null, 2) + '\n', 'utf8');
console.log(changed ? '  [OK] mcpServers 已添加: codex, codex-bridge' : '  [OK] mcpServers 已存在，未改动');
"@
    $tmpJs = Join-Path $env:TEMP "merge-mcp-$([guid]::NewGuid().ToString('N')).js"
    $mergeScript | Set-Content -Path $tmpJs -Encoding UTF8
    & $nodeExe $tmpJs
    Remove-Item -LiteralPath $tmpJs -Force
} else {
    Write-Host "  未找到 ~/.claude.json（请先启动过一次 Claude Code），跳过" -ForegroundColor Yellow
}

Write-Host "`n[6/6] 复制 skills..." -ForegroundColor Cyan
$srcClaudeSkill = Join-Path $scriptDir "skills\claude\codex-claude-collab-brain"
$srcCodexSkill  = Join-Path $scriptDir "skills\codex\codex-claude-collab-worker"
$dstClaudeSkill = Join-Path $home2 ".claude\skills\codex-claude-collab-brain"
$dstCodexSkill  = Join-Path $home2 ".codex\skills\codex-claude-collab-worker"
if (Test-Path $srcClaudeSkill) { Copy-Item -LiteralPath $srcClaudeSkill -Destination $dstClaudeSkill -Recurse -Force; Write-Host "  [OK] Claude Code skill -> ~/.claude/skills/codex-claude-collab-brain" -ForegroundColor Green }
else { Write-Host "  缺 skills/claude/codex-claude-collab-brain" -ForegroundColor Yellow }
if (Test-Path $srcCodexSkill) { Copy-Item -LiteralPath $srcCodexSkill -Destination $dstCodexSkill -Recurse -Force; Write-Host "  [OK] Codex skill -> ~/.codex/skills/codex-claude-collab-worker" -ForegroundColor Green }
else { Write-Host "  缺 skills/codex/codex-claude-collab-worker" -ForegroundColor Yellow }
# ponytail（懒高级开发模式）skills —— 同时装给 Claude Code 和 Codex
$ponytailSkills = @("ponytail","ponytail-review","ponytail-audit","ponytail-debt","ponytail-gain","ponytail-help")
foreach ($ps in $ponytailSkills) {
    $srcClaudeP = Join-Path $scriptDir "skills\claude\$ps"
    $dstClaudeP = Join-Path $home2 ".claude\skills\$ps"
    if (Test-Path $srcClaudeP) { Copy-Item -LiteralPath $srcClaudeP -Destination $dstClaudeP -Recurse -Force; Write-Host "  [OK] Claude Code skill -> ~/.claude/skills/$ps" -ForegroundColor Green }
    else { Write-Host "  缺 skills/claude/$ps" -ForegroundColor Yellow }
    $srcCodexP = Join-Path $scriptDir "skills\codex\$ps"
    $dstCodexP = Join-Path $home2 ".codex\skills\$ps"
    if (Test-Path $srcCodexP) { Copy-Item -LiteralPath $srcCodexP -Destination $dstCodexP -Recurse -Force; Write-Host "  [OK] Codex skill -> ~/.codex/skills/$ps" -ForegroundColor Green }
    else { Write-Host "  缺 skills/codex/$ps" -ForegroundColor Yellow }
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  安装完成！剩余手动步骤见 README.md 第 4 节：" -ForegroundColor Cyan
Write-Host "  0. 首次运行 claude 需登录 Anthropic（或配置 ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL）"
Write-Host "  1. codex login（ChatGPT 订阅）或配置 ~/.codex/config.toml + auth.json（API）"
Write-Host "  2. 验证： claude mcp list （应看到 codex / codex-bridge √ Connected）"
Write-Host "  3. 中文路径：建 junction + cwd_remaps + trust（README 4.6）"
Write-Host "  4. 重启 Claude Code，在项目目录说：启用 codex-claude-collab 协作模式"
Write-Host "==============================================" -ForegroundColor Cyan
