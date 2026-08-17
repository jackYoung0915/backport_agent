#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Excel 表格换行单元格拆行工具。

读取 .xlsx/.xlsm 工作簿，按首行表头定位 Commit信息 列；如果该列
字符串单元格包含换行符，则将这一行按换行段展开为多行。

拆分规则：
  - 支持 \\n、\\r\\n、\\r
  - 仅 Commit信息 列参与拆分
  - Commit信息 中的空白行会被忽略，不生成输出行
  - 其他列不参与拆分，生成的每一行都复制原行对应列的值
  - 找不到 Commit信息 表头的工作表会跳过并输出告警

导出补丁列表：
  python3 excel_tool.py export-commits INPUT OUTPUT

  Commit信息 原始内容按分号分隔字段，导出时只保留 commit hash 和
  commit title，格式为 12 位 hash、四个空格和 title。

内核补丁映射：
  python3 excel_tool.py map-patches INPUT OUTPUT --oe-repo OE_REPO
      --upstream-repo UPSTREAM_REPO [--oe-ref OLK-6.6]
      [--upstream-ref master] [--product-version 950]
"""

import argparse
import io
import logging
import os
import re
import sys
import tempfile
from copy import copy
from pathlib import Path
from time import monotonic
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


SUPPORTED_SUFFIXES = (".xlsx", ".xlsm")
DEFAULT_PROGRESS_INTERVAL = 10000
COMMIT_INFO_HEADER = "Commit信息"
COMMIT_EXPORT_SEPARATOR = "    "

MAP_PATCHES_SHEET = "全量补丁"
MAP_PATCHES_HEADERS = [
    "概述(SR粒度)", "功能介绍(AR粒度)", "关联社区Issue", "补丁类型",
    "产品版本", "用户态/内核态", "补丁模块", "Commit OE", "Commit ID OE",
    "PR前、后置补丁", "OE Merge tag", "OE PR", "Commit upstream",
    "upstream Merge tag", "唯一标识符", "是否需要合入", "客户影响",
    "华为合入状态", "客户合入状态", "合入版本", "OS发布版本",
]
MAP_KERNEL_COMMUNITIES = {
    "合入openEuler olk内核社区版本",
    "合入上游linux内核社区版本",
}
MAP_REQUIRED_HEADERS = (
    "SR描述", "AR编号", "AR子编号", "标题描述", "版本模块",
    COMMIT_INFO_HEADER, "社区类型", "社区链接", "客户影响",
)
MAP_COMMIT_START_RE = re.compile(r"(?m)^[ \t]*([0-9a-fA-F]{7,40});")
MAP_HASH_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
MAP_PR_RE = re.compile(
    r"^https?://(?:www\.)?(?:gitcode\.com|gitee\.com|atomgit\.com)"
    r"/openeuler/kernel/(?:-\/)?(?:pull|pulls|merge_requests)/(\d+)(?:/.*)?$",
    re.IGNORECASE,
)

LOG = logging.getLogger(__name__)


def _mapping_text(value: Any) -> str:
    """返回用于映射的清理后文本。"""
    if value is None:
        return ""
    return str(value).strip()


def _mapping_source_text(value: Any) -> str:
    """返回需原样写入目标工作簿的源单元格文本。"""
    if value is None:
        return ""
    return str(value)


def _normalize_mapping_title(value: Any) -> str:
    """折叠 commit title 中的换行和连续空白。"""
    return re.sub(r"\s+", " ", _mapping_text(value))


def _parse_mapping_commit_records(value: Any) -> Tuple[List[Dict[str, str]], int]:
    """解析 Commit信息 单元格中的多条 commit 记录。

    记录以行首的 ``hash;`` 开始，直到下一条行首 hash；从右侧拆分
    title/date/lines，允许 title 本身包含换行或分号。
    """
    if value in (None, ""):
        return [], 0
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    starts = list(MAP_COMMIT_START_RE.finditer(text))
    if not starts:
        return [], 1
    records: List[Dict[str, str]] = []
    invalid = 0
    for idx, match in enumerate(starts):
        end = starts[idx + 1].start() if idx + 1 < len(starts) else len(text)
        segment = text[match.start():end].strip()
        source_hash = match.group(1).lower()
        _, separator, rest = segment.partition(";")
        fields = rest.rsplit(";", 2) if separator else []
        title = _normalize_mapping_title(fields[0] if len(fields) == 3 else "")
        if not MAP_HASH_RE.fullmatch(source_hash) or not title:
            invalid += 1
            continue
        records.append({"source_hash": source_hash, "title": title})
    return records, invalid


def _mapping_patch_type(value: Any) -> str:
    """将 Feature_1/Bug_2/Clean up_3 转为目标补丁类型。"""
    raw = _mapping_text(value)
    match = re.match(r"^(.*)_\d+$", raw)
    patch_type = match.group(1).strip() if match else raw
    if patch_type.casefold() == "clean up":
        return "Cleanup"
    return patch_type


def _normalize_kernel_pr_url(value: Any) -> str:
    """将可识别的 openEuler kernel PR 链接统一为 GitCode URL。"""
    text = _mapping_text(value)
    match = MAP_PR_RE.match(text)
    if not match:
        return ""
    return "https://gitcode.com/openeuler/kernel/pull/{0}".format(match.group(1))


def _load_mapping_workbook(input_file: Path) -> Any:
    """加载映射输入；支持实际为 OOXML 的错扩展名 .xls。"""
    openpyxl = _require_openpyxl()
    if input_file.suffix.lower() == ".xls":
        data = input_file.read_bytes()
        if not data.startswith(b"PK\x03\x04"):
            raise RuntimeError(
                "仅支持实际为 OOXML 的 .xls 文件；检测到旧式 BIFF .xls，"
                "请先另存为 .xlsx"
            )
        return openpyxl.load_workbook(
            io.BytesIO(data), read_only=True, data_only=False,
        )
    return openpyxl.load_workbook(
        str(input_file), read_only=True, data_only=False,
    )


def _require_openpyxl() -> Any:
    """延迟导入 openpyxl，避免 --help 依赖运行时包。"""
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError(
            "缺少依赖 openpyxl，请先运行: python3 -m pip install -r requirements.txt"
        )
    return openpyxl


def _normalize_newlines(text: str) -> str:
    """将不同换行符归一为 \\n。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _split_cell_value(value: Any) -> List[Any]:
    """按换行符拆分字符串单元格值，并忽略空白段。"""
    if value is None:
        return []
    if not isinstance(value, str):
        return [value]

    normalized = _normalize_newlines(value)
    return [part for part in normalized.split("\n") if part.strip()]


