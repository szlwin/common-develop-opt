# long-task-core（续）

> 本文是 [long-task-core.detail.md](./long-task-core.detail.md) 的按需续篇。
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
必须符合 `assets/review/stage-result.schema.json`，由阶段 owner 从当前 PASSED attempt
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

人类和 AI 统一先执行 `wk status --summary`。若只需判断当前阶段，到此即止；需要合法动作/门禁原因时再执行 `wk next --explain`，需要执行上下文时再执行 `wk context` 并读取当前 revision 文档。只有需要完整诊断时才通过 `task_layout.py view` 查询 Manifest 投影；Agent 不得直接打开 `cache/current_manifest.json`。Manifest 绑定最后 event seq/digest，只是可删除重建的派生投影，不保存新事实；`current_impact.json` 不作为直接恢复输入。

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

`task_plan.json` 中的 `allowed_files` 会执行目录契约校验。禁止登记 `version/{VERSION}/doc/**`、`version/{VERSION}/docs/**`、`version/{VERSION}/document/**` 或 `version/{VERSION}/documents/**`；合法根仅为 `docs/**`、`version/{VERSION}/requirement/**`、`version/{VERSION}/module/**`、`version/{VERSION}/union/**` 和 `version/{VERSION}/task/**`。文件存在但路径不规范仍判定为门禁失败。`long_task.py validate` 与 `task_verify.py` 同时检查该规则，防止错误目录通过任务健康检查。



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
> 面向非专家的长任务选择与恢复遵循 [request-intake.md#4-面向非专家的沟通协议](../core/request-intake.md#4-面向非专家的沟通协议)；先说明当前事实和风险，再给出可执行的下一步。
