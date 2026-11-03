#!/usr/bin/env python3
"""Score Linux kernel backport difficulty from a patch and explicit evidence."""

import argparse
from decimal import Decimal, ROUND_HALF_UP
import json
import re
import shlex
import sys
from pathlib import Path

WEIGHTS = {"conflict": Decimal("0.30"), "kabi": Decimal("0.25"), "subsystem": Decimal("0.25"), "semantic": Decimal("0.10"), "verification": Decimal("0.10")}
LABELS = {"conflict": "代码冲突量", "kabi": "KABI 变化", "subsystem": "核心子系统侵入", "semantic": "语义适配", "verification": "验证成本"}
STATUS_FACTOR = {"observed": Decimal("1"), "manual": Decimal("0.85"), "heuristic": Decimal("0.65"), "unknown": Decimal("0.2")}
FORMULAS = {
    "conflict": "min(100,min(60,15*H+5*max(0,F-1))+30*min(1,H/T)+10*min(1,L/100))",
    "kabi": "min(100,10*A+30*R+25*P+30*S+25*W)",
    "subsystem": "min(100,min(50,10*C+6*H+2*O)+15*L+15*G+10*T+10*I)",
    "semantic": "min(100,min(20,5*D)+min(30,10*A)+min(30,30*R/T)+min(20,10*H))",
    "verification": "min(100,min(25,5*B)+min(25,5*S)+25*R+25*C)",
}
GAPS = {
    "conflict": "在临时 worktree 对明确的目标 commit 实际三方应用补丁，记录命令及冲突统计。",
    "kabi": "使用发行版 KABI 清单/白名单和 libabigail 等工具比较目标基线与回合结果。",
    "semantic": "检查依赖提交、目标树 API 与并发/生命周期前提，记录人工改写 hunk。",
    "verification": "确定目标架构、配置、功能、启动、压力与并发测试的最小验证矩阵。",
}
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")
EXPORT_RE = re.compile(r"\b(EXPORT_SYMBOL(?:_GPL|_NS|_GPL_NS)?)\s*\(\s*(\w+)\s*\)")


def rounded(value, places="0.1"):
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def as_number(value):
    return int(value) if value == int(value) else float(value)


def count(group, key):
    value = group.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} 必须是非负整数，且不能省略或为 null")
    return value


def flag(group, key):
    value = group.get(key)
    if isinstance(value, bool):
        return int(value)
    if not isinstance(value, int) or value not in (0, 1):
        raise ValueError(f"{key} 必须是布尔值或 0/1")
    return value


def group(evidence, name, counts=(), flags=(), source=False):
    if name not in evidence:
        return None
    value = evidence[name]
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{name} 必须是非空对象；没有证据时请省略整个维度")
    result = {key: count(value, key) for key in counts}
    result.update({key: flag(value, key) for key in flags})
    if source and (not isinstance(value.get("source"), str) or not value["source"].strip()):
        raise ValueError(f"{name}.source 必须说明实测工具、命令或证据来源")
    result["source"] = value.get("source", "未注明来源的人工分析")
    locations = value.get("locations", [])
    if not isinstance(locations, list) or not all(isinstance(item, str) for item in locations):
        raise ValueError(f"{name}.locations 必须是字符串数组")
    result["locations"] = locations
    return result


def clean_path(value):
    value = value.split("\t", 1)[0].strip()
    if value.startswith('"'):
        value = shlex.split(value)[0]
    if value.startswith(("a/", "b/")):
        value = value[2:]
    return value


def parse_patch(path):
    files, changed_lines = [], []
    hunks = additions = deletions = 0
    current = None
    in_hunk = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if raw.startswith("diff --git "):
            try:
                tokens = shlex.split(raw)
                current = clean_path(tokens[3])
            except (ValueError, IndexError):
                current = None
            if current and current != "/dev/null":
                files.append(current)
            in_hunk = False
        elif raw.startswith("+++ "):
            candidate = clean_path(raw[4:])
            if candidate != "/dev/null":
                current = candidate
                files.append(candidate)
            in_hunk = False
        elif HUNK_RE.match(raw):
            hunks += 1
            in_hunk = True
        elif raw.startswith(("diff ", "--- ", "index ", "@@")):
            in_hunk = False
        elif in_hunk and raw.startswith("+") and not raw.startswith("+++"):
            additions += 1
            changed_lines.append({"path": current, "sign": "+", "text": raw[1:]})
        elif in_hunk and raw.startswith("-") and not raw.startswith("---"):
            deletions += 1
            changed_lines.append({"path": current, "sign": "-", "text": raw[1:]})
    if not files or not hunks:
        raise ValueError("补丁中未识别到文件或标准 hunk，请提供统一 diff")
    return {"files": list(dict.fromkeys(files)), "changed_hunks": hunks, "additions": additions, "deletions": deletions, "changed_lines": changed_lines}


