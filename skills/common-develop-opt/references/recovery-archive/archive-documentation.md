# 研发事实增量归档

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

本文件定义 `wk -d` 版本增量归档和归档前 Review 门禁；归档不创造新研发事实。

## `wk -d` 版本增量归档

`wk -d` 将当前版本已确认增量合并到项目级全量事实文档。版本目录保存增量，项目级文档保存合并后的当前全量。

`merge_docs.py init` 创建 `changes/changeset.template.yaml`、`changes/dependency-impact.changeset.template.yaml`、`requirement/requirement_list.md`、`work.md`、`work_record.md` 和 `module/{MODULE_CODE}/{MODULE_CODE}_desc.md`。模板不会被自动发现；完成后复制或重命名为真实 changeset，并补全稳定 ID、base/result revision、操作和追踪项。

归档接受的布局、退休目录与设计主题声明统一见 [兼容边界](../core/compatibility-boundary.md)，preview/archive 不提供旧目录自动转换能力。

`wk -d preview` 生成单一 `generated/merge_plan.yaml`。顶层内容由脚本生成并绑定 changeset、base/result revisions、结构化产物、确定性 `artifactMerges`、`work.md` 模式/digest 和结果 digest；`archiveGate` 由授权 Agent 补充阶段门禁、证据、开放问题、授权和可选用户确认。普通流程不得手工增删 `artifactMerges`。`conflictDecisions` 只记录显式冲突处理结论；出现冲突后必须先修订源增量或 rebase、重新 preview，再登记已解决结论，不能用人工记录覆盖脚本结果。`REBASE_REQUIRED/REJECT_ARCHIVE` 仍然阻断，`SUPERSEDE` 必须已体现在当前确定性 merge 中。

固定顺序：

```text
merge_docs.py validate
→ merge_docs.py preview
→ 自动同步补充文档引用并核对 generated/、merge_plan.yaml 和各 preview
→ 自动计算 requirement/module、稳定设计主题、页面行为和 dependency impact 的归档结果
→ 在 merge_plan.archiveGate 中写入当前 StageOutcome、Review、测试/非测试证据和授权结论
→ merge_docs.py archive
→ 原子写入项目当前事实、设计索引、关系图、追踪矩阵、人类导航和 archive_manifest.yaml
→ 通过 long_task.py append-work-event 追加版本级归档摘要
```

`merge_docs.py` 自动修改项目级 `business_model`、`business_flow`、`dependency_impact`、`docs/requirement_list.md`、`docs/module/{MODULE_CODE}/{MODULE_CODE}_desc.md` 和 `docs/union/union_language.md`，并按稳定 Design topic/page behavior 生成 `docs/module/{MODULE}/design/` 当前快照与 `{MODULE}_design.md` 索引。API/DB 只登记版本 changeset 引用、digest、revision 与验证证据；不得生成或更新项目级 API/DB 全量 YAML、Markdown 或 ER。业务流程归档会在同一原子 payload 中生成 `docs/_flows/{FLOW_GROUP}_flow.yaml`、`{FLOW_GROUP}_flow.md`、`flow_index.yaml`、`flow_index.md`，并替换受影响模块说明中的流程索引；模块退出流程时删除旧索引，流程、关系或对象废弃时保留 `DEPRECATED/SUPERSEDED` 状态和历史关系，不物理删除。

归档 payload 同时确定性刷新 `docs/README.md`、当前 `version/{VERSION}/version_summary.md`、`docs/_relations/engineering_graph.yaml`、`traceability_matrix.md` 和 `dependency_graph.md`。这些都是 DERIVED 导航/关系视图，只链接真实存在或同一原子 payload 将生成的文档；链接缺失、越界、悬空关系或指向版本 preview/generated 目录时归档失败。

版本预览与项目归档的目录层级不同，禁止复用预览 Markdown 中的相对链接。需求列表、设计快照、页面行为、流程阅读文档、关系视图和模块索引中的链接均由脚本基于最终输出文件位置重新计算。例如版本需求列表的 `{TARGET_ID}/requirement.md` 在项目级列表中改写为 `../version/{VERSION}/requirement/{TARGET_ID}/requirement.md`；项目设计快照提供返回项目首页、模块说明和来源版本的链接。目标不存在、链接越界、链接仍指向 preview/generated、版本不匹配、Design/base revision 过期或 stable ID 冲突时，`validate/preview/archive` 必须失败。

归档门禁：