def _format_export_commit(value: Any) -> Optional[str]:
    """从分号分隔的 Commit信息 中导出 12 位 hash + 四个空格 + title。"""
    fields = [field.strip() for field in str(value).replace("；", ";").split(";")]
    if len(fields) < 2:
        return None
    commit_hash, title = fields[0].lower(), fields[1]
    if (
        len(commit_hash) < 12
        or len(commit_hash) > 40
        or not all(ch in "0123456789abcdef" for ch in commit_hash)
        or not title
    ):
        return None
    return "{0}{1}{2}".format(
        commit_hash[:12],
        COMMIT_EXPORT_SEPARATOR,
        title,
    )


def _header_text(value: Any) -> str:
    """返回用于表头匹配的文本。"""
    if value is None:
        return ""
    return str(value).strip()


def _find_commit_info_column(
    worksheet: Any, max_column: int, header_name: str = COMMIT_INFO_HEADER
) -> Optional[int]:
    """按首行表头查找 Commit信息 列号。"""
    matched_columns: List[int] = []
    for col_idx in range(1, max_column + 1):
        if _header_text(worksheet.cell(row=1, column=col_idx).value) == header_name:
            matched_columns.append(col_idx)

    if not matched_columns:
        return None
    if len(matched_columns) > 1:
        LOG.warning(
            "工作表存在多个 %s 表头: sheet=%s, columns=%s，使用第一个",
            header_name,
            worksheet.title,
            ",".join(str(col_idx) for col_idx in matched_columns),
        )
    return matched_columns[0]


def _copy_cell_format(source: Any, target: Any) -> None:
    """复制常见单元格格式。"""
    if source is target:
        return
    target._style = copy(source._style)
    target.number_format = source.number_format
    target.alignment = copy(source.alignment)
    target.protection = copy(source.protection)
    target.hyperlink = copy(source.hyperlink) if source.hyperlink else None
    target.comment = copy(source.comment) if source.comment else None


def _copy_row_format(
    worksheet: Any, source_row: int, target_row: int, max_column: int
) -> None:
    """复制行高、隐藏状态和单元格格式。"""
    _copy_row_dimension(worksheet, source_row, target_row)

    for col_idx in range(1, max_column + 1):
        _copy_cell_format(
            worksheet.cell(row=source_row, column=col_idx),
            worksheet.cell(row=target_row, column=col_idx),
        )


def _copy_row_dimension(worksheet: Any, source_row: int, target_row: int) -> None:
    """复制行高和隐藏状态等行级格式。"""
    if source_row == target_row:
        return

    source_dimension = worksheet.row_dimensions[source_row]
    target_dimension = worksheet.row_dimensions[target_row]
    target_dimension.height = source_dimension.height
    target_dimension.hidden = source_dimension.hidden
    target_dimension.outlineLevel = source_dimension.outlineLevel
    target_dimension.collapsed = source_dimension.collapsed


