# 研发阶段生命周期核心

> 本文件是 Lifecycle Core Reference。默认通过 `relevant_sections` 按主题章节读取；不要无选择地整篇加载。

## 通用生命周期

本文件定义研发阶段协议与阶段推进通用边界；完整阶段顺序只读取 `assets/runtime/runtime-contract.json#canonicalPhases`。

### 主流程

<!-- common-develop-invariant: GIT_PUSH_REQUIRES_CHECKPOINT -->
唯一阶段顺序只由 `assets/runtime/runtime-contract.json#canonicalPhases` 定义；可读顺序见 `runtime-contract.md#3-唯一生命周期`，本文件不复制第二份阶段清单。范围判断是进入流程前的 preflight，不创建平行事实阶段。`wk -mc`、`wk -v`、`wk -min`、`wk -auto`、`wk -ar`、`wk -gc`、`wk -gp` 是工作模式参数，`wk -l`、`wk -d` 是辅助动作，均不创建平行阶段。

未启用 `wk -v` 时不产出页面业务行为设计，`work.md#页面设计` 为“无”；启用时由 DesignAgent 在 design revision 中维护页面设计。使用 `wk -min` 时，canonical `test_design`、`testing` 不执行并分别记录 `SKIPPED` 及原因；Development 内嵌 TDD gate 设为 suppressed，不创建独立 `tdd` 阶段或 StageOutcome；运行契约同时取消适用阶段中的 TestDesign/Verification 必需分派。其余 canonical 阶段顺序不变。使用 `wk -mc` 只控制前后端伪代码产物，不改变阶段顺序。使用 `wk -auto` 时，当前阶段的必需产物、Review、证据和阻塞检查全部满足后自动进入下一阶段，不等待用户确认；任何失败/阻塞门禁、开放 P0/P1、缺失事实或安全边界仍立即停止。所有目标和任务按运行契约声明顺序执行；使用 `wk -gc` 时，ProjectManagerAgent 在阶段门禁通过后创建本地 Git 检查点，提交失败则不推进阶段；只有显式 `wk -gp` 才允许在 checkpoint 后同步当前分支。优先 native Git 非 force push；`github.com` 的 native 网络/认证 transport 不可用时可进入 GitHub Connector handoff，但 receipt 验证完成前仍不写下一阶段 transition。Connector 只允许 exact Git Data checkpoint publish/verify，不创建额外 PR/workflow/branch；local/remote SHA 可分离但必须 exact tree/message/parent 等价且记录双 SHA。始终禁止 force push、tag push、merge、创建 PR 或改写本地历史。


### 通用阶段协议

每个阶段固定执行：

1. 冻结目标、输入 revision、追踪项、决策、允许范围和开放问题；
2. ProjectManagerAgent 分派责任 Agent；
3. 责任 Agent 产出候选 revision 和自检证据，完成 PASSED attempt；ProjectManagerAgent 通过 `acceptance.py ensure-review` 幂等登记当前 revision 的 required Reviewer assertion 后，责任 Agent 再通过 `publish-artifact` 固定当前 revision、Evidence 和 `RUNNING` StageOutcome；
4. 必需 Reviewer 基于同一 revision 独立审查；
5. 汇总 finding，交叉质询，责任 Agent 修订；
6. 原 Reviewer 或指定验证者复核；
7. ProjectManagerAgent 通过 `finalize-phase` 核验 Review、Evidence、问题和追踪门禁，将同 revision Artifact 与 StageOutcome 原子置为 `PASSED`，并更新 traceability/task plan；只有跨 phase、跨团队或可独立恢复的 owner 边界才更新 dated handoff；
8. 启用 `wk -gc` 时创建当前阶段 Git 检查点；若同时启用 `wk -gp`，先完成 native push/verify 或 Connector request→receipt 验证该 checkpoint，成功后才允许 phase transition；
9. 门禁通过则推进，否则回到最早责任阶段；未启用 `wk -auto` 且当前阶段要求用户确认时等待用户，启用后由 ProjectManagerAgent 按运行契约自动继续或阻塞。