def path_class(path):
    if any(path.startswith(prefix) for prefix in ("kernel/sched/", "kernel/locking/", "kernel/rcu/", "mm/", "arch/", "include/linux/", "include/uapi/")):
        return "critical"
    if any(path.startswith(prefix) for prefix in ("fs/", "block/", "net/core/", "drivers/base/", "kernel/")):
        return "high"
    return "other"


def automatic_signals(patch):
    subsystem = {"critical_files": 0, "high_risk_files": 0, "other_files": 0}
    for path in patch["files"]:
        subsystem[{"critical": "critical_files", "high": "high_risk_files", "other": "other_files"}[path_class(path)]] += 1
    added, removed, headers, content = {}, {}, set(), []
    for item in patch["changed_lines"]:
        text = item["text"]
        if text.lstrip().startswith(("//", "*", "/*")):
            continue
        content.append(text)
        for macro, symbol in EXPORT_RE.findall(text):
            (added if item["sign"] == "+" else removed)[symbol] = macro
        path = item["path"] or ""
        if path.endswith(".h") and path.startswith(("include/", "arch/")):
            headers.add(path)
    joined = "\n".join(content)
    subsystem.update({
        "locking_changes": int(bool(re.search(r"\b(?:spin_lock|mutex_lock|rwsem|rcu_read_lock|synchronize_rcu|lockdep)\w*\b", joined))),
        "global_api_changes": int(bool(headers)),
        "lifetime_changes": int(bool(re.search(r"\b(?:kref|refcount|kfree|free_pages|put_device|get_device)\w*\b", joined))),
        "interrupt_context_changes": int(bool(re.search(r"\b(?:irq_disable|irq_enable|local_irq|in_interrupt|softirq|hardirq)\w*\b", joined))),
    })
    kabi_text = {
        "added_exports": sorted(set(added) - set(removed)),
        "removed_exports": sorted(set(removed) - set(added)),
        "export_attribute_changes": sorted(symbol for symbol in set(added) & set(removed) if added[symbol] != removed[symbol]),
        "header_files_touched": sorted(headers),
    }
    return subsystem, kabi_text


def unknown(source, detail, locations=None):
    return {"score": Decimal("50"), "status": "unknown", "detail": detail, "source": "无完整证据", "locations": locations or [], "measurements": None}


