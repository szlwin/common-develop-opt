# 统一 wk 入口

`scripts/wk.py` 只编排现有机器契约，不复制生命周期、权限、组合或 Review 规则。

## 命令发现

```bash
python3 scripts/wk.py help -g ProjectManagerAgent
python3 scripts/wk.py help -g ProjectManagerAgent --json
python3 scripts/wk.py help -g ProjectManagerAgent --category mode
python3 scripts/wk.py help -g ProjectManagerAgent --flag=-ar --json
python3 scripts/wk.py help -g ProjectManagerAgent --flag=-gp --json
python3 scripts/wk.py help -g ProjectManagerAgent --script long_task --json
python3 scripts/wk.py help -g ProjectManagerAgent --script long_task --command advance-phase --json
```

`help` 的命令目录从 `assets/runtime/runtime-contract.json#commandInterface` 和 `#/auxiliaryActions` 动态生成。详细字段可继续查询：

```bash
python3 scripts/runtime_contract.py command -g ProjectManagerAgent --flag=-ar --json
python3 scripts/runtime_contract.py phase -g ProjectManagerAgent --id development --json
python3 scripts/context_routing.py route -g ProjectManagerAgent --task-type wk_ar --json
```

不要为了发现参数而全文读取 `runtime-contract.json`、`context-routing.json` 或 `review-contract.json`。

脚本/子命令帮助由 `scripts/cli_help.py` 直接序列化已注册的 argparse parser，不扫描 Python 源码：

```bash
python3 scripts/cli_help.py list -g ProjectManagerAgent --json
python3 scripts/cli_help.py show -g ProjectManagerAgent --script long_task --command advance-phase --json
python3 scripts/long_task.py help -g ProjectManagerAgent --command advance-phase --json
python3 scripts/cli_help.py validate -g ProjectManagerAgent --json
```

JSON 至少返回 script、command、description、usage、arguments、Agent 默认值、mutation、previewSupported、effect、权限命令 ID 和权限查询命令。`assets/development/capability-pack-contract.json` 分别声明能力入口 `cliEntrypoints`、维护查询入口 `supportCliEntrypoints` 与仅供 import 的 `moduleInterfaces`；模块接口和已退役脚本不能伪装成当前 CLI。AI 必须先查询该目录，不得通过全文读取 `long_task.py` 或其它脚本发现参数。

## 统一编排

- `wk.py start`：默认预览；`--apply` 后依次分类请求、校验工作模式、初始化任务、绑定 Request Intake 并完成验证。
- `wk.py status --summary`：只读取 reducer，一次返回 phase、round、attempt、nextAgent 和 nextAction；只判断当前进度时使用此入口。
- `wk.py status`：需要诊断时读取当前 Task State、Request Intake、开放问题、authority consistency、health 和验证结果。
- `wk.py doctor`：默认只读聚合检查版本目录、work.md 模式、任务事件账本与运行时、Request Intake、current manifest、归档清单和版本级模式事件。`wk.py doctor --fix` 只预览安全修复注册表中的幂等修复，不写入任何文件；只有显式使用 `--fix --apply` 才会执行修复，且仅允许 `ProjectManagerAgent` 调用已登记的 `sync-work-mode` 与 `task_layout sync`。每个修复后都会重新检查 canonical 状态，部分成功返回 `PARTIAL`；未登记的建议（包括阶段重开/跳过、重试策略、归档和 Git push）只提示人工处理，绝不自动执行。
- `wk.py status` 的 `authorityConsistency` 是只读提示：它把 README/版本摘要中的宽泛完成声明与 `task_events.jsonl` reducer 当前状态比较；冲突标记为 `SUMMARY_DRIFT`，不能反向改变状态。
- `wk.py next`：返回当前门禁、允许命令、推荐命令、必需 Agent 和 reason code；`--explain` 额外返回 satisfied / missing / blockers / afterSuccess。
- `wk.py reconcile`：读取 reducer、Artifact、Attempt、Review、Evidence、Issue、Trace 和 StageOutcome，判断当前事实属于执行、发布、Review、收口、推进或阻塞；默认只预览，`--apply` 只允许执行唯一且确定性的 process-only 修复。
- `wk.py close`：默认预览当前阶段是否满足 `CLOSURE_ONLY`；`--apply` 仍调用既有 `finalize-phase` 硬门禁，不复制或降低 Gate。
- `wk.py impact`：基于 exact revision、通用 change class、changed scope/path 和可选项目 Adapter 生成 `DERIVED` 的最小重验计划。核心只认识 Review Profile/Criterion 与 capability 名称；没有 Adapter 或置信度不足时按 `CONSERVATIVE_FULL_PROFILE` fail-closed。
- `wk.py env`：只投影现有 `project_env.md / harness_env.md` 中声明的抽象 capability，不猜测项目技术栈。
- `wk.py context`：直接读取 reducer 派生的当前任务上下文；`cache/current_manifest.json` 只作为可重建读取优化，不成为 authority。
- 长任务的静态执行规格保存在 `task_plan.json`；大需求或显式 `wk -tp/--task-plan` 的实施计划保存在 `development_task_plan.json`。两者使用不同 schema，均只保留当前计划，历史由 `task_events.jsonl`、Review 和 Evidence 引用审计。
- `wk.py help`：按请求、工作模式、辅助动作、编排命令或单个参数返回紧凑目录。