阶段结果必须使用 `assets/runtime/runtime-contract.json#statuses.stageOutcome`；`SKIPPED` 必须说明风险，不等于通过。

## 阶段执行规则

本文件按阶段描述输入、输出和门禁，但不重新定义生命周期顺序。

### 需求确认

责任：RequirementConfirmationAgent。

必须创建或更新 `version/{VERSION}/requirement/{TARGET_ID}/requirement.md`，明确受影响对象、当前行为及证据、目标行为、现在做的原因、完成标准、范围内外、依赖、失败模式、恢复方式和关键决策；通过 `requirement_doc.py validate --stage confirmation` 后才能提交确认。连续澄清在下游分析 attempt 尚未启动前复用同一 Change Request 稳定 ID并递增 revision；需求确认不得提前重写详细设计、完整实施计划或项目级 README，只标记下游产物待重算。


通过条件：标准模式下用户确认当前 revision；`wk -auto` 下 RequirementConfirmationAgent 与必需 Reviewer 已基于当前证据确认 revision，ProjectManagerAgent 记录自动决策；无开放 P0/P1；不存在会改变完成语义的未决问题。


### 需求分析

责任：RequirementAnalysisAgent。

必须在同一 `requirement.md` 中形成单功能或多功能列表、每个功能的业务规则、权限、状态、输入输出、异常、幂等、并发、部分失败、稳定追踪 ID 和可观察验收标准；每个功能、每条 BR/CR 和每条 AC 必须进入合法追踪项；删除/取消/依赖/跨模块触发时，还必须形成需求/功能关系和条件化影响策略；三步以上、状态流转、变体、回退/补偿或端到端场景必须创建或更新结构化 L0/L1/L2 业务流程，跨模块技术协作另在 `dependency_impact.yaml` 中引用对应 flow/step。先通过 `requirement_doc.py validate --stage analysis`，再执行 `requirement_doc.py sync-traceability` 将当前需求 revision 同步到长任务追踪矩阵。


通过条件：每个范围内功能、BR/CR 和 AC 均有合法追踪引用；`sync-traceability` 已完成；追踪项无 `GAP/STALE`；适用关系和处置策略可判定；信息足以建模、设计和测试。

若旧版 `sync-traceability` 因需求正文变更将已有下游引用标记为 `STALE`，但当前需求确认 revision 已有独立 PASSED Review、ACTIVE Evidence 和 VERIFIED Acceptance，不得手工修改 `traceability.json`。由 ProjectManagerAgent 通过 `long_task.py revalidate-traceability` 按 Trace ID 逐条恢复；命令会保留旧状态和事件历史，并将每次写入后的 Trace digest 绑定到 `TRACEABILITY_REVALIDATED` 事件。校验逐条检查历史绑定，只要求最后一条恢复记录的 digest 等于当前 canonical 文件 digest；更早记录的 digest 代表当时的文件版本。恢复 Trace 后仍须按正常生命周期完成阶段收口和后续分析，不得把该命令当作 `finalize-phase` 或跳过需求分析。


### 业务建模

责任：BusinessModelAgent。

定义统一语言、实体、值对象、聚合、不变量、状态机、关联对象生命周期、命令、事件、策略和业务错误，并将当前业务流程/步骤映射到这些模型事实；不得在业务模型中重画另一套流程。


通过条件：术语唯一；不变量和状态转换有正反例；关联影响与补偿可判定；无平行模型；每个适用追踪项有模型引用。


### 设计

责任：DesignAgent。

定义模块、接口、API、DB，以及已确认业务流程步骤的跨模块调用/事件顺序、本地事务、最终一致性、幂等、并发、重试、补偿、错误、可观测性和兼容性。仅使用 `wk -mc` 时生成前端伪代码和后端伪代码实现契约；未使用时 `work.md` 对应两列为“无”。仅使用 `wk -v` 时生成页面业务行为设计；未使用时页面设计为“无”。标准模式仍设计测试 seam；`wk -min` 只保留实现所需边界，不产出测试用例或测试实现要求。


