# 长任务执行核心

> 本文件是 Long Task Reference Pack。默认通过 `relevant_sections` 按主题章节读取；不要无选择地整篇加载。

## 记录与状态

本文件定义长任务启用、目录、单写者、Schema、状态、任务计划、iteration 与 attempt。

### 启用条件

满足任一条件即启用：三个以上 Agent、多轮 Review、跨会话、长时间测试、可能中断，或用户明确要求持续执行。


### 当前记录契约

长任务记录的允许字段、嵌套结构和身份字段只由 `assets/long-task/record-contract.json` 定义。模板负责示例，`long_task.py` 负责执行校验，Reference 不复制第二套字段清单。未在记录契约声明的字段一律拒绝。


### 目录

```text
DOC_ROOT/version/{VERSION}/task/{TARGET_ID}/
├── task.json                           # 稳定身份 / workflow binding
├── task_plan.json                      # 当前静态执行规格
├── task_events.jsonl                   # mutable runtime / attempt / outcome / stale / issue / remediation authority
├── traceability.json
├── acceptance.json
├── risk.json
├── git_checkpoints.yaml                # 仅 wk -gc 首次实际 checkpoint 时创建
├── evidence/
│   ├── registry.jsonl
│   ├── reviews.jsonl
│   ├── objects/
│   └── runs/{revision}/
└── cache/                               # Git 忽略，可删除并由 canonical facts 重建
    ├── current_manifest.json             # reducer 派生恢复摘要
    └── current_impact.json               # 增量影响摘要
```

新任务只创建 `assets/storage-contract.json` 定义的 canonical 文件；状态、Evidence、Review 和投影均通过当前契约对应的脚本访问。

### 文件数量与空间边界

长任务需要少量稳定运行文件来支持跨会话恢复和 fail-closed 门禁；文件多并不等于恢复能力更强。目录内容分为三类：根部 canonical 状态/计划/追踪文件，`evidence/` 下不可变审计对象，以及 `cache/` 下可删除投影。

- 需求和设计正文始终位于 `version/{VERSION}/doc/**` 的单一当前文件，禁止复制进 task 或按 `R01/R02` 生成并列事实文档。
- Git 已跟踪且与 HEAD 一致的事实优先登记 `GIT_REF`，不得再创建 snapshot；同一 revision 的多个输入优先登记一个可复用 bundle，一个 Evidence 可声明多个 `used_as_types`。
- 不手工创建 `evidence/runs/{REVISION}/{phase-round}/`、成对的 `.out + .json`、逐轮 Review 草稿目录或重复 handoff。命令只登记结构化 command-result Evidence 和必要输出对象；Review 追加到唯一 `reviews.jsonl`。
- `cache/` 必须被忽略并可重建；它不应进入交付体积统计。只有 `wk -gc` 实际发生后才创建 checkpoint 文件，只有显式 `wk -tp` 才创建开发任务计划和 Review ledger。
- 历史不能为减小目录而无审计地改写或删除；减少体积优先依靠稳定当前文件、JSON Pointer 增量 Patch、Git 引用、内容寻址去重和不产生重复中间文件。活动账本超过阈值时，只允许 ProjectManagerAgent 通过显式 `compact-events` preview/apply 将完整旧 segment 写入确定性 gzip archive，再以 hash-linked `TASK_REDUCER_SNAPSHOT` 继续单调序列；禁止手工截断或直接 gzip 当前权威文件。


### 单写者

- `task_events.jsonl`：所有 mutable task runtime 的唯一 append-only authority，包括当前状态 patch、task runtime、attempt、StageOutcome、phase stale、issue lifecycle 与 auto-remediation；所有 Agent 只能通过运行契约授权脚本追加 event，不手工改写历史。每条事件使用单调 `seq` 与 `prev_digest/digest` hash chain。
- 活动账本默认达到 `262144` bytes 才建议滚动。先运行 `python3 scripts/long_task.py compact-events -g ProjectManagerAgent --task-dir {TASK_DIR} --json` 获取 `planDigest`，确认后再按返回的 `nextCommand` apply。旧 segment 保存在任务目录的 `history/task_events.{FIRST_SEQ}-{LAST_SEQ}.jsonl.gz`，snapshot 校验 archive SHA-256、未压缩 SHA-256、最后 seq/digest 和 reducer state；普通状态查询只读活动 segment，历史审计使用 `long_task.py task-events --include-archive` 显式读取。该操作不改变 phase、revision、Issue、Attempt 或 Review 结论。
- `cache/current_manifest.json`：由 reducer 确定性生成的纯 cache，可随时删除重建，不承担 authority。
- `task_plan.json`：ImplementationPlanAgent / ProjectManagerAgent 按阶段职责维护静态执行规格；不得写运行态字段。
- issue：提出者、owner、validator 通过 `ISSUE_*` event 执行新增、指派、修复、验证、关闭、延期与重开；新任务没有 mutable `review_issues.md`。
- `traceability.json`：需求分析阶段由 `requirement_doc.py finalize-requirement-analysis` 原子完成初始化与更新。
- 跨会话默认先消费 `wk status`、`wk next --explain`、`wk context` 和当前 revision 文档；仅在需要紧凑恢复摘要时条件读取 `cache/current_manifest.json#/traceabilitySummary`，`cache/current_impact.json` 只由 `wk next` 间接消费。完整追踪文档仅通过 Trace/Acceptance ID 定向查询或明确审计请求读取。
- 版本级 `work_record.md`：所有 Agent 只通过 `finish-attempt` 或 `append-work-event` 追加摘要事件，不复制 task-local 完整历史。

