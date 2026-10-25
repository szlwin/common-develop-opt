# Architecture Review 状态核心规范

## 1. 当前步骤

使用 AI Context 中的 `architectureReviewContext` 判断当前处于 skeleton、skeleton Review、具体实现还是 final Review 状态。

## 2. 门禁

在 skeleton Review 通过前，不得开始具体实现；在 final Code Review 确认符合已批准 skeleton 前，不得标记实现完成。