def _copy_row_values_and_format(
    worksheet: Any,
    source_row: int,
    target_row: int,
    max_column: int,
    commit_column: Optional[int] = None,
    commit_parts: Optional[Sequence[Any]] = None,
    part_idx: int = 0,
) -> None:
    """复制一行格式并写入目标行值。"""
    _copy_row_dimension(worksheet, source_row, target_row)

    for col_idx in range(1, max_column + 1):
        source = worksheet.cell(row=source_row, column=col_idx)
        target = worksheet.cell(row=target_row, column=col_idx)
        _copy_cell_format(source, target)
        if commit_parts is not None and col_idx == commit_column:
            target.value = _value_for_part(commit_parts, part_idx)
        else:
            target.value = source.value


def _value_for_part(parts: Sequence[Any], part_idx: int) -> Any:
    """返回某个拆分行应写入的单元格值。"""
    if part_idx < len(parts):
        return parts[part_idx]
    return None


def _should_log_progress(processed: int, total: int, interval: int) -> bool:
    """判断是否需要输出长任务进度日志。"""
    if interval <= 0 or total <= 0:
        return False
    return processed % interval == 0 or processed == total


def split_worksheet(
    worksheet: Any, progress_interval: int = DEFAULT_PROGRESS_INTERVAL
) -> Dict[str, int]:
    """拆分单个 worksheet 中 Commit信息 列包含换行的行。"""
    stats = {
        "rows_scanned": 0,
        "rows_split": 0,
        "rows_added": 0,
        "cells_split": 0,
    }
    max_row = worksheet.max_row
    max_column = worksheet.max_column
    commit_column = _find_commit_info_column(worksheet, max_column)
    if commit_column is None:
        LOG.warning(
            "跳过工作表: sheet=%s, reason=未找到 %s 表头",
            worksheet.title,
            COMMIT_INFO_HEADER,
        )
        return stats

    data_rows = max(0, max_row - 1)
    split_rows: Dict[int, List[Any]] = {}
    started_at = monotonic()

    LOG.info(
        "开始扫描工作表: sheet=%s, data_rows=%d, columns=%d, commit_column=%d",
        worksheet.title,
        data_rows,
        max_column,
        commit_column,
    )

    for row_idx in range(2, max_row + 1):
        stats["rows_scanned"] += 1
        commit_parts = _split_cell_value(
            worksheet.cell(row=row_idx, column=commit_column).value
        )
        if len(commit_parts) > 1:
            rows_to_add = len(commit_parts) - 1
            split_rows[row_idx] = commit_parts
            stats["rows_split"] += 1
            stats["rows_added"] += rows_to_add
            stats["cells_split"] += 1

        if _should_log_progress(stats["rows_scanned"], data_rows, progress_interval):
            LOG.info(
                "扫描进度: sheet=%s, scanned=%d/%d, split_rows=%d, "
                "added_rows=%d, elapsed=%.1fs",
                worksheet.title,
                stats["rows_scanned"],
                data_rows,
                stats["rows_split"],
                stats["rows_added"],
                monotonic() - started_at,
            )

    LOG.info(
        "扫描完成: sheet=%s, scanned=%d, split_rows=%d, added_rows=%d, "
        "split_cells=%d, elapsed=%.1fs",
        worksheet.title,
        stats["rows_scanned"],
        stats["rows_split"],
        stats["rows_added"],
        stats["cells_split"],
        monotonic() - started_at,
    )

    if not split_rows:
        LOG.info("无需拆分工作表: sheet=%s", worksheet.title)
        return stats

    rewrite_started_at = monotonic()
    final_rows = max_row + stats["rows_added"]
    LOG.info(
        "开始重写工作表: sheet=%s, original_rows=%d, final_rows=%d, commit_column=%d",
        worksheet.title,
        max_row,
        final_rows,
        commit_column,
    )

    added_after = 0
    rows_rewritten = 0
    for row_idx in range(max_row, 1, -1):
        commit_parts = split_rows.get(row_idx)
        rows_to_add = 0 if commit_parts is None else len(commit_parts) - 1
        added_before = stats["rows_added"] - added_after - rows_to_add
        target_start = row_idx + added_before

        if commit_parts is None:
            if target_start != row_idx:
                _copy_row_values_and_format(
                    worksheet, row_idx, target_start, max_column
                )
        else:
            for offset in range(len(commit_parts)):
                _copy_row_values_and_format(
                    worksheet,
                    row_idx,
                    target_start + offset,
                    max_column,
                    commit_column,
                    commit_parts,
                    offset,
                )

        added_after += rows_to_add
        rows_rewritten += 1
        if _should_log_progress(rows_rewritten, data_rows, progress_interval):
            LOG.info(
                "重写进度: sheet=%s, source_rows=%d/%d, elapsed=%.1fs",
                worksheet.title,
                rows_rewritten,
                data_rows,
                monotonic() - rewrite_started_at,
            )

    LOG.info(
        "重写完成: sheet=%s, final_rows=%d, elapsed=%.1fs",
        worksheet.title,
        final_rows,
        monotonic() - rewrite_started_at,
    )

    return stats