当前执行身份、项目管理责任、下一动作、开放问题和 current attempt 均从 event reducer/current context 获取；不得以某个 cache 文件是否存在推断生命周期事实。


### 长任务与任务存储契约

长任务记录字段使用 `schema v2`；任务存储职责由事件账本、静态计划和派生 manifest 分别承担，不把长任务业务字段与存储实现混在一起。版本级 `work_record.md` 独立使用 schema v4 canonical Markdown。阶段顺序、Reviewer、状态域和 work record 版本读取 `assets/runtime-contract.json`。


### 状态

任务、产物、计划任务、Review、阶段结果和最终结论分别使用 `assets/runtime-contract.json#statuses` 对应状态域。mutable current state 由 `TASK_STATE_PATCHED`、task/runtime/attempt/outcome/issue 等 event 经 reducer 计算；`current_manifest.json` 只是该结果的阅读优化。


### task_state cache

`cache/current_manifest.json` 可保存 reducer 当前视图和 event projection；缺失、陈旧或内容不一致时直接删除并从 `task_events.jsonl` 重建。

状态只从 `task_events.jsonl` reducer 与其派生 cache 恢复。


### task_plan

`task_plan.json` 是静态 execution specification。每项任务声明：

- `id`、稳定 `logical_task_id`，以及需要时的 `feature_id`；
- iteration 身份与 revision reason；
- 标题、目标、phase、owner_agent；
- `depends_on`；
- input revisions、allowed files、trace/flow refs、risk triggers；
- Reviewer、validation commands、expected results、stop conditions 和 `max_attempts`。

禁止保存这些 mutable 字段：`status`、`attempts`、`output_revision`、`validation_evidence_ids`。当前任务状态、attempt 数、output revision 和 validation evidence 由 `TASK_RUNTIME_SET`、attempt、StageOutcome 与 Evidence event/materialization 得到。依赖验证和 `wk next` 必须使用 reducer materialized task，而不是直接读取静态 plan 推断运行态。

新项目不拆分历史计划；历史审计由事件账本和 Evidence 引用承担，恢复时不遍历历史目录。


### iteration 与 task_events attempt 历史

同一功能或逻辑任务允许多轮设计、开发、Review 和测试：

- revision、上游事实或 Review 结论变化：调用 `reopen-phase`，为该阶段及受影响下游创建新 iteration；
- 同一 iteration 内执行失败：创建新 attempt；
- 已经 `PASSED` 的任务不能直接再次启动；
- `max_attempts` 只限制当前 iteration。

运行中的任务不得直接编辑 `task_plan.json` 调整重试上限。需要变更当前 iteration 的上限时，ProjectManagerAgent 使用：

```bash
python3 scripts/long_task.py set-task-policy -g ProjectManagerAgent \
  --task-dir {TASK_DIR} --task-id TASK-001 \
  --max-attempts 5 --reason "用户要求增加一次重试机会"
```

该命令不修改静态计划，而是在 `task_events.jsonl` 追加 `TASK_POLICY_SET` 事件，并将覆盖限定在指定任务的当前 iteration；`--max-attempts 0` 清除覆盖并恢复静态计划值。新上限不得小于或等于当前 iteration 已产生的 attempt 数；同值设置幂等，不追加重复事件。`reopen-phase` 创建新 iteration 后不会继承旧覆盖，如需继续使用必须重新设置。该命令仅允许 ProjectManagerAgent 执行。

一次实际执行对应稳定 `attempt_id`。`start-attempt` 追加 `ATTEMPT_STARTED`；`finish-attempt` 追加同一 ID 的 `ATTEMPT_FINISHED`；reducer 决定 current attempt、失败次数、task status 与版本级摘要。当前阶段任务通过后，owner 使用 `publish-artifact --attempt-id` 固定 output revision 和 Evidence。

一致性要求：

- 当前 `(task_id, iteration_id)` 的 attempt 数从 event ledger 计算；
- 每个 iteration 的 `attempt_no` 从 1 连续递增；
- 同一逻辑任务当前 iteration 最多一个 `RUNNING` attempt；
- current attempt 必须引用当前 RUNNING attempt；
- `PASSED` 必须有 output revision、当前 revision 的 ACTIVE evidence ID，以及全部 validation command 的结构化 command-result evidence；
- command-result 必须记录命令、退出码、执行时间、revision、输出文件与 SHA-256；
- 修改文件必须是 `allowed_files` 的子集；
- `FAILED/BLOCKED` 必须有失败类型或原因；
- 每个完成 attempt 在版本级 `work_record.md` 中恰有一个摘要；
- 达到 `max_attempts` 后只阻断当前 iteration。

完整日志、Diff 和测试输出不得写入 event payload，只保存 Evidence Registry ID 和短摘要。


### traceability

每个业务规则、验收项或重要非功能约束维护稳定 ID，并依次补充：需求、业务模型、设计、测试 Case、计划任务、实现、验证证据和验证者。

