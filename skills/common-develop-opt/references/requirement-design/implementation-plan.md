# 可执行实施计划规范

## 快速规则

将已批准设计拆成可独立 Review 的纵向任务，逐项关联设计决策、输入 revision、允许文件、依赖、验收与验证方式。快速决策只读 [计划快速规则](implementation-plan.detail.md#implementation-plan-quick-rules)。

通用边界见 [机器字段与状态边界](../core/machine-boundaries.md)，工作模式例外见 [工作模式](../core/work-mode-core.md)。

## 详细规则

| 当前问题 | 按需读取 |
|---|---|
| 文件划分、纵向切片、任务依赖或任务示例 | [计划编写](implementation-plan.detail.md) 的对应章节 |
| 占位符、执行、Review、返修或验证映射 | [计划校验与 Review](implementation-plan.detail.part-2.md) 的对应章节 |

两个 detail 是并列主题，不要求顺序全文读取。稳定章节 ID 由 `reference_reader.py index --resource implementation-plan --json` 查询；字段以 [计划 Schema](../../assets/development/development-task-plan.schema.json) 为准，命令参数查询 `task_plan.py --help`。
