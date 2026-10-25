# 实施计划校验与 Review

> 本文按校验问题独立读取；任务编写与示例见 [主入口](implementation-plan.md)。章节 ID 保持稳定。
## 8. 禁止占位符

计划中禁止：

- `TBD`、`TODO`、`later`；
- “添加适当校验”；
- “处理边界情况”；
- “编写相关测试”；
- “参考 TASK-N”；
- 没有代码位置或接口的泛化步骤；
- 使用计划中从未定义的类型、方法或字段。

不能确定时应回到需求或设计，而不是把不确定性藏在计划中。

## 9. TDD 步骤要求

标准模式下每个行为任务至少包含：

1. 写失败测试；
2. 运行并观察有效 RED；
3. 写最小实现；
4. 运行并观察 GREEN；
5. 重构；
6. 再运行测试；
7. 运行必要的集成和 Harness；
8. Review。

纯重构任务：先建立 GREEN 表征测试，不人为制造 RED。

BUG 任务：标准模式下回归测试必须在修复前能复现原始症状；`wk -min` 时不创建回归测试代码，但仍必须记录可重复的症状证据、根因和非测试验证方式。

使用 `wk -min` 时本节不适用：计划不得创建测试代码、测试用例执行或 TestAgent 任务，也不得伪造 RED/GREEN 证据。每个开发任务仍须给出至少一种非测试验证方式，例如编译、类型检查、静态分析、构建或人工可核对的代码/配置差异，并交给两个 Code Review 主轴。

## 9.1 `wk -ar` 计划展开

启用 `wk -ar` 时，计划不得把“写骨架”和“实现具体方法”合并为一个不可审查步骤。每个适用开发增量必须顺序声明：架构代码骨架交付、Architecture/Spec 第一轮 Review、`advance-development-step`、具体实现、适用验证以及最终 Code Review。具体允许与禁止内容只引用 `architecture-review-development.md`，不在计划中复制第二套规则。

## 10. 顺序执行计划

全流程只允许 `SEQUENTIAL`。任务顺序只通过 `depends_on` 表达；同一时刻最多一个任务处于 `RUNNING`。A、B、C 多需求可以按阶段组织，但必须按 `project_process.md` 声明顺序逐个执行，不增加其它执行分组字段；组合验证作为普通依赖任务执行。


## 11. 跨模块计划

当 `dependency_impact.yaml#crossModuleImplementations` 存在 `CMI-*` 时：

- 每个模块任务引用负责的 `CMSTEP-*`、对应业务 `FLOW/STEP` 和契约；
- 补偿动作与正向动作分别有 owner、Case 和验证命令；
- 跨模块任务按调用/事件顺序建立 `depends_on`；
- 增加组合验证任务，覆盖成功、每个关键失败点和补偿失败；
- 计划必须说明哪些步骤只能最终一致，不能用全局事务作为占位。

删除、取消等关联处置任务还必须引用 `IMP-*`，避免只实现主对象状态而遗漏商品、库存、支付、物料或历史数据。

## 12. 计划自检

ImplementationPlanAgent 完成计划后必须自检：

### 12.1 覆盖

- 每个追踪项是否至少映射到一个任务；
- 每项适用影响策略、跨模块步骤和补偿是否映射到任务与 Case；
- 每个设计接口、迁移和测试是否有任务；
- 是否有任务不对应任何需求或设计，形成范围漂移。

### 12.2 占位符

搜索：`TBD|TODO|later|appropriate|相关测试|类似`，修复所有模糊内容。

### 12.3 接口一致性

后续任务引用的方法、类型、字段和事件是否与前序任务定义完全一致。

### 12.4 依赖图

- 无循环；
- 阻塞边真实；
- 所有依赖边都对应真实阻塞关系；
- 共享文件、数据和外部状态的写入顺序已明确。

### 12.5 合适尺寸

- 是否存在过大任务，包含多个可独立拒绝的行为；
- 是否存在过小任务，只有脚手架或文档而无独立价值；
- 每项任务是否以可测试结果结束。

## 13. Plan Review 门禁

标准模式由 PlanReviewAgent、ArchitectureReviewAgent、TestDesignAgent 和 DevelopAgent 基于同一 plan revision 独立 Review；`wk -min` 由 PlanReviewAgent、ArchitectureReviewAgent 和 DevelopAgent Review：

- PlanReviewAgent：完整性、粒度、依赖和可执行性；
- ArchitectureReviewAgent：边界、接口和集成风险；
- TestDesignAgent：标准模式下检查测试步骤和期望；`wk -min` 时该 Reviewer 不参与计划门禁；
- DevelopAgent：文件、命令、现有能力和工程可行性。

全部通过且无开放 P0/P1 后，计划才可进入执行。

### 13.1 `wk -tp` 事实文件