状态：`PENDING/COVERED/GAP/STALE/NOT_APPLICABLE`。任何 `GAP/STALE` 会阻止相关阶段通过。旧版同步造成的单条失效只能通过 `long_task.py revalidate-traceability` 恢复：该命令要求 ProjectManagerAgent 明确指定 Trace、当前或上游 phase、同 revision 的 ACTIVE Evidence、独立 PASSED Review、绑定 Acceptance 和原因；只允许 `STALE/GAP -> COVERED`，不允许批量清除或直接编辑 canonical 状态。命令在任务 writer lock 内追加 `TRACEABILITY_REVALIDATED`，把更新后的 `traceability.json`、事件账本和 `cache/current_manifest.json` 原子写入，并记录更新后文件 SHA-256。普通 `validate` 会逐条核对历史记录的字段、Review、Evidence 和 Acceptance，并只将最后一条恢复记录的 digest 与当前 canonical 文件 digest 比较；更早 digest 是对应写入时的历史摘要。canonical 文件被事后改写时必须 fail-closed。该恢复只修复追踪事实，不完成阶段，不替代 `finalize-phase`/`advance-phase`，也不使旧下游 Evidence 或 Review 自动重新有效。


### 当前存储边界

任务状态必须具备 `task_events.jsonl`；`long_task.py task-events` 仅查询当前 ledger。

## Evidence、Review 与阶段结果

本文件定义长任务中的不可变 Evidence、决策、Review、Issue 和 StageOutcome 引用。

### 不可变 Evidence 引用

Evidence Artifact Registry 使用 schema v4。已被 Git 跟踪、内容与 HEAD 一致的文件自动使用 `capture_mode=GIT_REF`，记录 commit、repo 相对路径、blob OID、digest 和大小；验证直接读取 Git object，不依赖当前工作树。

脏文件、未跟踪文件或非 Git 来源无法形成 Git 引用时，`evidence.py register` 才创建 `evidence/objects/{DIGEST}.*`；后续修改源文件不会使历史 Evidence digest 失效。明确稳定且不可变的现成对象仍可使用 `DIRECT`。

同一 Revision 需要冻结两个及以上源码、配置、测试或文档时，`bundle-register` 会先尝试把同一 HEAD 下的所有成员登记为一个 `GIT_REF`；只有成员不能由同一 Git commit 解析时才生成确定性 Bundle：

```bash
python3 scripts/evidence.py bundle-register   -g {AGENT}   --task-dir project_doc/version/{VERSION}/task/{TARGET_ID}   --revision {REVISION}   --phase {PHASE}   --scope {SCOPE_ID}   --item requirement_ref=../../doc/{TARGET_ID}/requirement.md   --item config_ref=../../../../../dec-demo/src/main/resources/mix/system/systems.xml   --item test_ref=../../../../../dec-demo/src/test/java/example/ContractTest.java
```

多源对象统一写入 `evidence/objects/{DIGEST}.tar.xz`，内含固定时间戳的 manifest、成员路径、类型、大小和 SHA-256；一个 Evidence ID 可通过 `used_as_types` 满足多个 Review criterion。当前存储异常必须按现行 Evidence/Review contract 诊断并 fail-closed。


### 设计决策引用

跨阶段持续生效的业务、范围、模型和架构决策只记录在当前 Requirement/Design 文档的稳定锚点中，使用 `DEC-*` 或 `IMPL-DEC-*` 标识。新决策通过新的文档 revision 取代旧 revision；任务运行时不再维护独立的 `decision_log.md` 副本。


### collaboration_reviews

对运行契约中声明 Review 的阶段：

1. 阶段 owner 通过 `publish-artifact` 固定 PASSED attempt 的 artifact revision、Evidence 和 `RUNNING` StageOutcome；
2. 载入 required、条件触发和风险触发的额外 Reviewer；
3. Reviewer 使用 `scripts/review_contract.py show` 读取唯一 Profile，对全部 criterion 逐项输出状态和结构化证据；
4. 汇总问题并讨论；
5. 修订后原 Reviewer 复核；
6. 所有已登记 Reviewer 的 Profile 完整、逐项结论可由证据推导为 `PASSED`，且无开放 P0/P1，阶段 Review 才能通过。
7. ProjectManagerAgent 通过 `finalize-phase` 原子地将同 revision Artifact 与 StageOutcome 置为 `PASSED`；随后才允许 `advance-phase`。

风险触发 Reviewer 写入 `additional_reviewers`，每项必须包含运行契约中的 trigger、reason 和当前 evidence_ids。`additional_reviewers` 是风险登记而非互斥身份集合：若风险 Reviewer 已经是 required/conditional Reviewer，执行集合仍只出现一次，但只有 reviewer、trigger、development revision 和完整 evidence IDs 与当前 `risk.json` 精确一致时才允许同名风险登记；其它重复继续 fail-closed。责任 Agent 的自检不能写入 `independent_conclusions` 代替 Reviewer。`independent_conclusions` 只保存 `review_result_ref` 与结论摘要；完整 criterion 结果压缩后追加到单一 `evidence/reviews.jsonl`，读取时确定性恢复。字段只从 `assets/long-task/record-contract.json` 读取，验收语义从 `assets/review-contract.json` 读取。


### issue lifecycle