通过条件：设计与需求/模型/关联影响同义；跨模块成功、失败和补偿路径完整；追踪完整；可实现；标准模式具备可测试性，`wk -min` 具备运行契约规定的非测试可验证性；ArchitectureReviewAgent 对同一 design revision 通过。

> 架构审查必须绑定同一 `design` revision，作为 Reviewer 门禁证据；不得创建平行阶段或独立 revision。


### 测试设计

责任：TestDesignAgent。每个 Requirement 的主要产物固定为 `version/{VERSION}/requirement/{REQUIREMENT_ID}/test_case.md`，阶段名固定为 `test_design`；不得维护版本根级共享测试用例文件。

使用 `wk -min` 时本阶段不执行，不创建或更新测试用例代码及后续测试任务；StageOutcome 写 `SKIPPED`，`work.md#测试用例` 写“无”。

覆盖正常、边界、异常、权限、事务、幂等、并发、兼容性、关联对象处置，以及业务流程主路径、关键变体、阻塞失败路径和跨模块部分失败/补偿；Case 记录具体输入、具体输出、验证方式、flow/step/variant/failure-path、页面/操作、数据准备与清理、自动化测试方法和禁止副作用。


通过条件：每个适用追踪项至少有一个 Case；Case 可反向追溯且通过 `case_doc.py validate --stage ready`；关键失败和最终状态可观察；页面设计、数据脚本和自动化测试按适用性双向引用同一 Case。


### 实施计划

责任：ImplementationPlanAgent。

按可独立验证的纵向行为切片；每项任务声明具体目标、实现方式、验收标准、依赖、`flow_refs/flow_step_refs`、关系/技术映射引用、允许文件、输入输出、测试命令、预期结果、停止条件和 Reviewer。跨模块任务按模块 owner 拆分并用 `depends_on` 固定调用、补偿和组合验证顺序；同一时刻只执行一个任务。大需求或显式 `wk -tp` 时，使用 `task_plan.py` 维护 `development_task_plan.json` 和串行 Review 历史；任一 finding 必须先返修并生成新 `TP-*` revision，再从首位 Reviewer 重新审查。


通过条件：依赖无环；覆盖全部追踪项；无占位符；每项任务都以可执行验证结束；当前 `TP-*` revision 的全部任务已被其分配 Reviewer 精确覆盖并 `PASSED`。


### TDD（开发前 RED 门禁）

责任：TddAgent；本阶段只建立能够驱动开发的测试基线，不产生生产实现。

使用 `wk -min` 时本阶段不执行、不编写测试代码，StageOutcome 写 `SKIPPED`。标准模式下：

- 新行为和 BUG 修复必须有真实、可复现、因目标行为缺失而失败的 RED；
- 纯重构必须有当前 GREEN 表征测试基线；
- 测试必须映射 Case/契约并使用稳定公共 seam；
- RED 或表征基线的命令结果、日志、revision 和 digest 必须可回查；
- TDDReviewAgent 在 `development` 的 PRE_IMPLEMENTATION 轴独立确认开发前 RED 门禁。

通过条件：开发前测试门禁真实且当前；此时才允许 `development` attempt 启动。GREEN 和 REFACTOR 不属于本阶段，因为生产代码尚未实现。


### 开发（GREEN/REFACTOR）

责任：DevelopAgent。

只实现当前任务最小变更，遵循允许文件、关联影响、当前 business flow/step、跨模块实现映射、代码注释规范和现有项目模式；标准模式基于已通过的 RED/表征基线完成最小 GREEN 和必要 REFACTOR，并运行相关测试、静态检查、构建及必要集成/Harness。

通过条件：

- 当前测试由 RED 变为 GREEN，或纯重构持续 GREEN；
- REFACTOR 后仍通过；
- 修改文件在任务允许范围；
- 结构化 command-result、真实 diff 和证据属于当前 implementation revision；
- TDDReviewAgent 在 `development` revision 上复核 GREEN/REFACTOR；
- 无范围漂移和开放 P0/P1。