def _merge_stats(total: Dict[str, int], current: Dict[str, int]) -> None:
    """合并统计信息。"""
    for key, value in current.items():
        total[key] = total.get(key, 0) + value


def split_workbook(
    input_file: Path,
    output_file: Path,
    progress_interval: int = DEFAULT_PROGRESS_INTERVAL,
) -> Dict[str, int]:
    """读取、拆分并保存工作簿。"""
    openpyxl = _require_openpyxl()
    keep_vba = input_file.suffix.lower() == ".xlsm"
    started_at = monotonic()
    LOG.info("开始读取工作簿: input=%s, keep_vba=%s", input_file, keep_vba)
    workbook = openpyxl.load_workbook(str(input_file), keep_vba=keep_vba)
    LOG.info(
        "读取工作簿完成: sheets=%d, elapsed=%.1fs",
        len(workbook.worksheets),
        monotonic() - started_at,
    )

    stats = {
        "sheets": 0,
        "rows_scanned": 0,
        "rows_split": 0,
        "rows_added": 0,
        "cells_split": 0,
    }
    for worksheet in workbook.worksheets:
        stats["sheets"] += 1
        _merge_stats(stats, split_worksheet(worksheet, progress_interval))

    save_started_at = monotonic()
    LOG.info("开始保存工作簿: output=%s", output_file)
    workbook.save(str(output_file))
    LOG.info(
        "保存工作簿完成: output=%s, elapsed=%.1fs",
        output_file,
        monotonic() - save_started_at,
    )
    LOG.info("全部处理完成: elapsed=%.1fs", monotonic() - started_at)
    return stats


def collect_commits(input_file: Path) -> Dict[str, Any]:
    """从 Commit信息 列收集 patch_tool.py 可读取的 commit 列表。"""
    openpyxl = _require_openpyxl()
    started_at = monotonic()
    LOG.info("开始读取工作簿: input=%s, mode=export-commits", input_file)
    workbook = openpyxl.load_workbook(str(input_file), read_only=True, data_only=False)
    LOG.info(
        "读取工作簿完成: sheets=%d, elapsed=%.1fs",
        len(workbook.worksheets),
        monotonic() - started_at,
    )

    stats = {
        "sheets": 0,
        "sheets_skipped": 0,
        "rows_scanned": 0,
        "commits_exported": 0,
        "duplicates_skipped": 0,
        "invalid_commits_skipped": 0,
    }
    entries: List[str] = []
    seen: Set[str] = set()

    try:
        for worksheet in workbook.worksheets:
            stats["sheets"] += 1
            commit_column = _find_commit_info_column(
                worksheet, worksheet.max_column
            )
            if commit_column is None:
                stats["sheets_skipped"] += 1
                LOG.warning(
                    "跳过工作表: sheet=%s, reason=未找到 %s 表头",
                    worksheet.title,
                    COMMIT_INFO_HEADER,
                )
                continue

            sheet_started_at = monotonic()
            sheet_exported = 0
            sheet_duplicates = 0
            sheet_invalid = 0
            LOG.info(
                "开始导出工作表: sheet=%s, rows=%d, commit_column=%d",
                worksheet.title,
                max(0, worksheet.max_row - 1),
                commit_column,
            )

            for row_values in worksheet.iter_rows(
                min_row=2,
                max_row=worksheet.max_row,
                max_col=commit_column,
                values_only=True,
            ):
                stats["rows_scanned"] += 1
                if len(row_values) < commit_column:
                    continue
                for part in _split_cell_value(row_values[commit_column - 1]):
                    commit = _format_export_commit(part)
                    if commit is None:
                        stats["invalid_commits_skipped"] += 1
                        sheet_invalid += 1
                        continue
                    if commit in seen:
                        stats["duplicates_skipped"] += 1
                        sheet_duplicates += 1
                        continue
                    seen.add(commit)
                    entries.append(commit)
                    stats["commits_exported"] += 1
                    sheet_exported += 1

            LOG.info(
                "导出工作表完成: sheet=%s, exported=%d, duplicates=%d, invalid=%d, "
                "elapsed=%.1fs",
                worksheet.title,
                sheet_exported,
                sheet_duplicates,
                sheet_invalid,
                monotonic() - sheet_started_at,
            )
    finally:
        workbook.close()

    LOG.info(
        "全部收集完成: commits=%d, elapsed=%.1fs",
        len(entries),
        monotonic() - started_at,
    )
    return {"commits": entries, "stats": stats}


