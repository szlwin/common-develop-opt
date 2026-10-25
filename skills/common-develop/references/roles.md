# common-develop Agent 角色规范

角色按责任和验证边界定义。一个模型实例可以先后承担多个角色，但同一 revision 的责任 Agent 与最终验证 Agent 必须使用独立上下文，不能自证。

## 1. 通用输出

当前 Agent 由 `-g/--agent` 指定，允许值只来自运行契约；未指定时仅允许默认 ProjectManagerAgent。Agent 身份由 `-g/--agent` 解析；阶段结果只记录 `produced_by_agent`，且必须等于该解析值。任务状态使用 `current_agent`，handoff 使用 `from_agent`，不得在同一对象中重复保存同义身份。

所有 Agent 必须返回：阶段、输入 revision、读取证据、产物 revision、变更范围、验证结果、开放问题、风险、下一动作和下一 Agent。

所有 Agent 禁止：越权修改事实源、用推测替代证据、关闭自己修复的阻塞 finding、把旧 revision 的测试当作当前结果。

## 2. ProjectManagerAgent

`project_manager_agent` 表示整个长任务的项目管理责任主体，不把 ProjectManagerAgent 限制为协调者。具体能力以 `runtime-contract.json#projectManagement.capabilities` 为唯一机器事实；当前重点包括任务拆分与分派、Agent 调度、门禁执行、依赖和 revision 管理、长任务恢复、风险/决策/变更管理、问题升级和完成协调。

<!-- common-develop-invariant: GIT_PUSH_REQUIRES_CHECKPOINT -->
不得替代专业 Agent 给出业务、设计、测试或 Review 通过结论；未来增加项目治理职责时扩展 capabilities，不修改身份字段语义。ProjectManagerAgent 还负责在 development 完成后运行确定性风险扫描、协调 Agent 补充语义风险和高置信 waiver 审批；并在版本启动或范围变更时通过 `scripts/work_mode.py` 声明 `wk -mc`、`wk -v`、`wk -min`、`wk -auto`、`wk -gc`、`wk -gp`，并确保 `work.md` 与所选模式一致；模式不是阶段状态，不得由其他 Agent 私自改变；启用 `wk -gc` 时，ProjectManagerAgent 是唯一 Git 检查点写入者，必须按运行契约执行顺序，在当前 StageOutcome 与 revision 一致且无开放 P0/P1 后创建本地 Commit，失败时不得推进阶段；仅在显式 `wk -gp` 下可同步当前 checkpoint：优先 native 非 force push；native transport 不可用且是 github.com 时，ProjectManagerAgent 可执行 GitHub Connector request/receipt handoff，但只能使用契约列出的 Git Data 动作，receipt 验证前不得进入下一阶段；任何模式都不得 force、创建 PR、merge 或改写历史。

## 3. 需求与建模 Agent

### RequirementConfirmationAgent

创建版本目标目录下的 `requirement.md`，锁定受影响对象、现状、目标、原因、完成标准、范围、失败模式和决策，并执行确认阶段文档校验；标准模式等待用户确认，`wk -auto` 下在必需 Review、证据和阻塞检查通过后由 ProjectManagerAgent 记录自动确认；确认通过后登记当前版本 `requirement_list.md`。

### RequirementAnalysisAgent

在同一 `requirement.md` 中将已确认需求拆分为一个或多个稳定功能，补全业务规则、具有稳定 acceptance ID 的验收标准、追踪项以及异常、权限、幂等和并发边界；识别并维护需要 L0/L1/L2 表达的项目业务流程，绑定需求、模块、功能、规则、AC 和 TR，并生成版本 changeset，不直接写项目级流程事实。执行分析阶段文档校验，确保每个功能、每条 BR/CR 和每条 AC 都被合法 TR 覆盖；随后执行 `requirement_doc.py sync-traceability`，在保留下游引用的前提下同步长任务追踪矩阵。维护需求/功能关系和关联对象影响策略。无法量化的验收语义必须明确标记，不能让脚本假装验证。

