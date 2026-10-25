# 人工 Review 知识沉淀规范

本文件定义人工 Review 调整如何形成可被后续 Agent 读取、复用和验证的外部记忆。它不替代需求、设计、代码、测试或 `work_record.md`。

## 1. 事实来源与路径

项目级 Review 知识固定记录在：

```text
DOC_ROOT/review_knowledge.md
```

当前默认路径为 `project_doc/review_knowledge.md`。

- `work_record.md` 记录版本级摘要事件和索引；单个任务的逐次执行细节以 `task/{TARGET_ID}/task_events.jsonl` 为准。
- 需求、设计、业务模型、API、DB、ER、实现契约（可含轻量 DSL）和测试用例记录当前有效事实；结构化设计事实按 YAML/changeset 规则维护。
- `review_knowledge.md` 只记录经标准模式人工确认，或在 `wk -auto` 下由 ProjectManagerAgent 按授权门禁记录自动决策后，可跨任务复用的 Review 规则。
- 项目级 skill 记录稳定的项目技术、业务和代码规范。
- `common-develop` 记录跨项目通用流程规则。
- Harness、测试、Lint 或架构规则承载可自动验证的规则。

## 2. 任务开始前读取

ProjectManagerAgent 在分派任务前，必须确认 `review_knowledge.md` 是否存在，并把与当前模块、功能、技术栈、文件范围和任务阶段匹配的“有效”规则传递给相关 Agent。

相关责任 Agent 与 Reviewer 开始任务前必须读取匹配规则。存在冲突时，按 `SKILL.md` 的唯一事实优先级处理，并通知 ProjectManagerAgent 协调。

## 3. 人工 Review 后的处理顺序

收到人工调整或修改意见后：

1. 记录原始意见、适用范围和来源，不扩展用户未表达的含义。
2. 由ProjectManagerAgent 先分类为 BUG、需求变更或待确认：BUG 追加 `project_bug.md` 并走完整 BUG 流程；需求变更追加 `project_process.md` 并走完整开发流程；待确认先阻塞协调。
3. 对原有需求或功能调整，列出关联需求/功能、页面、流程、API、业务模型、DB、代码、测试和文档的影响，并标注“无需调整 / 待调整 / 已调整”。
4. 由ProjectManagerAgent 协调责任 Agent 同步更新受影响的当前事实、代码和测试用例，保证当前事实一致。
5. 详细修改与验证写入当前 task attempt 的证据引用；通过 `long_task.py append-work-event` 在版本级 `work_record.md` 追加分类、结果摘要和索引。
6. 判断该意见是否值得跨任务复用；值得复用时，新增或更新 `review_knowledge.md` 条目。
7. 能自动检测的规则，转化为测试、Lint、架构规则或 Harness 检查，并按 `harness.md` 与 `harness_env.md` 管理执行。
8. 修改完成后重新执行受影响范围的验证；未通过时仍由ProjectManagerAgent 协调，不进入后续流程。

## 4. 分类与升级

| 类型 | 处理方式 |
|---|---|
| 一次性数据、文案或页面调整 | 更新当前事实文档，并通过 `append-work-event` 写版本级摘要，不写知识库 |
| 可在同模块或后续任务复用 | 写入 `review_knowledge.md` |
| 已稳定的项目技术、业务或代码规则 | 保留知识条目并标记升级目标；标准模式经用户确认、`wk -auto` 经 ProjectManagerAgent 记录自动决策后同步到项目级 skill |
| 跨项目通用流程规则 | 保留知识条目并标记升级目标；标准模式经用户确认、`wk -auto` 经 ProjectManagerAgent 记录自动决策后同步到 `common-develop` |
| 可机械判断的规则 | 增加自动化检查，并在知识条目中记录检查方式 |

不得因为单次意见就直接扩充 skill。升级前至少确认规则适用范围、是否与现有规则冲突、是否仍有例外；标准模式获得用户明确授权，`wk -auto` 则必须在用户已授权自动模式的范围内由 ProjectManagerAgent 记录自动决策。

## 5. 条目格式

知识编号使用 `RK-{分类}-{四位序号}`，例如 `RK-BE-0001`。分类可使用 `REQ`、`UI`、`DESIGN`、`ARCH`、`FE`、`BE`、`DB`、`TEST`、`DOC`。

每条规则必须包含：

```md
### RK-BE-0001：规则标题

| 字段 | 内容 |
|---|---|
| 状态 | 有效 / 已废弃 / 待确认 |
| 分类 | 后端开发 |
| 适用范围 | 模块、功能、项目目录、文件类型或技术栈 |
| 来源任务 | 需求、功能、BUG 或任务编号 |
| 来源 | 人工 Review；日期或记录链接 |
| 问题描述 | 原问题及其影响 |
| 确认规则 | 后续 Agent 必须遵守的明确规则 |
| 例外条件 | 无，或允许例外的条件和确认人 |
| 错误示例 | 简短示例或“无” |
| 推荐示例 | 简短示例或“无” |
| 执行阶段 | 需求 / 业务模型 / 设计 / 计划 / 开发 / Review / 测试 |
| 验证方式 | 人工检查、测试、Lint、架构规则或 Harness 类型 |
| 关联文件 | 代码或文档路径 |
| 升级目标 | 无 / 项目级 skill / common-develop / 自动化检查 |
| 替代规则 | 无，或新规则编号 |
| 更新时间 | YYYY-MM-DD HH:mm:ss Asia/Shanghai |

### 变更记录

- YYYY-MM-DD：新增；原因……
```

## 6. 更新和失效

- 相同规则只维护一个主条目，新增证据写入变更记录，不重复建条目。
- 不删除已被后续任务引用的规则。规则失效时将状态改为“已废弃”，填写替代规则和原因。
- 对规则内容作实质修改时，保留旧结论摘要、修改原因、时间和来源。
- `待确认` 规则不得作为强制门禁；ProjectManagerAgent 应协调用户或责任 Agent 确认。
- 已升级到 skill 或自动化检查的规则，知识条目仍保留索引、升级位置和验证方式，便于追溯来源。

## 7. Review 与交付检查

Review Agent 必须检查本次实现是否违反匹配的有效规则，并确认新人工意见是否已完成分类和沉淀。ProjectManagerAgent 交付前检查：当前事实已同步、版本级摘要事件已追加、知识条目状态正确、自动化验证已执行或记录未执行原因。
