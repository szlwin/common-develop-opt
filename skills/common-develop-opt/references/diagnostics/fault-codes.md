# 故障代码索引

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

本索引只描述派生检查结果和修复方向；它不改变任务状态或授权写入。

| 代码 | 含义 | 处理 | 权威实现 |
|---|---|---|---|
| `NO_SUPPORTED_DECLARATIONS` | 配置范围内没有支持的声明文件 | 补充声明或停止 | `scripts/discover_dependencies.py` |
| `UNSUPPORTED_LAYOUT` | 当前布局不在声明发现覆盖范围 | 转换为显式声明或停止 | `scripts/discover_dependencies.py` |
| `DEPENDENCY_TARGET_MISSING` | 依赖目标不存在 | 补齐目标或修正引用 | `scripts/discover_dependencies.py` |
| `REVIEW_INPUT_DRIFT` | Review 输入与封存摘要不一致 | 重新生成输入并复核 | `scripts/review_lifecycle.py` |
| `RECOVERABLE_PARTIAL` | 封存只写入一侧 | 只重试缺失侧 | `scripts/publish_candidate.py` |

参数、Schema 和状态枚举以脚本 `--help` 与 `assets/*.schema.json` 为准。