问题当前视图由 `task_events.jsonl` 的 `ISSUE_*` event 经 reducer 生成；新任务不创建 `review_issues.md`。每个 issue 至少包含：ID、phase、axis、severity、confidence、artifact revision、raised_by_agent、owner_agent、title、description、question、question_to、精确 evidence、影响、建议、受影响追踪项和状态。

- `owner_agent` 不得等于 `verified_by_agent`；
- `CLOSED` 必须有 resolution revision、验证者和验证时间；
- `DEFERRED` 必须有理由和后续验证时机；
- 无精确证据的高置信 P0/P1 不合法。
- 指派、修复、验证、关闭、延期、重开和误报拒绝分别追加 `ISSUE_ASSIGNED/REMEDIATED/VERIFIED/CLOSED/DEFERRED/REOPENED/REJECTED`；不得覆盖旧 issue 记录。
- 可通过 `long_task.py issue-event` 执行受权限约束的生命周期转换。


### StageOutcome events

每个阶段结果记录：phase、iteration、输入 revisions、输出 revision、`produced_by_agent`、状态、证据、风险、开始/完成时间。

- 每阶段只能有一个 `is_current=true` 的 StageOutcome；
- `PASSED` 必须具有非空 output revision、当前真实证据和完成时间；
- 新 iteration 创建后，旧结果保持原 revision，改为历史记录并填写 `superseded_by`，不得覆盖或删除；
- 当前 StageOutcome 的 iteration 与 revision 必须等于 `artifact_revisions`；
- `wk -min` 的跳过阶段必须有当前 `SKIPPED` StageOutcome 和明确风险/原因；
- 每个适用阶段都必须有当前结果，`completion_verification` 的 PASSED outcome 是任务 `COMPLETED` 的必要条件。

Agent 的 `StageResult` 与 Runtime 的 `StageOutcome` 不是同一个对象。`StageResult`
必须符合 `assets/stage-result.schema.json`，由阶段 owner 从当前 PASSED attempt
生成，或通过 `publish-artifact --stage-result` 显式提供，并绑定当前 phase、owner、
revision、Evidence、trace 和变更文件。`publish-artifact` 先校验 StageResult，再把
`phase`、`produced_by_agent`、`input_revisions`、`output_revision`、`evidence_ids`
映射到 StageOutcome，把 `risks` 映射为 `open_risks`；`trace_refs`、`changed_files`
和 `open_issue_ids` 只保留在事件中的 StageResult 交接载荷，不冒充 StageOutcome
字段。任一绑定不一致都必须拒绝发布。

## Checkpoint、重开与恢复

本文件定义 Checkpoint、STALE、重试、恢复、完成、脚本入口、存储维护和 Handoff。

### Checkpoint

以下时机必须 checkpoint：

- 阶段开始/结束；
- 用户决策后；
- Agent handoff 前后；
- Review 轮结束；
- 长命令执行前后；
- 状态变为 WAITING/BLOCKED/PARTIAL；
- 上游变化触发 STALE；
- 会话即将中断。

Checkpoint 至少更新 task state、问题、任务、追踪、决策、阶段结果和下一动作。 启用 `wk -gc` 时，`git_checkpoints.yaml` 在首次实际创建 checkpoint 时按需生成；它只记录顺序阶段检查点声明，实际 Commit SHA 通过 `WK-Checkpoint` Trailer 解析。


### 增量影响与最小重验

Revision 变化后先用 `wk impact` 生成 DERIVED impact/revalidation plan，再决定需要重开的 task、Review Criterion、validation capability 与 Acceptance 范围。CLI 落盘结果中的 `derivedOutputPath` 只是可选传输元数据，必须能由同一 bounded-plan validator 接受，不参与语义影响判定；当前 Contract 允许调用方将非权威 `--output` 指定到任务目录外的临时位置，但该文件绝不被 `wk next` 隐式消费，调用方负责其外部路径的权限和清理。分析失败时，命令必须原子替换默认 `cache/current_impact.json` 为当前输入对应的 `BLOCKED`/`INVALID` 派生结果并同步 Manifest；即使指定了其他输出路径，也不得保留可被 `wk next` 自动消费的旧默认计划。该计划不写入 `task_events.jsonl`，不能替代 `amend-phase`、`reopen-phase`、Review、Evidence 或 StageOutcome authority。`PROCESS_ONLY` 可保持原语义 revision；其余变化缺少高/中置信项目 Adapter 时必须保守扩大重验范围。

`wk next --explain / reconcile / close --impact-plan` 只允许 Impact Plan 收紧生命周期：若存在最小重验项，返回 `REVALIDATION_REQUIRED` 或 `BLOCKED`，不得借 Impact 结论跳过原 Gate。Impact Analysis 为 `BLOCKED` 时，Decision 的 `nextCommand.command` 必须对应 `allowedCommands/legalActions` 中的合法动作（默认重试 `wk.impact`，必要时由结果明确转向 `reopen-phase`），并固定为 `ProjectManagerAgent`、当前会话可执行、需要确认且非只读；不得生成不在允许集合中的 `task-context` 命令。只要 `--task-dir` 已提供，任何可预期的分析输入错误（包括未知 phase、输出目录/路径解析失败、provenance 组合错误、CLI 参数非法值或 `--revision/--change-class` 缺失）都必须在进入分析前后生成当前输入对应的结构化 `BLOCKED`/`INVALID` 派生结果；任何成功或 `BLOCKED` 计划都必须先原子替换默认 `cache/current_impact.json`、刷新 Manifest，再写入可选自定义输出；若最终自定义输出写入失败，必须用当前输入对应的 `BLOCKED` 重新替换默认缓存并刷新 Manifest；自定义输出与默认缓存若只是路径别名，必须按规范化路径视为同一目标，避免重复写入使 Manifest 立即失效；无法解析的自定义输出不得被再次写入，避免异常掩盖结构化阻断并保留旧成功计划，防止 `wk next` 继续消费旧成功计划。由于缺少 `--task-dir` 无法安全定位缓存，该参数仍由 CLI 保持必填并在解析层拒绝。