Delivery Readiness 的唯一外部编排入口是 `wk delivery --action plan|verify`。`scripts/delivery_readiness.py` 只作为 `wk` 使用的共享实现库保留；直接调用共享实现不得新增生命周期状态或绕过 `wk` 权限门禁。

创建失败时，本次产生的不完整目录移动到 `start_failure_backup/WK-START-FAILED-*`，不直接删除项目文件。

## 增量有效性与最小重验

`wk.py impact` 输出不是 Task/Evidence/Review authority。其 `incremental_revalidation_plan` 只能通过 `--impact-plan` 收紧 `next --explain`、`reconcile` 或 `close`：只要计划要求 Criterion/Capability 重验，收口动作必须先停止；计划本身不能把任何阶段、Review、Evidence 或 Assertion 变成 PASSED。

Change class 与 Adapter Protocol 读取 `assets/runtime/incremental-validity-contract.json`。`PROCESS_ONLY` 只表示生命周期/存储元数据变化且语义与 production 均未变化；其他变化在缺少可靠项目级 Impact Adapter 时必须扩大到选定 Review Profile 的全部 Criterion。

项目 Adapter 只输出标准 JSON：`affectedScopes / affectedArtifacts / affectedReviewCriteria / affectedProfiles / affectedTaskIds / affectedTraceIds / affectedAcceptanceIds / requiredCapabilities / confidence` 等通用字段。具体语言、模块图、构建系统、测试框架、数据库和 dependency analyzer 均留在项目侧。

Impact Plan 判定为当前阶段有界变更后，由 ProjectManagerAgent 显式执行 `long_task.py amend-phase`，并提供 base/new revision、变更章节、唯一受影响 phase、依赖闭包完整的 task IDs，以及适用的 Trace、Acceptance 和 Evidence IDs。Impact Plan 必须携带 `affectedScope.taskIds/traceIds/acceptanceIds`，命令行集合必须与其精确一致；task 关联的全部 Trace 及这些 Trace 的全部 Acceptance 也必须形成精确闭包。命令在 task 级独占写锁内将 Amendment 标准块追加到所有权可证明的 `--canonical-file`，绑定文档摘要并保存不可变 Impact Plan 快照；普通校验会双向核对该文件的全部 Amendment 块与事件。计划为 UNKNOWN/保守范围、涉及高风险或跨阶段，或下游已有 PASSED StageOutcome 时改用 `reopen-phase`。`PROCESS_ONLY` 只更新流程/存储元数据，不走 `amend-phase`；无论哪种路径，Impact Plan 都不能授权生命周期 Gate。

跨阶段 `reopen-phase` 之前，ProjectManagerAgent 必须从变更发生阶段向上逐级只读检查影响边界；每次扩大到上游都先记录依据并取得用户确认，边界确认期间不修改阶段事实。确定最早受影响阶段后只执行一次 `reopen-phase`，再按阶段顺序向下修改、登记新 revision 和验证证据。`reopen-phase` 的 `--from-phase` 表示已确认的最早边界，不表示要求先重开其下游或再向上补开阶段。

用户确认必须先通过 `wk confirm --reopen-from <phase> --changed-phase <phase> --source-revision <revision> --reason <reason> --apply` 写入 `AUTHORIZATION_BATCH_CONFIRMED` 事件；随后 `reopen-phase --confirmation-ref <requestFingerprint>` 只能消费该真实事件。引用必须与 task、阶段、source revision 和 reason 完全一致，且确认事件必须先于重开并在同一事务中记录一次消费；任意自造或过期字符串均拒绝。确认和重分类写入使用统一 task writer lock；审计同时核对 `wk.confirm`/`long_task.py reopen-phase` 来源、ProjectManagerAgent、精确 `atomicActions`，并要求消费事件紧邻 `PHASE_STALE`。