def export_commits(input_file: Path, output_file: Path) -> Dict[str, int]:
    """从 Commit信息 列导出 patch_tool.py 可读取的文本列表。"""
    started_at = monotonic()
    result = collect_commits(input_file)
    entries = result["commits"]
    stats = result["stats"]

    output_started_at = monotonic()
    LOG.info("开始写出 commit 列表: output=%s", output_file)
    with output_file.open("w", encoding="utf-8", newline="\n") as f:
        for entry in entries:
            f.write("{0}\n".format(entry))

    LOG.info(
        "写出 commit 列表完成: output=%s, commits=%d, elapsed=%.1fs",
        output_file,
        stats["commits_exported"],
        monotonic() - output_started_at,
    )
    LOG.info("全部导出完成: elapsed=%.1fs", monotonic() - started_at)
    return stats


def _mapping_header_indexes(worksheet: Any) -> Dict[str, int]:
    """读取映射源工作表首行表头，保留每个表头首次出现的位置。"""
    indexes: Dict[str, int] = {}
    for col_idx, cell in enumerate(worksheet[1], 1):
        text = _mapping_text(cell.value)
        if text and text not in indexes:
            indexes[text] = col_idx - 1
    return indexes


def _collect_mapping_records(
    input_file: Path,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], List[Dict[str, Any]]]:
    """扫描输入工作簿并收集内核 commit 映射记录。"""
    workbook = _load_mapping_workbook(input_file)
    records: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    stats = {
        "worksheets_scanned": 0,
        "sheets_skipped": 0,
        "source_rows_scanned": 0,
        "source_rows_selected": 0,
        "commit_records": 0,
        "invalid_records": 0,
    }
    compatible_sheets = 0
    try:
        for worksheet in workbook.worksheets:
            stats["worksheets_scanned"] += 1
            indexes = _mapping_header_indexes(worksheet)
            missing = [name for name in MAP_REQUIRED_HEADERS if name not in indexes]
            if missing:
                stats["sheets_skipped"] += 1
                LOG.warning(
                    "跳过映射工作表: sheet=%s, 缺少表头=%s",
                    worksheet.title, ",".join(missing),
                )
                continue

            compatible_sheets += 1
            for row_number, values in enumerate(
                worksheet.iter_rows(min_row=2, values_only=True), 2
            ):
                stats["source_rows_scanned"] += 1
                row = {
                    name: values[index] if index < len(values) else None
                    for name, index in indexes.items()
                }
                community = _mapping_text(row.get("社区类型"))
                if community not in MAP_KERNEL_COMMUNITIES:
                    continue

                stats["source_rows_selected"] += 1
                parsed, invalid = _parse_mapping_commit_records(
                    row.get(COMMIT_INFO_HEADER),
                )
                if invalid:
                    stats["invalid_records"] += invalid
                    issues.append({
                        "kind": "invalid_commit_record",
                        "sheet": worksheet.title,
                        "source_row": row_number,
                        "count": invalid,
                    })
                for commit_position, commit in enumerate(parsed, 1):
                    records.append({
                        "sheet": worksheet.title,
                        "source_row": row_number,
                        "commit_position": commit_position,
                        "community": community,
                        "source_hash": commit["source_hash"],
                        "title": commit["title"],
                        "sr_description": _mapping_source_text(row.get("SR描述")),
                        "ar_id": _mapping_text(row.get("AR编号")),
                        "ar_sub_id": _mapping_text(row.get("AR子编号")),
                        "ar_description": _mapping_source_text(row.get("标题描述")),
                        "version_module": _mapping_source_text(row.get("版本模块")),
                        "community_link": _mapping_text(row.get("社区链接")),
                        "customer_impact": _mapping_source_text(row.get("客户影响")),
                    })
    finally:
        workbook.close()

    if compatible_sheets == 0:
        raise RuntimeError(
            "没有找到包含映射所需表头的工作表: {0}".format(
                ",".join(MAP_REQUIRED_HEADERS)
            )
        )
    stats["commit_records"] = len(records)
    return records, stats, issues


def _validate_mapping_paths(
    input_file: Path,
    output_file: Path,
    force: bool,
) -> Optional[str]:
    """校验 map-patches 的输入、输出和覆盖策略。"""
    if not input_file.exists():
        return "输入文件不存在: {0}".format(input_file)
    if not input_file.is_file():
        return "输入路径不是普通文件: {0}".format(input_file)
    if input_file.suffix.lower() not in (".xlsx", ".xlsm", ".xls"):
        return "不支持的输入格式，仅支持 .xlsx/.xlsm/.xls(OOXML): {0}".format(
            input_file
        )
    try:
        if input_file.resolve() == output_file.resolve():
            return "输入和输出不能是同一个文件"
    except OSError:
        return "无法解析输入或输出路径"
    if output_file.suffix.lower() != ".xlsx":
        return "映射输出必须使用 .xlsx 格式: {0}".format(output_file)
    if output_file.parent and not output_file.parent.exists():
        return "输出目录不存在: {0}".format(output_file.parent)
    if output_file.exists() and not force:
        return "输出文件已存在，请使用 --force 覆盖: {0}".format(output_file)
    return None


