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
import logging
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


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
    """判断字符串是否为 10-40 位十六进制 commit hash。"""
    return bool(s and re.match(r"^[0-9a-f]{10,40}$", s.lower()))


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
          "commit_time": str
        }
      }
    """
    if not commit_ids:
        return {}

    result: Dict[str, Dict[str, Any]] = {cid: {"describe": "", "timestamp": 0, "commit_time": ""} for cid in commit_ids}

    def key_for_full_hash(full_hash: str) -> Optional[str]:
        """将 40 位 full hash 映射回 result 的 key（key 可能是短 hash）。"""
        if full_hash in result:
            return full_hash
        for cid in commit_ids:
            if len(cid) < 40 and full_hash.startswith(cid):
                return cid
        return None

    code, out, _ = run_git(
        ["log", "--format=%H%n%ct%n%ci"] + commit_ids,
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

    return result


# --- check 子命令 ---


def cmd_check(args: argparse.Namespace) -> int:
    """检查输入文件中的 commit title 是否已合入目标分支并排序输出。"""
    inp = Path(args.input_file)
    out = Path(args.output_file)
    branch = args.branch

    if not inp.is_file():
        logging.error(f"输入文件不存在 '{inp}'")
        return 1

    # 输入文件每行一个 title，空行自动跳过。
    titles = []
    with open(inp, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            t = line.strip()
            if t:
                titles.append(t)

    branch_desc = f"分支 '{branch}'" if branch else "当前分支"
    logging.info(f"正在获取{branch_desc}的 git log（排除 merge commit）...")
    lines = get_branch_log_oneline(cwd=args.repo, long_hash=args.long_hash, branch=branch)
    if lines is None:
        logging.error("无法获取 git log，请确保在 git 仓库中且分支/引用有效")
        return 1

    # 构建 title -> (hash, raw_line) 索引；重复 title 仅保留首次出现。
    title_to_line: Dict[str, Tuple[str, str]] = {}
    for ln in lines:
        h, title = parse_oneline_line(ln)
        if not title:
            continue
        if title not in title_to_line:
            title_to_line[title] = (h, ln)

    repo = args.repo or "."
    results: List[Tuple[int, str, str, str, str, str]] = []
    matched_commit_ids: List[str] = []

    for title in titles:
        logging.debug(f"正在检查: {title}")
        if title in title_to_line:
            commit_id, _ = title_to_line[title]
            matched_commit_ids.append(commit_id)

    # 先批量获取命中提交的元信息，减少逐条 git 调用。
    commit_info_map = get_batch_commit_info(matched_commit_ids, cwd=repo)

    for title in titles:
        if title in title_to_line:
            commit_id, _ = title_to_line[title]
            status = "Y"

            info = commit_info_map.get(commit_id, {})
            git_describe = info.get("describe", "")
            commit_timestamp = info.get("timestamp", 0)
            commit_time = info.get("commit_time", "")

            results.append(
                (commit_timestamp, title, commit_id, status, git_describe, commit_time)
            )
            if git_describe:
                logging.info(f"  ✓ 找到: {commit_id}, 状态: {status}, describe: {git_describe}, 时间: {commit_time}")
            else:
                logging.info(f"  ✓ 找到: {commit_id}, 状态: {status}, 时间: {commit_time} (无法获取 describe)")
        else:
            status = "N"
            # 用较大时间戳占位，使未命中项在排序后位于末尾。
            results.append((9999999999, title, "", status, "", ""))
            logging.info(f"  ✗ 未在{branch_desc}中找到")

    # 排序优先级：
    # 1) status=Y 且 describe 可解析：按 tag + distance（合入序）排序
    # 2) status=Y 但 describe 不可解析：回退按时间戳排序
    # 3) status=N：置于末尾
    def result_sort_key(item: Tuple[int, str, str, str, str, str]) -> Tuple[int, Any, int, int, str]:
        commit_timestamp, title, _commit_id, status, git_describe, _commit_time = item
        if status != "Y":
            return (2, (), 0, commit_timestamp, title)

        parsed = parse_describe_order(git_describe)
        if parsed is not None:
            tag_key, distance = parsed
            return (0, tag_key, distance, commit_timestamp, title)

        return (1, (), 0, commit_timestamp, title)

    results.sort(key=result_sort_key)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for _, title, commit_id, status, git_describe, commit_time in results:
            f.write(f"{title}|{commit_id}|{status}|{git_describe}|{commit_time}\n")

    logging.info(f"检查完成，结果已保存到 {out}（优先按 describe 合入序排序）")
    return 0


# --- cherry-pick 子命令 ---


def cmd_cherry_pick(args: argparse.Namespace) -> int:
    """从补丁列表文件批量执行 cherry-pick。"""
    patch_file = Path(args.patch_file)
    if not patch_file.is_file():
        logging.error(f"补丁文件不存在 '{patch_file}'")
        return 1

    entries: List[Tuple[str, str]] = []
    with open(patch_file, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            h, rest = parse_oneline_line(line)
            entries.append((h, rest))

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

    print("")
    print("即将通过 'git filter-branch --env-filter' 改写历史。")
    print("注意: 这会重写当前分支历史，后续需要使用 push --force 推送到远端（如有）。")

    # 4) 构造 env-filter 脚本：按 commit hash 精确覆写作者字段。
    lines = ['case "$GIT_COMMIT" in']
    for commit, meta in commit_to_meta.items():
        lines.append(f"  {commit})")
        lines.append(
            f"    export GIT_AUTHOR_NAME={shlex.quote(meta['an'])}"
        )
        lines.append(
            f"    export GIT_AUTHOR_EMAIL={shlex.quote(meta['ae'])}"
        )
        lines.append(
            f"    export GIT_AUTHOR_DATE={shlex.quote(meta['ad'])}"
        )
        lines.append("    ;;")
    lines.append("esac")
    env_filter = "\n".join(lines)

    # 5) 执行 filter-branch 改写指定范围历史。
    print("")
    print("开始执行 git filter-branch ...")
    code, _, _ = run_git(
        ["filter-branch", "-f", "--env-filter", env_filter, commit_range],
        cwd=repo,
        capture=False,
    )
    if code != 0:
        print("错误: git filter-branch 执行失败。", file=sys.stderr)
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
    p_check = sub.add_parser("check", help="按 commit title 检查是否已合入当前分支")
    p_check.add_argument("input_file", metavar="INPUT", help="输入文件，每行一个 commit title")
    p_check.add_argument("output_file", metavar="OUTPUT", help="输出文件，格式: title|commit_id|status|git_describe|commit_time (Y/N)")
    p_check.add_argument("-b", "--branch", default=None, metavar="BRANCH", help="在指定分支上检查（默认当前分支，可为分支名或 commit/tag 等引用）")
    p_check.add_argument("-l", "--long-hash", action="store_true", help="log 使用 40 位完整 commit hash 匹配（默认使用短 hash）")
    p_check.set_defaults(func=cmd_check)

    # cherry-pick 子命令参数
    p_cp = sub.add_parser("cherry-pick", help="从补丁列表批量 cherry-pick")
    p_cp.add_argument("patch_file", metavar="PATCH_FILE", help="补丁文件，每行: commit_hash 或 commit_hash 空格 commit_title")
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
    p_sync.set_defaults(func=cmd_sync_meta)

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
