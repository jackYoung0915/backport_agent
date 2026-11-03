# 评分模型与证据格式

## 目录

- 权重与等级
- 五维计算
- evidence.json
- 证据采集建议

## 权重与等级

| 维度 | 权重 | 关注点 |
|---|---:|---|
| conflict | 30% | 目标树实际冲突的数量、广度与比例 |
| kabi | 25% | 导出符号、原型、结构体布局及白名单破坏 |
| subsystem | 25% | 核心路径侵入与锁、生命周期、全局 API 等机制变化 |
| semantic | 10% | 依赖提交、上下文重写和 API 语义适配 |
| verification | 10% | 构建矩阵、测试范围及运行时/并发回归成本 |

0–19 极易，20–39 较易，40–59 中等，60–79 困难，80–100 极难。未知维度以 50 暂算，不表示实测风险为 50。

置信度是状态系数（observed=1.0、manual=0.85、heuristic=0.65、unknown=0.2）的加权平均：≥0.80 高，≥0.55 中，其余低。置信度不是正确概率。维度分和贡献按四舍五入保留 1 位小数，总分等于展示贡献之和。

## 五维计算

### 1. 代码冲突量 conflict

```text
volume = min(60, 15 × conflicted_hunks + 5 × max(0, conflicted_files - 1))
ratio  = 30 × min(1, conflicted_hunks / changed_hunks)
lines  = 10 × min(1, conflict_lines / 100)
score  = min(100, volume + ratio + lines)
```

只有 `apply_attempted=1` 才接受冲突统计。零冲突必须同时满足 `apply_succeeded=1`、冲突 hunk/文件/行均为 0。存在冲突时允许 `apply_succeeded=0`。

### 2. KABI 变化 kabi

```text
score = min(100,
  10 × added_exported_symbols +
  30 × removed_exported_symbols +
  25 × prototype_changes +
  30 × layout_changes +
  25 × whitelist_violations)
```

新增导出符号不等于兼容，需结合发行版政策解释。布局变化包括导出结构体字段增删、重排、大小/对齐变化。没有完整工具证据时此维度为 unknown；文本中的导出行和头文件变化只作为线索，不参与零风险判定。

### 3. 核心子系统侵入 subsystem

```text
path_score = min(50, 10 × critical_files + 6 × high_risk_files + 2 × other_files)
mechanism  = 15 × locking_changes + 15 × global_api_changes
           + 10 × lifetime_changes + 10 × interrupt_context_changes
score      = min(100, path_score + mechanism)
```

机制字段为 0/1。关键路径包括 `kernel/sched/`、`kernel/locking/`、`kernel/rcu/`、`mm/`、`arch/` 及核心头文件；高风险路径包括 `fs/`、`block/`、`net/core/`、`drivers/base/`、`kernel/` 其他目录。自动路径和关键词扫描仅为 heuristic；人工复核四个机制字段后为 manual。

### 4. 语义适配 semantic

```text
score = min(100,
  min(20, 5 × dependency_commits) +
  min(30, 10 × api_adaptations) +
  min(30, 30 × rewritten_hunks / changed_hunks) +
  min(20, 10 × hidden_assumptions))
```

`rewritten_hunks` 指需要人工改写语义的 hunk，不等于冲突 hunk。`hidden_assumptions` 记录目标树缺失的锁、生命周期、配置或时序前提。

### 5. 验证成本 verification

```text
score = min(100,
  min(25, 5 × build_targets) +
  min(25, 5 × test_suites) +
  25 × runtime_testing_required +
  25 × concurrency_testing_required)
```

填写完成回合所需的最小合理验证范围，而非已经执行的测试数量。两个 required 字段为 0/1。

## evidence.json

省略整个维度表示未知。只要提供某个维度，就必须完整提供其所有计数字段；计数字段不能省略或为 null，显式填 0 才表示“已确认没有”。`conflict` 和 `kabi` 必须提供 `source`。每个维度可用 `locations` 字符串数组记录文件、符号或 hunk 定位。

```json
{
  "conflict": {
    "apply_attempted": 1,
    "apply_succeeded": 0,
    "conflicted_hunks": 3,
    "conflicted_files": 2,
    "conflict_lines": 35,
    "source": "git cherry-pick --3way；目标 commit abc123",
    "locations": ["mm/example.c:@@ -1,5 +1,6 @@"]
  },
  "kabi": {
    "added_exported_symbols": 0,
    "removed_exported_symbols": 1,
    "prototype_changes": 1,
    "layout_changes": 0,
    "whitelist_violations": 1,
    "source": "libabigail 2.x + distro KABI whitelist；基线 abc123",
    "locations": ["include/linux/example.h:example_lookup"]
  },
  "subsystem": {
    "locking_changes": 1,
    "global_api_changes": 1,
    "lifetime_changes": 1,
    "interrupt_context_changes": 0,
    "source": "人工复核锁和对象释放路径",
    "locations": ["kernel/locking/example.c"]
  },
  "semantic": {
    "dependency_commits": 2,
    "api_adaptations": 1,
    "rewritten_hunks": 3,
    "hidden_assumptions": 1,
    "source": "目标树依赖与调用点人工分析"
  },
  "verification": {
    "build_targets": 4,
    "test_suites": 3,
    "runtime_testing_required": 1,
    "concurrency_testing_required": 1,
    "source": "目标发行版最小验证矩阵"
  },
  "overrides": {"subsystem": 80},
  "override_reasons": {"subsystem": "修改 RCU 读侧临界区与对象释放次序"},
  "notes": ["目标分支缺少上游依赖 X"]
}
```

## 证据采集建议

- 冲突：在临时 worktree 对目标基线执行三方应用，保留命令、目标 commit 和冲突统计。
- KABI：使用发行版符号清单/白名单与 ABI 比较工具，记录工具版本及基线；按逻辑符号变化统计，不按 diff 行数统计。
- 子系统：列出关键文件、全局接口和并发机制变化，避免将头文件修改直接等同为 KABI 破坏。
- 语义：沿提交依赖、引用点和目标树差异检查前提，记录人工改写 hunk。
- 验证：按目标架构和配置说明构建、静态检查、启动、功能、压力与并发测试范围。