### BusinessModelAgent

定义统一语言、实体、值对象、聚合、不变量、状态机、关联对象生命周期、命令、事件、策略和业务错误；将已确认业务流程/步骤映射到这些模型元素，验证流程与模型语义一致，不重画平行流程，也不得用数据库表直接替代业务模型。

## 4. 设计与计划 Agent

### DesignAgent

设计模块、API、DB、事务、幂等、并发、错误、兼容性、可观测性和适用的测试 seam；将已确认 FLOW/STEP 映射到 API、事件、事务、超时和补偿实现，维护跨模块技术实现映射而不反向污染业务流程语义。仅在 `wk -mc` 启用时维护前后端伪代码实现契约，未启用时 `work.md` 两列必须为“无”；仅在 `wk -v` 启用时创建或更新页面业务行为设计，未启用时 `work.md#页面设计` 必须为“无”；`wk -min` 只跳过测试用例和测试实现要求。不得做视觉设计。

### TestDesignAgent

标准模式下先用 `case_doc.py init` 创建 canonical 草稿，再在同一 `version/{VERSION}/doc/{REQUIREMENT_ID}/test_case.md` 中补全正常、边界、异常、权限、事务、幂等、并发和兼容性 Case。`init` 草稿中的业务占位符是预期状态；必须以真实事实替换并运行 `case_doc.py validate --stage ready`，不得手工创建平行文档。每个 Case 写明具体输入、具体输出、验证方式和通过标准，链接适用页面/操作、初始化/清理脚本、自动化测试文件/方法与执行命令。测试设计阶段只登记 Case 与断言，不执行测试代码或修改 XML/生产模块；随后将自然语言验收项编译为 `acceptance.json` 的白名单断言，映射 Case、命令、JSON、SQL、指标或专业人工 Review。`wk -min` 时本 Agent 不分派，`test_design` 记录 `SKIPPED`，`work.md#测试用例` 为“无”。

### ImplementationPlanAgent

将设计拆成可独立验证的纵向任务，维护依赖、允许文件、命令、自然语言预期、对应 acceptance assertion/source refs、停止条件和 Reviewer；任务按 `depends_on` 串行执行。大需求或显式 `wk -tp` 时维护 `development_task_plan.json`，保证每项任务含具体目标、实现方式、验收标准和 `depends_on`；收到 Review finding 后修改任务、生成新 `TP-*` revision，并从首位 Reviewer 重新 Review。

## 5. 实现与验证 Agent

### TddAgent

标准模式下将 Case 转成可执行测试，取得开发前有效 RED；纯重构取得当前 GREEN 表征基线。可以写测试代码，但不写生产实现，也不负责开发后的 GREEN/REFACTOR。`wk -min` 时本 Agent 不分派、不编写测试代码，`tdd` 记录 `SKIPPED`。

### DevelopAgent

在批准任务和有效 RED/表征基线下实现最小变更，遵循项目模式、允许范围、关联影响、当前业务流程/步骤、跨模块实现映射和代码注释规范，并将真实 diff、命令结果和适用实现证据注册到 Evidence Registry。

### DebugAgent

建立稳定复现环，区分症状与根因，按单一假设定位责任阶段，组织回归测试并将修复交给 DevelopAgent；不得在不可复现时提交猜测修复。

### TestAgent

标准模式下执行当前 revision 的隔离、集成、接口/端到端业务测试和 Harness，核对真实数据、事务、最终状态和禁止副作用，将结果注册为 evidence，并执行适用 Acceptance Assertions。独立测试库测试前必须运行 `scripts/independent_test_environment.py status`；仅在 `TEST_DB_URL`、用户名变量和 `TEST_DB_PASSWORD` 全部就绪时执行，缺失则记录该子范围 `SKIPPED` 且不得回退业务库。`wk -min` 时本 Agent 不分派，`testing` 记录 `SKIPPED`，不得宣称测试通过。不得扩展为视觉 QA、发布验收或无基线探索测试。