### Code Review

主轴必须独立：

- SpecComplianceReviewAgent：检查是否完整实现需求、模型、设计和 Case；
- EngineeringStandardsReviewAgent：检查工程规范、结构、错误处理、代码注释、测试质量和维护性。

按当前 diff 和设计风险从 `runtime-contract.json#riskReviewerCatalog` 登记额外 Reviewer；登记项必须包含 trigger、reason 和 `evidence_ids`。架构代码检查属于 `code_review` 的额外 Review 轴，不创建平行生命周期阶段。

启用 `wk -ar` 时，development 先完成架构代码骨架及 Architecture/Spec 第一轮 Review，只有通过 `advance-development-step` 才能开启具体实现；实现完成后进入最终 Code Review。完整规则只读取 `architecture-review-development.md`。

通过条件：两个主轴均通过；必要专项 Review 完成；无开放 P0/P1；修复经过独立复核；受影响测试重新执行。


### 测试

责任：TestAgent。

使用 `wk -min` 时本阶段不执行，TestAgent 不被分派，`work.md#测试` 写“无”，StageOutcome 写 `SKIPPED`。

依次执行适用的隔离测试、数据访问/事务集成测试、关联对象处置测试、跨模块成功/部分失败/补偿测试、接口或端到端业务测试和项目 Harness，核对日志、最终状态和禁止副作用。独立测试库测试仅在 `TEST_DB_URL`、用户名变量 `TEST_DB_USER`和 `TEST_DB_PASSWORD` 均非空时执行；缺任一项时该测试子范围记录 `SKIPPED`，不得回退到 local/dev/生产业务库，其它适用测试继续执行。


失败时必须定位最早责任阶段，不允许 TestAgent 直接跨角色改写需求、模型或设计。


### 完成验证

责任：CompletionVerificationAgent。

逐条核对范围、追踪项、计划任务、diff、Review、测试、BUG 原始症状和开放问题。最终只输出：

- `DONE`：全部范围内工作通过且证据新鲜；
- `PARTIAL`：完成明确子集，剩余和恢复入口已记录；
- `BLOCKED`：无法继续，说明阻塞、证据和建议动作。

## 回退、STALE 与需求变更

本文件定义 STALE、阶段重开、下游失效和用户中途修改的处理规则。

### 回退与 STALE

上游实质变化必须使受影响下游失效：

- 需求确认变更 → 所有下游重评；
- 需求分析、业务流程或依赖关系变更 → 先用 stable requirement/feature/rule/AC/TR/FLOW/STEP 定位影响，再沿 `dependency_impact` 关系传播，业务模型及受影响下游重评；
- 业务模型变更 → 设计、测试设计、计划、代码和测试重评；
- 设计变更 → 测试设计、计划、TDD、代码、Review 和测试重评；
- 测试预期变更 → 计划、TDD、实现和测试按影响重评；
- 代码变更 → Code Review、测试和完成验证失效。

不得仅改状态文件掩盖真实失效。

`reopen-phase` 创建新 iteration 时，受影响任务的 `input_revisions` 必须立即收敛为该阶段机器契约声明的**直接前置阶段**；被同时重开的前置阶段尚无新 revision 时使用空 revision 占位，不保留旧 TDD、Development、Code Review 等额外历史输入。

前置阶段重新通过后，ProjectManagerAgent 使用 `advance-phase` 进入下一阶段时，必须通过 event transaction 把下一阶段当前 iteration 的 runtime input revision 原子重绑到刚通过的 canonical predecessor revision。`task_plan.json` 与 `development_task_plan.json` 保持静态；启用 `wk -gc` 且未启用 `wk -gp` 时，event ledger 与 Git checkpoint 保持既有同一推进事务；启用 `wk -gp` 时，当前 PASSED phase 的 checkpoint 先独立完成并远端验证，随后才追加下一阶段 transition。native 失败若返回 `CONNECTOR_REQUIRED` 只是 transport handoff，不是阶段通过；必须完成 Connector receipt 校验后重试 `advance-phase`。任何 remote 未验证状态都禁止 lifecycle 前移。


