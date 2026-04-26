#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补丁/PR/文件检查工具 MCP Server。

基于 FastMCP 暴露本仓库工具的核心能力，支持 stdio / SSE / streamable-http 传输。

启动方式：
  stdio:            python mcp_server.py
  streamable-http:  python mcp_server.py --transport http --port 8000
  sse:              python mcp_server.py --transport sse  --port 8000
"""

import argparse
import json
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from excel_tool import collect_commits, export_commits
from file_check_tool import (
    _build_rows,
    _count_summary,
    _resolve_roots,
    _walk_and_index,
    get_search_roots,
)
from patch_tool import check_commits, cherry_pick_commits, sync_meta_commits
from pr_tool import _fetch_pr_stats

mcp = FastMCP(
    "backport-agent",
    instructions=(
        "补丁/PR/文件检查工具，提供补丁合入检查、批量 cherry-pick、作者元信息同步、"
        "PR 统计、文件存在性检查与 Excel Commit信息 导出能力。"
    ),
)


@mcp.tool()
def check(
    commits: list[str],
    repo: str = ".",
    branch: Optional[str] = None,
    long_hash: bool = False,
) -> str:
    """检查提交列表是否已合入目标分支。

    Args:
        commits: 待检查的提交列表。每项支持三种格式：
                 - 纯 title: "cpufreq: Fix re-boost issue after hotplugging a CPU"
                 - hash + title: "5a76550645be3 cpufreq: Fix re-boost issue"
                 - 纯 hash (≥10 位): "5a76550645be3"
        repo: git 仓库路径（默认当前目录）。
        branch: 目标分支名（默认当前分支，可为 branch/tag/commit 引用）。
        long_hash: 为 true 时结果中 commit_id 使用 40 位完整 hash。

    Returns:
        JSON 对象，包含 total / matched / unmatched 统计及 results 列表。
        results 按 git describe 合入序排序，每项含：
          title, commit_id, status(Y/N), git_describe, commit_time, match_method
    """
    result = check_commits(commits, repo=repo, branch=branch, long_hash=long_hash)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def cherry_pick(
    commits: list[str],
    repo: str = ".",
    signoff: bool = True,
    start: int = 1,
) -> str:
    """批量 cherry-pick 提交（非交互模式，遇冲突自动 abort 并停止）。

    Args:
        commits: 补丁列表。每项支持两种格式：
                 - "hash [title]": 标准补丁列表
                 - check 输出行: "title|commit_id|Y/N|describe|time"
                   （status=N 的行自动跳过）
        repo: git 仓库路径（默认当前目录）。
        signoff: 是否使用 --signoff（默认 true）。
        start: 从第几个有效提交开始（1-based，默认从头开始）。

    Returns:
        JSON 对象，包含 total_valid / processed / succeeded 统计、
        conflict 信息（若有）、及逐条 results。
    """
    result = cherry_pick_commits(
        commits, repo=repo, signoff=signoff, start=start,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def sync_meta(
    source_branch: str,
    commit_range: str,
    repo: str = ".",
    dry_run: bool = True,
    backend: str = "auto",
) -> str:
    """根据参考分支的同名提交，同步当前分支指定范围内的作者/时间元数据。

    Args:
        source_branch: 参考分支名/引用（从该分支读取正确的作者/时间）。
        commit_range: 当前分支提交范围，如 "base_commit..HEAD"。
        repo: git 仓库路径（默认当前目录）。
        dry_run: 为 true 时仅返回变更预览，不实际改写历史（默认 true）。
        backend: 历史改写后端，可选 "auto" / "filter-repo" / "filter-branch"。

    Returns:
        JSON 对象，包含 total_in_range / matched / changes 列表。
        每条 change 含 old_author → new_author、old_date → new_date。
        当 dry_run=false 时 applied=true 表示已实际改写历史。

    注意:
        dry_run=false 会通过 git filter-repo 或 git filter-branch 重写历史，
        后续需 git push --force 推送远端。
    """
    result = sync_meta_commits(
        source_branch, commit_range, repo=repo, dry_run=dry_run, backend=backend,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def pr_stats(urls: list[str], timeout: int = 15) -> str:
    """统计 PR 的 commit 数量、代码变更行数与 commit 列表。

    Args:
        urls: PR URL 列表。支持 gitee、gitcode.net、gitcode.com、atomgit。
        timeout: 单次 HTTP 请求超时时间（秒，默认 15）。

    Returns:
        JSON 对象，包含 total 及 results 列表。
        每项含 url, platform, commit_count, lines_changed, commits, error。

    环境变量:
        GITEE_TOKEN: 访问 gitee 私有仓库或提升限额。
        GITCODE_TOKEN: 访问 gitcode/atomgit 私有仓库或提升限额。
    """
    clean_urls = [url.strip() for url in urls if url and url.strip()]
    results = []
    for url in clean_urls:
        stats = _fetch_pr_stats(url, timeout=timeout)
        stats["url"] = url
        results.append(stats)
    return json.dumps(
        {"total": len(clean_urls), "results": results},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def file_check(names: list[str], roots: Optional[list[str]] = None) -> str:
    """批量检查文件名是否存在于指定目录树中。

    Args:
        names: 待检查文件名列表，按 basename 精确匹配。
        roots: 搜索根目录列表；为空时使用 file_check_tool 自动识别的默认系统目录。
               names 包含 .h/.c 时优先搜索内核开发包目录。

    Returns:
        JSON 对象，包含 total / found / not_found 统计、warnings 及 results 列表。
        results 每项含 文件名、状态、命中数量、命中路径。
    """
    clean_names = [name.strip() for name in names if name and name.strip()]
    search_roots = get_search_roots(clean_names) if roots is None else roots
    resolved_roots = _resolve_roots(search_roots)
    index, warnings = _walk_and_index(resolved_roots)
    rows = _build_rows(clean_names, index)
    total, found, not_found = _count_summary(rows)
    result = {
        "total": total,
        "found": found,
        "not_found": not_found,
        "roots": [str(root) for root in resolved_roots],
        "warnings": warnings,
        "results": rows,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def excel_export_commits(input_file: str, output_file: Optional[str] = None) -> str:
    """从 Excel 的 Commit信息 列导出提交列表。

    Args:
        input_file: 输入 Excel 文件路径（.xlsx/.xlsm）。每个 worksheet 首行按
                    表头精确匹配 Commit信息 列。
        output_file: 可选输出文本路径；提供时写出 UTF-8 文本。Commit信息
                     原始内容按分号分隔字段，只保留第 1 段 hash 和第 2 段
                     title，输出格式为 "12位hash    commit title"。

    Returns:
        JSON 对象，包含 commits 列表、统计信息、可选 output_file 及 error。
        commits 可直接作为 MCP check/cherry_pick 的 commits 参数；分号字段中
        不能解析出至少 12 位 hash 和非空 title 的 Commit信息 会被跳过并计数。
    """
    input_path = Path(input_file)
    try:
        if output_file:
            output_path = Path(output_file)
            stats = export_commits(input_path, output_path)
            commits = output_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            result = {
                "output_file": str(output_path),
                "commits": commits,
                "stats": stats,
            }
        else:
            result = collect_commits(input_path)
    except Exception as exc:
        result = {"error": str(exc)}

    return json.dumps(result, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="backport-agent MCP Server")
    parser.add_argument(
        "--transport", choices=["stdio", "sse", "http"],
        default="stdio", help="传输方式（默认 stdio）",
    )
    parser.add_argument("--port", type=int, default=8000, help="HTTP/SSE 端口")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP/SSE 监听地址")
    args = parser.parse_args()

    if args.transport != "stdio":
        mcp.settings.host = args.host
        mcp.settings.port = args.port

    transport_map = {"http": "streamable-http", "sse": "sse", "stdio": "stdio"}
    mcp.run(transport=transport_map[args.transport])


if __name__ == "__main__":
    main()
