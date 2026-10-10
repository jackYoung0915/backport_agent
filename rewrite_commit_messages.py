#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按 title 匹配来源日志并批量改写提交信息。

输入:
  - title 文本文件，每个非空行是一个需要处理的 commit title
  - openEuler git log
  - 可选 Linux git log

参数:
  -f FORMAT 厂商格式，必填：zj（字节）、tx（腾讯）或 al（阿里）。
  -t FILE  title 列表，每行一个完整 title。
  -o FILE  openEuler git log，必填。未传 -l 时，所有补丁都按 openEuler 来源处理。
  -l FILE  Linux git log，可选。传入后优先匹配 Linux。
  -r PATH  目标 git 仓库路径，默认当前目录。
  -R RANGE 只改写指定提交范围，例如 base..HEAD。未传时默认 -t 中第一个成功
           匹配的 title 是最老补丁，并使用它的父提交..HEAD。
  -n       dry-run，只打印 WOULD/FAIL，不改写历史。

默认会直接通过 git filter-branch 改写目标仓库当前分支历史；未显式指定
-R/--range 时，会按 -t 文件里的第一个成功匹配 title 计算父提交..HEAD。
因此 -t 文件应按从旧到新的顺序排列，第一个 title 是最老补丁。如需预览，
请使用 -n/--dry-run。
导出 git log 使用: git log --oneline --no-abbrev-commit，保持默认的
从新到旧顺序。同一 title 匹配多个 commit 时，如果其数量比精确对应的
`Revert "<title>"` 多一个，使用日志中最先出现的 title commit。
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


# 这些定义按需临时修改。
VENDOR_FORMATS = ("zj", "tx", "al")
DOWNSTREAM_TITLE_PREFIX = "downstream: "
ANBZ_LINE = "ANBZ: #IJSBBS"
SIGNOFF_LINE = "Signed-off-by: Shengming Qiu <846759657@qq.com>"


HASH_RE = r"[0-9a-fA-F]{40}"
STANDARD_COMMIT_RE = re.compile(r"^commit\s+(" + HASH_RE + r")(?:\s|$)")
ONELINE_COMMIT_RE = re.compile(r"^(" + HASH_RE + r")(?:\x01|\s+)(.*\S)\s*$")
EXISTING_MARKER_RE = re.compile(
    r"^(?:"
    r"\[Upstream commit\s+[0-9a-fA-F]{40}\]"
    r"|commit\s+[0-9a-fA-F]{40}\s+(?:upstream|openEuler)"
    r"|commit\s+[0-9a-fA-F]{40}\s+(?:upstream|Upstream|openEuler)\."
    r")$"
)


def _decode(data: Optional[bytes]) -> str:
    return (data or b"").decode("utf-8", errors="replace")


def run_git(
    repo: Path,
    args: Sequence[str],
    env: Optional[Dict[str, str]] = None,
) -> Tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo)] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError:
        return -1, "", "git not found"
    return result.returncode, _decode(result.stdout), _decode(result.stderr)


def die(message: str) -> int:
    print("error: {0}".format(message), file=sys.stderr)
    return 1


