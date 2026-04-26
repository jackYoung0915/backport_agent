#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Excel 表格换行单元格拆行工具。

读取 .xlsx/.xlsm 工作簿，逐行检查每个单元格内容；如果某一行中任意
字符串单元格包含换行符，则将这一行按换行段展开为多行。

拆分规则：
  - 支持 \\n、\\r\\n、\\r
  - 同一行按所有单元格的最大拆分段数展开
  - 无换行单元格只保留在展开后的第一行，后续行留空
  - 某个单元格段数不足时，缺失段留空
"""

import argparse
import logging
import sys
from copy import copy
from pathlib import Path
from time import monotonic
from typing import Any, Dict, List, Optional, Sequence, Tuple


SUPPORTED_SUFFIXES = (".xlsx", ".xlsm")
DEFAULT_PROGRESS_INTERVAL = 10000

LOG = logging.getLogger(__name__)


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
    """按换行符拆分字符串单元格值。"""
    if not isinstance(value, str):
        return [value]

    normalized = _normalize_newlines(value)
    if "\n" not in normalized:
        return [value]
    return normalized.split("\n")


def _row_split_parts_from_values(
    row_values: Sequence[Any],
) -> Tuple[int, int, List[List[Any]]]:
    """返回一行的最大拆分段数、含换行单元格数和每列拆分段。"""
    max_parts = 1
    split_cells = 0
    parts_by_column: List[List[Any]] = []

    for value in row_values:
        parts = _split_cell_value(value)
        if len(parts) > 1:
            split_cells += 1
            max_parts = max(max_parts, len(parts))
        parts_by_column.append(parts)

    return max_parts, split_cells, parts_by_column


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
    parts_by_column: Optional[Sequence[Sequence[Any]]] = None,
    part_idx: int = 0,
) -> None:
    """复制一行格式并写入目标行值。"""
    _copy_row_dimension(worksheet, source_row, target_row)

    for col_idx in range(1, max_column + 1):
        source = worksheet.cell(row=source_row, column=col_idx)
        target = worksheet.cell(row=target_row, column=col_idx)
        _copy_cell_format(source, target)
        if parts_by_column is None:
            target.value = source.value
        else:
            target.value = _value_for_part(parts_by_column[col_idx - 1], part_idx)


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
    """拆分单个 worksheet 中包含换行单元格的行。"""
    stats = {
        "rows_scanned": 0,
        "rows_split": 0,
        "rows_added": 0,
        "cells_split": 0,
    }
    max_row = worksheet.max_row
    max_column = worksheet.max_column
    split_rows: Dict[int, Tuple[int, List[List[Any]]]] = {}
    started_at = monotonic()

    LOG.info(
        "开始扫描工作表: sheet=%s, rows=%d, columns=%d",
        worksheet.title,
        max_row,
        max_column,
    )

    for row_idx, row_values in enumerate(
        worksheet.iter_rows(
            min_row=1,
            max_row=max_row,
            max_col=max_column,
            values_only=True,
        ),
        start=1,
    ):
        stats["rows_scanned"] += 1
        max_parts, split_cells, parts_by_column = _row_split_parts_from_values(
            row_values
        )
        if max_parts > 1:
            rows_to_add = max_parts - 1
            split_rows[row_idx] = (max_parts, parts_by_column)
            stats["rows_split"] += 1
            stats["rows_added"] += rows_to_add
            stats["cells_split"] += split_cells

        if _should_log_progress(stats["rows_scanned"], max_row, progress_interval):
            LOG.info(
                "扫描进度: sheet=%s, scanned=%d/%d, split_rows=%d, "
                "added_rows=%d, elapsed=%.1fs",
                worksheet.title,
                stats["rows_scanned"],
                max_row,
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
        "开始重写工作表: sheet=%s, original_rows=%d, final_rows=%d",
        worksheet.title,
        max_row,
        final_rows,
    )

    added_after = 0
    rows_rewritten = 0
    for row_idx in range(max_row, 0, -1):
        split_plan = split_rows.get(row_idx)
        rows_to_add = 0 if split_plan is None else split_plan[0] - 1
        added_before = stats["rows_added"] - added_after - rows_to_add
        target_start = row_idx + added_before

        if split_plan is None:
            if target_start != row_idx:
                _copy_row_values_and_format(
                    worksheet, row_idx, target_start, max_column
                )
        else:
            max_parts, parts_by_column = split_plan
            for offset in range(max_parts):
                _copy_row_values_and_format(
                    worksheet,
                    row_idx,
                    target_start + offset,
                    max_column,
                    parts_by_column,
                    offset,
                )

        added_after += rows_to_add
        rows_rewritten += 1
        if _should_log_progress(rows_rewritten, max_row, progress_interval):
            LOG.info(
                "重写进度: sheet=%s, source_rows=%d/%d, elapsed=%.1fs",
                worksheet.title,
                rows_rewritten,
                max_row,
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


def _validate_paths(input_file: Path, output_file: Path) -> Optional[str]:
    """校验输入输出路径。"""
    if not input_file.exists():
        return "输入文件不存在: {0}".format(input_file)
    if not input_file.is_file():
        return "输入路径不是普通文件: {0}".format(input_file)
    if input_file.suffix.lower() not in SUPPORTED_SUFFIXES:
        return "不支持的输入格式，仅支持 .xlsx/.xlsm: {0}".format(input_file)
    if output_file.suffix.lower() not in SUPPORTED_SUFFIXES:
        return "不支持的输出格式，仅支持 .xlsx/.xlsm: {0}".format(output_file)
    if output_file.parent and not output_file.parent.exists():
        return "输出目录不存在: {0}".format(output_file.parent)
    return None


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="按单元格换行符拆分 Excel 行",
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


def main(argv: Optional[Sequence[str]] = None) -> int:
    """程序入口。"""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

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


if __name__ == "__main__":
    sys.exit(main())