def score_dimensions(patch, evidence):
    auto_sub, kabi_text = automatic_signals(patch)
    result = {}

    c = group(evidence, "conflict", counts=("conflicted_hunks", "conflicted_files", "conflict_lines"), flags=("apply_attempted", "apply_succeeded"), source=True)
    if c is None:
        result["conflict"] = unknown("conflict", "缺少目标树实际应用结果")
    else:
        ch, cf, cl = c["conflicted_hunks"], c["conflicted_files"], c["conflict_lines"]
        if not c["apply_attempted"]:
            raise ValueError("conflict.apply_attempted=0 时不能提供 observed 冲突统计")
        if ch > patch["changed_hunks"] or cf > len(patch["files"]):
            raise ValueError("冲突 hunk/文件数不能超过补丁总 hunk/文件数")
        if ch == 0 and (cf != 0 or cl != 0 or not c["apply_succeeded"]):
            raise ValueError("零冲突必须同时满足零冲突文件/行且补丁成功应用")
        if ch > 0 and cf == 0:
            raise ValueError("存在冲突 hunk 时 conflicted_files 必须大于 0")
        score = min(100, min(60, 15 * ch + 5 * max(0, cf - 1)) + 30 * min(1, ch / patch["changed_hunks"]) + 10 * min(1, cl / 100))
        result["conflict"] = {"score": Decimal(str(score)), "status": "observed", "detail": f"{ch} 个冲突 hunk / {cf} 个文件 / {cl} 个冲突行；应用成功={bool(c['apply_succeeded'])}", **c}

    k = group(evidence, "kabi", counts=("added_exported_symbols", "removed_exported_symbols", "prototype_changes", "layout_changes", "whitelist_violations"), source=True)
    if k is None:
        detail = "未实测 KABI；文本线索：" + json.dumps(kabi_text, ensure_ascii=False)
        result["kabi"] = unknown("kabi", detail, kabi_text["header_files_touched"])
        result["kabi"]["text_signals"] = kabi_text
    else:
        score = min(100, 10 * k["added_exported_symbols"] + 30 * k["removed_exported_symbols"] + 25 * k["prototype_changes"] + 30 * k["layout_changes"] + 25 * k["whitelist_violations"])
        detail = f"新增/删除导出符号 {k['added_exported_symbols']}/{k['removed_exported_symbols']}，原型 {k['prototype_changes']}，布局 {k['layout_changes']}，白名单 {k['whitelist_violations']}"
        result["kabi"] = {"score": Decimal(str(score)), "status": "observed", "detail": detail, **k}

    supplied = evidence.get("subsystem")
    if supplied is not None:
        mechanisms = group(evidence, "subsystem", flags=("locking_changes", "global_api_changes", "lifetime_changes", "interrupt_context_changes"))
        s = {**auto_sub, **mechanisms}
        status = "manual"
    else:
        s = {**auto_sub, "source": "补丁路径与变更行自动扫描", "locations": patch["files"]}
        status = "heuristic"
    path_score = min(50, 10 * s["critical_files"] + 6 * s["high_risk_files"] + 2 * s["other_files"])
    mechanism = 15 * s["locking_changes"] + 15 * s["global_api_changes"] + 10 * s["lifetime_changes"] + 10 * s["interrupt_context_changes"]
    result["subsystem"] = {"score": Decimal(str(min(100, path_score + mechanism))), "status": status, "detail": f"关键/高风险/其他文件 {s['critical_files']}/{s['high_risk_files']}/{s['other_files']}，机制附加分 {mechanism}", **s}

    sem = group(evidence, "semantic", counts=("dependency_commits", "api_adaptations", "rewritten_hunks", "hidden_assumptions"))
    if sem is None:
        result["semantic"] = unknown("semantic", "缺少依赖与目标树语义分析")
    else:
        if sem["rewritten_hunks"] > patch["changed_hunks"]:
            raise ValueError("semantic.rewritten_hunks 不能超过补丁总 hunk 数")
        score = min(100, min(20, 5 * sem["dependency_commits"]) + min(30, 10 * sem["api_adaptations"]) + min(30, 30 * sem["rewritten_hunks"] / patch["changed_hunks"]) + min(20, 10 * sem["hidden_assumptions"]))
        result["semantic"] = {"score": Decimal(str(score)), "status": "manual", "detail": f"依赖 {sem['dependency_commits']}，API 适配 {sem['api_adaptations']}，人工改写 {sem['rewritten_hunks']}，隐藏前提 {sem['hidden_assumptions']}", **sem}

    ver = group(evidence, "verification", counts=("build_targets", "test_suites"), flags=("runtime_testing_required", "concurrency_testing_required"))
    if ver is None:
        result["verification"] = unknown("verification", "缺少目标验证矩阵")
    else:
        score = min(100, min(25, 5 * ver["build_targets"]) + min(25, 5 * ver["test_suites"]) + 25 * ver["runtime_testing_required"] + 25 * ver["concurrency_testing_required"])
        result["verification"] = {"score": Decimal(str(score)), "status": "manual", "detail": f"构建目标 {ver['build_targets']}，测试套件 {ver['test_suites']}，运行时/并发要求 {ver['runtime_testing_required']}/{ver['concurrency_testing_required']}", **ver}

    overrides = evidence.get("overrides", {})
    reasons = evidence.get("override_reasons", {})
    if not isinstance(overrides, dict) or not isinstance(reasons, dict):
        raise ValueError("overrides 和 override_reasons 必须是对象")
    for name, value in overrides.items():
        if name not in result:
            raise ValueError(f"未知维度覆盖：{name}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise ValueError(f"{name} 覆盖分必须是 0–100 数值")
        if not isinstance(reasons.get(name), str) or not reasons[name].strip():
            raise ValueError(f"覆盖 {name} 时必须提供 override_reasons.{name}")
        result[name]["score"] = Decimal(str(value))
        result[name]["status"] = "manual"
        result[name]["detail"] += f"；专家覆盖：{reasons[name]}"
    return result


def level(score):
    if score < 20: return "极易"
    if score < 40: return "较易"
    if score < 60: return "中等"
    if score < 80: return "困难"
    return "极难"


def recommendation(name):
    return {
        "conflict": "按依赖和语义拆分实际冲突 hunk，再逐个复核目标树上下文。",
        "kabi": "核对发行版 KABI 白名单；必要时用兼容封装或影子结构保持既有 ABI。",
        "subsystem": "扩大锁、生命周期与并发回归范围，并请对应子系统维护者复核。",
        "semantic": "记录目标树与上游的语义差异，为人工改写 hunk 增加针对性测试。",
        "verification": "按最小合理范围落实构建、运行时和并发验证矩阵。",
    }[name]


def md_text(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def make_markdown(report):
    lines = ["# 内核补丁回合难度评分", "", f"- 补丁：`{report['patch']}`", f"- 总分：**{report['total_score']}/100（{report['level']}）**", f"- 置信度：**{report['confidence_label']}（{report['confidence']}）**", f"- 结果性质：{'预评分' if report['provisional'] else '证据驱动评分'}", "", "| 维度 | 得分 | 权重 | 加权贡献 | 状态 | 来源 | 关键证据 |", "|---|---:|---:|---:|---|---|---|"]
    for name, item in report["dimensions"].items():
        lines.append(f"| {LABELS[name]} | {item['score']} | {int(WEIGHTS[name] * 100)}% | {item['weighted_contribution']} | {item['status']} | {md_text(item['source'])} | {md_text(item['detail'])} |")
    lines += ["", "## 已识别风险", ""]
    if report["top_risks"]:
        for name in report["top_risks"]:
            item = report["dimensions"][name]
            locations = "；定位：" + "、".join(item["locations"]) if item["locations"] else ""
            lines.append(f"- **{LABELS[name]}**：{item['detail']}{locations}")
    else:
        lines.append("- 尚无带证据的非零风险项。")
    if report["unknown_dimensions"]:
        lines += ["", "## 待补证据", ""]
        for name in report["unknown_dimensions"]:
            lines.append(f"- **{LABELS[name]}**：{GAPS[name]}")
    if report["top_risks"]:
        lines += ["", "## 建议", ""] + [f"- {recommendation(name)}" for name in report["top_risks"]]
    if report["notes"]:
        lines += ["", "## 备注", ""] + [f"- {note}" for note in report["notes"]]
    return "\n".join(lines) + "\n"


def json_ready(value):
    if isinstance(value, Decimal):
        return as_number(value)
    if isinstance(value, dict):
        return {key: json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    return value


def build_report(patch_path, patch, evidence):
    dimensions = score_dimensions(patch, evidence)
    total = Decimal("0")
    for name, item in dimensions.items():
        item["score"] = rounded(item["score"])
        item["weight"] = WEIGHTS[name]
        item["weighted_contribution"] = rounded(item["score"] * WEIGHTS[name])
        item["formula"] = FORMULAS[name]
        total += item["weighted_contribution"]
    confidence = rounded(sum(STATUS_FACTOR[item["status"]] * WEIGHTS[name] for name, item in dimensions.items()), "0.001")
    candidates = [name for name, item in dimensions.items() if item["status"] != "unknown" and item["score"] >= 40]
    top_risks = sorted(candidates, key=lambda name: dimensions[name]["weighted_contribution"], reverse=True)[:3]
    unknowns = [name for name, item in dimensions.items() if item["status"] == "unknown"]
    notes = evidence.get("notes", [])
    if not isinstance(notes, list) or not all(isinstance(note, str) for note in notes):
        raise ValueError("notes 必须是字符串数组")
    report = {
        "schema_version": "1.0", "patch": str(patch_path), "total_score": total, "level": level(total),
        "confidence": confidence, "confidence_label": "高" if confidence >= Decimal("0.8") else "中" if confidence >= Decimal("0.55") else "低",
        "provisional": bool(unknowns) or any(item["status"] == "heuristic" for item in dimensions.values()),
        "rounding_rule": "维度分与贡献均四舍五入到 1 位小数；总分为展示贡献之和",
        "patch_stats": {key: patch[key] for key in ("files", "changed_hunks", "additions", "deletions")},
        "dimensions": dimensions, "top_risks": top_risks, "unknown_dimensions": unknowns, "notes": notes,
    }
    return json_ready(report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patch", required=True, help="统一 diff 补丁路径")
    parser.add_argument("--evidence", help="完整实测/人工证据 JSON")
    parser.add_argument("--json", dest="json_out", help="JSON 报告输出路径")
    parser.add_argument("--markdown", help="Markdown 报告输出路径")
    args = parser.parse_args()
    patch_path = Path(args.patch).expanduser().resolve()
    evidence_path = Path(args.evidence).expanduser().resolve() if args.evidence else None
    if not patch_path.is_file():
        raise ValueError(f"补丁不存在：{patch_path}")
    outputs = [Path(value).expanduser().resolve() for value in (args.json_out, args.markdown) if value]
    if any(path in outputs for path in (patch_path, evidence_path)):
        raise ValueError("输出路径不能覆盖补丁或证据输入")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path else {}
    if not isinstance(evidence, dict):
        raise ValueError("evidence.json 顶层必须是对象")
    patch = parse_patch(patch_path)
    report = build_report(patch_path, patch, evidence)
    json_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.json_out:
        output = Path(args.json_out).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json_text, encoding="utf-8")
    if args.markdown:
        output = Path(args.markdown).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(make_markdown(report), encoding="utf-8")
    if not outputs:
        sys.stdout.write(json_text)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        sys.exit(2)