标准模式下，用户在会话中明确要求跳过 Review 或测试时，使用 `wk confirm --skip-phase code_review|testing --reason <reason> --apply` 记录确认，再由 `long_task.py skip-phase` 使用相同 phase/reason 和返回的 `requestFingerprint`。确认只能消费一次，并绑定当前 iteration 和跳过前 revision；跳过结果是 `SKIPPED/NOT_APPLICABLE` 而非 PASSED，Manifest 持续显示 `explicitReopenRequired=true`，直到从该阶段或上游显式 reopen。重试耗尽后，若当前 iteration 已无 `RUNNING` attempt 且尚无 current StageOutcome，用户仍可显式授权跳过；命令必须同时把该 iteration 的 `BLOCKED` 阶段任务置为 `NOT_APPLICABLE`，不得返回成功却留下不可推进任务。

## Git checkpoint 远端同步

<!-- common-develop-invariant: GIT_PUSH_REQUIRES_CHECKPOINT -->
`wk -gp/--git-push` 是工作模式，使用前必须先启用 checkpoint（`wk -gc/--git-checkpoint`）。它不创建额外 lifecycle phase。默认先在有界 transport timeout 内用 native Git 把 checkpoint 非 force push 到当前分支并验证 remote HEAD；若 `github.com` 的 native Git 网络/认证 transport 不可用，`push` 返回 `CONNECTOR_REQUIRED` 和 deterministic request。`-auto` 必须在同一授权链中调用连接的 GitHub tool 执行 request，并把 receipt 交给 `git_checkpoint.py connector-complete`；receipt 未验证前 `advance-phase` 保持阻断。Connector fallback 不授权创建临时 PR/workflow/branch，也不把 local/remote 不同 SHA 伪装成一致；必须以 exact tree + normalized message + equivalent parent 证明 remote checkpoint。

## UX-1：恢复提示与日常体检

`wk next` 的 JSON 输出使用结构化的 `nextCommand` 作为规范的下一步命令字段；旧字段
`recommendedCommand` 仍保留为命令字符串，供旧消费者平滑迁移。`nextCommand` 包含
`command`、`reason`、`requiredAgent`、`requiresConfirmation`、`readOnly`、`executionContext` 和 `ownerContextReuseAllowed`，便于 AI
在执行前判断权限、确认和副作用。使用 `--explain` 时，除命令外还会返回当前决策、
已满足条件、缺失条件、阻断原因和执行成功后的下一步。

Review/Revalidation 动作固定返回 `executionContext=ISOLATED_REVIEW_SESSION_REQUIRED` 和 `ownerContextReuseAllowed=false`。协调者应创建真实子智能体、隔离任务上下文或分派独立人工 Reviewer；在 Owner 同一上下文中只切换 `-g` 不满足独立 Review。
创建子智能体时，主会话必须先显示包含 Reviewer、phase、revision、scope 和停止条件的分派回执；宿主没有流式子智能体消息时也不能省略该回执。等待只使用宿主原生 wait/status/send-input/interrupt，不得执行 `sleep`、`echo true` 或无输出 shell 轮询。详细协议见 `review-core.md#21-子智能体分派握手与等待`。

普通 `wk status` 除了返回当前任务事实，还附带只读的 `health` 投影；`--summary` 为快速定位而明确不运行这些诊断：

- `health.status`：`PASSED`、`DRIFT` 或 `INVALID`；
- `health.quickChecks`：有界检查 `work-file`、`task-events`、`runtime-reducer`、
  `request-intake`、`authority-consistency` 和 `task-validation`；
- `health.readOnly: true`：体检不会重写事件账本、任务状态缓存、work.md 或其它事实。

体检失败时，`status` 仍尽量返回结构化 JSON，并把具体失败放入对应 quick check；
调用方不应把 `health` 当作新的状态权威，生命周期状态仍由事件 reducer 决定。

## UX-2：治理深度建议

