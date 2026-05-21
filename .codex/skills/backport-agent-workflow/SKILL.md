---
name: backport-agent-workflow
description: 在目标 git 仓库中用 backport_agent 处理补丁合入检查、批量 cherry-pick、提交元信息同步、PR 统计、文件存在性检查、Excel 提交数据导出，或维护本仓库 CLI/MCP/AGENTS/skill 契约时使用。
---

# Backport Agent 工作流

## 使用边界

- 工具仓库固定为 `/data/slim/backport_agent`；补丁操作对象是当前目标 git 仓库，通常不是工具仓库本身。
- 在目标仓库调用 CLI 时使用绝对路径和 `-C`：`python3 /data/slim/backport_agent/patch_tool.py -C <TARGET_REPO> ...`。
- 调用 MCP tool 时显式传 `repo=<TARGET_REPO>` 绝对路径；不要依赖 MCP 进程工作目录。
- 修改任何公共 CLI/tool 行为后，必须同步检查 `mcp_server.py`、`AGENTS.md`、schema/docstring、skill 文档和验证命令。

## 核心工作流

- `patch_tool.py check`：按 title、`hash title` 或 hash 检查目标分支是否已合入；默认输出 `title|commit_id|status|git_describe|commit_time|lines_changed`，其中 `lines_changed` 是新增加删除行数。
- `patch_tool.py check --repos-file repos.json`：按配置顺序查询多个本地仓库，配置项为 `name/path/branch`；缺省 branch 继承 `--branch` 后再回退当前分支。输入可用 `repo<TAB>title/hash...` 限定单行仓库；未限定时任一仓库命中即 `Y`。无效仓库或分支只跳过并记录错误。
- `patch_tool.py check --include-repo`：输出 7 列 `title|repo|commit_id|status|git_describe|commit_time|lines_changed`；需要喂给 `cherry-pick` 时不要使用该模式。
- `patch_tool.py cherry-pick`：兼容旧 5 列和新 6 列 check 输出；无人值守场景优先用 MCP `cherry_pick`，因为 CLI 冲突后可能等待 `input()`。
- `patch_tool.py sync-meta`：先 dry-run；未经用户明确同意，不执行会重写历史的同步。
- `pr_tool.py`：统计 gitee、gitcode.net、gitcode.com、atomgit PR；需要给 `patch_tool.py check` 喂输入时用 `--commits-only`。
- `file_check_tool.py`：`-i INPUT` 逐行读取文件名并按 basename 精确匹配；`--roots` 覆盖默认根，`--csv` 输出机器可读结果。
- `excel_tool.py export-commits`：从 `Commit信息` 分号字段保留 hash 和 title，输出 `12位hash    title`，中间固定四个空格。

## MCP 使用

- Cursor 或其他客户端启动 MCP 时使用绝对路径：

```bash
/data/slim/backport_agent/.venv/bin/python /data/slim/backport_agent/mcp_server.py
```

- 只有明确需要 HTTP/SSE 时才改 transport；常规验证使用：

```bash
/data/slim/backport_agent/.venv/bin/python /data/slim/backport_agent/mcp_server.py --help
```

## 校验

- 仅修改 skill 时，运行：

```bash
python3 /root/.codex/skills/.system/skill-creator/scripts/quick_validate.py .codex/skills/backport-agent-workflow
find -L .codex/skills -maxdepth 20 -type l -printf '%p -> %l\n'
```

- 修改工具代码时，在 `/data/slim/backport_agent` 运行：

```bash
.venv/bin/python -m py_compile patch_tool.py pr_tool.py file_check_tool.py excel_tool.py mcp_server.py
python3 patch_tool.py --help
python3 pr_tool.py --help
python3 file_check_tool.py --help
python3 excel_tool.py --help
.venv/bin/python mcp_server.py --help
```

- 若 `ruff` 可用，lint 改动的 Python 文件；本仓库常见位置是 `/root/.local/bin/ruff`。
- 不把 `sync-meta`、force-push、无人值守 CLI `cherry-pick` 当作常规校验。

## Skill 维护

- `SKILL.md` 只写可触发的流程、硬性契约和安全边界；不要放 README、安装说明、变更日志或长模板。
- 每个 skill 目录保留 `agents/openai.yaml`，并确保 `default_prompt` 含对应 `$skill-name`。