def _save_mapping_workbook(
    output_file: Path,
    rows: List[List[Any]],
    force: bool,
) -> None:
    """以临时文件原子写出映射工作簿。"""
    openpyxl = _require_openpyxl()
    if output_file.exists() and not force:
        raise RuntimeError("输出文件已存在，请使用 --force 覆盖: {0}".format(output_file))

    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = MAP_PATCHES_SHEET
    worksheet.append(MAP_PATCHES_HEADERS)
    for row in rows:
        worksheet.append(row)
        for cell in worksheet[worksheet.max_row]:
            if isinstance(cell.value, str):
                # openpyxl 会默认把以 = 开头的字符串当作公式。映射
                # 产物只包含文本数据，显式标记为字符串以保留原值。
                cell.data_type = "s"

    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".xlsx", prefix=".map-patches-",
            dir=str(output_file.parent), delete=False,
        ) as temp_file:
            temp_name = temp_file.name
        workbook.save(temp_name)
        os.replace(temp_name, str(output_file))
    finally:
        workbook.close()
        if temp_name and os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                LOG.warning("无法清理临时文件: %s", temp_name)


def map_patches_workbook(
    input_file: Path,
    output_file: Path,
    oe_repo: str,
    upstream_repo: str,
    oe_ref: str = "OLK-6.6",
    upstream_ref: str = "master",
    product_version: str = "950",
    force: bool = False,
) -> Dict[str, Any]:
    """将 950 内核使能清单转换为 patches 21 列格式。"""
    path_error = _validate_mapping_paths(input_file, output_file, force)
    if path_error:
        raise RuntimeError(path_error)
    if not _mapping_text(oe_repo) or not _mapping_text(upstream_repo):
        raise RuntimeError("必须同时提供 openEuler 和 upstream git 仓库路径")

    records, stats, issues = _collect_mapping_records(input_file)
    from patch_tool import resolve_commit_entries

    oe_queries: List[Dict[str, Optional[str]]] = []
    upstream_queries: List[Dict[str, Optional[str]]] = []
    for record in records:
        if record["community"] == "合入openEuler olk内核社区版本":
            oe_queries.append({"hash": record["source_hash"], "title": ""})
            upstream_queries.append({"hash": "", "title": record["title"]})
        else:
            oe_queries.append({"hash": "", "title": record["title"]})
            upstream_queries.append({"hash": record["source_hash"], "title": ""})

    oe_result = resolve_commit_entries(
        oe_queries, repo=oe_repo, branch=oe_ref, long_hash=True, hash_first=True,
    )
    upstream_result = resolve_commit_entries(
        upstream_queries, repo=upstream_repo, branch=upstream_ref,
        long_hash=True, hash_first=True,
    )
    repo_errors = list(oe_result.get("repo_errors", [])) + list(
        upstream_result.get("repo_errors", [])
    )
    if repo_errors:
        raise RuntimeError("无法读取提交仓库: {0}".format(repo_errors))

    output_rows: List[List[Any]] = []
    ambiguous_oe = 0
    ambiguous_upstream = 0
    for index, record in enumerate(records):
        oe_match = oe_result["results"][index]
        upstream_match = upstream_result["results"][index]
        if oe_match["alternate_matches"]:
            ambiguous_oe += 1
            issues.append({
                "kind": "ambiguous_oe_title",
                "sheet": record["sheet"],
                "source_row": record["source_row"],
                "commit_position": record["commit_position"],
                "source_hash": record["source_hash"],
                "title": record["title"],
                "alternates": oe_match["alternate_matches"],
            })
        if upstream_match["alternate_matches"]:
            ambiguous_upstream += 1
            issues.append({
                "kind": "ambiguous_upstream_title",
                "sheet": record["sheet"],
                "source_row": record["source_row"],
                "commit_position": record["commit_position"],
                "source_hash": record["source_hash"],
                "title": record["title"],
                "alternates": upstream_match["alternate_matches"],
            })

        if oe_match["status"] != "Y":
            issues.append({
                "kind": "oe_unmatched",
                "sheet": record["sheet"],
                "source_row": record["source_row"],
                "commit_position": record["commit_position"],
                "source_hash": record["source_hash"],
                "title": record["title"],
            })
        if upstream_match["status"] != "Y":
            issues.append({
                "kind": "upstream_unmatched",
                "sheet": record["sheet"],
                "source_row": record["source_row"],
                "commit_position": record["commit_position"],
                "source_hash": record["source_hash"],
                "title": record["title"],
            })

        oe_id = _mapping_text(oe_match.get("commit_id"))[:12]
        upstream_id = _mapping_text(upstream_match.get("commit_id"))[:12]
        source_id = record["source_hash"][:12]
        commit_key = oe_id or upstream_id or source_id
        canonical_title = (
            _mapping_text(oe_match.get("title"))
            if oe_match["status"] == "Y"
            else record["title"]
        )
        patch_type = _mapping_patch_type(record["ar_sub_id"])
        unique_id = "{0}-{1}-{2}".format(
            record["ar_id"], record["ar_sub_id"], commit_key,
        )
        output_rows.append([
            record["sr_description"],
            record["ar_description"],
            "",
            patch_type,
            product_version,
            "内核态",
            record["version_module"],
            canonical_title,
            oe_id,
            "",
            _mapping_text(oe_match.get("git_describe")),
            _normalize_kernel_pr_url(record["community_link"]),
            upstream_id,
            _mapping_text(upstream_match.get("git_describe")),
            unique_id,
            "否",
            record["customer_impact"],
            "未合入",
            "",
            "",
            "",
        ])

    stats.update({
        "rows_written": len(output_rows),
        "oe_matched": oe_result.get("matched", 0),
        "oe_unmatched": oe_result.get("unmatched", 0),
        "upstream_matched": upstream_result.get("matched", 0),
        "upstream_unmatched": upstream_result.get("unmatched", 0),
        "ambiguous_oe": ambiguous_oe,
        "ambiguous_upstream": ambiguous_upstream,
    })
    _save_mapping_workbook(output_file, output_rows, force=force)
    return {
        "output_file": str(output_file),
        "stats": stats,
        "issues": issues,
    }