### 当前阶段有界修订

仅当变更发生在当前 phase、没有 RUNNING attempt、下游没有当前 PASSED StageOutcome，且结构与字段关联均有效的 PASSED Impact Plan 能通过高/中置信 Adapter 或高置信 Graph 将影响限定在单一 phase 时，ProjectManagerAgent 才可执行 `amend-phase`。非 `PROCESS_ONLY` 计划至少要有一个 affected Review criterion，或明确声明 validation capability；不能把完整 Profile 全部声明为 unaffected 后伪造无重验的有界修订。Adapter/Graph 使用状态、置信度、跨 Context 标志、影响范围和风险模板必须保持类型正确且相互一致；风险模板 ID 必须存在于 Review Contract 且允许当前 phase，未知或跨 phase 风险一律结构化拒绝。Graph 生成阶段遇到当前 phase 不允许的风险模板必须直接输出 `BLOCKED` 和 `reopen-phase` fallback，不能先产出不完整的 `PASSED` 计划。Graph 自动触发的风险 Profile（包括动态 Profile）必须先并入当前 phase 的 Review 集合，再计算 affected/unaffected 分区，不能因静态 Profile 集合过滤而丢失重验项；每个 required risk template 对应 Profile 的全部 criteria 都必须位于 affected Review 分区，缺失任一 criterion 即拒绝。Review Profile 既包括 Contract 中的固定 Profile，也包括由风险模板动态解析的 Profile；解析后的 `phase` 必须等于计划当前 phase，不能跨阶段借用 criteria。显式 `affected-task-id` 必须存在于当前 phase 并包含全部依赖下游任务；Trace、Acceptance、保留及失效 Evidence ID 均必须真实存在。`PROCESS_ONLY` 无论是否携带 Adapter 或 Graph 都不产生语义重验，不使用 `amend-phase`；`UNKNOWN`、保守全量、跨 Bounded Context、安全/权限/事务/信任边界、数据迁移或跨模块高风险变化一律改用 `reopen-phase`。

成功修订使用 `<BASE>-AM<NN>@<SHA-256>` revision；`amend-phase` 会在指定 version canonical Markdown 原文件追加标准 Amendment 块，并将文档、Impact Plan 快照、`ARTIFACT_AMENDED` 事件和状态投影作为一个原子事务写入。文档前缀摘要必须与 revision 一致。只有受影响任务进入 `REWORK` 并清空其 output revision/validation Evidence；未受影响的 `PASSED/NOT_APPLICABLE` 任务沿 amendment revision 链保留原 iteration、output revision 和 Evidence。当前 phase 的 Review 归档后重置，StageOutcome 仅失效当前 phase；`task_plan.json`、`development_task_plan.json`、`traceability.json`、`acceptance.json` 和 Evidence Registry 不因命令本身重写。

`--canonical-file` 必须指向当前 task/phase 所有权可证明的已有 canonical Markdown：需求阶段只能写当前 target 的 `requirement.md`，测试设计阶段只能写当前 target 的 `test_case.md`，其他阶段必须由受影响任务的精确文档声明证明归属；无法证明归属时拒绝。Amendment 不支持 `doc/` 或 `history/amendments/` 根及其树内的符号链接（包括 dangling link 和 loop）；命令与普通任务校验发现任意 symlink 时必须以结构化错误 fail-closed，不跟随链接读取或写入。普通历史校验对两个受保护目录各扫描一次，并在本轮校验中复用已验证的根与目录项。命令自动追加 Amendment 内容，不创建按轮次复制的需求/设计文件。多轮修订继续追加并形成 `AM01 -> AM02` revision 链；Reducer 只投影当前仍在处理的 `active_amendment`，阶段通过或推进后该投影自动消失，完整历史、文档摘要和 Impact Plan 快照仍保留并由普通 `validate_task` 持续核对。所有会追加 task lifecycle event 的命令（包括 amend/reopen、phase advance/finalize、attempt、artifact、issue、policy 和 event compaction）共享同一 task 级非阻塞独占写锁；发生并发写入时失败并要求重读当前状态。仅写派生恢复缓存的 `resume` 和版本级 `append-work-event` 不参与该 task ledger 锁。

### STALE、重开与传播

上游 revision、用户决策或 Review 结论发生实质变化，且不满足“当前阶段有界修订”全部条件时，ProjectManagerAgent 必须调用 `reopen-phase`：

