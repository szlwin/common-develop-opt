# 任务验证核心规范

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

使用 `task_verify.py` 和 AI Context 验证当前任务事实、revision、StageOutcome、Evidence、Review、Acceptance Assertion 以及投影完整性。验证只报告事实和 reason code；不会隐式修改生命周期状态或修复记录。
