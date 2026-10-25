# 调试核心规范

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

## 1. 当前事实

从 Request Intake、可复现的失败命令、当前 revision、预期结果和已登记 Evidence 开始。Bug 描述保持为 `BUG_FLOW`；不要根据关键词重新分类。

## 2. 最小闭环

复现 → 隔离最小根因 → 创建或确认失败测试 → 实现最小修正 → 执行回归和 Review。使用 `tdd-core` 和 `red-green-refactor`；只有需要详细诊断模式时才加载 `debugging.md`。

## 3. 停止条件

出现以下任一情况时停止：复现不稳定、预期结果未确认、修复超出授权范围，或 Evidence 绑定到了过期 revision。