- 为起始阶段及全部受影响下游创建新 iteration；
- 起始阶段进入 `IN_PROGRESS`，下游进入 `STALE/REWORK`；
- 取消受影响的 RUNNING attempt，但保留其历史；
- 清除新 iteration 的 output revision/evidence；
- 当前 collaboration review 归档到 `review_history` 后重置；
- 旧 StageOutcome 标记为历史并关联新 iteration；
- `wk -min` 中受影响的跳过阶段自动生成新一轮 `SKIPPED` outcome；
- 记录 stale event 的来源 revision、影响范围、原因和执行 Agent。

`reopen-phase` 的失效是逻辑失效，不是文件清理：canonical `requirement.md`、设计文档、`test_case.md`、实施计划、开发源码和测试源码必须原地保留。旧 revision 的 Evidence/Review/StageOutcome 保持可审计但不能用于新门禁；当前阶段重新通过后，再由下游 Owner 在同一版本范围内修订。尤其从 `design` 重开时，Design Review 通过且推进到 `test_design` 之前，不得删除、重新初始化或覆盖现有 `test_case.md`。

重开不创建新的产品/发布版本。需求、流程、业务模型、设计、开发和测试修订均归属同一
`version/{VERSION}`；对应文档、源码和测试资产原位修改，版本任务事实追加 revision、
变更、Evidence 和 Review 记录。准备重开时，从变更发生阶段逐级向上只读检查影响；每次
扩大到直接上游前必须先取得用户确认，且整个边界判定期间禁止修改任何候选阶段产物或
生命周期状态。最早受影响阶段确定后只执行一次 `reopen-phase --from-phase`，随后按阶段
顺序向下修改，禁止先改下游再逐级向上重开。

只有实际纳入重开的阶段才产生新 iteration/revision，未受影响上游保持原 revision。
若需求确认最终被纳入重开，`REQCONF-P3-R06` 失效后的新确认 revision 为
`REQCONF-P3-R07`；若确认阶段不受影响，则继续使用原 revision。任何情况下都不得回收
已使用编号。

历史 Evidence/Review 采用惰性处理：普通生命周期校验只读取当前 iteration、当前
revision 的 ACTIVE Evidence，历史条目保留 ID 和元数据供显式审计，不因旧文件 digest、
旧 Trace 或旧 Review 的内容变化阻断新 attempt。只有当命令明确查询历史 revision 或
执行归档/完整审计时，才展开并校验这些旧对象；禁止为一次重开批量改写或逐条“清理”历史
Evidence。

禁止直接改写任务状态、删除 canonical 产物或删除旧记录来“重新执行”。有界当前阶段修订使用 `amend-phase`，其余失效传播使用 `reopen-phase`。


### 重试和停止

- 每个 task iteration 有独立 `max_attempts`；
- 失败重试消耗 attempt，正常 revision 迭代不消耗上一轮配额；
- 同一 Review iteration 使用 `max_auto_review_rounds` 作为当前 Review 轮次上限；
- 未启用 `wk -auto` 时，达到上限仍有开放 P0/P1，当前 iteration 必须 `BLOCKED`；
- 启用 `wk -auto` 时，P0/P1 只冻结 batch-forward，不因轮次数本身结束 remediation；使用 `auto_remediation.py convergence` 判断是否仍有新证据、旧失败是否已消除或失败是否推进到更深层，只有 `NON_CONVERGING_REMEDIATION` 才真正停止；
- 需求/设计事实冲突、业务或架构决策歧义、安全绕过才可修等情况仍必须停止，禁止重复生成代码绕过。


### 恢复

人类和 AI 统一先执行 `wk status --summary`。若只需判断当前阶段，到此即止；需要合法动作/门禁原因时再执行 `wk next --explain`，需要执行上下文时再执行 `wk context` 并读取当前 revision 文档。只有需要完整诊断时才条件读取 `cache/current_manifest.json`。Manifest 绑定最后 event seq/digest，只是可删除重建的派生投影，不保存新事实；`current_impact.json` 不作为直接恢复输入。

<!-- common-develop-invariant: LIGHTWEIGHT_TEST_ACTIONS_FORBIDDEN -->
<!-- common-develop-invariant: LIGHTWEIGHT_CLOSE_NON_RETROACTIVE -->
<!-- common-develop-invariant: LIGHTWEIGHT_ARCHIVE_AFTER_CLOSE_WITH_HISTORY -->
AI 恢复时先执行 `wk status --summary`；仅在需要下一动作或详细上下文时再执行 `wk next --explain`、`wk context` 或 `long_task.py task-context`。查询只读取 reducer 中的当前任务、当前/最新 attempt、最近失败、开放问题和下一动作；返回摘要路径/freshness，不遍历任务目录。再按需读取 active decisions、当前追踪缺口、最新 StageOutcome 和 handoff；需要细节时按引用读取事实文档、代码或指定 attempt。跨会话切换模式时，状态同步只能以 reducer 当前结果为输入，不能依据陈旧 cache、摘要或聊天记忆重建状态映射；`sync-work-mode`、`reopen-phase`、`advance-phase`、`finalize-phase`、风险同步、Request Intake 重分类和 Review 写入等生命周期写操作同样必须先读取 reducer authority，再追加事件，禁止以 cache 推断 iteration、phase 或门禁状态。轻量模式保持开启期间，已记录为 `SKIPPED`/`NOT_APPLICABLE` 的 Review、TDD、TestDesign 和 Testing 阶段不可执行；即使用户明确要求测试、TDD 或 Review 也不得恢复，必须先关闭轻量模式，再通过 `reopen-phase` 从满足前置依赖的合法阶段边界开始。轻量模式关闭后，未重开的阶段继续保持历史豁免。

