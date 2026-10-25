---
name: common-develop
description: 面向软件研发事实与多 Agent 协作的通用 Skill。覆盖需求、设计、计划、TDD、开发、独立 Review、测试、完成验证、长任务恢复和研发事实归档；支持 wk -b、wk -f、wk -min、wk -ws、wk -wd、wk -wc、wk -auto、wk -ar、wk -review-only、wk -tp、wk -gc、wk -gp、wk -l、wk -d。排除视觉稿、商业计划、Git merge、Release/部署和无基线泛 QA；Git push 仅在显式 `wk -gp` 且 `wk -gc` 已启用时执行。
---
当前包版本、发布阶段和基线标签只读取 `assets/release-contract.json`。
# common-develop
本入口只定义启动顺序、事实来源和不可绕过门禁；详细规则按需读取机器契约与 Reference。

维护本 Skill 前先运行 `python3 scripts/baseline_guard.py -g ProjectManagerAgent status --json`；发现偏差时先保存副本并核对 diff，禁止用 reset/clean 覆盖现状。

## 1. AI 必须先执行：按任务状态选择一个入口
确认项目、版本、目标和 `-g/--agent`；默认 Agent 是 ProjectManagerAgent。以下 `wk` 表示用 Python 调用当前安装包中的 `scripts/wk.py`，不得假定项目根目录存在 `scripts/`；执行返回命令时使用结果中的完整路径。

| 场景 | 默认入口与后续动作 |
|---|---|
| 新建任务 | ProjectManagerAgent 使用 `wk start -b/-f ...` 预览；`NEEDS_CONFIRMATION` 时补齐必要决策。分类、范围与模式明确后以同一参数加 `--apply` 创建，再使用返回的 taskDir 调用 `wk begin`。预览不创建任务。 |
| 恢复任务 | 使用 `wk begin --task-dir ...` 一次取得新鲜阶段状态、条件路由、最小上下文和动作计划；Owner 按需用 `begin --apply` 启动或恢复 attempt。仅询问当前位置时才用 `wk status --summary`。 |
| 普通执行 | 读取 actionPlan 的 requiredAgent 与 executionContext，由对应 Owner/独立 Reviewer 执行原子命令；有命令结果时调用 record-result。阶段推进由 ProjectManagerAgent 使用返回的 progressCommand/planDigest；完成时调用 finish。 |
| 维护诊断 | 参数缺失按需 `wk help`，阻断按 repairPlan；需要额外上下文时才使用 `python3 scripts/ai_context.py resolve`（带当前请求），并按 diagnostics 定向补读。Skill 源维护另见第 12 节。 |

`begin` 已提供上下文时，不再固定串行运行 ai_context、status、next、context。先消费 context.contextRouting 的资源、命中条件、omittedResources 和预算；资料不足时按 requiredInputs 和返回引用补读。输入 revision、范围、停止条件和模式必须明确；只通过声明脚本推进、重开和归档。
普通项目工作禁止默认阅读 Skill 的 `scripts/**/*.py` 或 `scripts/_core/**` 实现；参数与行为通过 CLI help/query 和返回结果确认。仅 Skill 维护、CLI 故障诊断、实现 Review 或用户明确要求时可按 `ai/source-map.json#skillImplementationReadPolicy` 记录阅读例外。任务事实通过查询按稳定 ID、阶段、revision 与字段定向读取，`development_task_plan.json` 使用 `task_plan.py status/query` 的有界结果；不得默认全文加载 Evidence、Review、Acceptance 或历史。`cache/current_manifest.json` 仅按条件读取，`cache/current_impact.json` 经 `wk next` 间接查询。具体映射见 `references/task-context-optimization.md#5-读取规则`。
Owner 产生候选 revision，Reviewer 在独立上下文验证同一 revision；命令、文件、测试和审查注册 Evidence，最终只报告 DONE、PARTIAL 或 BLOCKED。

