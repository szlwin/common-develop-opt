# Git、交付与恢复

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](core/machine-boundaries.md)。

## 详细规则

阶段门禁通过后，按用户选择创建本地 checkpoint。`git push` 必须由用户明确启用并满足既有门禁；禁止隐式 merge、Release、部署和覆盖用户变更。

按需读取 `git-checkpoint-core.md`、`git-checkpoint.md`、`work-mode-core.md`、`lifecycle-core.md` 和 `long-task-core.md`。