def _validate_input_excel_path(input_file: Path) -> Optional[str]:
    """校验输入 Excel 路径。"""
    if not input_file.exists():
        return "输入文件不存在: {0}".format(input_file)
    if not input_file.is_file():
        return "输入路径不是普通文件: {0}".format(input_file)
    if input_file.suffix.lower() not in SUPPORTED_SUFFIXES:
        return "不支持的输入格式，仅支持 .xlsx/.xlsm: {0}".format(input_file)
    return None


def _validate_paths(input_file: Path, output_file: Path) -> Optional[str]:
    """校验输入输出路径。"""
    input_error = _validate_input_excel_path(input_file)
    if input_error:
        return input_error
    if output_file.suffix.lower() not in SUPPORTED_SUFFIXES:
        return "不支持的输出格式，仅支持 .xlsx/.xlsm: {0}".format(output_file)
    if output_file.parent and not output_file.parent.exists():
        return "输出目录不存在: {0}".format(output_file.parent)
    return None


def _validate_export_paths(input_file: Path, output_file: Path) -> Optional[str]:
    """校验 export-commits 输入输出路径。"""
    input_error = _validate_input_excel_path(input_file)
    if input_error:
        return input_error
    if output_file.parent and not output_file.parent.exists():
        return "输出目录不存在: {0}".format(output_file.parent)
    return None


def build_parser(prog: Optional[str] = None) -> argparse.ArgumentParser:
    """构造默认拆分命令参数解析器。"""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="按 Commit信息 列换行符拆分 Excel 行",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", metavar="INPUT", help="输入 Excel 文件（.xlsx/.xlsm）")
    parser.add_argument("output", metavar="OUTPUT", help="输出 Excel 文件（.xlsx/.xlsm）")
    parser.add_argument(
        "--log-interval",
        type=int,
        default=DEFAULT_PROGRESS_INTERVAL,
        metavar="ROWS",
        help=(
            "扫描/重写进度日志的行间隔，0 表示关闭进度日志 "
            "（默认 {0}）".format(DEFAULT_PROGRESS_INTERVAL)
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="输出 debug 日志",
    )
    return parser


def build_export_commits_parser(prog: Optional[str] = None) -> argparse.ArgumentParser:
    """构造 export-commits 子命令参数解析器。"""
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "从分号分隔的 Excel Commit信息 列导出 12 位 hash + 四个空格 + "
            "title 的 commit 列表"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", metavar="INPUT", help="输入 Excel 文件（.xlsx/.xlsm）")
    parser.add_argument("output", metavar="OUTPUT", help="输出文本文件")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="输出 debug 日志",
    )
    return parser


