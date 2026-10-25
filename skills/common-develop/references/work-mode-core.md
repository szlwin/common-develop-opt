# 工作模式与辅助流程

本文件只定义工作模式和辅助命令对生命周期的覆盖边界；机器矩阵读取运行契约。

## `wk -mc`、`wk -v`、`wk -min`、`wk -ws`、`wk -wd`、`wk -wc`、`wk -auto`、`wk -gc`、`wk -gp` 与 `wk -review-only`

<!-- common-develop-invariant: LIGHTWEIGHT_TEST_ACTIONS_FORBIDDEN -->
`wk -ws` 开启轻量对话开发。它采用用户确认驱动的结对编程方式，允许需求、设计和开发在会话中交错收敛；会话是需求和设计的输入，但不能替代最终文档事实。轻量模式默认且强制跳过 `test_design`、TDD、`code_review` 和 `testing`，并抑制所有标准 Reviewer、测试代码/测试用例编写和测试执行；轻量模式保持开启期间不能执行测试、TDD 或 Review。若确需恢复这些动作，必须先执行 `wk -wc`，再通过 `reopen-phase` 从所需阶段重新打开并记录决定。

标准模式也支持用户在当前会话显式要求跳过 `code_review` 或 `testing`。ProjectManagerAgent 必须先执行 `wk confirm --skip-phase <phase> --reason <reason> --apply`，再把返回的 `requestFingerprint` 传给 `long_task.py skip-phase --phase <phase> --reason <same-reason> --confirmation-ref <fingerprint>`；确认与消费必须绑定同一 task、phase、iteration、原 revision 和 reason，伪造、篡改、过期或重放引用均 fail-closed。跳过命令只把对应 Artifact 标记为 `NOT_APPLICABLE`、StageOutcome 标记为 `SKIPPED` 并记录未验证风险，不生成 Review 或测试通过证据；豁免保留在 `work_policy.phase_overrides`，普通门禁据此放行。之后如需恢复，必须从该阶段或其上游执行 `reopen-phase`，它会清除所选阶段及下游的标准豁免并重新建立 iteration。标准模式不得通过任意自然语言、模式标记或伪造 `NOT_APPLICABLE` 绕过 Review/测试。

轻量模式下，设计或 Review 问题仍必须遵循详细会话回答协议：先拆分概念和边界，再展示有效/无效组合、失败时序、owner/generation 不变量和验证场景，最后等待用户确认。会话中的解释是候选结论；`wk -wd` 归档时只把用户已确认、能回查当前文档/代码/Evidence 的内容写入 canonical 需求、设计和决策事实。

<!-- common-develop-invariant: LIGHTWEIGHT_ARCHIVE_AFTER_CLOSE_WITH_HISTORY -->
`wk -wd` 是轻量模式的文档归档动作：按 `current_session → existing_requirement_documents → existing_design_documents → implementation_code` 汇总事实，检查需求完整、设计完整、实现可追踪、未决决定显式记录和未测试风险已记录，然后生成/更新标准归档计划。它不是发布、部署、Git merge 或测试通过结论。轻量模式仍开启时，直接执行 `wk -wd`；若已执行 `wk -wc`，只要当前版本任务运行时曾记录 `lightweight_waived=true`，仍可执行 `wk -wd`，归档计划会继续保留该版本历史的 `NOT_APPLICABLE` 阶段豁免。`wk -wc` 关闭轻量模式是非追溯动作：已记录为 `SKIPPED`/`NOT_APPLICABLE` 的 Review、TDD、TestDesign 和 Testing 阶段继续保持豁免，不自动追补 Review/Test 文件证据。没有轻量历史记录的普通标准模式不会获得该豁免。只有用户明确要求恢复流程且已关闭轻量模式时，才通过 `reopen-phase` 从指定阶段重新打开；轻量模式保持开启时，重开测试设计、Review 或测试阶段会被明确拒绝。

ProjectManagerAgent 在版本工作开始或模式变化时执行：

```bash
python3 scripts/work_mode.py apply -g ProjectManagerAgent --doc-root project_doc --version {VERSION} -mc
python3 scripts/work_mode.py apply -g ProjectManagerAgent --doc-root project_doc --version {VERSION} -v
python3 scripts/work_mode.py apply -g ProjectManagerAgent --doc-root project_doc --version {VERSION} -min
python3 scripts/work_mode.py apply -g ProjectManagerAgent --doc-root project_doc --version {VERSION} -v -min
python3 scripts/work_mode.py apply -g ProjectManagerAgent --doc-root project_doc --version {VERSION} -mc -v -min
python3 scripts/work_mode.py apply -g ProjectManagerAgent --doc-root project_doc --version {VERSION} -auto
python3 scripts/work_mode.py apply -g ProjectManagerAgent --doc-root project_doc --version {VERSION} -gc
python3 scripts/work_mode.py apply -g ProjectManagerAgent --doc-root project_doc --version {VERSION} -review-only
python3 scripts/work_mode.py apply -g ProjectManagerAgent --doc-root project_doc --version {VERSION} -mc -v -min -auto -gc
```