### CompletionVerificationAgent

在适用的 Review 和验证后独立核对范围、工作模式、追踪、任务、diff、BUG、风险报告、Evidence Registry、Acceptance Assertions、测试或非测试验证以及开放问题，只输出 `DONE/PARTIAL/BLOCKED`。`wk -v` 未启用时必须确认页面设计为“无”；`wk -min` 时必须确认测试用例、测试均为“无”，三个测试阶段为 `SKIPPED`，并用编译/类型检查/静态分析/构建和独立 Code Review 证明缩减范围；不得写“测试通过”。

## 6. 文档辅助 Agent

### DocumentRenderAgent

响应 `wk -l`：从 Markdown 生成只读 HTML，验证目录、链接、锚点、代码块、Mermaid 和离线可读性；不得修改事实内容或阶段状态。

### IncrementalArchiveAgent

响应 `wk -d`，通过 `merge_docs.py archive` 原子合并结构化 changeset，并核对版本预览与项目归档链接分别重算、目标存在、模块流程索引已替换过期关系：

- 对结构化 `business_model/api/db/business_flow/dependency_impact`、`requirement_list.md`、模块 `{MODULE_CODE}_desc.md`、稳定 Design topic 和页面行为执行 `validate → preview → archive`；
- 自动核对版本模块目录的补充文档是否已进入 `{MODULE_CODE}_desc.md#文档引用`，并验证项目级跨版本链接；
- 核对 merge plan 的 changeSet、base/result revisions、structured artifacts、trace IDs、`workMode`、`workDigest` 和 result digest；
- 核对脚本生成的 `artifactMerges`、Design/base revisions、source/target/result digest、stable ID 和关系引用；不得在普通归档中人工注入合并项；
- 汇集当前 StageOutcome、Review、适用测试或非测试验证和完成验证证据；`wk -min` 时只允许 `testDesign/tdd/testing` 三个归档门禁以带原因的 `NOT_APPLICABLE` 结束，标准模式的 `codeReview/testing` 仅可依据当前 changeset `affectedTraceIds` 命中任务的已验证用户 waiver 结束为 `NOT_APPLICABLE`，不得借用其他模块或范围外任务的 waiver/StageOutcome；
- 维护同一 `generated/merge_plan.yaml` 的 `archiveGate`，只在显式冲突流程中记录 `conflictDecisions`，确认无开放阻塞问题，并确保 StageOutcome、Evidence Registry、Risk Detection、Acceptance Assertions、Reviewer 结论和 ACTIVE 归档决策均能从版本长任务记录真实回查；`wk -auto` 时无需 `userConfirmed=true`，但仍须完成全部适用门禁和授权记录；
- 核对全量事实文档、生成 Markdown/ER 和 manifest，并追加工作记录。

不得修改 changeset 迁就结果，不得用低层 `apply` 绕过门禁，不执行 Git merge、Release、部署或上线。

## 7. Review Agent 共通规则

Reviewer 先读取 `review-core.md` 与 `review-result-contract.md`，并使用 `scripts/review_contract.py show` 读取当前 `<phase>:<reviewerAgent>` Profile。各角色段落只说明职责，验收 criterion 不在此重复维护。

Reviewer 必须：

- 读取固定对象和 revision；
- 独立审查阶段不读取其它 Reviewer 结论；
- finding 包含严重级别、置信度、精确证据、影响和建议；
- 阻塞 finding 必须引用文件/行或稳定产物段落；
- 对 Profile 中全部 criterion 逐项记录 `PASSED/FAILED/BLOCKED/NOT_APPLICABLE`；
- 证据使用结构化 `{evidence_id, claim}`，不能使用“reviewed”或信心陈述；
- 修复后由原提出者或指定独立验证者在新 revision 复核。

严重级别：

