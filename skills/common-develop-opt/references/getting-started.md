# 开发入口

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](core/machine-boundaries.md)。

## 详细规则

先识别任务是新建、恢复、普通执行、Review 失败、迁移还是维护诊断；确认项目根目录、版本、目标、Agent、工作模式、允许范围和停止条件。

通过本包 `scripts/wk.py` 执行生命周期动作，参数以实际 `--help` 为准。

新建先用 `start` 预览，已有任务先用 `begin`；优先消费返回的 actionPlan、Owner、inputs、stopWhen 和 resources。按返回动作继续执行；只有返回内容不足时才定向读取主题文档。已返回上下文时不重复调用 `ai_context` 或读取所有主题。下表只列常用入口，专题命令仍按返回动作或 `wk help` 定向发现。

## 常用命令入口

以下路径相对于安装包。项目目录中使用完整脚本路径和已配置的 Python 环境；表中用途不代替参数或写入授权。

| 入口 | 何时使用 |
| --- | --- |
| `scripts/wk.py` | 新建、恢复、执行、推进及完成的工作流入口 |
| `scripts/ai_context.py` | 执行包仍缺少上下文或需要诊断时 |
| `scripts/reference_reader.py` | 按资源 ID 和章节补读规则 |
| `scripts/requirement_doc.py` | 当前动作要求处理需求文档时 |
| `scripts/business_flow.py` | 当前动作要求处理业务 Flow 时 |
| `scripts/design_doc.py` | 当前动作要求处理设计文档时 |
| `scripts/task_plan.py` | 当前动作要求查询或维护实施计划时 |
| `scripts/evidence.py` | 按当前身份登记或查询 Evidence 时 |
| `scripts/manual_review.py` | 独立 Reviewer 按正式 Review 流程执行时 |
| `scripts/quality_gate.py` | 仅维护 Skill 源时运行包级门禁 |

Git checkpoint、归档、Review 会话、环境、底层生命周期等专题入口继续保留。执行返回的精确命令，不自行改写为上述其他脚本。公共路径的保留与内部实现共享规则见 [维护入口](core/maintenance-entrypoints.md)。

## 按需主题

- 新建：读取 [任务入口](core/request-intake.md)，用 `wk start` 确认分类与范围。
- 恢复：读取 [长任务恢复](recovery-archive/long-task-core.md)，用 `wk begin` 重新取得当前动作与门禁。
- Review 失败：读取 [Review 规则](review-agents/review-core.md)，解决当前 revision 的阻断，再按正式 Review 流程提交。

候选封存服务的结果为派生输入，职责边界见 [候选服务](diagnostics/candidate-service-boundary.md)。