## 2. 唯一事实与冲突顺序
冲突顺序：用户当前明确指令与项目真实事实 → 已确认且未失效的项目 revision → Runtime Contract → Review Contract → Context Routing → 命中的领域 Reference；无法消解时登记 issue，不默选低优先级规则。

机器事实归属：生命周期/权限 → `assets/runtime-contract.json`；Review → `assets/review-contract.json`；路由 → `context-routing.json`；语义和 ID → `assets/semantic-contract.json`；其他权威路径 → `ai/source-map.json`；版本和存储模型 → `assets/release-contract.json` 与 `assets/storage-contract.json`。大型 Contract 只能通过对应 query CLI 读取 materialized view。

### 2.1 单 Skill 与内部能力包
`common-develop` 是唯一安装、触发、授权和发布单元；内部能力包仅有 `document-facts`、`task-runtime`、`review-verification`；接口、依赖和写入权以 `assets/capability-pack-contract.json` 为准。只有 `task-runtime` 可以追加生命周期事件或推进、重开、完成阶段，其他包不得形成第二套状态机。

## 3. Domain / Bounded Context 与工程知识图
Domain/Bounded Context 治理是可选能力；存在 `docs/_domain/domain_context_map.yaml` 时启用边界和术语校验。Business Model 保有 TERM/AGG/INV/STATE 事实；`semantic_index.py`、`engineering_graph.py` 和 `wk impact` 只生成 DERIVED projection，未知或无效 Graph 必须保守阻断或回退。

## 3.1 Canonical 引用与公开结构边界
- APG2 引用必须经过 `scripts/reference_resolution.py` 的 canonical 解析：Markdown 只接受真实 heading/显式 anchor，JSON/YAML 只接受 JSON Pointer 或结构化记录中的稳定 ID，JSONL 只接受记录稳定 ID；普通字符串包含不能伪造引用。
- 稳定 ID 由 `id`、`stableId` 或 `stable_id` 结构字段建立索引；重复 ID、格式错误 ID 和无法唯一定位的 ID 必须 fail-closed。相对路径统一以 declaration 所在文档目录为基准，并限制在项目根内。
- APG4 的治理推荐内部快照与 `governanceProfile`/`decisionGuide` 公开投影分离；公开输出只允许 Schema 明确声明的字段，内部诊断字段不得通过 `dict(source)` 泄漏。

## 4. 生命周期与状态
阶段顺序、Owner、Reviewer、前置、跳过和状态域只从 Runtime Contract 读取；架构审查是 development 内的 Review iteration，不是平行阶段。生命周期、任务/iteration/attempt、Evidence、Review、StageOutcome、归档和恢复规则见 `references/runtime-contract.md`、`references/lifecycle-core.md`、`references/long-task-core.md` 与 `references/evidence-core.md`。

## 5. 最小上下文路由
`wk begin` 和按需的 `ai_context.py resolve` 使用 materialized `context-routing.json#routingPolicy`；大文档只有命中提示条件才定向读取。请求入口会给出带理由的治理深度建议，`wk start` 的 `governanceProfile`/`decisionGuide` 是 advisory，不会静默改变显式模式；非专家解释见 `references/request-intake.md#4-面向非专家的沟通协议`。