`wk start` 会在初始化/预览结果中返回严格 Schema 的公共 `governanceProfile` 和
`decisionGuide`，并将完整内部推荐快照写入 `request_intake.json`；任务状态只保留 Profile、推荐输入摘要 digest
和 override 标志，便于跨会话回查而不复制整份建议。可通过 `--changed-scope`、
`--graph/--changed-ref`、`--constraints`、`--current-profile`、`--exploration` 和
`--override-reason` 提供完整推荐输入。
它们根据 Request Intake、影响面和项目声明能力给出 `SMALL`、`PRODUCT` 或
`HIGH_RISK` 建议，以及推荐的 workflow、工作模式和风险理由。该建议始终是
**advisory**，并带有 `overrideAllowed: true`；用户显式提供的工作模式优先，
推荐结果不会静默覆盖模式，也不会跳过生命周期门禁。

因此，AI 应把 `decisionGuide` 当成选择辅助，而不是授权结果：在用户已经明确选择
模式时继续执行该模式；在未选择模式时向用户说明建议理由，并在存在不确定性时请求
确认。简单优化的治理 Profile 也不等价于把工作模式强制改成 `-min`。

## UX-3：有界授权批处理

`wk confirm` 用于一次记录一个有界的授权意图，降低逐门禁确认的往返成本；它不
执行阶段动作：

```bash
python3 scripts/wk.py confirm -g ProjectManagerAgent \
  --task-dir {TASK_DIR} --until testing \
  --reason "用户授权本批次处理" --preview

python3 scripts/wk.py confirm -g ProjectManagerAgent \
  --task-dir {TASK_DIR} --until testing \
  --reason "用户授权本批次处理" --apply
```

`--until` 接受 canonical phase，也接受 Runtime Contract 中对应的 archive gate
别名。`--preview`（默认语义）只计算并输出 `atomicActions`；`--apply` 只追加
`AUTHORIZATION_BATCH_CONFIRMED` 事件。两者都不会推进阶段、创建 Attempt、写入
Evidence、执行 Review、创建 Git checkpoint 或绕过任何 lifecycle gate。后续每个
原子生命周期命令仍必须由其既有 Owner/Reviewer 权限和前置条件单独通过。

当当前任务启用了 `-min` 或 `-ws` 等会跳过阶段的工作流时，`atomicActions` 仍保留
请求范围内的 canonical phase，便于审计范围完整可见；但合法跳过的阶段使用
`status: NOT_APPLICABLE`、空 `command` 和 `PHASE_SKIPPED_BY_ACTIVE_WORKFLOW`
reason code。此类阶段不需要 Gate、不执行命令，也不会产生授权消费；
`NOT_APPLICABLE` 表示当前工作流不适用，不表示阶段已通过。

相同 task、范围、Agent、理由和原子动作的授权请求使用稳定 fingerprint；重复的
`--apply` 返回 `NO_OP`，不会追加重复事件。成功事件同时绑定当前任务的 `taskId`、
当前阶段 `iterationId`、当前阶段 `revision` 和相对项目文档根的 `taskPath`；历史绝对
`taskDir` 仅在 `version/{VERSION}/task/{TASK_ID}` 路径尾部与当前任务一致时兼容读取，
缺少任务身份绑定的 legacy 授权只保留为历史记录，不会被接受为可消费授权。只有 ProjectManagerAgent 可
执行该命令，授权原因必须非空，并且目标不能早于当前阶段。事件账本损坏或缺失时命令
fail-closed。

只有正常 `advance-phase` 在既有 Gate 通过后，才按连续阶段逐步消费授权，并追加
`AUTHORIZATION_BATCH_CONSUMED`。消费结果从输出的 `authorization.status` 读取：
成功为 `CONSUMED`，无匹配或已失效授权为 `NOT_USED`。`PHASE_STALE`、`reopen-phase`
或上下文绑定变化都会使授权失效；消费事件与状态事件在同一事务中，事务失败时一起
回滚。授权仍不能替代 Gate、Evidence、Review、Owner/Reviewer 权限或 Git checkpoint。

## UX-5：阶段动作预览

`wk step --preview` 只读取当前任务的 reducer 状态、任务计划、事件账本和当前阶段事实，生成当前阶段可执行的原子动作计划。它不会执行任何动作，也不会写入任务状态、`task_events.jsonl`、Evidence、授权事件或派生缓存：

```bash
python3 scripts/wk.py step -g ProjectManagerAgent \
  --task-dir {TASK_DIR} --preview --json
```

