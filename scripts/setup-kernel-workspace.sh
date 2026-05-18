#!/usr/bin/env bash
# 在内核（或任意目标 git）源码树中接入 backport-agent-workflow skill 与 Cursor MCP 配置。
set -euo pipefail

BACKPORT_AGENT_HOME="${BACKPORT_AGENT_HOME:-/data/slim/backport_agent}"
SKILL_SRC="${BACKPORT_AGENT_HOME}/.codex/skills/backport-agent-workflow"

usage() {
    cat <<EOF
用法: $0 <KERNEL_REPO>

在目标 git 仓库根目录创建:
  - .codex/skills/backport-agent-workflow -> ${SKILL_SRC}
  - .cursor/mcp.json（注册 backport-agent MCP，若已存在则合并）

环境变量:
  BACKPORT_AGENT_HOME  工具仓库路径（默认: ${BACKPORT_AGENT_HOME}）
EOF
}

if [[ $# -ne 1 ]]; then
    usage >&2
    exit 1
fi

KERNEL_REPO="$(cd "$1" && pwd)"

if ! git -C "${KERNEL_REPO}" rev-parse --show-toplevel >/dev/null 2>&1; then
    echo "错误: ${KERNEL_REPO} 不是有效的 git 仓库" >&2
    exit 1
fi

if [[ ! -d "${SKILL_SRC}" ]]; then
    echo "错误: 未找到 skill 源目录 ${SKILL_SRC}" >&2
    exit 1
fi

mkdir -p "${KERNEL_REPO}/.codex/skills"
ln -sfn "${SKILL_SRC}" "${KERNEL_REPO}/.codex/skills/backport-agent-workflow"

PYTHON="${BACKPORT_AGENT_HOME}/.venv/bin/python"
MCP_SCRIPT="${BACKPORT_AGENT_HOME}/mcp_server.py"
if [[ ! -x "${PYTHON}" ]]; then
    echo "警告: ${PYTHON} 不存在，MCP 配置将使用 python3" >&2
    PYTHON="python3"
fi

mkdir -p "${KERNEL_REPO}/.cursor"
MCP_JSON="${KERNEL_REPO}/.cursor/mcp.json"

if [[ -f "${MCP_JSON}" ]]; then
    "${PYTHON}" - "${MCP_JSON}" "${PYTHON}" "${MCP_SCRIPT}" <<'PY'
import json
import sys

path, python, script = sys.argv[1:4]
with open(path, encoding="utf-8") as f:
    data = json.load(f)
servers = data.setdefault("mcpServers", {})
servers["backport-agent"] = {
    "command": python,
    "args": [script],
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
else
    cat >"${MCP_JSON}" <<EOF
{
  "mcpServers": {
    "backport-agent": {
      "command": "${PYTHON}",
      "args": ["${MCP_SCRIPT}"]
    }
  }
}
EOF
fi

echo "已配置: ${KERNEL_REPO}"
echo "  skill: .codex/skills/backport-agent-workflow -> ${SKILL_SRC}"
echo "  MCP:   .cursor/mcp.json (backport-agent)"
echo ""
echo "验证:"
echo "  ls -l ${KERNEL_REPO}/.codex/skills/backport-agent-workflow/SKILL.md"
echo "  ${PYTHON} ${MCP_SCRIPT} --help"
echo "  python3 ${BACKPORT_AGENT_HOME}/patch_tool.py -C ${KERNEL_REPO} --help"