## 6. 全局硬门禁
- 决策、范围、验收语义和不可逆取舍不可猜测，必须有授权记录。
- Design、测试设计、Review、Evidence、Assertion、StageOutcome 和归档都必须绑定当前 revision；详细字段和阶段门禁见对应 Reference。
- 实现者不自证；开放 P0/P1、失效证据、未确认需求和不兼容工作模式阻断推进。
- 测试或 Review 跳过不得表述为通过；标准模式只有用户在当前会话明确要求后，先由 `wk confirm --skip-phase ... --apply` 形成绑定 task/phase/iteration/revision/reason 的确认事件，再由 `long_task.py skip-phase --confirmation-ref ...` 原子消费，才可标记 `SKIPPED/NOT_APPLICABLE`。必须保留未验证风险、可审计记录和后续显式 `reopen-phase` 恢复路径。`wk -min`/`wk -ws` 的契约豁免仍按各自模式执行。真实项目命令、能力和 delivery gate 由项目声明，Skill 不绑定语言、构建、测试、数据库或依赖工具。
- `case_doc.py init` 创建的是当前 Requirement 目录下的 canonical 测试设计草稿；身份字段会自动填充，其余业务占位符在草稿阶段是预期状态。必须在同一文件补全真实 Case 后运行 `validate --stage ready`，不得因草稿占位符手工创建平行 `test_case.md`；`test_design` 只登记 Case/断言，不执行测试代码或修改 XML/生产模块。
- `case_doc.py init` 还会刷新派生关系图和文档门户；若报 `inline collections must use JSON syntax` 或其他 YAML/关系错误，失败原因来自项目已有结构化文档，不是测试模板占位符。先按错误中的文件和行修复项目 YAML，或运行关系图校验后再重试；`case_doc.py validate` 只验证测试文档本身，不能替代关系图校验。
- `work.md` 是模式唯一事实源；`-review-only` 的写入限制由机器 Mutation Policy 执行。

## 7. 条件触发器
触发条件只由 `context-routing.json#promptConditions` 和风险契约维护；AI 不在入口、角色文档或会话中复制关键词表、模式矩阵或 Reviewer 矩阵。命中后按路由读取相关 Reference。

## 8. Review 与阶段结果
Review 必须由独立 Reviewer 按 Review Contract 执行，finding、StageOutcome、Assertion 和 Evidence 绑定 revision；Agent 输出遵循 `assets/stage-result.schema.json`。当宿主支持子智能体/独立任务上下文时，每个 Reviewer 必须在独立执行上下文中运行，不能只在 Owner 会话中把 `-g` 改成 Reviewer 名称；宿主不支持时只能交给明确的独立人工 Reviewer，并记录限制。分派前后主会话必须显示 Reviewer/phase/revision/scope/停止条件回执；等待和中断只用宿主原生子智能体能力，禁止用 `sleep`、`echo true`、空循环或 no-op shell 保活/轮询。`wk next` 的 `nextCommand.executionContext=ISOLATED_REVIEW_SESSION_REQUIRED` 是分派门禁提示。完整握手、等待、并行只读和串行提交规则见 `references/review-core.md#21-子智能体分派握手与等待`；Review 类型、阶段结果和完成声明见同 Reference、`assets/stage-result.schema.json` 与 `references/test-completion.md`。
Owner 发布前必须由 ProjectManagerAgent 通过 `acceptance.py ensure-review` 幂等登记当前 revision 的 required Reviewer assertion；缺失时发布和 Review 分派均 fail-closed。恢复与旧文件兼容规则见 `references/manual-review-guide.md`。

## 9. 长任务
事件账本和 reducer 是状态权威；跨会话默认 `wk begin`，状态简询才用 `wk status --summary`，其他查询用于返回计划要求的定向补读。不得猜测或全文扫描历史。当前 revision、attempt、重试、amend/reopen 范围、历史 Evidence 及 Trace 修复规则按需见 `references/long-task-core.md`、`references/task-context-optimization.md` 和 `references/wk-cli.md#按需修订与恢复诊断`。
重开沿用当前 version，只标记下游 STALE/REWORK 并保留 canonical 文档与历史；范围扩展仍需要真实用户授权，不能以修复或缓存跳过硬门禁。