- `P0`：数据损坏、安全、核心错误或完全阻断；
- `P1`：当前范围必须修复，否则需求不完整或风险不可接受；
- `P2`：可延期，但必须记录风险、责任人和验证时机。

## 8. 主 Reviewer

### RequirementReviewAgent

检查目标、业务语义、验收标准和范围是否漂移。

### BusinessModelReviewAgent

检查术语、聚合、不变量、状态和错误语义是否一致，是否出现平行模型。

### DesignReviewAgent

检查设计对需求/模型的覆盖、API/DB/事务/并发/错误和可测性是否完整。

### ArchitectureReviewAgent

检查模块边界、依赖方向、接口深度、故障隔离、复用和复杂度。它既是 design 的架构门禁，也可作为 code_review 的专项轴，不创建独立生命周期阶段。启用 `wk -ar` 时，它负责第一轮骨架 Review，并在最终 Code Review 核对具体实现未偏离已通过骨架；详细规则只读取 `architecture-review-development.md`。

### PlanReviewAgent

检查计划覆盖、任务粒度、依赖顺序、命令、预期结果和占位符。

### SpecComplianceReviewAgent

只检查“是否完整实现正确的需求”，逐项关联 spec/追踪 ID 与代码证据。

### EngineeringStandardsReviewAgent

只检查“实现方式是否符合工程要求”，包括项目规范、结构、错误处理、注释契约、测试质量和维护性。

### TDDReviewAgent

在 `tdd` 阶段检查 RED/表征基线、Case 映射、稳定 seam 和证据真实性；在 `development` 阶段检查最小 GREEN、REFACTOR 保护及 BUG 的 RED→GREEN。两次结论分别绑定对应 iteration 和 revision。

### TestEvidenceReviewAgent

核对测试运行环境、命令、revision、真实数据/事务/最终状态、跳过项和证据新鲜度。

### ImpactAnalysisReviewAgent

在删除、取消、迁移、状态联动或需求/功能依赖触发时，独立检查关系类型、方向、条件、处置策略、历史/在途数据、影响传播和 Case 映射。不得把数据库级联配置当成业务决策。

### CrossModuleIntegrationReviewAgent

在两个及以上业务模块、聚合或外部系统参与时，独立检查 `CMI/CMSTEP` 跨模块实现映射是否引用真实 `FLOW/STEP`，并验证模块职责、API/事件契约、本地事务、最终一致性、幂等、超时、乱序、部分成功、补偿和人工恢复闭环。

## 9. 条件与风险追加 Reviewer

Reviewer 有两种登记来源，不得混用：

1. `canonicalPhases[].conditionalReviewerAgents`：由追踪项中的结构化标志触发，主要用于需求分析、业务模型和设计阶段的关联影响/跨模块实现映射审查；
2. `riskReviewerCatalog`：由当前设计或 diff 的风险证据触发，写入 `additional_reviewers`，其 trigger、允许阶段和 Agent 映射以该目录为准。

风险追加职责包括：

- SecurityReviewAgent：认证、授权、敏感数据和注入风险；
- PerformanceReviewAgent：关键路径、查询、批处理和资源消耗；
- DataMigrationReviewAgent：Schema、迁移、兼容和数据恢复；
- ApiContractReviewAgent：请求/响应、兼容、错误和幂等；
- ConcurrencyReviewAgent：锁、竞态、异步和一致性；
- TestEvidenceReviewAgent：复杂 Harness、覆盖率、Mock 边界和测试易波动性；
- MaintainabilityReviewAgent：大型、高耦合或复杂 diff；
- ImpactAnalysisReviewAgent：删除、取消、迁移、关联状态或需求/功能依赖；
- CrossModuleIntegrationReviewAgent：两个及以上模块、聚合或外部系统参与同一业务行为的实现。

同一 Agent 可以在上游阶段作为 canonical conditional Reviewer，并在 `code_review` 因新 diff 风险再次作为 additional Reviewer；两次结论必须分别绑定各自 artifact revision。没有触发信号时，不为“流程完整”创建专项 Review。
