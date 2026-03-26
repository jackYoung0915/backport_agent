#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PR 信息统计工具。

从输入文件读取 PR 链接，统计每个 PR 的 commit 数量和代码行数变更，
支持 gitee、gitcode、atomgit 平台。

输出格式（每行）: url|platform|commit_count|lines_changed|error
"""

import argparse
import json
import logging
import os
import sys
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _decode_output(data: Optional[bytes]) -> str:
    """解码 HTTP 响应。"""
    return (data or b"").decode("utf-8", errors="replace").strip()


def _make_request(url: str, token: Optional[str] = None, timeout: int = 15, use_private_token: bool = False) -> Tuple[int, str]:
    """发起 HTTP GET 请求并返回状态码和响应体。

    Args:
        url: 请求 URL
        token: 可选的访问 token
        timeout: 超时时间（秒）
        use_private_token: 是否使用 private-token header（atomgit 使用）

    Returns:
        (status_code, response_body)
    """
    headers = {
        "Accept": "application/json",
        "User-Agent": "pr_tool/1.0",
    }
    if token:
        if use_private_token:
            # atomgit 使用 private-token header
            headers["private-token"] = token
        else:
            # gitee 和 gitcode 都支持 access_token 参数
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}access_token={token}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = _decode_output(resp.read())
            return (resp.status, body)
    except urllib.error.HTTPError as e:
        body = _decode_output(e.read())
        return (e.code, body)
    except urllib.error.URLError as e:
        return (-1, f"url_error: {e.reason}")
    except TimeoutError:
        return (-1, "timeout")
    except Exception as e:
        return (-1, f"unknown_error: {e}")


def _parse_gitee_pr_url(url: str) -> Optional[Tuple[str, str, str]]:
    """解析 gitee PR URL。

    支持格式:
    - https://gitee.com/owner/repo/pulls/123
    - https://gitee.com/owner/repo/pull/123 (旧格式)

    Returns:
        (owner, repo, pr_number) 或 None
    """
    try:
        parsed = urllib.parse.urlparse(url.strip())
        if "gitee.com" not in parsed.netloc:
            return None

        path = parsed.path.rstrip("/")
        # 移除前导空字符串
        parts = [p for p in path.split("/") if p]

        # 查找 pulls 或 pull 的位置
        # 格式: [owner, repo, pulls/pull, number]
        idx = -1
        for i, p in enumerate(parts):
            if p in ("pulls", "pull"):
                idx = i
                break

        if idx < 0 or idx + 1 >= len(parts):
            return None

        owner = parts[0]
        repo = parts[1]
        pr_num = parts[idx + 1]

        if pr_num.isdigit():
            return (owner, repo, pr_num)
        return None
    except Exception:
        return None


def _parse_gitcode_pr_url(url: str) -> Optional[Tuple[str, str, str]]:
    """解析 gitcode PR URL。

    支持格式:
    - https://gitcode.net/owner/repo/-/merge_requests/123
    - https://gitcode.net/owner/repo/merge_requests/123
    - https://gitcode.com/owner/repo/pull/123

    Returns:
        (owner, repo, pr_number) 或 None
    """
    try:
        parsed = urllib.parse.urlparse(url.strip())
        netloc = parsed.netloc.lower()
        if "gitcode.net" not in netloc and "gitcode.com" not in netloc:
            return None

        path = parsed.path.rstrip("/")
        parts = [p for p in path.split("/") if p]

        # gitcode 使用 merge_requests 或 pull
        # 格式: [owner, repo, '-', merge_requests, number] 或 [owner, repo, merge_requests, number] 或 [owner, repo, pull, number]
        idx = -1
        for i, p in enumerate(parts):
            if p in ("merge_requests", "pulls", "pull"):
                idx = i
                break

        if idx < 0 or idx + 1 >= len(parts):
            return None

        owner = parts[0]
        repo = parts[1]
        pr_num = parts[idx + 1]

        if pr_num.isdigit():
            return (owner, repo, pr_num)
        return None
    except Exception:
        return None


def _parse_atomgit_pr_url(url: str) -> Optional[Tuple[str, str, str]]:
    """解析 atomgit PR URL。

    支持格式:
    - https://atomgit.com/owner/repo/pull/123

    Returns:
        (owner, repo, pr_number) 或 None
    """
    try:
        parsed = urllib.parse.urlparse(url.strip())
        netloc = parsed.netloc.lower()
        if "atomgit.com" not in netloc:
            return None

        path = parsed.path.rstrip("/")
        parts = [p for p in path.split("/") if p]

        # atomgit 使用 pull
        # 格式: [owner, repo, pull, number]
        idx = -1
        for i, p in enumerate(parts):
            if p in ("pulls", "pull"):
                idx = i
                break

        if idx < 0 or idx + 1 >= len(parts):
            return None

        owner = parts[0]
        repo = parts[1]
        pr_num = parts[idx + 1]

        if pr_num.isdigit():
            return (owner, repo, pr_num)
        return None
    except Exception:
        return None


def _detect_platform(url: str) -> Optional[str]:
    """检测 URL 所属平台。"""
    url_lower = url.lower()
    if "gitee.com" in url_lower:
        return "gitee"
    if "gitcode.net" in url_lower:
        return "gitcode"  # GitLab API
    if "gitcode.com" in url_lower:
        return "gitcode_com"  # REST API v5，与 gitee/atomgit 类似
    if "atomgit.com" in url_lower:
        return "atomgit"
    return None


def _fetch_gitee_pr_stats(owner: str, repo: str, pr_num: str, token: Optional[str], timeout: int) -> Dict[str, Any]:
    """获取 gitee PR 的 commit 数量和行数变更。

    Gitee API 需要两次请求：
    1. /pulls/{number}/commits - 获取 commit 列表
    2. /pulls/{number}/files - 获取变更文件（每个文件有 additions/deletions）
    """
    base_url = "https://gitee.com/api/v5/repos"

    # 1. 获取 commits（添加 per_page 参数获取所有 commits，Gitee 默认最多 20 条）
    commits_url = f"{base_url}/{owner}/{repo}/pulls/{pr_num}/commits?per_page=100"
    status, body = _make_request(commits_url, token, timeout)

    if status != 200:
        return {
            "commit_count": -1,
            "lines_changed": -1,
            "commits": "",
            "error": f"commits API failed: status={status}, body={body[:200]}",
        }

    try:
        commits_data = json.loads(body)
        commit_count = len(commits_data) if isinstance(commits_data, list) else 0

        # 提取 commit SHA 和 title，格式：commit_id commit_title（每行一个）
        commits = ""
        if isinstance(commits_data, list):
            lines = []
            for c in commits_data:
                sha = c.get("sha", "")
                title = ""
                if "commit" in c and isinstance(c["commit"], dict):
                    title = c["commit"].get("message", "")
                # 只保留第一行作为 title
                title = title.split("\n")[0] if title else ""
                lines.append(f"{sha} {title}")
            commits = "\n".join(lines)
    except json.JSONDecodeError:
        return {
            "commit_count": -1,
            "lines_changed": -1,
            "commits": "",
            "error": f"commits JSON parse error: {body[:200]}",
        }

    # 2. 获取 files（用于统计 additions/deletions）
    files_url = f"{base_url}/{owner}/{repo}/pulls/{pr_num}/files"
    status, body = _make_request(files_url, token, timeout)

    lines_changed = 0
    if status == 200:
        try:
            files_data = json.loads(body)
            if isinstance(files_data, list):
                for f in files_data:
                    # gitee 返回的可能是字符串，需要转换
                    additions = int(f.get("additions", 0) or 0)
                    deletions = int(f.get("deletions", 0) or 0)
                    lines_changed += (additions + deletions)
        except json.JSONDecodeError:
            pass  # files 解析失败不影响主结果
    else:
        # 如果 files API 失败，记录但继续
        logging.debug(f"gitee files API failed: status={status}")

    return {
        "commit_count": commit_count,
        "lines_changed": lines_changed,
        "commits": commits,
        "error": "" if commit_count >= 0 else "unknown",
    }


def _fetch_gitcode_pr_stats(owner: str, repo: str, pr_num: str, token: Optional[str], timeout: int) -> Dict[str, Any]:
    """获取 gitcode (GitLab) PR 的 commit 数量和行数变更。

    GitLab/GitCode API:
    1. /projects/{id}/merge_requests/{iid}/commits - 获取 commit 列表
    2. /projects/{id}/merge_requests/{iid}/changes - 获取变更详情（包含 additions/deletions）
    """
    # 需要先获取 project id，因为 API 使用 path-encoded project ID
    # owner/repo 需要 URL encode
    proj_id = urllib.parse.quote(f"{owner}/{repo}", safe="")
    base_url = "https://gitcode.net/api/v4"

    # 1. 获取 commits（添加 per_page 参数获取所有 commits，GitLab 默认最多 20 条）
    commits_url = f"{base_url}/projects/{proj_id}/merge_requests/{pr_num}/commits?per_page=100"
    status, body = _make_request(commits_url, token, timeout)

    if status != 200:
        return {
            "commit_count": -1,
            "lines_changed": -1,
            "commits": "",
            "error": f"commits API failed: status={status}, body={body[:200]}",
        }

    try:
        commits_data = json.loads(body)
        commit_count = len(commits_data) if isinstance(commits_data, list) else 0

        # 提取 commit SHA 和 title，格式：commit_id commit_title（每行一个）
        commits = ""
        if isinstance(commits_data, list):
            lines = []
            for c in commits_data:
                # GitLab/GitCode 使用 id 字段作为 SHA
                commit_id = c.get("id", "")
                # title 或 message 字段作为标题
                title = c.get("title", "") or c.get("message", "")
                # 只保留第一行作为 title
                title = title.split("\n")[0] if title else ""
                lines.append(f"{commit_id} {title}")
            commits = "\n".join(lines)
    except json.JSONDecodeError:
        return {
            "commit_count": -1,
            "lines_changed": -1,
            "commits": "",
            "error": f"commits JSON parse error: {body[:200]}",
        }
    # 2. 获取 changes（包含 additions/deletions）
    changes_url = f"{base_url}/projects/{proj_id}/merge_requests/{pr_num}/changes"
    status, body = _make_request(changes_url, token, timeout, use_private_token=True)

    lines_changed = 0
    if status == 200:
        try:
            changes_data = json.loads(body)
            # GitLab merge_request changes 格式
            if isinstance(changes_data, dict):
                changes = changes_data.get("changes", {})
                diffs = changes.get("diffs", [])
                for diff in diffs:
                    additions = diff.get("new_linenos", {}).get("additions", 0)
                    deletions = diff.get("old_linenos", {}).get("deletions", 0)
                    # 另一种可能：直接从 diff 文本计算
                    # diff 中有 new_path 和 old_path
                    # GitLab 7.x+ 格式
                # 或者使用 stats
                if "stats" in changes_data:
                    stats = changes_data["stats"]
                    lines_changed = stats.get("additions", 0) + stats.get("deletions", 0)
                else:
                    # 从 diff 数组估算 - 简单方式：每个文件变更都算 1 行
                    lines_changed = len(diffs)
            elif isinstance(changes_data, list):
                # 旧版格式，直接是 changes 数组
                lines_changed = len(changes_data)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    else:
        logging.debug(f"gitcode changes API failed: status={status}")

    return {
        "commit_count": commit_count,
        "lines_changed": lines_changed,
        "commits": commits,
        "error": "" if commit_count >= 0 else "unknown",
    }


def _fetch_gitcode_com_pr_stats(owner: str, repo: str, pr_num: str, token: Optional[str], timeout: int) -> Dict[str, Any]:
    """获取 gitcode.com PR 的 commit 数量和行数变更。

    GitCode.com API（与 gitee/atomgit 类似的 REST API）:
    1. /pulls/{number}/commits - 获取 commit 列表
    2. /pulls/{number}/files - 获取变更文件（每个文件有 additions/deletions）
    """
    base_url = "https://gitcode.com/api/v5/repos"

    # 1. 获取 commits（添加 per_page 参数获取所有 commits，默认最多 20 条）
    commits_url = f"{base_url}/{owner}/{repo}/pulls/{pr_num}/commits?per_page=100"
    status, body = _make_request(commits_url, token, timeout, use_private_token=True)

    if status != 200:
        return {
            "commit_count": -1,
            "lines_changed": -1,
            "commits": "",
            "error": f"commits API failed: status={status}, body={body[:200]}",
        }

    try:
        commits_data = json.loads(body)
        commit_count = len(commits_data) if isinstance(commits_data, list) else 0

        # 提取 commit SHA 和 title，格式：commit_id commit_title（每行一个）
        commits = ""
        if isinstance(commits_data, list):
            lines = []
            for c in commits_data:
                sha = c.get("sha", "")
                title = ""
                if "commit" in c and isinstance(c["commit"], dict):
                    title = c["commit"].get("message", "")
                # 只保留第一行作为 title
                title = title.split("\n")[0] if title else ""
                lines.append(f"{sha} {title}")
            commits = "\n".join(lines)
    except json.JSONDecodeError:
        return {
            "commit_count": -1,
            "lines_changed": -1,
            "commits": "",
            "error": f"commits JSON parse error: {body[:200]}",
        }

    # 2. 获取 files（用于统计 additions/deletions）
    files_url = f"{base_url}/{owner}/{repo}/pulls/{pr_num}/files"
    status, body = _make_request(files_url, token, timeout, use_private_token=True)

    lines_changed = 0
    if status == 200:
        try:
            files_data = json.loads(body)
            if isinstance(files_data, list):
                for f in files_data:
                    # gitcode.com 返回的可能是字符串，需要转换
                    additions = int(f.get("additions", 0) or 0)
                    deletions = int(f.get("deletions", 0) or 0)
                    lines_changed += (additions + deletions)
        except json.JSONDecodeError:
            pass
    else:
        logging.debug(f"gitcode.com files API failed: status={status}")

    return {
        "commit_count": commit_count,
        "lines_changed": lines_changed,
        "commits": commits,
        "error": "" if commit_count >= 0 else "unknown",
    }


def _fetch_atomgit_pr_stats(owner: str, repo: str, pr_num: str, token: Optional[str], timeout: int) -> Dict[str, Any]:
    """获取 atomgit PR 的 commit 数量和行数变更。

    AtomGit API（与 gitee 类似的 REST API）:
    1. /pulls/{number}/commits - 获取 commit 列表
    2. /pulls/{number}/files - 获取变更文件（每个文件有 additions/deletions）
    """
    base_url = "https://atomgit.com/api/v5/repos"

    # 1. 获取 commits（添加 per_page 参数获取所有 commits，默认最多 20 条）
    commits_url = f"{base_url}/{owner}/{repo}/pulls/{pr_num}/commits?per_page=100"
    status, body = _make_request(commits_url, token, timeout, use_private_token=True)

    if status != 200:
        return {
            "commit_count": -1,
            "lines_changed": -1,
            "commits": "",
            "error": f"commits API failed: status={status}, body={body[:200]}",
        }

    try:
        commits_data = json.loads(body)
        commit_count = len(commits_data) if isinstance(commits_data, list) else 0

        # 提取 commit SHA 和 title，格式：commit_id commit_title（每行一个）
        commits = ""
        if isinstance(commits_data, list):
            lines = []
            for c in commits_data:
                sha = c.get("sha", "")
                title = ""
                if "commit" in c and isinstance(c["commit"], dict):
                    title = c["commit"].get("message", "")
                # 只保留第一行作为 title
                title = title.split("\n")[0] if title else ""
                lines.append(f"{sha} {title}")
            commits = "\n".join(lines)
    except json.JSONDecodeError:
        return {
            "commit_count": -1,
            "lines_changed": -1,
            "commits": "",
            "error": f"commits JSON parse error: {body[:200]}",
        }

    # 2. 获取 files（用于统计 additions/deletions）
    files_url = f"{base_url}/{owner}/{repo}/pulls/{pr_num}/files"
    status, body = _make_request(files_url, token, timeout, use_private_token=True)

    lines_changed = 0
    if status == 200:
        try:
            files_data = json.loads(body)
            if isinstance(files_data, list):
                for f in files_data:
                    # atomgit 返回的可能是字符串，需要转换
                    additions = int(f.get("additions", 0) or 0)
                    deletions = int(f.get("deletions", 0) or 0)
                    lines_changed += (additions + deletions)
        except json.JSONDecodeError:
            pass
    else:
        logging.debug(f"atomgit files API failed: status={status}")

    return {
        "commit_count": commit_count,
        "lines_changed": lines_changed,
        "commits": commits,
        "error": "" if commit_count >= 0 else "unknown",
    }


def _fetch_pr_stats(url: str, timeout: int = 15) -> Dict[str, Any]:
    """获取单个 PR 的统计信息。"""
    platform = _detect_platform(url)
    if not platform:
        return {
            "platform": "unknown",
            "commit_count": -1,
            "lines_changed": -1,
            "commits": "",
            "error": "unsupported platform",
        }

    token = None
    if platform == "gitee":
        token = os.environ.get("GITEE_TOKEN")
    elif platform in ("gitcode", "gitcode_com", "atomgit"):
        token = os.environ.get("GITCODE_TOKEN")

    parsed = None
    if platform == "gitee":
        parsed = _parse_gitee_pr_url(url)
    elif platform in ("gitcode", "gitcode_com"):
        parsed = _parse_gitcode_pr_url(url)
    elif platform == "atomgit":
        parsed = _parse_atomgit_pr_url(url)

    if not parsed:
        return {
            "platform": platform,
            "commit_count": -1,
            "lines_changed": -1,
            "commits": "",
            "error": "failed to parse PR URL",
        }

    owner, repo, pr_num = parsed
    logging.info(f"Fetching {platform} PR: {owner}/{repo}#{pr_num}")

    if platform == "gitee":
        result = _fetch_gitee_pr_stats(owner, repo, pr_num, token, timeout)
    elif platform == "gitcode":
        result = _fetch_gitcode_pr_stats(owner, repo, pr_num, token, timeout)
    elif platform == "gitcode_com":
        result = _fetch_gitcode_com_pr_stats(owner, repo, pr_num, token, timeout)
    elif platform == "atomgit":
        result = _fetch_atomgit_pr_stats(owner, repo, pr_num, token, timeout)
    else:
        result = {
            "commit_count": -1,
            "lines_changed": -1,
            "commits": "",
            "error": "unknown platform",
        }

    result["platform"] = platform
    return result


def _read_input_file(path: Path) -> List[str]:
    """读取输入文件，返回非空、非注释行。"""
    urls = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)
    return urls


def cmd_stats(args: argparse.Namespace) -> int:
    """主命令：统计输入文件中各 PR 的 commit 数量和行数变更。"""
    inp = Path(args.input_file)
    out = Path(args.output_file)
    timeout = args.timeout

    if not inp.is_file():
        logging.error(f"输入文件不存在: {inp}")
        return 1

    urls = _read_input_file(inp)
    if not urls:
        logging.warning("输入文件中没有有效的 PR URL")
        out.write_text("", encoding="utf-8")
        return 0

    logging.info(f"读取到 {len(urls)} 个 PR URL")

    results: List[Dict[str, Any]] = []
    for url in urls:
        logging.info(f"处理: {url}")
        stats = _fetch_pr_stats(url, timeout)
        stats["url"] = url
        stats["platform"] = stats.get("platform", _detect_platform(url) or "unknown")
        results.append(stats)

        # 输出进度
        commit_count = stats.get("commit_count", -1)
        lines_changed = stats.get("lines_changed", -1)
        error = stats.get("error", "")
        if error:
            logging.warning(f"  结果: commit={commit_count}, lines={lines_changed}, error={error}")
        else:
            logging.info(f"  结果: commit={commit_count}, lines={lines_changed}")

    # 写入输出文件
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for r in results:
            url = r.get("url", "")
            platform = r.get("platform", "unknown")
            commit_count = r.get("commit_count", -1)
            lines_changed = r.get("lines_changed", -1)
            error = r.get("error", "")
            commits = r.get("commits", "")
            f.write(f"{url}|{platform}|{commit_count}|{lines_changed}|{error}\n")
            # 在 PR 行后输出 commit 列表
            if commits:
                f.write(f"{commits}\n")

    logging.info(f"结果已保存到 {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PR 信息统计工具：统计 gitee/gitcode PR 的 commit 数量和代码行数变更",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input_file",
        metavar="INPUT",
        help="输入文件，每行一个 PR URL，忽略空行和 # 注释行",
    )
    parser.add_argument(
        "output_file",
        metavar="OUTPUT",
        help="输出文件，格式: url|platform|commit_count|lines_changed|error",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        metavar="SECONDS",
        help="HTTP 请求超时时间（默认 15 秒）",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="输出详细日志",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    return cmd_stats(args)


if __name__ == "__main__":
    sys.exit(main())