---
name: backport-patch-tools
description: Use when working in this repository to check whether commits or patch titles are already merged, batch cherry-pick patches, sync commit author metadata, collect PR commit/change stats, check file presence, or operate the local MCP/CLI tools for backport workflows.
---

# Backport Patch Tools

## Workflow

- Prefer the repository tools over ad hoc shell pipelines:
  - MCP server: `mcp_server.py`
  - Patch management CLI/API: `patch_tool.py`
  - PR statistics CLI/API: `pr_tool.py`
  - File presence CLI/API: `file_check_tool.py`
- Use `.venv/bin/python` when the repo virtual environment is available; system `python3` may not have the MCP dependency installed.
- For MCP, start with stdio unless the user asks for HTTP/SSE:
  - `.venv/bin/python mcp_server.py`
  - `.venv/bin/python mcp_server.py --transport http --host 127.0.0.1 --port 8000`

## Patch Operations

- Use `check` to match input commit titles, `hash title` lines, or hash-only entries against a target branch. Results are sorted by git describe order when possible.
- Use `cherry_pick` through MCP for non-interactive automation; it aborts and stops on conflicts. The CLI `cherry-pick` subcommand can block on `input()` after conflicts, so avoid it in unattended runs.
- Use `sync_meta` with `dry_run=true` first. Only run with `dry_run=false` when the user explicitly approves history rewriting.
- For `sync_meta`, leave `backend="auto"` unless the user requires `filter-repo` or `filter-branch`.

## PR And File Checks

- Use `pr_stats` for gitee, gitcode.net, gitcode.com, and atomgit PR URLs. Private repositories or rate limits may require `GITEE_TOKEN` or `GITCODE_TOKEN`.
- Avoid live PR stats calls when the user only needs interface validation; they require network access.
- Use `file_check` with explicit `roots` when the user gives a target filesystem tree. Without `roots`, it scans the tool's default system directories and can be slower.

## Validation

- Syntax-check changed Python with `.venv/bin/python -m py_compile mcp_server.py`.
- Check MCP CLI startup with `.venv/bin/python mcp_server.py --help`.
- If available, lint the touched MCP file with `.venv/bin/python -m ruff check mcp_server.py`.
- Do not run destructive `sync_meta` or force-push commands as validation.