大需求或显式使用 `wk -tp/--task-plan` 时，ImplementationPlanAgent 必须维护：

- `development_task_plan.json`：唯一机器事实源；
- `evidence/reviews.jsonl`：绑定 `TP-*` revision 的追加 Review 历史；Review 结果只使用运行契约的 `PASSED/NEEDS_CHANGES/BLOCKED`。

每个任务必须至少包含：具体目标、实现方式、验收标准、`depends_on`、owner、Reviewer、验证命令、预期结果、交付物、停止条件、追踪项和 Git checkpoint 要求。不得把“实现模块”“补充测试”“处理边界”等泛化描述当作任务。

`implementation_strategy_contract_version` 和 `implementation_decision_ref` 等字段由 [计划 Schema](../../assets/development/development-task-plan.schema.json) 定义。计划只引用 Design 已批准结论，不重复保存 CREATE 的完整排除理由；当前格式不满足契约时拒绝推进。

### 13.2 Review 与返修循环

状态、结论和 finding 严重级别统一读取 [Runtime Contract](../../assets/runtime/runtime-contract.json)、[计划 Review Schema](../../assets/development/development-task-review.schema.json) 与 [Review Contract](../../assets/review/review-contract.json)。Review 串行执行，当前模式的 Reviewer 顺序由 `task_plan.py status` 返回；专项 Reviewer 只审查被分配的任务。

- 任一 Reviewer 返回 `NEEDS_CHANGES` 或 `BLOCKED` 时，立即停止后续 Reviewer；
- ImplementationPlanAgent 必须先按 finding 实际修改任务内容；内容 digest 未变化时 `revise` 拒绝生成空 revision。修改后生成新的 `TP-*` revision；
- 新 revision 不继承旧 Review 结论，必须从首位 Reviewer 重新 Review；
- 只有当前 revision 的全部 Reviewer 对其精确任务集合均 `PASSED`，且计划校验通过，ProjectManagerAgent 才能 `finalize`；
- 历史 revision、Review 和 finding 只追加保留，不覆盖或删除。

### 13.3 命令映射

参数与必填字段通过 `python3 scripts/task_plan.py --help` 和对应子命令 `--help` 查询，例如 `task_plan.py seal --help`、`task_plan.py revise --help`。先用 status 确认当前动作，再执行 validate、seal、submit-review、revise 或 finalize；本文不维护第二套参数表。

读取大需求开发计划时使用有界查询，不直接 `cat` 或全文加载 canonical JSON：

```bash
python3 scripts/task_plan.py query -g ProjectManagerAgent --task-dir {TASK_DIR}
python3 scripts/task_plan.py query -g DevelopAgent --task-dir {TASK_DIR} --task-id {TASK_ID} --field implementation --field acceptance_criteria
```

## 机器验证映射

- `expected_results` 保留自然语言说明，不能直接作为 `PASSED` 的机器依据；
- 每个 `acceptance_trace_ids` 所引用的 acceptance ID 必须在 `acceptance.json` 中有对应 assertion；
- `validation_evidence_ids`、attempt 的 `command_evidence_ids/evidence_ids` 均保存 evidence ID；
- `risk_triggers` 只能使用 `runtime-contract.json#riskReviewerCatalog` 的 Key，并且必须允许用于当前 phase；使用 `long_task.py list-risk-triggers --phase {PHASE}` 查询，不得填写 Reviewer 名或自由文本；
- development 完成后由 ProjectManagerAgent 对实际 changed files 运行 `risk_detect.py scan`，再运行 `long_task.py sync-risk-reviewers`；code-review 任务的 `risk_triggers` 和 `reviewer_agents` 必须覆盖所有未豁免高置信风险；
- 每条 `expected_results` 都必须被 assertion 的稳定 `source_refs` 逐项覆盖，格式为 `{TASK_ID}#expected_results/{从 0 开始的序号}`；
- 该映射至少包含一个 `blocking=true` 且最终为 `VERIFIED` 的 assertion，并通过 PlanReviewAgent 的 `RC-ACPT-001` 语义编译审查；非阻断断言或 PENDING 占位不能满足完成门禁；
- 详细契约见 `evidence-core.md`、`acceptance-core.md` 与 `risk-verification.md`。


## 14. 业务流程切片

涉及结构化业务流程的任务必须包含 `flow_refs` 和 `flow_step_refs`。纵向切片应覆盖可独立验证的业务结果，可包含多个连续简单步骤；不得机械地“一步骤一任务”，也不得让一个任务横跨无关流程组。准备、主交易、后续和补偿任务必须声明依赖和停止条件。

当流程 revision 或步骤语义变化时，关联计划任务标记 STALE；仅 API/DB 技术实现变化且流程语义不变时，不产生无意义的流程 revision。
