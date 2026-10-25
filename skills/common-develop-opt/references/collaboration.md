# Agent 协作与 Review

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](core/machine-boundaries.md)。

## 详细规则

分派必须包含目标、阶段、输入 revision、允许范围、停止条件、验证方式和下一 Reviewer。Owner 不关闭自己产生的 Finding；Reviewer 使用独立上下文检查当前 revision。

按需读取 `roles.md`、`orchestration-core.md`、`collaboration-review.md`、`review-core.md` 和 `review-evidence.md`。

Review 失败后从 [Review 规则](review-agents/review-core.md) 查找停止、修复与重新提交条件。