## 10. wk 命令发现
<!-- common-develop-invariant: WORK_MODE_APPLY_SYNCS_EXISTING_TASKS_BY_DEFAULT --> <!-- common-develop-invariant: LIGHTWEIGHT_TEST_ACTIONS_FORBIDDEN --> <!-- common-develop-invariant: LIGHTWEIGHT_CLOSE_NON_RETROACTIVE --> <!-- common-develop-invariant: LIGHTWEIGHT_ARCHIVE_AFTER_CLOSE_WITH_HISTORY --> <!-- common-develop-invariant: GIT_PUSH_REQUIRES_CHECKPOINT -->
- 请求入口：`-b/--bug`、`-f/--feature`；模式包括 `-mc`、`-v`、`-min`、`-ws`、`-wd`、`-wc`、`-auto`、`-ar`、`-gc`、`-gp`、`-review-only`。模式矩阵、增量 Patch、`--no-*` 关闭方式及默认同步规则见 `references/work-mode-core.md` 和 Runtime Contract。
- 辅助动作：`-tp/--task-plan`、`-l`、`-d`；统一入口为 `scripts/wk.py start/begin/progress/finish/record-result/status/next/step/reconcile/close/impact/env/context/help`。需要发现命令时运行 `python3 scripts/wk.py help -g {CURRENT_AGENT} --json`；查询单项使用 `--flag=-ar` 或 `--category mode`。
- 参数、权限和组合门禁只由 `assets/runtime-contract.json#commandInterface` 维护；需要查询脚本参数时运行 `python3 scripts/wk.py help -g {CURRENT_AGENT} --script {SCRIPT_ID} --command {COMMAND} --json`。
- `nextCommand` 是结构化下一步提示，包含命令、理由、责任 Agent、确认和只读标志；`recommendedCommand` 仅为兼容字段。`wk step --preview` 只读生成当前阶段原子动作计划和稳定 `planDigest`；`wk step --apply` 只能携带同一份摘要，按受限白名单调用已有原子命令，不直接修改状态。Evidence、增量影响和 Git checkpoint 的详细规则见 `references/wk-cli.md`。

## 10.1 维护入口与并发边界
仓库级 `README.md`/`README.zh-CN.md` 提供用户说明；安装后的 Skill 以本文件为入口。用户入口、维护/诊断入口、稳定 Alias、资源 `priorityClass` 和只读并行/写入串行策略见 `references/maintenance-entrypoints.md`。不要通过维护脚本绕过 `wk` 生命周期或 reducer。

## 11. Agent 输出契约
默认执行协议以第 1 节为准。`begin --apply` 初始化或恢复 attempt；ProjectManagerAgent 的 `progress --apply` 直接消费当前返回的 `planDigest`，不需要额外查询 step。缺失执行参数读取 `requiredInputs`，不得执行占位符命令。仅在 `requiresUserDecision=true` 或确有用户专属的范围、不可逆取舍、风险接受决策时询问用户；普通结果用自然语言，Evidence/Acceptance/Traceability 内部字段只在用户要求审计时展示，`--json` 供 AI 和维护脚本使用。复合动作、风险建议和 revision 缓存不能覆盖显式工作模式、独立 Review 上下文或硬门禁。详见 `references/wk-cli.md#ai-编排入口`。分派至少包含目标、阶段、输入 revisions、允许范围、停止条件、验证方式和下一 Reviewer；返回 `assets/stage-result.schema.json` 可验证的结构化结果。

## 12. 交付前自检
普通业务任务只执行项目和当前任务声明的验证，使用 `wk finish` 汇总既有 Evidence、Acceptance、Review 与 Traceability；不得因此运行 Skill 自身测试、清理安装包缓存或重写包清单。
仅在修改本 Skill 源时，运行 `python3 scripts/quality_gate.py -g ProjectManagerAgent --profile full --clean-transient --write-manifest`（脚本路径相对安装源），确保契约、索引、路由、Schema、权限、规则扫描、全部测试和 FILE_MANIFEST.sha256 均通过。维护诊断的只读检查不隐含清理或重写授权。具体命令见 `references/maintenance-entrypoints.md`。