def read_titles(path: Path) -> List[Tuple[int, str]]:
    titles: List[Tuple[int, str]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, raw in enumerate(handle, 1):
            title = raw.rstrip("\r\n").strip()
            if title:
                titles.append((line_no, title))
    return titles


def _add_log_entry(
    index: Dict[str, List[str]],
    commit_hash: str,
    title: str,
) -> None:
    commit_hash = commit_hash.lower()
    title = title.strip()
    if not title:
        return
    entries = index.setdefault(title, [])
    if commit_hash not in entries:
        entries.append(commit_hash)


def parse_git_log(path: Path) -> Dict[str, List[str]]:
    """解析普通 git log 或 '<40hash> <title>' 一行格式。"""
    index: Dict[str, List[str]] = {}
    pending_hash: Optional[str] = None

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")

            one_line = ONELINE_COMMIT_RE.match(line)
            if one_line:
                _add_log_entry(index, one_line.group(1), one_line.group(2))
                pending_hash = None
                continue

            commit_line = STANDARD_COMMIT_RE.match(line)
            if commit_line:
                pending_hash = commit_line.group(1)
                continue

            if pending_hash and line.startswith("    ") and line.strip():
                _add_log_entry(index, pending_hash, line.strip())
                pending_hash = None

    return index


def _format_hashes(hashes: Sequence[str], limit: int = 4) -> str:
    shown = list(hashes[:limit])
    if len(hashes) > limit:
        shown.append("...")
    return ",".join(shown)


def resolve_log_title(
    title: str,
    index: Dict[str, List[str]],
    log_name: str,
) -> Tuple[Optional[str], str]:
    matches = index.get(title, [])
    if not matches:
        return None, ""
    if len(matches) == 1:
        return matches[0], ""

    revert_title = 'Revert "{0}"'.format(title)
    revert_matches = index.get(revert_title, [])
    if len(matches) == len(revert_matches) + 1:
        return matches[0], ""

    return (
        None,
        (
            "source title matched multiple commits in {0} log: "
            "direct_count={1} revert_count={2} candidates={3}"
        ).format(
            log_name,
            len(matches),
            len(revert_matches),
            _format_hashes(matches),
        ),
    )


def find_source(
    title: str,
    linux_index: Optional[Dict[str, List[str]]],
    openeuler_index: Dict[str, List[str]],
) -> Tuple[bool, Optional[str], Optional[str], str]:
    if linux_index is not None:
        linux_match, linux_error = resolve_log_title(
            title,
            linux_index,
            "linux",
        )
        if linux_error:
            return False, None, None, linux_error
        if linux_match:
            return True, "upstream", linux_match, ""

    openeuler_match, openeuler_error = resolve_log_title(
        title,
        openeuler_index,
        "openEuler",
    )
    if openeuler_error:
        return False, None, None, openeuler_error
    if openeuler_match:
        return True, "openEuler", openeuler_match, ""

    if linux_index is None:
        return False, None, None, "source not found in openEuler log"
    return False, None, None, "source not found in linux/openEuler logs"


def get_target_index(
    repo: Path,
    commit_range: Optional[str],
) -> Tuple[Optional[Dict[str, List[str]]], str]:
    log_ref = commit_range if commit_range else "HEAD"
    code, out, err = run_git(
        repo,
        ["log", "--no-merges", "--format=%H%x01%s", log_ref],
    )
    if code != 0:
        return None, err.strip() or "failed to read target git log"

    index: Dict[str, List[str]] = {}
    for raw in out.splitlines():
        if not raw.strip() or "\x01" not in raw:
            continue
        commit_hash, title = raw.split("\x01", 1)
        _add_log_entry(index, commit_hash, title)
    return index, ""


def ensure_git_repo(repo: Path) -> Optional[str]:
    code, out, err = run_git(repo, ["rev-parse", "--is-inside-work-tree"])
    if code != 0 or out.strip() != "true":
        return err.strip() or "target path is not a git worktree"

    code, out, err = run_git(repo, ["rev-parse", "--is-bare-repository"])
    if code != 0:
        return err.strip() or "failed to inspect target repository"
    if out.strip() == "true":
        return "target repository must not be bare"
    return None


def ensure_ready_to_rewrite(repo: Path) -> Optional[str]:
    code, out, err = run_git(repo, ["rev-parse", "--git-dir"])
    if code != 0:
        return err.strip() or "failed to inspect git dir"

    git_dir = Path(out.strip())
    if not git_dir.is_absolute():
        git_dir = repo / git_dir

    blockers = [
        ("rebase is in progress", git_dir / "rebase-merge"),
        ("rebase or am is in progress", git_dir / "rebase-apply"),
        ("merge is in progress", git_dir / "MERGE_HEAD"),
        ("cherry-pick is in progress", git_dir / "CHERRY_PICK_HEAD"),
        ("revert is in progress", git_dir / "REVERT_HEAD"),
    ]
    for reason, path in blockers:
        if path.exists():
            return reason

    code, _, err = run_git(repo, ["diff", "--quiet", "--"])
    if code != 0:
        return "working tree has unstaged changes: {0}".format(err.strip())

    code, _, err = run_git(repo, ["diff", "--cached", "--quiet", "--"])
    if code != 0:
        return "index has staged changes: {0}".format(err.strip())

    return None


def remove_legacy_metadata(body: List[str]) -> List[str]:
    separator_idx: Optional[int] = None
    for idx, line in enumerate(body):
        if re.fullmatch(r"-{4,}", line.strip()):
            separator_idx = idx
            break

    if separator_idx is None:
        return body

    metadata = [line.strip().lower() for line in body[:separator_idx]]
    has_category = any(line.startswith("category:") for line in metadata)
    has_bugzilla = any(line.startswith("bugzilla:") for line in metadata)
    if has_category and has_bugzilla:
        return body[separator_idx + 1 :]
    return body


def strip_leading_empty(lines: List[str]) -> List[str]:
    while lines and lines[0].strip() == "":
        lines.pop(0)
    return lines


def strip_trailing_empty(lines: List[str]) -> List[str]:
    while lines and lines[-1].strip() == "":
        lines.pop()
    return lines


def collapse_empty_lines(lines: Sequence[str]) -> List[str]:
    collapsed: List[str] = []
    for line in lines:
        if line.strip() == "":
            if collapsed and collapsed[-1] != "":
                collapsed.append("")
            continue
        collapsed.append(line)
    return collapsed


def remove_existing_marker(body: List[str]) -> List[str]:
    if not body:
        return body

    if body[0].strip() == ANBZ_LINE:
        cursor = 1
        while cursor < len(body) and body[cursor].strip() == "":
            cursor += 1
        if cursor < len(body) and EXISTING_MARKER_RE.match(body[cursor].strip()):
            body = body[cursor + 1 :]
            return strip_leading_empty(body)
        return body

    if EXISTING_MARKER_RE.match(body[0].strip()):
        body = body[1:]
        return strip_leading_empty(body)
    return body


def format_title_and_header(
    title: str,
    source_hash: str,
    source: str,
    vendor_format: str,
) -> Tuple[str, List[str]]:
    if vendor_format not in VENDOR_FORMATS:
        raise ValueError("unsupported vendor format: {0}".format(vendor_format))
    if source not in ("upstream", "openEuler"):
        raise ValueError("unsupported source: {0}".format(source))

    if vendor_format == "zj":
        if source == "openEuler":
            if not title.startswith(DOWNSTREAM_TITLE_PREFIX):
                title = DOWNSTREAM_TITLE_PREFIX + title
            return title, []
        return title, ["commit {0} upstream.".format(source_hash)]

    if vendor_format == "tx":
        if source == "upstream":
            return title, ["[Upstream commit {0}]".format(source_hash)]
        return title, ["commit {0} openEuler".format(source_hash)]

    return title, [
        ANBZ_LINE,
        "",
        "commit {0} {1}.".format(source_hash, source),
    ]


def rewrite_commit_message(
    message: str,
    source_hash: str,
    source: str,
    vendor_format: str,
) -> str:
    normalized = message.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    strip_trailing_empty(lines)
    if not lines:
        return normalized if normalized.endswith("\n") else normalized + "\n"

    title = lines[0].rstrip()
    body = lines[1:]
    body = remove_legacy_metadata(body)
    body = strip_leading_empty(body)
    body = remove_existing_marker(body)
    body = strip_trailing_empty(body)

    title, header = format_title_and_header(
        title,
        source_hash,
        source,
        vendor_format,
    )
    output = [title, ""]
    if header:
        output.extend(header)
        output.append("")
    if body:
        output.extend(body)
        if SIGNOFF_LINE and body[-1] != SIGNOFF_LINE:
            output.append(SIGNOFF_LINE)
    elif SIGNOFF_LINE:
        output.append(SIGNOFF_LINE)
    output = collapse_empty_lines(output)
    output = strip_trailing_empty(output)
    return "\n".join(output) + "\n"


def print_result(
    status: str,
    line_no: int,
    title: str,
    reason: Optional[str] = None,
    target_hash: Optional[str] = None,
    source: Optional[str] = None,
    source_hash: Optional[str] = None,
) -> None:
    parts = [status, "line={0}".format(line_no)]
    if reason:
        parts.append("reason={0}".format(reason))
    if target_hash:
        parts.append("target={0}".format(target_hash))
    if source:
        parts.append("source={0}".format(source))
    if source_hash:
        parts.append("source_hash={0}".format(source_hash))
    parts.append("title={0}".format(title))
    print(" ".join(parts))


def print_summary(total: int, success: int = 0, failed: int = 0, would: int = 0) -> None:
    parts = ["SUMMARY", "total={0}".format(total)]
    if would:
        parts.append("would={0}".format(would))
    if success:
        parts.append("success={0}".format(success))
    parts.append("failed={0}".format(failed))
    print(" ".join(parts))


def short_error(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "unknown error"
    return " | ".join(lines[-8:])


def default_rewrite_range(repo: Path, oldest_target: str) -> Tuple[Optional[str], str]:
    """按 -t 文件顺序，使用第一个成功匹配提交的父提交作为默认范围起点。"""
    if not oldest_target:
        return None, "missing oldest matched target commit"

    code, parent, _err = run_git(repo, ["rev-parse", "--verify", "{0}^".format(oldest_target)])
    if code != 0:
        return "HEAD", ""
    return "{0}..HEAD".format(parent.strip()), ""


def run_filter_branch(
    repo: Path,
    rewrite_range: str,
    mapping: Dict[str, Dict[str, str]],
) -> Tuple[bool, str]:
    mapping_path = ""
    helper_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", delete=False, prefix="rewrite-message-map-", suffix=".json"
        ) as handle:
            json.dump(mapping, handle, ensure_ascii=False)
            mapping_path = handle.name

        with tempfile.NamedTemporaryFile(
            "wb", delete=False, prefix="rewrite-message-helper-", suffix=".py"
        ) as handle:
            helper_path = handle.name
        shutil.copyfile(str(Path(__file__).resolve()), helper_path)

        filter_cmd = "{0} {1} --msg-filter {2}".format(
            shlex.quote(sys.executable or "python3"),
            shlex.quote(helper_path),
            shlex.quote(mapping_path),
        )
        env = os.environ.copy()
        env["FILTER_BRANCH_SQUELCH_WARNING"] = "1"
        code, _out, err = run_git(
            repo,
            ["filter-branch", "-f", "--msg-filter", filter_cmd, rewrite_range],
            env=env,
        )
        if code != 0:
            return False, short_error(err)
        return True, ""
    finally:
        for path in (mapping_path, helper_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def cmd_msg_filter(mapping_file: str) -> int:
    with open(mapping_file, "r", encoding="utf-8") as handle:
        mapping = json.load(handle)

    commit_hash = os.environ.get("GIT_COMMIT", "").lower()
    message_bytes = sys.stdin.buffer.read()
    entry = mapping.get(commit_hash)
    if not entry:
        sys.stdout.buffer.write(message_bytes)
        return 0

    message = message_bytes.decode("utf-8", errors="surrogateescape")
    rewritten = rewrite_commit_message(
        message,
        entry["source_hash"],
        entry["source"],
        entry["vendor_format"],
    )
    sys.stdout.buffer.write(rewritten.encode("utf-8", errors="surrogateescape"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按 title 匹配 Linux/openEuler 来源并改写 commit message",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 rewrite_commit_messages.py -f zj -t titles.txt -o openEuler.log -r /path/to/repo
  python3 rewrite_commit_messages.py -f tx -t titles.txt -l linux.log -o openEuler.log -r /path/to/repo
  python3 rewrite_commit_messages.py -f al -t titles.txt -l linux.log -o openEuler.log -r /path/to/repo -R base..HEAD

说明:
  zj: Linux 使用 "commit <hash> upstream."，openEuler 在 title 前添加 "downstream: "。
  tx: Linux 使用 "[Upstream commit <hash>]"，openEuler 使用 "commit <hash> openEuler"。
  al: title 后添加 "ANBZ: #IJSBBS"，再添加带句点的来源 marker。
  来源日志需保持 git log 默认的从新到旧顺序。同一 title 精确匹配多个
  commit 时，如果直接匹配数比 `Revert "<title>"` 精确匹配数多一个，
  使用日志中最先出现的直接匹配 commit。
  默认会直接改写 git 历史。未传 -R/--range 时，脚本认为 -t 文件里第一个
  成功匹配的 title 是最老补丁，并使用该提交的父提交..HEAD，避免重写整条
  老历史。使用 -n/--dry-run 可只打印逐条处理计划。
""",
    )
    parser.add_argument(
        "-f",
        "--format",
        dest="vendor_format",
        required=True,
        choices=VENDOR_FORMATS,
        help="厂商格式：zj=字节，tx=腾讯，al=阿里",
    )
    parser.add_argument("-t", "--titles", metavar="FILE", help="title 列表，每行一个完整 title")
    parser.add_argument(
        "-l",
        "--linux-log",
        metavar="FILE",
        help="Linux git log；传入后优先匹配 Linux",
    )
    parser.add_argument(
        "-o",
        "--openeuler-log",
        metavar="FILE",
        help="openEuler git log",
    )
    parser.add_argument("-r", "--repo", default=".", metavar="PATH", help="目标 git 仓库路径，默认当前目录")
    parser.add_argument("-R", "--range", dest="commit_range", metavar="REV_RANGE", help="只改写指定范围，例如 base..HEAD")
    parser.add_argument("-n", "--dry-run", action="store_true", help="只打印 WOULD/FAIL，不改写历史")
    return parser


def validate_paths(args: argparse.Namespace) -> Optional[str]:
    if not args.titles:
        return "missing --titles"
    if not args.openeuler_log:
        return "missing --openeuler-log"

    checks = [
        ("titles", Path(args.titles)),
        ("openeuler log", Path(args.openeuler_log)),
    ]
    if args.linux_log:
        checks.append(("linux log", Path(args.linux_log)))

    for label, path in checks:
        if not path.is_file():
            return "{0} file not found: {1}".format(label, path)
    return None


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--msg-filter":
        return cmd_msg_filter(sys.argv[2])

    args = build_parser().parse_args()

    path_error = validate_paths(args)
    if path_error:
        return die(path_error)

    repo = Path(args.repo).resolve()
    repo_error = ensure_git_repo(repo)
    if repo_error:
        return die(repo_error)

    if not args.dry_run:
        ready_error = ensure_ready_to_rewrite(repo)
        if ready_error:
            return die(ready_error)

    titles = read_titles(Path(args.titles))
    if not titles:
        return die("no non-empty titles found in {0}".format(args.titles))

    linux_index = parse_git_log(Path(args.linux_log)) if args.linux_log else None
    openeuler_index = parse_git_log(Path(args.openeuler_log))

    target_index, target_error = get_target_index(repo, args.commit_range)
    if target_index is None:
        return die(target_error)

    work_items: List[Dict[str, str]] = []
    mapping: Dict[str, Dict[str, str]] = {}
    fail_count = 0

    for line_no, title in titles:
        ok, source, source_hash, reason = find_source(
            title,
            linux_index,
            openeuler_index,
        )
        if not ok:
            print_result("FAIL", line_no, title, reason=reason)
            fail_count += 1
            continue

        target_matches = target_index.get(title, [])
        if len(target_matches) > 1:
            print_result(
                "FAIL",
                line_no,
                title,
                reason="target title matched multiple commits: {0}".format(
                    _format_hashes(target_matches)
                ),
            )
            fail_count += 1
            continue
        if len(target_matches) == 0:
            print_result("FAIL", line_no, title, reason="target title not found")
            fail_count += 1
            continue

        target_hash = target_matches[0]
        item = {
            "line_no": str(line_no),
            "title": title,
            "target_hash": target_hash,
            "source": source or "",
            "source_hash": source_hash or "",
        }
        work_items.append(item)
        mapping[target_hash] = {
            "source": source or "",
            "source_hash": source_hash or "",
            "vendor_format": args.vendor_format,
        }

    if args.dry_run:
        for item in work_items:
            print_result(
                "WOULD",
                int(item["line_no"]),
                item["title"],
                target_hash=item["target_hash"],
                source=item["source"],
                source_hash=item["source_hash"],
            )
        print_summary(len(titles), failed=fail_count, would=len(work_items))
        return 0

    if not work_items:
        print_summary(len(titles), failed=fail_count)
        return 0

    if args.commit_range:
        rewrite_range = args.commit_range
    else:
        rewrite_range, range_error = default_rewrite_range(
            repo,
            work_items[0]["target_hash"],
        )
        if range_error or not rewrite_range:
            for item in work_items:
                print_result(
                    "FAIL",
                    int(item["line_no"]),
                    item["title"],
                    reason="failed to compute default rewrite range: {0}".format(
                        range_error or "unknown error"
                    ),
                    target_hash=item["target_hash"],
                    source=item["source"],
                    source_hash=item["source_hash"],
                )
            print_summary(
                len(titles),
                failed=fail_count + len(work_items),
            )
            return 1

    ok, error = run_filter_branch(repo, rewrite_range, mapping)
    if not ok:
        for item in work_items:
            print_result(
                "FAIL",
                int(item["line_no"]),
                item["title"],
                reason="git filter-branch failed: {0}".format(error),
                target_hash=item["target_hash"],
                source=item["source"],
                source_hash=item["source_hash"],
            )
        print_summary(
            len(titles),
            failed=fail_count + len(work_items),
        )
        return 1

    for item in work_items:
        print_result(
            "SUCCESS",
            int(item["line_no"]),
            item["title"],
            target_hash=item["target_hash"],
            source=item["source"],
            source_hash=item["source_hash"],
        )
    print_summary(len(titles), success=len(work_items), failed=fail_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
