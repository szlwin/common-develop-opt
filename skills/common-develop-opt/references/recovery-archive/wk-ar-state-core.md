# Architecture Review 状态核心规范

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

## 1. 当前步骤

使用 AI Context 中的 `architectureReviewContext` 判断当前处于 skeleton、skeleton Review、具体实现还是 final Review 状态。

## 2. 门禁

在 skeleton Review 通过前，不得开始具体实现；在 final Code Review 确认符合已批准 skeleton 前，不得标记实现完成。
