#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件存在性批量检查工具。

从输入文件逐行读取待检查的文件名，在一组常见系统目录中递归查找，
并输出检查结果表；可选写出 CSV 报告。

默认匹配规则：
  - 按文件名（basename）精确匹配
  - 默认扫描常见系统目录
  - 输出终端表格，可选输出 CSV
"""

import argparse
import csv
import os
import platform
import sys
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


DEFAULT_ROOTS = (
    "/lib",
    "/lib64",
    "/usr/lib",
    "/usr/lib64",
    "/usr/local/lib",
    "/bin",
    "/sbin",
    "/usr/bin",
    "/usr/sbin",
    "/usr/local/bin",
    "/vendor",
    "/system",
)

OS_FAMILY_CHOICES = ("auto", "generic", "debian", "rpm")

DEBIAN_OS_IDS = ("debian", "ubuntu", "linuxmint")
RPM_OS_IDS = (
    "almalinux",
    "centos",
    "fedora",
    "openeuler",
    "rhel",
    "rocky",
)
RPM_OS_LIKE_IDS = ("rhel", "fedora")

RPM_EXTRA_ROOTS = (
    "/usr/libexec",
    "/usr/local/lib64",
    "/lib/modules",
    "/usr/lib/modules",
)

KERNEL_SOURCE_SUFFIXES = (".h", ".c")
KERNEL_SRC_ROOT = Path("/usr/src")

DEBIAN_MULTIARCH_TRIPLETS = {
    "aarch64": "aarch64-linux-gnu",
    "arm64": "aarch64-linux-gnu",
    "armv6l": "arm-linux-gnueabihf",
    "armv7l": "arm-linux-gnueabihf",
    "i386": "i386-linux-gnu",
    "i486": "i386-linux-gnu",
    "i586": "i386-linux-gnu",
    "i686": "i386-linux-gnu",
    "ppc64le": "powerpc64le-linux-gnu",
    "s390x": "s390x-linux-gnu",
    "x86_64": "x86_64-linux-gnu",
}


def _display_width(text: str) -> int:
    """计算字符串的终端显示宽度。"""
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
    return width


def _pad_cell(text: str, width: int) -> str:
    """按显示宽度右侧补空格。"""
    return text + (" " * max(0, width - _display_width(text)))


def _join_paths(paths: Sequence[str]) -> str:
    """将命中路径拼接为单个单元格文本。"""
    if not paths:
        return "-"
    return "; ".join(paths)


def _read_input_names(input_file: Path) -> List[str]:
    """读取输入文件中的待检查文件名。"""
    names: List[str] = []
    with input_file.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            name = line.strip()
            if name:
                names.append(name)
    return names


def _is_kernel_source_name(name: str) -> bool:
    """判断输入名是否为内核源码/头文件名。"""
    return name.lower().endswith(KERNEL_SOURCE_SUFFIXES)


def _has_kernel_source_names(names: Sequence[str]) -> bool:
    """判断输入列表是否包含 .h 或 .c 文件。"""
    return any(_is_kernel_source_name(name) for name in names)


def _read_os_release(os_release_file: Path = Path("/etc/os-release")) -> Dict[str, str]:
    """读取 os-release 信息。"""
    info: Dict[str, str] = {}
    try:
        with os_release_file.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                info[key.strip()] = value.strip().strip("\"'")
    except OSError:
        return {}
    return info


def detect_os_family(os_release_file: Path = Path("/etc/os-release")) -> str:
    """识别当前系统目录布局族。"""
    info = _read_os_release(os_release_file)
    os_id = info.get("ID", "").lower()
    os_like_ids = set(info.get("ID_LIKE", "").lower().replace(",", " ").split())

    if os_id in DEBIAN_OS_IDS or os_like_ids.intersection(DEBIAN_OS_IDS):
        return "debian"
    if (
        os_id in RPM_OS_IDS
        or os_like_ids.intersection(RPM_OS_IDS)
        or os_like_ids.intersection(RPM_OS_LIKE_IDS)
    ):
        return "rpm"
    return "generic"


def _debian_multiarch_triplet() -> Optional[str]:
    """根据机器架构推导 Debian multiarch triplet。"""
    machine = platform.machine().lower()
    return DEBIAN_MULTIARCH_TRIPLETS.get(machine)


def _debian_multiarch_roots() -> Tuple[str, ...]:
    """构造 Debian multiarch 常见库目录。"""
    triplet = _debian_multiarch_triplet()
    if not triplet:
        return ()
    return (
        "/lib/{0}".format(triplet),
        "/usr/lib/{0}".format(triplet),
        "/usr/local/lib/{0}".format(triplet),
    )


def _existing_glob_dirs(root: Path, pattern: str) -> Tuple[str, ...]:
    """展开 root 下匹配 pattern 的现有目录。"""
    if not root.exists() or not root.is_dir():
        return ()
    return tuple(str(path) for path in sorted(root.glob(pattern)) if path.is_dir())


def _kernel_devel_roots() -> Tuple[str, ...]:
    """返回当前系统常见内核开发包目录。"""
    release = platform.release()
    roots = (
        "/lib/modules/{0}/build".format(release),
        "/lib/modules/{0}/source".format(release),
    )
    roots += _existing_glob_dirs(KERNEL_SRC_ROOT, "linux-headers-*")
    roots += _existing_glob_dirs(KERNEL_SRC_ROOT / "kernels", "*")
    roots += _existing_glob_dirs(KERNEL_SRC_ROOT, "linux-*")
    return _dedupe_roots(roots)


def _dedupe_roots(raw_roots: Sequence[str]) -> Tuple[str, ...]:
    """按真实路径去重 root 字符串，并保留首次出现形式。"""
    roots: List[str] = []
    seen: Set[str] = set()
    for raw in raw_roots:
        root = raw.strip()
        if not root:
            continue
        real_root = os.path.realpath(os.path.abspath(root))
        if real_root in seen:
            continue
        seen.add(real_root)
        roots.append(root)
    return tuple(roots)


def get_default_roots(os_family: str = "auto") -> Tuple[str, ...]:
    """按系统族返回默认搜索根目录。"""
    if os_family not in OS_FAMILY_CHOICES:
        raise ValueError("unsupported os family: {0}".format(os_family))

    resolved_family = detect_os_family() if os_family == "auto" else os_family
    if resolved_family == "debian":
        return _dedupe_roots(DEFAULT_ROOTS + _debian_multiarch_roots())
    if resolved_family == "rpm":
        return _dedupe_roots(DEFAULT_ROOTS + RPM_EXTRA_ROOTS)
    return _dedupe_roots(DEFAULT_ROOTS)


def get_search_roots(names: Sequence[str], os_family: str = "auto") -> Tuple[str, ...]:
    """根据输入文件名返回搜索根目录。"""
    default_roots = get_default_roots(os_family)
    if _has_kernel_source_names(names):
        return _dedupe_roots(_kernel_devel_roots() + default_roots)
    return default_roots


def _resolve_roots(raw_roots: Sequence[str]) -> List[Path]:
    """解析并去重搜索根目录。"""
    roots: List[Path] = []
    seen: Set[str] = set()
    for raw in raw_roots:
        root = raw.strip()
        if not root:
            continue
        real_root = os.path.realpath(os.path.abspath(root))
        if real_root in seen:
            continue
        seen.add(real_root)
        roots.append(Path(root))
    return roots


def _walk_and_index(roots: Sequence[Path]) -> Tuple[Dict[str, List[str]], List[str]]:
    """遍历搜索根目录并建立 basename -> 路径列表 索引。"""
    index: Dict[str, List[str]] = {}
    warnings: List[str] = []

    for root in roots:
        root_str = str(root)
        if not root.exists():
            warnings.append("警告: 搜索目录不存在，已跳过: {0}".format(root_str))
            continue
        if not root.is_dir():
            warnings.append("警告: 搜索路径不是目录，已跳过: {0}".format(root_str))
            continue

        def _on_error(exc: OSError) -> None:
            target = getattr(exc, "filename", root_str) or root_str
            warnings.append("警告: 无法访问目录 {0}: {1}".format(target, exc))

        for dirpath, dirnames, filenames in os.walk(root_str, topdown=True, onerror=_on_error, followlinks=False):
            dirnames.sort()
            filenames.sort()
            for filename in filenames:
                full_path = os.path.abspath(os.path.join(dirpath, filename))
                index.setdefault(filename, []).append(full_path)

    for filename, paths in index.items():
        deduped: List[str] = []
        seen: Set[str] = set()
        for path in paths:
            real_path = os.path.realpath(path)
            if real_path in seen:
                continue
            seen.add(real_path)
            deduped.append(path)
        index[filename] = deduped

    return index, warnings


def _build_rows(names: Sequence[str], index: Dict[str, List[str]]) -> List[Dict[str, str]]:
    """根据输入文件名和索引构造结果行。"""
    rows: List[Dict[str, str]] = []
    for name in names:
        matched_paths = index.get(name, [])
        rows.append(
            {
                "文件名": name,
                "状态": "FOUND" if matched_paths else "NOT_FOUND",
                "命中数量": str(len(matched_paths)),
                "命中路径": _join_paths(matched_paths),
            }
        )
    return rows


def _format_table(rows: Sequence[Dict[str, str]], headers: Sequence[str]) -> str:
    """将结果行格式化为对齐表格。"""
    widths: Dict[str, int] = {}
    for header in headers:
        max_width = _display_width(header)
        for row in rows:
            max_width = max(max_width, _display_width(row.get(header, "")))
        widths[header] = max_width

    border = "+-" + "-+-".join("-" * widths[header] for header in headers) + "-+"
    header_line = "| " + " | ".join(_pad_cell(header, widths[header]) for header in headers) + " |"

    lines = [border, header_line, border]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(_pad_cell(row.get(header, ""), widths[header]) for header in headers)
            + " |"
        )
    lines.append(border)
    return "\n".join(lines)


def _write_csv(rows: Sequence[Dict[str, str]], headers: Sequence[str], output_file: Path) -> None:
    """将结果写入 CSV 文件。"""
    with output_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(headers))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _count_summary(rows: Iterable[Dict[str, str]]) -> Tuple[int, int, int]:
    """统计总数、命中数、未命中数。"""
    total = 0
    found = 0
    for row in rows:
        total += 1
        if row.get("状态") == "FOUND":
            found += 1
    return total, found, total - found


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="批量检查 ko/so/二进制文件是否存在于当前环境中",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        metavar="FILE",
        help="输入文件路径，每行一个待检查的文件名",
    )
    parser.add_argument(
        "--csv",
        dest="csv_file",
        default=None,
        metavar="FILE",
        help="可选：将结果写出为 CSV 文件",
    )
    parser.add_argument(
        "--roots",
        nargs="+",
        default=None,
        metavar="PATH",
        help="搜索根目录列表（指定后不使用自动系统目录）",
    )
    parser.add_argument(
        "--os-family",
        choices=OS_FAMILY_CHOICES,
        default="auto",
        help="默认搜索目录的系统族（默认 auto: 自动识别 debian/rpm/generic）",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """程序入口。"""
    args = build_parser().parse_args(argv)

    input_file = Path(args.input)
    if not input_file.exists():
        print("错误: 输入文件不存在: {0}".format(input_file), file=sys.stderr)
        return 1
    if not input_file.is_file():
        print("错误: 输入路径不是普通文件: {0}".format(input_file), file=sys.stderr)
        return 1

    try:
        names = _read_input_names(input_file)
    except OSError as exc:
        print("错误: 无法读取输入文件 {0}: {1}".format(input_file, exc), file=sys.stderr)
        return 1

    if not names:
        print("错误: 输入文件中没有可检查的文件名", file=sys.stderr)
        return 1

    raw_roots = (
        args.roots
        if args.roots is not None
        else get_search_roots(names, args.os_family)
    )
    roots = _resolve_roots(raw_roots)
    index, warnings = _walk_and_index(roots)
    for warning in warnings:
        print(warning, file=sys.stderr)

    headers = ("文件名", "状态", "命中数量", "命中路径")
    rows = _build_rows(names, index)
    print(_format_table(rows, headers))

    total, found, not_found = _count_summary(rows)
    print("总计: {0}，命中: {1}，未命中: {2}".format(total, found, not_found))

    if args.csv_file:
        output_file = Path(args.csv_file)
        try:
            _write_csv(rows, headers, output_file)
        except OSError as exc:
            print("错误: 无法写出 CSV 文件 {0}: {1}".format(output_file, exc), file=sys.stderr)
            return 1
        print("CSV 已写出: {0}".format(output_file))

    return 0


if __name__ == "__main__":
    sys.exit(main())
