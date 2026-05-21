#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补丁管理工具。

提供三个子命令：
  check
      按 commit title 检查是否已合入目标分支，并按 describe 合入序输出报告。
  cherry-pick
      从补丁列表批量执行 cherry-pick，支持 --signoff、冲突暂停与续跑。
  sync-meta
      参考另一分支的同名提交，批量同步作者与作者时间。
"""

import argparse
import bisect
import json
import logging
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


def _decode_output(data: Optional[bytes]) -> str:
    """解码 git 命令输出。

    使用 errors='replace'，避免因非 UTF-8 字节导致解码异常。
    """
    return (data or b"").decode("utf-8", errors="replace").strip()


def run_git(args: List[str], capture: bool = True, check: bool = False, cwd: Optional[str] = None) -> Tuple[int, str, str]:
    """执行 git 命令并统一返回结果。

    返回值恒为 (returncode, stdout, stderr)。
    当 capture=False 时，stdout/stderr 为空字符串。
    """
    cmd = ["git"] + args
    try:
        r = subprocess.run(
            cmd,
            capture_output=capture,
            cwd=cwd or ".",
            check=check,
        )
        if capture:
            stdout = _decode_output(r.stdout)
            stderr = _decode_output(r.stderr)
            return (r.returncode, stdout, stderr)
        return (r.returncode, "", "")
    except FileNotFoundError:
        return (-1, "", "git not found")
    except subprocess.CalledProcessError as e:
        out = _decode_output(e.stdout)
        err = _decode_output(e.stderr)
        return (e.returncode, out, err)


def _has_filter_repo(cwd: Optional[str] = None) -> bool:
    """检查 git-filter-repo 是否可用。"""
    code, _, _ = run_git(["filter-repo", "--version"], cwd=cwd)
    return code == 0


def _resolve_backend(backend: str, cwd: Optional[str] = None) -> str:
    """将后端选择 'auto' 解析为具体后端名称。

    Returns:
        'filter-repo' 或 'filter-branch'

    Raises:
        RuntimeError: 当指定 'filter-repo' 但未安装时。
    """
    if backend == "filter-repo":
        if not _has_filter_repo(cwd):
            raise RuntimeError(
                "git-filter-repo 未安装。"
                "请通过 'pip install git-filter-repo' 安装，"
                "或使用 --backend filter-branch 回退到 git filter-branch。"
            )
        return "filter-repo"
    if backend == "filter-branch":
        return "filter-branch"
    return "filter-repo" if _has_filter_repo(cwd) else "filter-branch"


def _get_raw_author_dates(hashes: List[str], cwd: Optional[str] = None) -> Dict[str, str]:
    """批量获取提交的 raw 格式作者时间（'epoch +tz'）。"""
    if not hashes:
        return {}
    code, out, _ = run_git(
        ["log", "--no-walk", "--date=raw", "--format=%H%x01%ad"] + hashes,
        cwd=cwd,
    )
    result: Dict[str, str] = {}
    if code == 0:
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\x01", 1)
            if len(parts) == 2:
                result[parts[0].strip()] = parts[1].strip()
    return result


def _build_filter_repo_callback(
    commit_to_meta: Dict[str, Dict[str, str]],
    raw_dates: Dict[str, str],
) -> str:
    """构建 git filter-repo --commit-callback 所需的 Python 代码片段。

    生成一段按 original_id 精确匹配的回调脚本，用于替换作者元信息。
    """
    lines = ["m = {"]
    for commit_hash, meta in commit_to_meta.items():
        src_hash = meta["hash"]
        raw_ad = raw_dates.get(src_hash, meta["ad"])
        key = commit_hash.encode("utf-8")
        name = meta["an"].encode("utf-8")
        email = meta["ae"].encode("utf-8")
        date = raw_ad.encode("utf-8")
        lines.append(f"  {key!r}: ({name!r}, {email!r}, {date!r}),")
    lines.append("}")
    lines.append("if commit.original_id in m:")
    lines.append("  n, e, d = m[commit.original_id]")
    lines.append("  commit.author_name = n")
    lines.append("  commit.author_email = e")
    lines.append("  commit.author_date = d")
    return "\n".join(lines)


def _apply_filter_repo(
    commit_to_meta: Dict[str, Dict[str, str]],
    commit_range: str,
    cwd: str,
    capture: bool = True,
) -> Tuple[int, str]:
    """使用 git filter-repo 应用作者元数据变更。

    自动获取 raw 格式时间戳，构建 --commit-callback 脚本并执行。
    使用 --partial --force 以确保仅改写指定范围且允许在非 fresh clone 上运行。
    """
    src_hashes = [meta["hash"] for meta in commit_to_meta.values()]
    raw_dates = _get_raw_author_dates(src_hashes, cwd=cwd)
    callback = _build_filter_repo_callback(commit_to_meta, raw_dates)
    code, _, err = run_git(
        ["filter-repo", "--force", "--partial",
         "--refs", commit_range,
         "--commit-callback", callback],
        cwd=cwd,
        capture=capture,
    )
    return code, err


def _apply_filter_branch(
    commit_to_meta: Dict[str, Dict[str, str]],
    commit_range: str,
    cwd: str,
    capture: bool = True,
) -> Tuple[int, str]:
    """使用 git filter-branch 应用作者元数据变更。

    构建 shell case 语句作为 --env-filter，按 commit hash 精确覆写作者字段。
    """
    lines = ['case "$GIT_COMMIT" in']
    for commit, meta in commit_to_meta.items():
        lines.append(f"  {commit})")
        lines.append(f"    export GIT_AUTHOR_NAME={shlex.quote(meta['an'])}")
        lines.append(f"    export GIT_AUTHOR_EMAIL={shlex.quote(meta['ae'])}")
        lines.append(f"    export GIT_AUTHOR_DATE={shlex.quote(meta['ad'])}")
        lines.append("    ;;")
    lines.append("esac")
    env_filter = "\n".join(lines)
    code, _, err = run_git(
        ["filter-branch", "-f", "--env-filter", env_filter, commit_range],
        cwd=cwd,
        capture=capture,
    )
    return code, err


def get_branch_log_oneline(cwd: Optional[str] = None, long_hash: bool = False, branch: Optional[str] = None) -> Optional[List[str]]:
    """获取指定分支的非 merge 提交列表（每项一行）。

    - branch=None: 使用当前分支
    - long_hash=False: 使用 --oneline（短 hash）
    - long_hash=True: 使用 --format=%H %s（40 位 hash + title）
    """
    log_args = ["log", "--no-merges"]
    if branch:
        log_args.append(branch)
    if long_hash:
        log_args.append("--format=%H %s")
    else:
        log_args.append("--oneline")
    code, out, _ = run_git(log_args, cwd=cwd)
    if code != 0:
        return None
    return [line for line in out.splitlines() if line.strip()]


def parse_oneline_line(line: str) -> Tuple[Optional[str], str]:
    """解析 'hash title...' 形式的文本，返回 (hash, title)。"""
    parts = line.split(None, 1)
    if not parts:
        return None, ""
    return parts[0], (parts[1].strip() if len(parts) > 1 else "")


def is_valid_commit_hash(s: str) -> bool:
    """判断字符串是否为 7-40 位十六进制 commit hash。"""
    return bool(s and re.match(r"^[0-9a-f]{7,40}$", s.lower()))


_INPUT_HASH_TITLE_RE = re.compile(r"^([0-9a-fA-F]{7,40})\s+(.*\S.*)$")
_INPUT_HASH_ONLY_RE = re.compile(r"^[0-9a-fA-F]{10,40}$")


def parse_repo_prefixed_input_line(line: str, repo_names: Set[str]) -> Tuple[Optional[str], str]:
    """解析可选的 'repo<TAB>patch line' 输入前缀。"""
    stripped = line.strip()
    if "\t" not in stripped:
        return None, stripped
    maybe_repo, rest = stripped.split("\t", 1)
    maybe_repo = maybe_repo.strip()
    rest = rest.strip()
    if maybe_repo in repo_names and rest:
        return maybe_repo, rest
    return None, stripped


def parse_input_line(line: str) -> Tuple[Optional[str], str]:
    """解析输入行，自动识别 'hash title'、纯 hash 或纯 title 格式。

    支持的格式：
      "5a76550645be3 cpufreq: Fix ..."                → ('5a76550645be3', 'cpufreq: Fix ...')
      "5a76550645be3abcdef1234567890abcdef12345 ..."   → (40 位 hash, title)
      "5a76550645be3abcdef1234567890abcdef12345"       → (40 位 hash, '')
      "cpufreq: Fix re-boost issue"                    → (None, 'cpufreq: Fix re-boost issue')

    仅当行首 token 为 7-40 位纯十六进制且后跟非空文本时识别为 hash + title；
    纯 hash 行（无 title 部分）需 ≥10 位才识别为 hash，避免短十六进制串误判。
    """
    line = line.strip()
    m = _INPUT_HASH_TITLE_RE.match(line)
    if m:
        return m.group(1).lower(), m.group(2).strip()
    if _INPUT_HASH_ONLY_RE.fullmatch(line):
        return line.lower(), ""
    return None, line


def parse_check_output_line(line: str) -> Optional[Tuple[str, str, str]]:
    """尝试将行解析为 check 输出格式。

    兼容以下两种格式：
      - 旧格式: title|commit_id|Y/N|git_describe|commit_time
      - 新格式: title|commit_id|Y/N|git_describe|commit_time|lines_changed

    使用从右侧分割的策略，以正确处理 title 中可能包含 '|' 的情况。
    返回 (commit_hash, title, status)；若不是 check 格式则返回 None。
    """
    tokens = line.strip().split("|")
    for tail_fields in (3, 2):
        if len(tokens) < tail_fields + 3:
            continue
        status_idx = len(tokens) - tail_fields - 1
        status = tokens[status_idx].strip()
        if status not in ("Y", "N"):
            continue
        commit_id = tokens[status_idx - 1].strip()
        title = "|".join(tokens[:status_idx - 1]).strip()
        return (commit_id, title, status)
    return None


def _natural_sort_key(text: str) -> Tuple[Tuple[int, Any], ...]:
    """为字符串生成自然排序 key（例如 v6.6 < v6.10）。"""
    key: List[Tuple[int, Any]] = []
    for part in re.split(r"(\d+)", text.lower()):
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


def parse_describe_order(describe: str) -> Optional[Tuple[Tuple[Tuple[int, Any], ...], int]]:
    """解析 git describe，提取用于排序的 (tag_key, distance)。

    - 形如 '<tag>-<distance>-g<hash>' 时，distance 表示相对 tag 的提交距离。
    - 形如 '<tag>'（正好命中 tag）时，distance 视为 0。
    - 若 describe 仅为 hash（无 tag）或为空，返回 None。
    """
    if not describe:
        return None

    desc = describe.strip()
    m = re.match(r"^(.+)-(\d+)-g[0-9a-f]+(?:-.+)?$", desc.lower())
    if m:
        tag = m.group(1)
        distance = int(m.group(2))
        return (_natural_sort_key(tag), distance)

    # --always 在无 tag 场景下可能返回短 hash；此时无法从 describe 推导顺序。
    if re.fullmatch(r"[0-9a-f]{7,40}", desc.lower()):
        return None

    # 命中 tag（无 -N-gHASH 后缀）
    return (_natural_sort_key(desc), 0)


def get_batch_commit_info(commit_ids: List[str], cwd: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """批量查询多个提交的元信息。

    返回格式：
      {
        commit_id: {
          "describe": str,
          "timestamp": int,
          "commit_time": str,
          "lines_changed": int | None
        }
      }
    """
    if not commit_ids:
        return {}

    result: Dict[str, Dict[str, Any]] = {
        cid: {
            "describe": "",
            "timestamp": 0,
            "commit_time": "",
            "lines_changed": None,
        }
        for cid in commit_ids
    }

    def key_for_full_hash(full_hash: str) -> Optional[str]:
        """将 40 位 full hash 映射回 result 的 key（key 可能是短 hash）。"""
        if full_hash in result:
            return full_hash
        for cid in commit_ids:
            if len(cid) < 40 and full_hash.startswith(cid):
                return cid
        return None

    code, out, _ = run_git(
        ["log", "--no-walk", "--format=%H%n%ct%n%ci"] + commit_ids,
        cwd=cwd,
    )
    if code == 0:
        current_key: Optional[str] = None
        line_index = 0
        skip_next = 0
        for line in out.splitlines():
            if not line.strip():
                continue
            line = line.strip()
            if skip_next > 0:
                skip_next -= 1
                continue
            if current_key is None:
                current_key = key_for_full_hash(line)
                line_index = 0
                if current_key is not None:
                    result[current_key]["timestamp"] = 0
                    result[current_key]["commit_time"] = ""
                else:
                    # 当前 full hash 不在请求列表中：跳过后续两行（ct/ci）。
                    skip_next = 2
            else:
                line_index += 1
                if current_key in result:
                    if line_index == 1:
                        try:
                            result[current_key]["timestamp"] = int(line)
                        except ValueError:
                            result[current_key]["timestamp"] = 0
                    elif line_index == 2:
                        result[current_key]["commit_time"] = line
                if line_index == 2:
                    current_key = None

    code, out, _ = run_git(
        ["describe", "--tags", "--always"] + commit_ids,
        cwd=cwd,
    )
    if code == 0:
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        for cid, describe_line in zip(commit_ids, lines):
            if cid in result and describe_line:
                result[cid]["describe"] = describe_line

    code, out, _ = run_git(
        ["log", "--no-walk", "--format=%H", "--numstat"] + commit_ids,
        cwd=cwd,
    )
    if code == 0:
        current_key: Optional[str] = None
        for line in out.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            mapped_key = key_for_full_hash(stripped)
            if mapped_key is not None:
                current_key = mapped_key
                result[current_key]["lines_changed"] = 0
                continue

            if current_key is None:
                continue

            parts = line.split("\t", 2)
            if len(parts) < 3:
                continue
            added, deleted = parts[0].strip(), parts[1].strip()
            if added.isdigit() and deleted.isdigit():
                result[current_key]["lines_changed"] += int(added) + int(deleted)

    return result


# --- 核心 API（供 MCP / 外部调用） ---


def load_check_repos_file(path: str) -> List[Dict[str, Any]]:
    """读取 check 多仓库 JSON 配置文件。"""
    p = Path(path)
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("repos-file 必须是 JSON 数组")
    repos: List[Dict[str, Any]] = []
    for i, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"repos-file 第 {i} 项必须是对象")
        name = str(item.get("name", "")).strip()
        repo_path = str(item.get("path", "")).strip()
        branch_value = item.get("branch")
        branch = None if branch_value is None else str(branch_value).strip()
        if not name:
            raise ValueError(f"repos-file 第 {i} 项缺少 name")
        if not repo_path:
            raise ValueError(f"repos-file 第 {i} 项缺少 path")
        repos.append({"name": name, "path": repo_path, "branch": branch or None})
    return repos


def _normalize_check_repos(
    repo: str,
    branch: Optional[str],
    repos: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Optional[str]]]:
    """归一化 check 仓库配置，单仓模式也转成统一结构。"""
    if repos is None:
        return [{"name": "default", "path": repo, "branch": branch}]

    normalized: List[Dict[str, Optional[str]]] = []
    seen: Set[str] = set()
    for i, item in enumerate(repos, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"repos 第 {i} 项必须是对象")
        name = str(item.get("name", "")).strip()
        path_value = str(item.get("path", "")).strip()
        branch_value = item.get("branch")
        if branch_value is None or str(branch_value).strip() == "":
            branch_value = branch
        repo_branch = None if branch_value is None else str(branch_value).strip()
        if not name:
            raise ValueError(f"repos 第 {i} 项缺少 name")
        if name in seen:
            raise ValueError(f"repos 中存在重复 name: {name}")
        if not path_value:
            raise ValueError(f"repos 第 {i} 项缺少 path")
        seen.add(name)
        normalized.append({
            "name": name,
            "path": path_value,
            "branch": repo_branch or None,
        })
    if not normalized:
        raise ValueError("repos 不能为空")
    return normalized


def _parse_check_input_entries(
    input_lines: List[str],
    repo_names: Set[str],
) -> List[Dict[str, Optional[str]]]:
    """解析 check 输入，支持可选 repo<TAB> 前缀。"""
    entries: List[Dict[str, Optional[str]]] = []
    for line in input_lines:
        t = line.strip()
        if not t:
            continue
        repo_name, payload = parse_repo_prefixed_input_line(t, repo_names)
        src_hash, title = parse_input_line(payload)
        if title or src_hash:
            entries.append({"repo": repo_name, "hash": src_hash, "title": title})
    return entries


def _load_repo_check_index(
    repo_spec: Dict[str, Optional[str]],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, str]]]:
    """读取单个仓库的日志索引。"""
    repo_name = repo_spec["name"] or ""
    repo_path = repo_spec["path"] or "."
    branch = repo_spec.get("branch")
    log_args = ["log", "--no-merges"]
    if branch:
        log_args.append(branch)
    log_args.append("--format=%H%x01%h%x01%s")

    code, log_out, err = run_git(log_args, cwd=repo_path)
    if code != 0:
        return None, {
            "repo": repo_name,
            "path": repo_path,
            "branch": branch or "",
            "error": err or "无法获取 git log，请确保在 git 仓库中且分支/引用有效",
        }

    title_index: Dict[str, List[Tuple[str, str, str]]] = {}
    hash_entries: List[Tuple[str, str, str]] = []
    full_hashes: List[str] = []
    for ln in log_out.splitlines():
        parts = ln.split("\x01", 2)
        if len(parts) != 3:
            continue
        full_h, short_h, title = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not title:
            continue
        candidate = (full_h, short_h, title)
        title_index.setdefault(title, []).append(candidate)
        hash_entries.append(candidate)
        full_hashes.append(full_h)

    hash_entries.sort(key=lambda item: item[0])
    full_hashes = [item[0] for item in hash_entries]
    return {
        "name": repo_name,
        "path": repo_path,
        "branch": branch,
        "title_index": title_index,
        "hash_entries": hash_entries,
        "full_hashes": full_hashes,
    }, None


def _find_hash_matches(index: Dict[str, Any], src_hash: str) -> List[Tuple[str, str, str]]:
    """在单个仓库索引中按 hash 前缀查找。"""
    full_hashes: List[str] = index["full_hashes"]
    hash_entries: List[Tuple[str, str, str]] = index["hash_entries"]
    pos = bisect.bisect_left(full_hashes, src_hash)
    matches: List[Tuple[str, str, str]] = []
    while pos < len(full_hashes) and full_hashes[pos].startswith(src_hash):
        matches.append(hash_entries[pos])
        pos += 1
    return matches


def _entry_matches_repo(
    entry: Dict[str, Optional[str]],
    index: Dict[str, Any],
    long_hash: bool,
) -> List[Dict[str, Any]]:
    """返回某条输入在单个仓库中的所有候选命中。"""
    title = entry.get("title") or ""
    src_hash = entry.get("hash")
    method = ""
    matches: List[Tuple[str, str, str]] = []
    if title and title in index["title_index"]:
        method = "title"
        matches = list(index["title_index"][title])
    elif src_hash:
        method = "hash"
        matches = _find_hash_matches(index, src_hash)

    result: List[Dict[str, Any]] = []
    for full_h, short_h, matched_title in matches:
        result.append({
            "repo": index["name"],
            "repo_path": index["path"],
            "branch": index["branch"] or "",
            "commit_id": full_h if long_hash else short_h,
            "full_hash": full_h,
            "matched_title": matched_title,
            "match_method": method,
        })
    return result


def _sort_check_rows(raw: List[Tuple[int, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """按 describe 合入序排序 check 结果。"""
    def _sk(pair: Tuple[int, Dict[str, Any]]) -> Tuple[int, Any, int, int, str]:
        ts, r = pair
        if r["status"] != "Y":
            return (2, (), 0, ts, r["title"])
        p = parse_describe_order(r["git_describe"])
        if p:
            return (0, p[0], p[1], ts, r["title"])
        return (1, (), 0, ts, r["title"])

    raw.sort(key=_sk)
    return [r for _, r in raw]


def check_commits(
    input_lines: List[str],
    repo: str = ".",
    branch: Optional[str] = None,
    long_hash: bool = False,
    repos: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """检查提交列表是否已合入目标分支，返回结构化结果。

    Args:
        input_lines: 待检查的提交列表，每项支持 'title' / 'hash title' / 'hash' 格式。
        repo: git 仓库路径。
        branch: 目标分支（None 表示当前分支）。
        long_hash: 结果使用 40 位完整 hash。
        repos: 多仓库配置；每项包含 name/path/branch。

    Returns:
        {"total", "matched", "unmatched", "results": [{...}]}
    """
    try:
        repo_specs = _normalize_check_repos(repo, branch, repos)
    except ValueError as e:
        return {
            "error": str(e),
            "total": 0, "matched": 0, "unmatched": 0, "results": [],
        }

    multi_repo = repos is not None
    repo_names = {r["name"] or "" for r in repo_specs} if multi_repo else set()
    input_entries = _parse_check_input_entries(input_lines, repo_names)

    indexes: List[Dict[str, Any]] = []
    repo_errors: List[Dict[str, str]] = []
    for repo_spec in repo_specs:
        index, error = _load_repo_check_index(repo_spec)
        if error:
            repo_errors.append(error)
            continue
        if index:
            indexes.append(index)

    if not indexes and repo_errors:
        result: Dict[str, Any] = {
            "error": "无法获取任何 git log，请确保仓库路径和分支/引用有效",
            "total": 0, "matched": 0, "unmatched": 0, "results": [],
        }
        if multi_repo:
            result["repo_errors"] = repo_errors
        return result

    per_entry: List[Optional[Dict[str, Any]]] = []
    per_entry_alternates: List[List[Dict[str, Any]]] = []
    matched_by_repo: Dict[str, List[str]] = {}
    repo_path_by_name: Dict[str, str] = {idx["name"]: idx["path"] for idx in indexes}
    n_title_match = 0
    n_hash_match = 0

    for entry in input_entries:
        requested_repo = entry.get("repo")
        candidates: List[Dict[str, Any]] = []
        for index in indexes:
            if requested_repo and index["name"] != requested_repo:
                continue
            candidates.extend(_entry_matches_repo(entry, index, long_hash))

        selected = candidates[0] if candidates else None
        per_entry.append(selected)
        per_entry_alternates.append(candidates[1:] if len(candidates) > 1 else [])
        if selected:
            matched_by_repo.setdefault(selected["repo"], []).append(selected["full_hash"])
            if selected["match_method"] == "title":
                n_title_match += 1
            else:
                n_hash_match += 1

    info_by_repo: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for repo_name, full_hashes in matched_by_repo.items():
        unique_hashes = list(dict.fromkeys(full_hashes))
        info_by_repo[repo_name] = get_batch_commit_info(
            unique_hashes, cwd=repo_path_by_name[repo_name],
        )

    raw: List[Tuple[int, Dict[str, Any]]] = []
    n_matched = 0
    for i, entry in enumerate(input_entries):
        m = per_entry[i]
        src_hash = entry.get("hash") or ""
        title = entry.get("title") or ""
        display = title if title else (m["matched_title"] if m else src_hash)
        if m:
            display = display or m["matched_title"]
            n_matched += 1
            ci = info_by_repo.get(m["repo"], {}).get(m["full_hash"], {})
            ts = ci.get("timestamp", 0)
            row = {
                "title": display, "commit_id": m["commit_id"], "status": "Y",
                "git_describe": ci.get("describe", ""),
                "commit_time": ci.get("commit_time", ""),
                "lines_changed": ci.get("lines_changed"),
                "match_method": m["match_method"],
            }
            if multi_repo:
                row.update({
                    "repo": m["repo"],
                    "repo_path": m["repo_path"],
                    "branch": m["branch"],
                    "alternate_matches": per_entry_alternates[i],
                })
            raw.append((ts, row))
        else:
            row = {
                "title": display, "commit_id": "", "status": "N",
                "git_describe": "", "commit_time": "", "lines_changed": None,
                "match_method": "",
            }
            if multi_repo:
                row.update({
                    "repo": entry.get("repo") or "",
                    "repo_path": "",
                    "branch": "",
                    "alternate_matches": [],
                })
            raw.append((9999999999, row))

    result = {
        "total": len(input_entries),
        "matched": n_matched,
        "unmatched": len(input_entries) - n_matched,
        "results": _sort_check_rows(raw),
    }
    if n_title_match or n_hash_match:
        result["title_matched"] = n_title_match
        result["hash_matched"] = n_hash_match
    if multi_repo:
        result["repo_errors"] = repo_errors
    return result


def cherry_pick_commits(
    input_lines: List[str],
    repo: str = ".",
    signoff: bool = True,
    start: int = 1,
) -> Dict[str, Any]:
    """批量 cherry-pick（非交互模式，遇冲突自动 abort 并停止）。

    Args:
        input_lines: 补丁列表，每项支持 'hash [title]' 或 check 输出格式。
        repo: git 仓库路径。
        signoff: 是否使用 --signoff。
        start: 从第几个有效提交开始（1-based）。

    Returns:
        {"total_valid", "processed", "succeeded", "skipped_invalid",
         "skipped_not_merged", "conflict", "results": [{...}]}
    """
    entries: List[Tuple[str, str]] = []
    skipped_not_merged = 0
    for line in input_lines:
        line = line.strip()
        if not line:
            continue
        cp = parse_check_output_line(line)
        if cp is not None:
            cid, title, st = cp
            if st == "N":
                skipped_not_merged += 1
                continue
            entries.append((cid, title))
        else:
            h, rest = parse_oneline_line(line)
            entries.append((h or "", rest))

    total = sum(1 for h, _ in entries if is_valid_commit_hash(h))
    start = max(1, start)
    signoff_args: List[str] = ["--signoff"] if signoff else []

    results: List[Dict[str, str]] = []
    processed = 0
    succeeded = 0
    skipped_invalid = 0
    conflict: Optional[Dict[str, str]] = None

    for h, rest in entries:
        if not is_valid_commit_hash(h):
            skipped_invalid += 1
            results.append({"hash": h, "title": rest, "status": "SKIP"})
            continue

        processed += 1
        if processed < start:
            results.append({"hash": h, "title": rest, "status": "SKIP"})
            continue

        code, stdout, stderr = run_git(
            ["cherry-pick"] + signoff_args + [h], cwd=repo,
        )
        if code == 0:
            succeeded += 1
            results.append({"hash": h, "title": rest, "status": "OK"})
        else:
            run_git(["cherry-pick", "--abort"], cwd=repo)
            conflict = {"hash": h, "title": rest, "message": stderr or stdout}
            results.append({"hash": h, "title": rest, "status": "CONFLICT"})
            break

    return {
        "total_valid": total,
        "processed": processed,
        "succeeded": succeeded,
        "skipped_invalid": skipped_invalid,
        "skipped_not_merged": skipped_not_merged,
        "conflict": conflict,
        "results": results,
    }


def sync_meta_commits(
    source_branch: str,
    commit_range: str,
    repo: str = ".",
    dry_run: bool = True,
    backend: str = "auto",
) -> Dict[str, Any]:
    """根据参考分支同名提交同步作者/时间元数据。

    Args:
        source_branch: 参考分支名/引用。
        commit_range: 当前分支提交范围，如 'base..HEAD'。
        repo: git 仓库路径。
        dry_run: 若 True 只返回预览不实际改写。
        backend: 历史改写后端，'auto'（默认）、'filter-repo' 或 'filter-branch'。

    Returns:
        {"total_in_range", "matched", "skipped_not_found", "skipped_ambiguous",
         "changes": [{...}], "applied": bool, "backend": str|None}
    """
    code, out, err = run_git(
        ["log", "--no-merges",
         "--format=%H%x01%s%x01%an%x01%ae%x01%ad", source_branch],
        cwd=repo,
    )
    if code != 0:
        return {"error": f"无法获取参考分支 {source_branch} 的 log: {err}",
                "total_in_range": 0, "matched": 0, "skipped_not_found": 0,
                "skipped_ambiguous": 0, "changes": [], "applied": False}

    title_to_src: Dict[str, List[Dict[str, str]]] = {}
    for ln in out.splitlines():
        if not ln.strip():
            continue
        parts = ln.split("\x01")
        if len(parts) != 5:
            continue
        sh, t, an, ae, ad = parts
        t = t.strip()
        if not t:
            continue
        title_to_src.setdefault(t, []).append(
            {"hash": sh, "an": an, "ae": ae, "ad": ad}
        )

    code, out, err = run_git(
        ["log", "--no-merges", "--format=%H%x01%s", commit_range], cwd=repo,
    )
    if code != 0:
        return {"error": f"无法获取提交范围 {commit_range} 的 log: {err}",
                "total_in_range": 0, "matched": 0, "skipped_not_found": 0,
                "skipped_ambiguous": 0, "changes": [], "applied": False}

    commits_in_range: List[Tuple[str, str]] = []
    for ln in out.splitlines():
        if not ln.strip():
            continue
        h, t = ln.split("\x01", 1)
        commits_in_range.append((h.strip(), t.strip()))

    changes: List[Dict[str, str]] = []
    commit_to_meta: Dict[str, Dict[str, str]] = {}
    skipped_nf = 0
    skipped_amb = 0

    for h, title in commits_in_range:
        src_list = title_to_src.get(title)
        if not src_list:
            skipped_nf += 1
            continue
        if len(src_list) > 1:
            skipped_amb += 1
            continue
        src = src_list[0]

        code2, cur, _ = run_git(
            ["log", "-1", "--format=%an%x01%ae%x01%ad", h], cwd=repo,
        )
        old_an = old_ae = old_ad = ""
        if code2 == 0 and cur:
            p = cur.split("\x01")
            if len(p) == 3:
                old_an, old_ae, old_ad = p

        changes.append({
            "hash": h, "title": title,
            "old_author": f"{old_an} <{old_ae}>",
            "new_author": f"{src['an']} <{src['ae']}>",
            "old_date": old_ad, "new_date": src["ad"],
        })
        commit_to_meta[h] = src

    applied = False
    error = None
    resolved_backend = None
    if commit_to_meta and not dry_run:
        try:
            resolved_backend = _resolve_backend(backend, cwd=repo)
        except RuntimeError as e:
            error = str(e)

        if resolved_backend == "filter-repo":
            code, serr = _apply_filter_repo(commit_to_meta, commit_range, repo)
            if code != 0:
                error = f"git filter-repo 执行失败: {serr}"
            else:
                applied = True
        elif resolved_backend == "filter-branch":
            code, serr = _apply_filter_branch(commit_to_meta, commit_range, repo)
            if code != 0:
                error = f"git filter-branch 执行失败: {serr}"
            else:
                applied = True

    result: Dict[str, Any] = {
        "total_in_range": len(commits_in_range),
        "matched": len(changes),
        "skipped_not_found": skipped_nf,
        "skipped_ambiguous": skipped_amb,
        "changes": changes,
        "applied": applied,
        "backend": resolved_backend,
    }
    if error:
        result["error"] = error
    return result


# --- check 子命令 ---


def cmd_check(args: argparse.Namespace) -> int:
    """检查输入文件中的 commit 是否已合入目标分支并排序输出。

    输入文件每行支持以下格式：
      - 纯 title:    "cpufreq: Fix re-boost issue after hotplugging a CPU"
      - hash + title: "5a76550645be3 cpufreq: Fix re-boost issue after hotplugging a CPU"
      - 纯 hash:      "5a76550645be3" (≥10 位十六进制)
      - 多仓模式可选仓库前缀: "repo_name<TAB>title"

    匹配策略：优先按 title 精确匹配；若 title 未命中且输入行携带 hash，
    则以该 hash 作为前缀在目标分支日志中查找同一提交。
    """
    inp = Path(args.input_file)
    out = Path(args.output_file)

    if not inp.is_file():
        logging.error(f"输入文件不存在 '{inp}'")
        return 1

    repos_config: Optional[List[Dict[str, Any]]] = None
    if args.repos_file:
        try:
            repos_config = load_check_repos_file(args.repos_file)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logging.error(f"无法读取多仓库配置 '{args.repos_file}': {e}")
            return 1

    repo_names = {str(r["name"]) for r in repos_config} if repos_config else set()
    input_lines: List[str] = []
    with open(inp, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            t = line.strip()
            if not t:
                continue
            input_lines.append(t)

    input_entries = _parse_check_input_entries(input_lines, repo_names)

    n_with_hash = sum(1 for entry in input_entries if entry.get("hash"))
    n_pure_title = len(input_entries) - n_with_hash
    n_with_repo = sum(1 for entry in input_entries if entry.get("repo"))
    logging.info(
        f"读取 {len(input_entries)} 条输入（含 hash: {n_with_hash}, "
        f"纯 title: {n_pure_title}, 指定仓库: {n_with_repo}）"
    )

    repo = args.repo or "."
    if repos_config:
        logging.info(f"正在获取 {len(repos_config)} 个候选仓库的 git log（排除 merge commit）...")
    else:
        branch_desc = f"分支 '{args.branch}'" if args.branch else "当前分支"
        logging.info(f"正在获取{branch_desc}的 git log（排除 merge commit）...")

    result = check_commits(
        input_lines,
        repo=repo,
        branch=args.branch,
        long_hash=args.long_hash,
        repos=repos_config,
    )

    for repo_error in result.get("repo_errors", []):
        logging.warning(
            "跳过仓库 {repo} ({path}) branch={branch}: {error}".format(
                repo=repo_error.get("repo", ""),
                path=repo_error.get("path", ""),
                branch=repo_error.get("branch", "") or "<current>",
                error=repo_error.get("error", ""),
            )
        )

    if result.get("error"):
        logging.error(str(result["error"]))
        return 1

    n_title_match = int(result.get("title_matched", 0))
    n_hash_match = int(result.get("hash_matched", 0))
    if n_title_match or n_hash_match:
        logging.info(
            f"匹配统计: title 命中 {n_title_match}, hash 命中 {n_hash_match}, "
            f"未命中 {result.get('unmatched', 0)}"
        )

    results = result.get("results", [])
    for row in results:
        title = row.get("title", "")
        commit_id = row.get("commit_id", "")
        git_describe = row.get("git_describe", "")
        commit_time = row.get("commit_time", "")
        lines_changed = row.get("lines_changed")
        lines_changed_text = "" if lines_changed is None else str(lines_changed)
        repo_prefix = f"{row.get('repo')}:" if row.get("repo") else ""
        if row.get("status") == "Y":
            match_method = row.get("match_method", "")
            alt_count = len(row.get("alternate_matches", []) or [])
            alt_note = f", alternate={alt_count}" if alt_count else ""
            if git_describe:
                logging.info(
                    f"  ✓ {repo_prefix}[{match_method}] {commit_id} | "
                    f"{git_describe} | {commit_time} | lines={lines_changed_text}{alt_note}"
                )
            else:
                logging.info(
                    f"  ✓ {repo_prefix}[{match_method}] {commit_id} | "
                    f"{commit_time} | lines={lines_changed_text} (无 describe){alt_note}"
                )
        else:
            logging.info(f"  ✗ 未找到: {title}")

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for row in results:
            title = row.get("title", "")
            commit_id = row.get("commit_id", "")
            status = row.get("status", "")
            git_describe = row.get("git_describe", "")
            commit_time = row.get("commit_time", "")
            lines_changed = row.get("lines_changed")
            lines_changed_text = "" if lines_changed is None else str(lines_changed)
            if args.include_repo:
                f.write(
                    f"{title}|{row.get('repo', '')}|{commit_id}|{status}|"
                    f"{git_describe}|{commit_time}|{lines_changed_text}\n"
                )
            else:
                f.write(
                    f"{title}|{commit_id}|{status}|{git_describe}|"
                    f"{commit_time}|{lines_changed_text}\n"
                )

    logging.info(f"检查完成，结果已保存到 {out}（优先按 describe 合入序排序）")
    return 0


# --- cherry-pick 子命令 ---


def cmd_cherry_pick(args: argparse.Namespace) -> int:
    """从补丁列表文件批量执行 cherry-pick。

    输入文件同时兼容以下格式（可混合使用）：
      - hash [title]              — 标准补丁列表
      - check 输出行              — title|commit_id|Y/N|describe|time
      - check 新输出行            — title|commit_id|Y/N|describe|time|lines_changed
    check 输出中 status=N 的行会被自动跳过。
    """
    patch_file = Path(args.patch_file)
    if not patch_file.is_file():
        logging.error(f"补丁文件不存在 '{patch_file}'")
        return 1

    entries: List[Tuple[str, str]] = []
    skipped_not_merged = 0
    with open(patch_file, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            check_parsed = parse_check_output_line(line)
            if check_parsed is not None:
                commit_id, title, status = check_parsed
                if status == "N":
                    skipped_not_merged += 1
                    logging.debug(f"跳过 check 输出 status=N: {title}")
                    continue
                entries.append((commit_id, title))
            else:
                h, rest = parse_oneline_line(line)
                entries.append((h, rest))

    if skipped_not_merged:
        logging.info(f"已跳过 {skipped_not_merged} 条 check 输出中 status=N 的记录")

    # total 仅统计合法 hash，用于统一进度口径。
    valid = [(h, rest) for h, rest in entries if is_valid_commit_hash(h)]
    total = len(valid)
    try:
        start = max(1, int(args.start))
    except ValueError:
        logging.error(f"起始序号须为正整数: {args.start}")
        return 1

    if args.log_file:
        Path(args.log_file).write_text("", encoding="utf-8")

    def log_result(status: str, hash_val: str, rest: str) -> None:
        if args.log_file:
            with open(args.log_file, "a", encoding="utf-8") as lf:
                lf.write(f"{status}|{hash_val}|{rest}\n")

    repo = args.repo or "."
    signoff: List[str] = [] if args.no_signoff else ["--signoff"]

    logging.info(f"补丁文件: {patch_file}")
    logging.info(f"有效提交数: {total}")
    if not args.no_signoff:
        logging.info("使用: git cherry-pick --signoff")
    if args.log_file:
        logging.info(f"结果日志: {args.log_file}")
    if start > 1:
        logging.info(f"起始序号: {start}")
    logging.info("==========================================")

    # processed 仅统计“合法 hash”项，与 total 保持一致。
    processed = 0
    skipped_invalid = 0
    line_number = 0

    for raw_line in entries:
        line_number += 1
        h, rest = raw_line
        if not is_valid_commit_hash(h):
            logging.info(f"跳过第 {line_number} 行: {h} {rest} (无效的 commit hash)")
            skipped_invalid += 1
            log_result("SKIP", h, rest)
            continue

        processed += 1
        if processed < start:
            logging.info(f"跳过 {processed}/{total}: {h} (未到起始序号)")
            continue

        logging.info("")
        logging.info(f"处理 {processed}/{total}: {h}")
        if rest:
            logging.info(f"描述: {rest}")
        logging.info("==========================================")

        code, _, _ = run_git(
            ["cherry-pick"] + signoff + [h],
            cwd=repo,
            capture=False,
        )

        if code == 0:
            logging.info(f"✓ 已 cherry-pick: {h}")
            log_result("OK", h, rest)
        else:
            logging.info(f"✗ 冲突: {h}")
            logging.info("")
            # 冲突处理策略：人工解决后回车，脚本继续执行 --continue。
            logging.info("请解决冲突后按 Enter 继续（将执行 git cherry-pick --continue）")
            logging.info(f"当前进度: {processed}/{total}")
            try:
                input("按 Enter 继续...")
            except (EOFError, KeyboardInterrupt):
                log_result("FAIL", h, rest)
                return 1

            code2, _, _ = run_git(["cherry-pick", "--continue"], cwd=repo)
            if code2 == 0:
                logging.info(f"✓ 冲突已解决并继续: {h}")
                log_result("OK", h, rest)
            else:
                logging.info(f"✗ cherry-pick --continue 失败: {h}")
                log_result("FAIL", h, rest)
                logging.info(f"进度: {processed}/{total}")
                return 1
        logging.info("==========================================")

    session = max(0, processed - start + 1)
    logging.info("")
    logging.info("✓ 本轮完成")
    logging.info(f"  本轮处理: {session} 个提交")
    logging.info(f"  跳过无效行: {skipped_invalid}")
    logging.info(f"  累计进度: {processed}/{total}")
    if args.log_file:
        logging.info(f"  结果已写入: {args.log_file}")
    logging.info("")
    logging.info("可用 'git log --oneline' 检查或推送。")
    return 0


# --- sync-meta 子命令 ---


def cmd_sync_meta(args):
    """
    用参考分支上“同名提交”的作者信息，刷新当前分支指定范围内的提交元数据。

    仅更新作者相关字段（name/email/date），不修改提交者信息。

    典型用法（在当前分支上）:
      patch_tool.py sync-meta OLK-6.6 base_commit..HEAD
    """
    repo = args.repo or "."
    src_branch = args.source_branch
    commit_range = args.range

    print(f"参考分支: {src_branch}")
    print(f"当前分支提交范围: {commit_range}")

    # 1) 从参考分支构建 title -> [meta...] 映射（保留列表用于检测同名歧义）。
    code, out, err = run_git(
        [
            "log",
            "--no-merges",
            "--format=%H%x01%s%x01%an%x01%ae%x01%ad%x01%cn%x01%ce%x01%cd",
            src_branch,
        ],
        cwd=repo,
    )
    if code != 0:
        print(f"错误: 无法获取参考分支 {src_branch} 的 log: {err}", file=sys.stderr)
        return 1

    title_to_src = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\x01")
        if len(parts) != 8:
            continue
        (
            src_hash,
            title,
            an,
            ae,
            ad,
            cn,
            ce,
            cd,
        ) = parts
        title = title.strip()
        if not title:
            continue
        title_to_src.setdefault(title, []).append(
            {
                "hash": src_hash,
                "an": an,
                "ae": ae,
                "ad": ad,
                "cn": cn,
                "ce": ce,
                "cd": cd,
            }
        )

    if not title_to_src:
        print(f"警告: 在参考分支 {src_branch} 上未找到任何非 merge 提交")

    # 2) 枚举当前分支给定范围内的非 merge 提交。
    code, out, err = run_git(
        [
            "log",
            "--no-merges",
            "--format=%H%x01%s",
            commit_range,
        ],
        cwd=repo,
    )
    if code != 0:
        print(f"错误: 无法获取当前分支范围 {commit_range} 的 log: {err}", file=sys.stderr)
        return 1

    commits_in_range = []
    for line in out.splitlines():
        if not line.strip():
            continue
        h, title = line.split("\x01", 1)
        title = title.strip()
        commits_in_range.append((h, title))

    if not commits_in_range:
        print(f"范围 {commit_range} 内没有非 merge 提交，退出。")
        return 0

    # 3) 逐条匹配同名提交，生成“当前提交 -> 目标作者元信息”的映射。
    commit_to_meta = {}
    print("")
    print("将要检查的提交（当前分支）:")
    for h, title in commits_in_range:
        print(f"- {h[:12]} {title}")
        src_list = title_to_src.get(title)
        if not src_list:
            print(f"  ✗ 未在参考分支 {src_branch} 上找到同名提交，跳过。")
            continue
        if len(src_list) > 1:
            # 为避免误改，默认要求参考分支中的同名提交唯一。
            print(
                f"  ✗ 在参考分支 {src_branch} 上找到 {len(src_list)} 个同名提交，出于安全考虑跳过。"
            )
            for src in src_list[:3]:
                print(f"    - {src['hash'][:12]}")
            if len(src_list) > 3:
                print("    ...")
            continue

        src = src_list[0]

        # 读取当前提交现有作者信息，仅用于展示“变更前 -> 变更后”。
        code2, cur_meta_out, _ = run_git(
            ["log", "-1", "--format=%an%x01%ae%x01%ad%x01%cn%x01%ce%x01%cd", h],
            cwd=repo,
        )
        if code2 == 0 and cur_meta_out:
            can, cae, cad, ccn, cce, ccd = cur_meta_out.split("\x01")
        else:
            can = cae = cad = ccn = cce = ccd = ""

        print("  ✓ 匹配到参考分支提交:")
        print(f"    源: {src['hash'][:12]} {title}")
        print(
            f"    作者: {can} <{cae}>  ->  {src['an']} <{src['ae']}>"
        )
        print(
            f"    作者时间: {cad}  ->  {src['ad']}"
        )

        commit_to_meta[h] = src

    if not commit_to_meta:
        print("")
        print("没有任何提交需要更新，退出。")
        return 0

    print("")
    print("上述提交的作者/作者时间将被更新为参考分支上的对应值（提交者及提交时间保持不变）。")
    if args.dry_run:
        print("当前为 --dry-run 模式，不会真正改写 git 历史。")
        return 0

    # 4) 解析后端并执行历史改写。
    try:
        resolved = _resolve_backend(args.backend, cwd=repo)
    except RuntimeError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    print("")
    print(f"即将通过 'git {resolved}' 改写历史。")
    print("注意: 这会重写当前分支历史，后续需要使用 push --force 推送到远端（如有）。")

    print("")
    print(f"开始执行 git {resolved} ...")
    if resolved == "filter-repo":
        code, err = _apply_filter_repo(
            commit_to_meta, commit_range, repo, capture=False,
        )
    else:
        code, err = _apply_filter_branch(
            commit_to_meta, commit_range, repo, capture=False,
        )

    if code != 0:
        print(f"错误: git {resolved} 执行失败。", file=sys.stderr)
        return 1

    print("")
    print("✓ 作者/时间已根据参考分支刷新完成。")
    print("请使用 'git log' 检查结果，如需推送远端请使用 'git push --force'.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="补丁管理工具：检查提交合入状态 + 批量 Cherry-Pick + 提交元信息同步",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "-C", "--repo",
        default=None,
        metavar="PATH",
        help="git 仓库路径（默认当前目录）",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="输出详细日志",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # check 子命令参数
    p_check = sub.add_parser(
        "check",
        help="检查 commit 是否已合入目标分支（支持 title / hash+title / hash 输入）",
    )
    p_check.add_argument(
        "input_file", metavar="INPUT",
        help="输入文件，每行格式: 'title' 或 'hash title' 或 'hash'（≥10 位 hex）",
    )
    p_check.add_argument(
        "output_file",
        metavar="OUTPUT",
        help=(
            "输出文件，格式: title|commit_id|status|git_describe|commit_time|"
            "lines_changed；--include-repo 时为 title|repo|commit_id|status|"
            "git_describe|commit_time|lines_changed"
        ),
    )
    p_check.add_argument("-b", "--branch", default=None, metavar="BRANCH", help="在指定分支上检查（默认当前分支，可为分支名或 commit/tag 等引用）")
    p_check.add_argument("-l", "--long-hash", action="store_true", help="输出使用 40 位完整 commit hash（默认使用短 hash）")
    p_check.add_argument(
        "--repos-file",
        default=None,
        metavar="JSON",
        help=(
            "多仓库查询配置文件，JSON 数组格式: "
            "[{\"name\":\"repo\",\"path\":\"/path/to/repo\",\"branch\":\"origin/master\"}]"
        ),
    )
    p_check.add_argument(
        "--include-repo",
        action="store_true",
        help="输出扩展 7 列并在 commit_id 前增加命中仓库名（默认保持 6 列兼容输出）",
    )
    p_check.set_defaults(func=cmd_check)

    # cherry-pick 子命令参数
    p_cp = sub.add_parser("cherry-pick", help="从补丁列表批量 cherry-pick（兼容 check 输出）")
    p_cp.add_argument(
        "patch_file",
        metavar="PATCH_FILE",
        help="补丁文件，支持 'hash [title]' 或 check 输出格式（兼容旧 5 列和新 6 列，status=N 自动跳过）",
    )
    p_cp.add_argument("start", nargs="?", default="1", metavar="START", help="从第几个有效提交开始（默认 1）")
    p_cp.add_argument("-o", "--output", dest="log_file", default=None, metavar="FILE", help="将处理结果写入文件（每行: 状态|hash|描述）")
    p_cp.add_argument("-n", "--no-signoff", action="store_true", help="不使用 git cherry-pick --signoff")
    p_cp.set_defaults(func=cmd_cherry_pick)

    # sync-meta 子命令参数
    p_sync = sub.add_parser(
        "sync-meta",
        help="根据指定分支的同名提交，刷新当前分支一段提交的作者/时间",
    )
    p_sync.add_argument(
        "source_branch",
        metavar="SRC_BRANCH",
        help="参考分支名/引用（从该分支读取作者/时间信息）",
    )
    p_sync.add_argument(
        "range",
        metavar="RANGE",
        help="当前分支提交范围，例如 base..HEAD",
    )
    p_sync.add_argument(
        "--dry-run",
        action="store_true",
        help="仅显示将要修改的提交，不实际改写 git 历史",
    )
    p_sync.add_argument(
        "--backend",
        choices=["auto", "filter-repo", "filter-branch"],
        default="auto",
        help="历史改写后端（默认 auto：优先 filter-repo，不可用时回退 filter-branch）",
    )
    p_sync.set_defaults(func=cmd_sync_meta)

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