### 用户中途修改分类

- 当前阶段内的小范围澄清或方案修订，若 PASSED Impact Plan 能以高/中置信限定影响，且不触发跨 Context、安全、权限、事务、信任边界或已通过下游阶段 → 使用 `amend-phase`，只重做受影响任务；
- 改变目标、范围、规则或验收且无法限定在当前阶段 → 按“版本内追加与重开边界判定”只读确认最早受影响阶段，再使用 `reopen-phase` 回到该阶段；
- 不改变业务语义，只改变模型/API/DB/模块方案但影响跨阶段或高风险 → 同样先确认最早受影响阶段，再使用 `reopen-phase` 回到业务建模、设计或更早阶段；
- 已确认行为与实际不一致 → DebugAgent + TDD；
- 只修 Review 质量问题 → 回到责任实现阶段；
- 无法唯一分类 → `WAITING_USER`。

### 版本内追加与重开边界判定

版本目录是本次需求的固定事实边界；需求、业务流程、业务模型、设计、开发和测试的
修订默认继续归属同一个 `version/{VERSION}`。canonical 文档、生产源码和测试资产均
原位修订，并在版本任务事实中追加可定位的 revision、变更、Evidence 和 Review 记录；
不创建按返工轮次复制的新版本目录。只有用户明确提出新的产品/发布版本时，才允许
启动新的版本目录。

任一阶段发现需要调整时，先确定最早受影响阶段，再执行重开：

1. 从变更发生阶段开始，只读检查直接上游是否也需要调整；不得修改当前阶段、候选
   上游阶段或任何中间阶段的文档、源码、测试、Evidence、Review 和状态；
2. 若判断直接上游需要调整，先向用户说明影响依据和扩大范围，取得明确确认后，才把
   候选边界移到该上游并继续检查它的直接上游；若不需要调整，则停止向上扩展；
3. 重复该过程直到确定最早受影响阶段。随后只从该阶段执行一次 `reopen-phase`，由
   生命周期统一把其下游标记为 `STALE/REWORK`，再按 canonical 阶段顺序向下实施修改；
4. 禁止先修改某一阶段、再逐步向上重开和确认。业务模型变化只是该通用规则的一个
   示例：先只读检查 `requirement_analysis`，需要扩大时经用户确认后再检查
   `requirement_confirmation`；最终从确认出的最早阶段一次重开。

每次重开只产生新的 iteration 和逻辑失效事件；canonical `requirement.md`、Flow
changeset、业务模型、设计、开发源码和测试资产原地保留并修订。只有实际纳入重开的
阶段才递增 revision；未受影响的上游 revision 保持有效。若需求确认确实被重开，历史
`REQCONF-P3-R06` 失效后使用 `REQCONF-P3-R07`，不得回收已使用编号。旧 Evidence、
Review 和 StageOutcome 仍可审计，但不参与当前门禁；不要求为每条历史 Evidence 执行
状态迁移或重新计算 digest。

`amend-phase` 必须通过 `--canonical-file` 指定同一 version `doc/` 树内且所有权可证明的 canonical 需求或设计文件，并在 task 级独占写锁内原子追加可定位的 Amendment，记录 amendment ID、base/new revision、原因、变更章节、受影响任务/Trace/Acceptance 和 Evidence 保留/失效声明及文档摘要。原正文和历史记录不得删除；若追加内容与原文冲突，以最新有效 Amendment 为当前解释。Impact Plan 的 affected scope 必须与命令集合精确一致，且 task 对应的 Trace 及其 Acceptance 必须形成完整闭包；canonical Amendment 块与事件账本双向精确对应。Impact Plan 只界定本轮重做范围，不授权跳过正常 Review、Evidence、StageOutcome 或阶段推进门禁。`PROCESS_ONLY` 不走此命令，不产生语义 Amendment revision。