返回结果包含 `currentPhase`、`currentIterationId`、`currentRevision`、生命周期
`classification`、`reasonCodes`、`steps` 和稳定的 `planDigest`。每个 `steps` 项至少
包含 `command`、`permissionCommandId`、`requiresConfirmation`、`readOnly`、
`requiredAgent` 和 `reason`，便于调用方在真正执行前识别动作权限与确认要求。

`planDigest` 是当前计划输入的 SHA-256 绑定摘要，不包含时间戳或随机值；同一任务事实
重复预览会得到相同摘要。事件账本、任务计划、当前阶段 iteration 或 artifact revision
发生变化后，旧摘要必须视为失效并重新执行 preview。`readOnly: true`、
`mutationsApplied: false` 和 `authorizationRecorded: false` 是本次预览的结果声明，
不是对计划内动作的授权。

## UX-6：受限阶段编排

`wk step --apply` 必须引用同一任务最近一次 `--preview` 返回的完整 `planDigest`：

```bash
python3 scripts/wk.py step -g ProjectManagerAgent \
  --task-dir {TASK_DIR} --apply --plan-digest sha256:{PLAN_DIGEST} --json
```

Apply 会先重新执行 validation、reducer decision 和 preview；阶段、iteration、artifact
revision、`task_events.jsonl` 或 `task_plan.json` 任一绑定变化，都会以
`STEP_PLAN_STALE` 拒绝且不写入。它是**闭世界受限编排器**，只允许调用已有原子命令
`wk.reconcile`、`long_task.publish-artifact`、`long_task.finalize-phase` 和
`long_task.advance-phase`，参数只能由当前 decision 生成；不会接受 shell、模块路径、
任意权限 ID 或状态字段 patch。每个原子命令仍独立执行自身的 Agent、Owner、Evidence、
Review、P0/P1、Git checkpoint 和阶段 Gate，失败立即停止，不伪造后续成功。

需要人工输入的 `start-attempt`、`finish-attempt`、Review、Evidence、外部发布、Git push、
归档和高风险确认不会自动执行；预览中的 `autoApplyAllowed: false` 动作须按输出的
`nextCommand` 单独执行。全部原子动作成功返回 `APPLIED`；中途失败返回 `PARTIAL`，并
包含 `completedSteps`、`failedStep`、`repairCommand` 和 `resumeToken`。`PARTIAL` 不能视为
整体成功，修复后须重新 preview，不能复用已经失效的摘要。

`readOnly`、`mutationsApplied` 和 `authorizationRecorded` 是结果声明；step 本身的
权限只允许 ProjectManagerAgent 使用，且不能降低被调用原子命令的权限要求。

`wk.step` 的入口授权人与原子动作的业务 Owner 是两个不同概念。入口始终由
`ProjectManagerAgent` 调用；当计划包含 `long_task.publish-artifact` 时，编排器会从当前
canonical phase 的 Runtime Contract 读取 `ownerAgent`，并以该 Owner 调用发布命令（例如
`development` 阶段使用 `DevelopAgent`），而不是把 `ProjectManagerAgent` 或用户提供的
任意 Agent 传给发布命令。`requiredAgent` 若与 canonical Owner 不一致，编排器会以
`STEP_OWNER_RESOLUTION_FAILED` fail-closed，并要求重新预览/检查 doctor。用户不能通过
CLI 覆盖这个内部身份。`long_task.finalize-phase`、`long_task.advance-phase` 与
`wk.reconcile` 仍按各自原子命令的既有授权语义执行，不会被 `publish-artifact` 的 Owner
解析逻辑改写。

## AI 编排入口

`begin`、`progress` 和 `finish` 复用现有生命周期决策和原子命令。无 `--apply` 时只读；`--apply` 会写入任务事实或派生上下文，因此命令的 mutability 保守声明为 MUTATING。参数以 CLI help 为准。