恢复后先验证状态文件，再执行 `next_action`；不得凭聊天记忆猜测上次进度。


### 完成条件

任务可设为 `COMPLETED` 仅当：

- 所有计划任务为 `PASSED/NOT_APPLICABLE`；
- 无开放问题；
- 无 `FAILED/STALE/BLOCKED` 产物；
- completion_verification artifact 和 StageOutcome 均为 `PASSED`；
- 最终证据对应当前 revision。


### 脚本

```bash
python3 scripts/long_task.py init -g ProjectManagerAgent --doc-root project_doc --version {VERSION} --target MOD0001
python3 scripts/work_mode.py apply -g ProjectManagerAgent --doc-root project_doc --version {VERSION}
python3 scripts/long_task.py sync-work-mode -g ProjectManagerAgent --task-dir project_doc/version/{VERSION}/task/MOD0001
python3 scripts/long_task.py list-risk-triggers -g ProjectManagerAgent --phase design --json
python3 scripts/long_task.py validate -g RequirementConfirmationAgent --task-dir project_doc/version/{VERSION}/task/MOD0001
python3 scripts/long_task.py advance-phase -g ProjectManagerAgent --task-dir ...
python3 scripts/git_checkpoint.py status -g ProjectManagerAgent --task-dir ... --json
python3 scripts/long_task.py reopen-phase -g ProjectManagerAgent --task-dir ... --from-phase design --reason "设计 revision 变化" --source-revision DESIGN-R02@hash
python3 scripts/long_task.py amend-phase -g ProjectManagerAgent --task-dir ... --phase design --base-revision DESIGN-R02@hash --revision DESIGN-R02-AM01@hash --change-class SEMANTIC_AUTHORITY --changed-section "接口错误码" --affected-phase design --affected-task-id TASK-DESIGN-API --canonical-file doc/MOD0001/design.md --impact-plan ... --reason "补充已确认错误语义"
python3 scripts/long_task.py resume -g DesignAgent --task-dir ...
python3 scripts/long_task.py start-attempt -g DevelopAgent --task-dir ... --task-id TASK-001 --input-revision DESIGN-R02@hash
python3 scripts/long_task.py finish-attempt -g DevelopAgent --task-dir ... --attempt-id ATTEMPT-TASK-001-I002-A001 --status PASSED --output-revision DEV-R02@hash --evidence-ref evidence/summary.json --command-ref evidence/test.command-result.json
python3 scripts/long_task.py publish-artifact -g DevelopAgent --task-dir ... --attempt-id ATTEMPT-TASK-001-I002-A001
python3 scripts/long_task.py finalize-phase -g ProjectManagerAgent --task-dir ...
python3 scripts/long_task.py task-context -g DevelopAgent --task-dir ... --task-id TASK-001
```

### 自动验证证据

`finish-attempt` 支持将一次**项目已声明的验证命令**执行、证据登记和原有完成门禁串成一个受限操作：

```bash
python3 scripts/long_task.py finish-attempt \
  -g DevelopAgent --task-dir ... --attempt-id ATTEMPT-TASK-001-I002-A001 \
  --status PASSED --output-revision DEV-R02@hash \
  --auto-evidence --check "python3 -m pytest tests/test_x.py" \
  --modified-file src/example.py
```

硬规则：

- `--check` 必须逐字匹配当前 task 的 `validation_commands`；没有声明的命令、任意附加 shell 选项或未配对的 `--check` 都会在执行前拒绝；
- 自动执行只接受 `PASSED`，并使用调用进程当前工作目录；输出会记录 stdout、stderr、退出码、开始/结束时间、实际 timeout、运行时长、当前 phase 和 output revision；
- 成功后自动注册一个 `command_ref` Evidence，并同时写入 attempt 的 `command_evidence_ids` 与 `evidence_ids`，然后继续执行原有 `finish-attempt` 门禁；它不会代替普通实现 Evidence，也不会代替 Reviewer Evidence；
- 命令失败仍保留真实失败的 command-result 和 Evidence，但 attempt 保持 `RUNNING`，不会追加 `ATTEMPT_FINISHED`，并追加 `ATTEMPT_COMMAND_RECORDED` 事件保存最近一次命令审计；timeout 使用 `COMMAND_TIMEOUT` 和 `LIFECYCLE_AUTO_EVIDENCE_COMMAND_TIMEOUT` 分类，不会产生成功结论；修复后可重新执行；
- Evidence Registry 的写入与 attempt 完成不是一个假成功事务：后续完成门禁失败时，已登记的命令结果仍代表真实执行事实，但不能被解释为 attempt 已完成。