<!-- common-develop-invariant: LIGHTWEIGHT_CLOSE_NON_RETROACTIVE -->
<!-- common-develop-invariant: WORK_MODE_APPLY_SYNCS_EXISTING_TASKS_BY_DEFAULT -->
<!-- common-develop-invariant: GIT_PUSH_REQUIRES_CHECKPOINT -->
新建版本的 `work.md` 默认关闭可选模式；修改已有 `work.md` 时，未在本次命令中声明的模式保持现状，启用使用对应的短参数（如 `-mc`、`-auto`），关闭使用对应的 `--no-*` 参数（如 `--no-auto`、`--no-gc`），轻量模式使用 `-ws` 开启、`-wc/--no-ws` 关闭。`work_mode.py apply/init` 默认同步已有任务运行时；只有明确传入 `--no-sync-existing-tasks` 才跳过同步。`wk -auto` 只取消用户确认等待，不取消 Agent 审批、Review、证据、权限和阻塞门禁；`wk -gc` 与 `wk -auto` 相互独立，只增加顺序本地 Commit 门禁；`wk -gp` 必须依赖 `wk -gc`，只增加 checkpoint 的受限远端同步与验证门禁。模式变化属于计划范围变化：必须更新 `work.md`，重新评估实施计划、下游 revision、Reviewer 和归档门禁；不能只修改阶段状态。


## `wk -l`

`wk -l` 只生成或刷新 HTML 阅读副本，不推进任何研发阶段。详细规则见 `onepage-html.md`。


## `wk -d`

`wk -d` 的结构化自动合并流程：

```text
validate changeset/base revision、requirement_list 和 module_desc
→ 自动发现模块目录补充文档并同步到版本级 module_desc 文档引用
→ preview merge_plan/result digest、requirement_list/module_desc 与 business-flow 预览（按预览目录生成链接）
→ 自动计算并验证稳定设计主题、页面行为和 dependency impact 增量
→ 汇集当前 StageOutcome、Review、测试和完成证据
→ 在同一 merge_plan.yaml.archiveGate 中填写门禁证据和授权结论
→ merge_docs.py archive
→ 原子合并项目事实、设计快照、业务流程、关系图/追踪矩阵、人类导航和 archive_manifest
→ 通过 finish-attempt 追加 task_attempts 明细和 work_record v4 紧凑事件
```

- `merge_plan` 固定脚本自动处理的 `business_model/api/db/business_flow/dependency_impact` changeset，以及 `requirement_list.md`、`{MODULE_CODE}_desc.md`、稳定 Design topic、页面行为的 source/target/result digest；业务流程预览和项目归档 Markdown 必须依据各自输出目录重新计算链接；版本需求列表中的 requirement 链接必须指向真实文件，归档时自动从 `doc/{TARGET_ID}/requirement.md` 改写为项目级 `../version/{VERSION}/doc/{TARGET_ID}/requirement.md`；
- 版本模块目录中非标准补充文档会自动进入 `{MODULE_CODE}_desc.md#文档引用`，项目级引用改写到对应版本路径；
- `merge_plan.artifactMerges` 是确定性只读结果，普通归档不得人工增删；冲突只进入 `conflictDecisions` 记录，修订源增量或 rebase 后必须重新 preview；
- base revision、digest、changeSet、结构化产物、文档合并证据或阶段门禁任一不一致时停止；归档脚本会回查真实 StageOutcome、Evidence Registry、Risk Detection、Acceptance Assertions、Reviewer 结论和 ACTIVE 决策，不接受仅填写字符串的占位门禁；
- 此处 archive 仅表示研发事实基线合并，不执行 Git/Release/部署。


## 机器写入边界

`work.md` 继续是工作模式唯一事实源。机器化 mutation policy 使用同一文件中的 `common-develop-mutation-policy` 标记，不新增平行 profile 文件。

- 标准模式按 lifecycle phase 计算 `taskFacts / projectDocs / tests / config / productionCode` 是否允许写入；
- `wk -review-only` 固定允许 `taskFacts` 与 `projectDocs`，拒绝 `tests`、`config`、`productionCode`；
- `auto_remediation.py preflight --task-dir ...` 会把该策略作为 production mutation 的硬门禁；
- 自动化只能自动生成、绑定或校验事实，不能通过 mutation policy 绕过权限、Review、Evidence 或 P0/P1 Gate。
> 面向非专家的模式选择与解释遵循 [request-intake.md#4-面向非专家的沟通协议](request-intake.md#4-面向非专家的沟通协议)；模式推荐只提供 advisory decision guide，不替用户改写当前模式。