- `wk begin --task-dir TASK_DIR` 返回当前 phase、owner、revision、上下文、`actionPlan` 和 `planDigest`。Owner 使用 `--apply` 启动当前阶段待执行任务或恢复 RUNNING attempt；多任务时可指定 `--task-id`。它不会把 ProjectManagerAgent 自动换成 Owner。
- `wk next` 和 `wk progress` 返回同一套可执行计划。`progress --apply --plan-digest DIGEST` 最多调用一个现有原子动作；摘要直接取自 `begin`、`next`、`progress` 或 `step --preview`。事实变化会使旧摘要失效。`step --apply` 原有的受限动作序列仍保持兼容。
- `progress` 与 `step` 均由 ProjectManagerAgent 调用；Owner 使用 begin、对应原子命令和 record-result。计划的 `requiredAgent` 指定原子动作责任人，`progressAgent`/`progressCommand` 指定协调推进人，不代表 Owner 可提升权限。命令路径按当前安装包生成，执行时不依赖终端目录；相对 taskDir 应在首次查询时解析为绝对路径。
- `begin` 根据已保存 Intake 的 originalInput 和当前阶段任务描述解析条件路由；Intake、任务计划和事件变化均使上下文缓存失效。已有执行包时无需固定再次运行 ai_context、status、next 和 context；它们保留为按需查询或诊断入口。
- `actionPlan` 只包含参数完整的现有命令。缺失信息进入 `requiredInputs`；独立 Review 必须继承 `nextCommand.executionContext`。`progressCommand` 是消费当前摘要的复合入口，`actionPlan` 中的命令是对应原子入口。
- 执行声明的验证命令时，可使用 `execution_evidence.py run --attempt-id ATTEMPT_ID` 把结果文件绑定到 attempt。然后使用 `wk record-result --task-dir TASK_DIR --attempt-id ATTEMPT_ID --command-id COMMAND_ID --result-file RESULT_FILE --revision REVISION --phase PHASE --json`；COMMAND_ID 是结果 JSON 的文件名去掉扩展名，或其显式 `command_id`。
- `record-result` 校验当前 iteration、RUNNING 状态、Owner、phase、revision、结果摘要及精确声明的命令。传入 `--acceptance-id` 时，该 ID 必须属于当前任务所链接 Trace 的 `acceptance_criteria`；生成的 COMMAND_EXIT Assertion 绑定 Evidence 并验证。命令成功不代表业务语义或独立 Review 通过。
- 登记在同一 task writer lock 内完成 Registry、Assertion、attempt 事件和 Manifest 更新；失败恢复已存在文件并删除本次新文件。相同 attempt、command、revision、Acceptance 的重试复用 Assertion，重复 Evidence 不追加 attempt 事件。返回的 `gate` 始终来自现有生命周期决策。
- `wk finish --apply` 执行当前阶段任务声明的 validation commands，并聚合 `task_verify` 的 Evidence、Acceptance、Review、Traceability 校验。未发布的候选产物需提供 `--revision`，可用 `--task-id` 定位任务。只有 canonical COMPLETED 且校验成功才返回 DONE；CLOSURE_ONLY 仍为 PARTIAL；失败和未满足门禁为 BLOCKED。无 `--apply` 只投影已有事实，`validationExecuted=false` 表示本次未执行命令。
- `finish` 从 `DOC_ROOT/version/{VERSION}/task/{TARGET_ID}` 推导项目根目录（DOC_ROOT 的父目录），默认在该根目录执行验证，与调用终端所在目录无关。`--cwd` 可指定项目内的现有子目录；相对路径按项目根目录解释，绝对路径也必须属于该项目，解析符号链接后越界或目录不存在时在执行前阻断。预览和执行响应的 `validationCwd`、命令结果的 `metadata.cwd` 标明实际目录。

风险深度由 `assets/runtime/runtime-contract.json#/aiOrchestration/riskPolicy` 决定。AI 可使用 `--risk-facts` 传递带当前 `revision` 的 JSON 观察，例如 `changedModules` 和 Schema/API/DB/权限/事务/安全/迁移是否发生变化；它是本次编排的建议输入，不是授权记录，不持久化为第二套任务事实。未知信息按 MEDIUM 处理，任意已知 HIGH 或开放 P0/P1 均升级；LOW 必须证明单模块和全部关键影响为 false/NONE。标准模式保留 STANDARD 下限，所有模式都继续执行原有硬门禁。风险建议不能自动跳过阶段或降低独立 Review 要求。

`--risk-facts` 在任何 apply 动作之前递归校验：顶层必须为对象；revision、status、severity 等为字符串；riskLevel 为 LOW/MEDIUM/HIGH；模块与触发项为字符串数组；影响信号为布尔值或已识别字符串。无效 JSON、字段类型、风险等级或超过 64 层的嵌套返回 `RISK_FACTS_INVALID`，不会启动 attempt、执行阶段动作或登记验证。读取历史事实时，异常字段不能证明 LOW；P0/P1 的关闭状态不明确时仍按开放风险处理。