<!-- common-develop-invariant: WORK_MODE_APPLY_SYNCS_EXISTING_TASKS_BY_DEFAULT -->
- 版本 `work.md` 改动时使用 `work_mode.py apply` 原子更新；该命令默认同步已有任务运行时，只有明确需要跳过时才传入 `--no-sync-existing-tasks`；直接编辑后才单独运行 `sync-work-mode`；
<!-- common-develop-invariant: GIT_PUSH_REQUIRES_CHECKPOINT -->
- 阶段完成后用 `advance-phase`，不直接修改 `current_phase`；启用 `wk -gc` 时该命令创建当前阶段 checkpoint；若同时启用 `wk -gp`，必须先完成 native push/verify；若返回 `CONNECTOR_REQUIRED`，先执行 GitHub Connector request 并通过 `connector-complete` receipt 校验，再写入下一阶段 transition；
- 全流程只支持 `SEQUENTIAL`；A、B、C 多需求按 `project_process.md` 声明顺序逐个执行和提交，不引入多工作区调度、自动 push 或自动 merge；
- 当前阶段边界明确的小范围多轮修订用 `amend-phase`；跨阶段、未知或高风险返工用 `reopen-phase`；
- `validate` 或任何启动门禁失败时不得继续下游阶段。



### 机器验证运行文件

长任务初始化必须同时创建 `evidence/registry.jsonl`、`acceptance.json` 和 `risk.json`。`validate`、阶段推进、完成验证和归档缺少任一文件都失败。

所有 attempt、StageOutcome、Review criterion、trace verification 和 archive gate 引用的证据值均为 evidence ID。自然语言验收标准必须编译为 Acceptance Assertions；development 完成到 code review 之间必须生成当前 revision 的风险报告。详细规则按需读取 `evidence-core.md`、`review-evidence.md`、`acceptance-core.md`、`risk-verification.md` 与 `verification-integration.md`。



### Evidence 与 Review 存储维护

当前写入、按 ID 懒加载和内容寻址规则属于本文件。

Evidence 只通过 `evidence.py query/show/validate` 按 ID 定向读取；发现禁止路径时直接 fail-closed，由用户修复当前 canonical 文件后重新执行命令。


### 文档路径契约

`task_plan.json` 中的 `allowed_files` 会执行目录契约校验。禁止登记 `version/{VERSION}/docs/**`、`document/**` 或 `documents/**`；合法根仅为 `docs/**`、`version/{VERSION}/doc/**` 和 `version/{VERSION}/task/**`。文件存在但路径不规范仍判定为门禁失败。`long_task.py validate` 与 `task_verify.py` 同时检查该规则，防止错误目录通过任务健康检查。



### Handoff 收敛规则

- 同一 phase 内仅因需求文字补充或 Revision 递增，不创建新的 `handoff/{DATE}-*.md`；
- 该历史由 `task_events.jsonl`、`work_record.md` 和 Review Registry 保留；
- 只有推进到下一 phase、移交外部团队、跨会话需要独立恢复，或当前 owner 发生变化时，才创建 dated handoff；
- `handoff.md` 始终只维护当前交接，不复制完整 Evidence、Review 或 diff。


### 版本级工作模式变更事件

`version/{VERSION}/work_mode_events.jsonl` 是版本级工作模式切换的审计账本；它只记录有效模式快照发生变化的 `WORK_MODE_CHANGED` 事件，不替代版本 `work.md` 或任务事件账本。每行必须符合 `schemaVersion=1`，并包含 `eventId`、`timestamp`、`version`、`agent`、`source`、`before`、`after`、`previousDigest` 和 `digest`。

- `version` 必须与所在版本目录一致；`source` 只能是 `work_mode.apply` 或 `work_mode.init`；`agent` 是执行本次切换的 Agent。
- `before` 与 `after` 是完整工作模式快照；`previousDigest` 必须等于上一条事件的 `digest`（首条为空）；`digest` 是包含当前事件全部字段（不含自身 `digest`）的链完整性摘要。
- 读取、归档预览和 `wk doctor` 都必须 fail-closed 校验字段、版本、来源和摘要链；发现损坏时不得猜测历史模式。
- 事件生成由 `work_mode.py init/apply` 负责；模式未发生有效变化时不追加事件。


### 生命周期事实收敛

`wk next --explain`、`wk reconcile` 与 `wk close` 必须消费同一个 reducer-driven Lifecycle Decision，不各自实现另一套状态判断。Decision 至少区分：`EXECUTION_REQUIRED`、`PUBLISH_READY`、`REVIEW_REQUIRED`、`VALIDATION_REQUIRED`、`RECONCILIATION_REQUIRED`、`CLOSURE_ONLY`、`ADVANCE_READY`、`COMPLETED`、`BLOCKED`。

- `reconcile` 默认 dry-run；只有唯一 PASSED attempt 可以确定性发布等 process-only 场景才允许 `--apply`；
- 语义变化、Review 缺失、Evidence 缺失、Trace GAP、P0/P1 或不唯一 revision 均不得自动补造结论；
- `close --apply` 只是在 `CLOSURE_ONLY` 时编排现有 `finalize-phase`，StageOutcome、Review、Evidence、Issue 和 Trace Gate 保持原样；
- reason code 由 `_long_task/reason_codes.py` 统一维护，AI 不依赖自由文本推断下一步。
> 面向非专家的长任务选择与恢复遵循 [request-intake.md#4-面向非专家的沟通协议](request-intake.md#4-面向非专家的沟通协议)；先说明当前事实和风险，再给出可执行的下一步。