- `baseRevisions` 必须等于项目级当前基线；`resultRevisions`、`changeSetIds`、`resultDigest`、`structuredArtifacts` 和 `workMode` 必须与当前输入一致；
- requirementConfirmation、requirementAnalysis、design、testDesign、implementationPlan、tdd、development、completionVerification 门禁必须为 `PASSED` 并有 revision、StageOutcome、Reviewer 和 evidence；codeReview/testing 在标准模式也可依据当前归档 changeset `affectedTraceIds` 命中任务的真实用户 waiver 归档为 `NOT_APPLICABLE`；若 `affectedTraceIds` 为空，优先按 task plan/development task plan 的正式 `owner_module` 绑定或 changeset `featureCode` 精确绑定任务，只有没有结构化绑定时才接受模块码本身、`{MODULE}-*`、`{MODULE}_*` 的分隔符边界，禁止使用未定义的 `task.json.module` 或数字作为隐含边界。waiver 必须带当前 `SKIPPED` StageOutcome 引用、理由且不得声明 pass evidence/reviewer。有效 waiver 存在时 gate 必须保持 `NOT_APPLICABLE`，不能改为 `PASSED` 或引用范围外任务的通过结果；其他模块或不相交 Trace 的 waiver 不得扩散到当前 plan。每个 PASSED StageOutcome 必须在版本任务目录中真实存在、为当前 iteration 的 `PASSED` 且 revision 一致，每个 evidence 必须解析到真实文件，每个 Reviewer 必须有同 revision 的独立 `PASSED` 结论；
- businessModel 在涉及业务模型增量时必须 `PASSED`，否则可带原因写 `NOT_APPLICABLE`；`wk -min` 只允许 testDesign/tdd/testing 三个门禁带原因写 `NOT_APPLICABLE`；标准模式的 codeReview/testing 仅允许使用已验证的用户 waiver 记录为 `NOT_APPLICABLE`；
- `artifactMerges` 必须完全等于脚本从当前源文件计算出的 `READY` 集合，source/target/result digest、revision、stable ID 和 Evidence 引用必须一致；禁止人工注入或删除合并项；
- `dependency_impact` 与稳定设计主题/page behavior 必须使用当前 base revision；同 revision 内容变化、过期基线、重复 stable ID、悬空引用或同一 changeset 重复目标一律 fail-closed；
- `archiveGate.openIssueIds` 必须为空，`archiveGate.status` 必须为 `READY`；`authorizedBy` 必须是声明 Agent，`decisionRef` 必须引用当前 Requirement/Design 中的 `DEC-*` 或 `IMPL-DEC-*` 稳定决策锚点，且填写带时区的 `authorizedAt`；
- 使用 `archive --require-user-confirmation` 时必须 `archiveGate.userConfirmed: true`；`wk -auto` 与该参数互斥，自动模式仍要求全部适用门禁和授权；
- 禁止用底层 `apply` 绕过门禁，禁止手工覆盖项目级全量 YAML。

这里的“归档”表示研发事实基线合并，不是 Git merge、Release、部署或上线。



### P6-A 语义归档机制

本节只定义与工作模式无关的归档机制；阶段 Gate、Review/Evidence 要求和 Standard/Lightweight/Min 差异继续由现有生命周期契约决定，不在 P6-A 重定义。

`wk -d` 的文档侧固定执行：

```text
validate
→ preview
→ semantic merge
→ link normalize
→ conflict detection
→ archive manifest
```

- `version/{VERSION}/module/{MODULE}/{MODULE}_desc.md#枚举` 按枚举 code/symbol 语义合并到 `docs/module/{MODULE}/{MODULE}_desc.md`；已发布 symbol 改值、value 复用、无废弃说明的删除返回 `ENUM_ARCHIVE_CONFLICT`。
- `version/{VERSION}/union/union_language_changes.md` 按稳定 `UL-*` ID 合并到 `docs/union/union_language.md`；同名多义、别名冲突、缺失变更原因或废弃后悬空引用返回 `UNION_LANGUAGE_CONFLICT`。
- 版本链接与归档链接按最终目标位置重新计算；项目级输出不得保留指向 `generated/preview` 的正式链接。
- 以上冲突必须修改源事实并重新 preview，不允许通过人工 conflict note 覆盖。

## Review 门禁

文档 Review 至少检查：

- 是否基于当前 revision；
- 是否存在冲突、遗漏、不可测试语句或隐藏假设；
- 术语和稳定 ID 是否一致；
- 追踪是否连通需求、模型、设计、计划、实现和测试；
- 是否混入视觉设计、商业计划、发布部署或无基线泛 QA；
- `PASSED` 是否具有独立 Reviewer 和实际证据。

## 关闭轻量模式后的归档

<!-- common-develop-invariant: LIGHTWEIGHT_ARCHIVE_AFTER_CLOSE_WITH_HISTORY -->
`wk -wc` 不会撤销已经写入任务事件账本的轻量模式历史。关闭后执行 `wk -wd` 时，归档脚本读取当前版本任务运行时的 `work_mode.lightweight_waived` 和历史跳过阶段；只有存在该真实历史记录时，`test_design`、`tdd`、`code_review`、`testing` 对应的归档门禁才可继续为 `NOT_APPLICABLE`。如果版本没有轻量历史，必须按标准模式提供完整门禁证据。归档前不需要重新开启 `-ws`。
# 归档输出边界

普通知识归档只面向后续开发、维护和项目阅读，默认包含需求、设计以及被需求/设计实际引用的业务流程或模块说明。changeset、Traceability、Review、Acceptance、Evidence 和归档门禁是归档资格证明与审计索引，不应全文复制到知识归档正文；它们必须保留稳定 ID、revision、digest 和来源引用。

审计需要时，单独生成机器可读的审计包（`archive_manifest`、`archive_gate_result` 及各 registry 引用），不得改变知识归档正文的结构。归档校验仍必须验证这些审计对象的有效性；输出分层不降低任何生命周期门禁。