上下文缓存位于 `cache/context/{revision}/{profile}.json`，profile 包含 Agent 身份以隔离权限上下文。仅缓存 Contract 查询、Routing、允许命令、稳定 ID 索引和 revision 摘要。命中时直接复用这些计算；Gate、Evidence、Review 和 Acceptance 结论仍读取当前事实。无 revision 时禁用缓存；只读预览不会创建缓存，写入发生在 apply 路径。revision、work.md、Contract 及其依赖、Routing、任务计划、Traceability、Intake 或事件账本变化都会失效，因此 reopen/amend 也会失效。

AI 默认使用 JSON；面向用户仅输出简短自然语言。阻断包含 `reasonCode/message/repairPlan/requiresUserDecision/nextReviewer`，已有领域错误保留兼容 `error` 对象。仅明确的范围变更、不可逆取舍、风险接受或其他必要决策需要询问用户；一般命令失败、重试和只读诊断不隐含确认。

## 按需修订与恢复诊断

`finish-attempt --auto-evidence --check` 只能执行当前 task 的 `validation_commands` 中精确声明的命令；成功才完成 attempt，失败只保留真实失败证据并保持 `RUNNING`，且不替代 Reviewer Evidence。详见 `references/recovery-archive/long-task-core.md#自动验证证据`。
当前阶段内、影响范围可由 PASSED Impact Plan 高/中置信界定且不涉及跨 Context、安全、权限、事务或信任边界的需求/设计修订，使用 `amend-phase`：Impact Plan 必须给出 `affectedScope` 的 task/trace/acceptance 稳定 ID 完整闭包，命令行集合必须精确匹配；`--canonical-file` 必须能证明属于当前 task/phase，命令在 task 级独占写锁内原子追加 Amendment 块并绑定 SHA-256 文档 revision，且 canonical 块必须与事件账本双向一致。只重做显式受影响且依赖闭包完整的任务，保留未受影响的 PASSED 任务和 Evidence。失效 Evidence 会被当前 Amendment 的所有新 Gate 拒绝。`PROCESS_ONLY` 仅更新流程/存储元数据，不使用 `amend-phase`。Impact Plan 只证明影响范围，不能替代 Review、Evidence、StageOutcome 或任何 lifecycle gate。影响未知、范围较广、高风险、跨阶段，或下游已有 PASSED StageOutcome 时必须使用 `reopen-phase`。
`reopen-phase` 只创建新 iteration 并把下游事实标记为 `STALE/REWORK`；不得物理删除 canonical `requirement.md`、设计文档、`test_case.md` 或实施计划。旧 Evidence/Review 作为历史保留但不得证明新 revision；上游重新通过并推进后才更新同一 canonical 下游文件。

需求、流程、业务模型、设计、开发和测试变更均沿用同一 `version/{VERSION}`：原位修改对应 canonical 产物，并追加 revision、变更、Evidence 和 Review 记录，不因返工创建新版本。决定重开前从变更发生阶段逐级向上只读检查；每次认为上游也需调整时先取得用户确认，再继续检查更上游，期间不得修改任何候选阶段产物。最早受影响阶段确定后只从该阶段执行一次 `reopen-phase`，随后按 canonical 顺序向下修改；只为实际重开的阶段递增 revision，且不回收废弃编号。跨阶段重开必须消费由 `wk confirm --reopen-from` 记录的真实授权事件，禁止用任意 `--confirmation-ref` 伪造确认。重开后的普通门禁只惰性读取当前 revision Evidence，历史对象仅在显式审计/归档时展开。

旧版本需求同步可能只将已有下游引用标记为 `STALE`，且当前 revision 的需求确认事实已经由独立 Reviewer 和 Evidence 重新确认时，ProjectManagerAgent 可使用 `long_task.py revalidate-traceability` 恢复明确指定的单条 `STALE/GAP` Trace。该命令必须绑定当前或上游 phase、同 revision 的 ACTIVE Evidence、独立 PASSED Review 和 VERIFIED Acceptance；它只追加 `TRACEABILITY_REVALIDATED` 事件并原子更新该 Trace 与 Manifest，不替代 `finalize-phase`、`advance-phase` 或下游重新执行。校验会检查每条历史记录的字段及 Evidence/Review/Acceptance 绑定，并只将最后一条恢复记录的 digest 与当前 canonical `traceability.json` 比较（更早 digest 是其写入时的历史摘要）；无法满足绑定条件时必须保持阻断。
