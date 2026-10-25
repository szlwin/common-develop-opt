# TDD Evidence

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

## 1. Evidence set

TDD Evidence 分两个时间点：`tdd` iteration 保存测试代码、Case/契约映射、有效 RED 或纯重构 GREEN 表征基线；`development` iteration 保存生产实现 revision、GREEN、REFACTOR 后 GREEN，以及适用覆盖率/复杂度和其它 Harness 结果。

每个命令使用结构化 command-result，至少记录命令、退出码、执行时间、关联 revision、输出引用和 SHA-256。`task_events.jsonl` 只保存 Evidence 引用与短摘要，Work Record 只保存索引，不复制完整日志。Development 的 TDD pre/post gate 结论最终通过当前 revision 的 `StageOutcome` 进入生命周期投影，不再创建独立顶层 TDD StageOutcome。

## 2. Stage gates

进入 `development` 的 production mutation 前：PRE_IMPLEMENTATION RED gate 为 `RED_VERIFIED`；新行为/BUG 有有效 RED，纯重构有 GREEN 基线；TDDReviewAgent 在同一 TDD revision 通过；测试 Evidence 可解析且 input revisions 未失效。

进入 `code_review` 前：契约与调用边界已确认；测试矩阵覆盖适用正常/边界/异常；RED/GREEN/REFACTOR 证据完整；项目要求的 Harness 已通过；代码测试与 Case 一致，未覆盖项有明确记录。任一不满足即停止推进并由 ProjectManagerAgent 协调。

## 3. Revision freshness

每个 RED、GREEN、REFACTOR、覆盖率和复杂度结论都绑定当前代码/流程 revision。代码、测试、配置、依赖或相关流程 revision 变化后必须重新执行受影响证据；旧 GREEN 不能证明新 revision。流程型 Case 的 `flow_refs/flow_path/variant_refs/failure_path_refs` 同样必须绑定当前流程 revision。
