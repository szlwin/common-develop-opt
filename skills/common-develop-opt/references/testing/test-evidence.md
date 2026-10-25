# 测试证据与断言

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

## 1. 命令证据
每次测试记录候选 revision、环境、完整命令、开始和结束时间、退出码、Case 数、失败数及报告引用。旧 revision 或部分测试结果不能证明当前任务整体通过。

## 2. Acceptance Assertion
每个适用 Acceptance 和任务 expected result 至少映射到一个 blocking Assertion。可自动验证的结果使用白名单断言类型；不能机器化时使用有当前专业 Reviewer 结论的 `MANUAL_REVIEW`，不得伪造自动通过。

## 3. 阶段门禁
所有 blocking Assertion 为 `VERIFIED`，测试 Evidence ACTIVE 且 digest 可回查，失败 Case 和开放 P0/P1 为零，才允许 Testing StageOutcome 通过。
