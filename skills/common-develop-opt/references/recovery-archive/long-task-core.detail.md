# 长任务执行核心

> 本文件是 Long Task Reference Pack。默认通过 `relevant_sections` 按主题章节读取；不要无选择地整篇加载。

## 记录与状态

本文件定义长任务启用、目录、单写者、Schema、状态、任务计划、iteration 与 attempt。

### 启用条件

满足任一条件即启用：三个以上 Agent、多轮 Review、跨会话、长时间测试、可能中断，或用户明确要求持续执行。


### 当前记录契约

长任务记录的允许字段、嵌套结构和身份字段只由 `assets/storage/long-task/record-contract.json` 定义。模板负责示例，`long_task.py` 负责执行校验，Reference 不复制第二套字段清单。未在记录契约声明的字段一律拒绝。


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

新任务只创建 `assets/storage/storage-contract.json` 定义的 canonical 文件；状态、Evidence、Review 和投影均通过当前契约对应的脚本访问。

### 文件数量与空间边界

长任务需要少量稳定运行文件来支持跨会话恢复和 fail-closed 门禁；文件多并不等于恢复能力更强。目录内容分为三类：根部 canonical 状态/计划/追踪文件，`evidence/` 下不可变审计对象，以及 `cache/` 下可删除投影。

- 需求和设计正文始终位于 `version/{VERSION}/requirement/**`、`version/{VERSION}/module/**` 或 `version/{VERSION}/union/**` 的单一当前文件，禁止复制进 task 或按 `R01/R02` 生成并列事实文档。
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
- 跨会话默认先消费 `wk status`、`wk next --explain`、`wk context` 和当前 revision 文档；仅在需要紧凑恢复摘要时通过 `wk context` 查询 `traceabilitySummary`，`current_impact` 只由 `wk next` 间接消费。Agent 不直接打开任何 Manifest/Traceability/Impact JSON；完整追踪文档仅通过 Trace/Acceptance ID 定向查询或明确审计脚本读取。
- 版本级 `work_record.md`：所有 Agent 只通过 `finish-attempt` 或 `append-work-event` 追加摘要事件，不复制 task-local 完整历史。

当前执行身份、项目管理责任、下一动作、开放问题和 current attempt 均从 event reducer/current context 获取；不得以某个 cache 文件是否存在推断生命周期事实。


### 长任务与任务存储契约

长任务记录字段使用 `schema v2`；任务存储职责由事件账本、静态计划和派生 manifest 分别承担，不把长任务业务字段与存储实现混在一起。版本级 `work_record.md` 独立使用 schema v4 canonical Markdown。阶段顺序、Reviewer、状态域和 work record 版本读取 `assets/runtime/runtime-contract.json`。


### 状态

任务、产物、计划任务、Review、阶段结果和最终结论分别使用 `assets/runtime/runtime-contract.json#statuses` 对应状态域。mutable current state 由 `TASK_STATE_PATCHED`、task/runtime/attempt/outcome/issue 等 event 经 reducer 计算；`current_manifest.json` 只是该结果的阅读优化。


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

风险触发 Reviewer 写入 `additional_reviewers`，每项必须包含运行契约中的 trigger、reason 和当前 evidence_ids。`additional_reviewers` 是风险登记而非互斥身份集合：若风险 Reviewer 已经是 required/conditional Reviewer，执行集合仍只出现一次，但只有 reviewer、trigger、development revision 和完整 evidence IDs 与当前 `risk.json` 精确一致时才允许同名风险登记；其它重复继续 fail-closed。责任 Agent 的自检不能写入 `independent_conclusions` 代替 Reviewer。`independent_conclusions` 只保存 `review_result_ref` 与结论摘要；完整 criterion 结果压缩后追加到单一 `evidence/reviews.jsonl`，读取时确定性恢复。字段只从 `assets/storage/long-task/record-contract.json` 读取，验收语义从 `assets/review/review-contract.json` 读取。



> 后续规则见 [long-task-core.detail.part-2.md](./long-task-core.detail.part-2.md)。