def build_map_patches_parser(prog: Optional[str] = None) -> argparse.ArgumentParser:
    """构造 map-patches 子命令参数解析器。"""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="将 950 芯片内核使能清单映射为 patches 21 列格式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input", metavar="INPUT",
        help="输入 .xlsx/.xlsm 或实际为 OOXML 的 .xls 工作簿",
    )
    parser.add_argument("output", metavar="OUTPUT", help="输出 .xlsx 工作簿")
    parser.add_argument(
        "--oe-repo", required=True, metavar="PATH",
        help="openEuler kernel 本地 git 仓库",
    )
    parser.add_argument(
        "--upstream-repo", required=True, metavar="PATH",
        help="torvalds/linux 本地 git 仓库",
    )
    parser.add_argument(
        "--oe-ref", default="OLK-6.6", metavar="REF",
        help="openEuler 查询引用（默认 OLK-6.6；也可使用 origin/REF）",
    )
    parser.add_argument(
        "--upstream-ref", default="master", metavar="REF",
        help="上游查询引用（默认 master；也可使用 origin/REF）",
    )
    parser.add_argument(
        "--product-version", default="950", metavar="VERSION",
        help="产品版本字段（默认 950）",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="允许覆盖已存在的输出文件",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="输出 debug 日志",
    )
    return parser


def _configure_logging(verbose: bool) -> None:
    """初始化日志。"""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
    )


def cmd_split(args: argparse.Namespace) -> int:
    """执行默认 Excel 拆分命令。"""
    input_file = Path(args.input)
    output_file = Path(args.output)

    error = _validate_paths(input_file, output_file)
    if error:
        print("错误: {0}".format(error), file=sys.stderr)
        return 1

    try:
        stats = split_workbook(input_file, output_file, args.log_interval)
    except RuntimeError as exc:
        print("错误: {0}".format(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print("错误: 无法处理 Excel 文件: {0}".format(exc), file=sys.stderr)
        return 1

    print(
        "处理完成: sheets={sheets}, scanned_rows={rows_scanned}, "
        "split_rows={rows_split}, added_rows={rows_added}, split_cells={cells_split}".format(
            **stats
        )
    )
    print("输出文件: {0}".format(output_file))
    return 0


def cmd_export_commits(args: argparse.Namespace) -> int:
    """执行 export-commits 子命令。"""
    input_file = Path(args.input)
    output_file = Path(args.output)

    error = _validate_export_paths(input_file, output_file)
    if error:
        print("错误: {0}".format(error), file=sys.stderr)
        return 1

    try:
        stats = export_commits(input_file, output_file)
    except RuntimeError as exc:
        print("错误: {0}".format(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print("错误: 无法导出 commit 列表: {0}".format(exc), file=sys.stderr)
        return 1

    print(
        "导出完成: sheets={sheets}, skipped_sheets={sheets_skipped}, "
        "scanned_rows={rows_scanned}, commits={commits_exported}, "
        "duplicates_skipped={duplicates_skipped}, "
        "invalid_skipped={invalid_commits_skipped}".format(**stats)
    )
    print("输出文件: {0}".format(output_file))
    return 0


def cmd_map_patches(args: argparse.Namespace) -> int:
    """执行 map-patches 子命令。"""
    input_file = Path(args.input)
    output_file = Path(args.output)
    try:
        result = map_patches_workbook(
            input_file=input_file,
            output_file=output_file,
            oe_repo=args.oe_repo,
            upstream_repo=args.upstream_repo,
            oe_ref=args.oe_ref,
            upstream_ref=args.upstream_ref,
            product_version=args.product_version,
            force=args.force,
        )
    except (RuntimeError, OSError, ValueError) as exc:
        print("错误: 无法映射 Excel 补丁: {0}".format(exc), file=sys.stderr)
        return 1

    stats = result["stats"]
    print(
        "映射完成: worksheets={worksheets_scanned}, skipped_sheets={sheets_skipped}, "
        "source_rows={source_rows_scanned}, selected_rows={source_rows_selected}, "
        "commits={commit_records}, rows_written={rows_written}, "
        "oe_matched={oe_matched}, oe_unmatched={oe_unmatched}, "
        "upstream_matched={upstream_matched}, "
        "upstream_unmatched={upstream_unmatched}, "
        "ambiguous_oe={ambiguous_oe}, "
        "ambiguous_upstream={ambiguous_upstream}, "
        "issues={issues}".format(
            issues=len(result.get("issues", [])), **stats
        )
    )
    print("输出文件: {0}".format(output_file))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """程序入口。"""
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] == "map-patches":
        parser = build_map_patches_parser(
            prog="{0} map-patches".format(Path(sys.argv[0]).name)
        )
        args = parser.parse_args(raw_args[1:])
        _configure_logging(args.verbose)
        return cmd_map_patches(args)
    if raw_args and raw_args[0] == "export-commits":
        parser = build_export_commits_parser(
            prog="{0} export-commits".format(Path(sys.argv[0]).name)
        )
        args = parser.parse_args(raw_args[1:])
        _configure_logging(args.verbose)
        return cmd_export_commits(args)

    args = build_parser(prog=Path(sys.argv[0]).name).parse_args(raw_args)
    _configure_logging(args.verbose)
    return cmd_split(args)


if __name__ == "__main__":
    sys.exit(main())
