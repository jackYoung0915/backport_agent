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
import sys
from copy import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


SUPPORTED_SUFFIXES = (".xlsx", ".xlsm")


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


def _row_split_parts(
    worksheet: Any, row_idx: int, max_column: int
) -> Tuple[int, int, List[List[Any]]]:
    """返回一行的最大拆分段数、含换行单元格数和每列拆分段。"""
    max_parts = 1
    split_cells = 0
    parts_by_column: List[List[Any]] = []

    for col_idx in range(1, max_column + 1):
        value = worksheet.cell(row=row_idx, column=col_idx).value
        parts = _split_cell_value(value)
        if len(parts) > 1:
            split_cells += 1
            max_parts = max(max_parts, len(parts))
        parts_by_column.append(parts)

    return max_parts, split_cells, parts_by_column


def _copy_cell_format(source: Any, target: Any) -> None:
    """复制常见单元格格式。"""
    if source.has_style:
        target._style = copy(source._style)
    target.number_format = source.number_format
    target.alignment = copy(source.alignment)
    target.protection = copy(source.protection)


def _copy_row_format(
    worksheet: Any, source_row: int, target_row: int, max_column: int
) -> None:
    """复制行高、隐藏状态和单元格格式。"""
    source_dimension = worksheet.row_dimensions[source_row]
    target_dimension = worksheet.row_dimensions[target_row]
    target_dimension.height = source_dimension.height
    target_dimension.hidden = source_dimension.hidden
    target_dimension.outlineLevel = source_dimension.outlineLevel
    target_dimension.collapsed = source_dimension.collapsed

    for col_idx in range(1, max_column + 1):
        _copy_cell_format(
            worksheet.cell(row=source_row, column=col_idx),
            worksheet.cell(row=target_row, column=col_idx),
        )


def _value_for_part(parts: Sequence[Any], part_idx: int) -> Any:
    """返回某个拆分行应写入的单元格值。"""
    if part_idx < len(parts):
        return parts[part_idx]
    return None


def split_worksheet(worksheet: Any) -> Dict[str, int]:
    """拆分单个 worksheet 中包含换行单元格的行。"""
    stats = {
        "rows_scanned": 0,
        "rows_split": 0,
        "rows_added": 0,
        "cells_split": 0,
    }
    max_column = worksheet.max_column

    for row_idx in range(worksheet.max_row, 0, -1):
        stats["rows_scanned"] += 1
        max_parts, split_cells, parts_by_column = _row_split_parts(
            worksheet, row_idx, max_column
        )
        if max_parts <= 1:
            continue

        rows_to_add = max_parts - 1
        worksheet.insert_rows(row_idx + 1, rows_to_add)
        for offset in range(1, max_parts):
            _copy_row_format(worksheet, row_idx, row_idx + offset, max_column)

        for offset in range(max_parts):
            target_row = row_idx + offset
            for col_idx, parts in enumerate(parts_by_column, start=1):
                worksheet.cell(row=target_row, column=col_idx).value = _value_for_part(
                    parts, offset
                )

        stats["rows_split"] += 1
        stats["rows_added"] += rows_to_add
        stats["cells_split"] += split_cells

    return stats


def _merge_stats(total: Dict[str, int], current: Dict[str, int]) -> None:
    """合并统计信息。"""
    for key, value in current.items():
        total[key] = total.get(key, 0) + value


def split_workbook(input_file: Path, output_file: Path) -> Dict[str, int]:
    """读取、拆分并保存工作簿。"""
    openpyxl = _require_openpyxl()
    keep_vba = input_file.suffix.lower() == ".xlsm"
    workbook = openpyxl.load_workbook(str(input_file), keep_vba=keep_vba)

    stats = {
        "sheets": 0,
        "rows_scanned": 0,
        "rows_split": 0,
        "rows_added": 0,
        "cells_split": 0,
    }
    for worksheet in workbook.worksheets:
        stats["sheets"] += 1
        _merge_stats(stats, split_worksheet(worksheet))

    workbook.save(str(output_file))
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
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """程序入口。"""
    args = build_parser().parse_args(argv)
    input_file = Path(args.input)
    output_file = Path(args.output)

    error = _validate_paths(input_file, output_file)
    if error:
        print("错误: {0}".format(error), file=sys.stderr)
        return 1

    try:
        stats = split_workbook(input_file, output_file)
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
