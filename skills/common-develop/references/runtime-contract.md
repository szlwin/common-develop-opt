# AI 运行契约

本文件是 AI 的最小可读词表；机器事实源为 `assets/runtime-contract.json`（运行契约 schema v2），长任务主体记录使用 `assets/long-task/record-contract.json` 声明的 schema v2；版本级 `work_record.md` 使用 `runtime-contract.json#workRecordSchemaVersion=4` 的人类可读 Markdown 契约；隐藏元数据提供机器读取与正文 digest 校验。其它 Reference 不得重新定义阶段、状态、Agent、Reviewer 或脚本权限矩阵。每个 Reviewer 的验收 Profile、criterion、证据策略和结论推导只由 `assets/review-contract.json` 定义；AI 使用 `scripts/review_contract.py` 按阶段或单个 Profile 查询，不应整文件加载。

## 1. 当前 Agent 选择

- 使用 `-g <Agent>` 或 `--agent <Agent>` 指定当前 Agent；允许值来自 `runtime-contract.json#agents`。
- 未指定时只使用默认 `ProjectManagerAgent`。
- 直接执行专业 Agent、Reviewer、`wk -l` 或 `wk -d` 时必须指定当前 Agent。
- 当前 Agent 必须是阶段 owner、已触发的条件 Reviewer、已登记额外 Reviewer或辅助动作 owner；`-g` 不能授权越权。
- `scripts/agent_context.py` 统一解析身份；各脚本命令从 `runtime-contract.json#scriptPermissions` 获取 STATIC 或 TASK_CONTEXT 权限，不得在脚本中另写角色白名单。
- 身份落盘字段由 `commandInterface.currentAgent.recordFields` 唯一规定：任务状态使用 `current_agent`，阶段结果使用 `produced_by_agent`，handoff 使用 `from_agent`；均必须等于对应动作的解析身份，不在同一对象重复保存同义字段。
- 阶段 owner 通过 `long_task.py publish-artifact` 创建带自身 `produced_by_agent` 的候选 StageOutcome；ProjectManagerAgent 只能通过 `finalize-phase` 核验并更新该记录，不得替代 owner 创建或冒充阶段结果。

### 1.1 `wk` 工作模式

工作模式唯一由 `runtime-contract.json#commandInterface.workModes` 定义，状态只以当前版本 `work.md` 的机器标记为准：

| 模式 | 工作表变化 | 阶段变化 | Reviewer/上下文变化 |
|---|---|---|---|
| 默认 | 前端伪代码、后端伪代码为“无” | 无 | 无 |
| `-mc` | 前后端伪代码进入范围 | 无 | 按设计任务加载实现契约 |
| `-v` | 页面设计进入工作范围，列值为 `AIW` | 不新增生命周期阶段；由 DesignAgent 在 design revision 产出页面业务行为设计 | 路由增加 `page-design` Reference；视觉设计仍排除 |
| `-min` | 测试用例、测试为“无” | canonical `test_design/testing` 写 `SKIPPED`；Development 内嵌 TDD gate 设为 suppressed，不生成独立 `tdd` StageOutcome | 取消运行契约声明的 TestDesign/Verification 必需分派；排除 `tdd/harness` 资源 |
| `-auto` | 不改变工作表任务列 | 阶段门禁通过后自动推进 | 不等待用户确认；仍保留权限、Review、证据和阻塞门禁 |
| `-gc` | 不改变工作表任务列 | 阶段门禁通过后创建本地 Git 检查点 | 路由增加 `git-checkpoint` Reference；提交失败不推进 |
| `-gp` | 不改变工作表任务列 | 依赖 `-gc`；checkpoint 后优先 native push，github.com transport 不可用时 Connector handoff；remote receipt 未验证不推进 | native 要求 exact commit SHA；Connector 要求 exact tree/message/equivalent parent + 双 SHA；均仅当前分支、非 force、不建 PR |
| 组合模式 | 五种模式效果叠加 | 先解析产物范围，再应用测试跳过、自动推进和 Git 检查点 | 所有非绕过门禁仍生效 |

AI 固定按以下顺序解析：

1. 读取并校验 `version/{VERSION}/work.md` 的唯一模式标记；
2. 若 `model_code=true`，将前后端伪代码实现契约纳入 design 输出；若 `page_design=true`，将页面业务行为设计纳入 design 输出并加载 `page-design` Reference；
3. 从 `workModes.minimal.skippedPhases` 得到跳过阶段；
4. 从 `suppressedReviewerAssignments` 删除前置阶段中不适用的 TestDesignAgent 分派；
5. 从 `context-routing.json#modeOverrides.minimal` 删除 TDD/Harness 资源并拒绝跳过阶段 task type；
6. 最小模式只接受 `requiredNonTestEvidence` 声明的非测试证据，阶段结果用 `SKIPPED`，归档门禁用有原因的 `NOT_APPLICABLE`；
7. 若 `auto=true`，从 `workModes.auto.confirmationPolicy` 读取自动推进条件和停止条件；满足所有门禁后由 ProjectManagerAgent 记录自动决策，不等待用户确认；
8. 若 `git_checkpoint=true`，加载 `git-checkpoint` Reference，并在阶段推进前执行本地检查点门禁；若同时 `git_push=true`，必须在写入下一阶段 transition 前完成 native push/verify 或 GitHub Connector request→receipt 的 remote checkpoint 验证；
9. 所有目标和任务按 `runtime-contract.json#/executionPolicy` 声明顺序执行；
10. 模式由 ProjectManagerAgent 通过 `scripts/work_mode.py` 写入和校验；AI 可调用 `status --json` 获取已解析策略，其他 Agent 不得口头改写。

## 1.2 独立测试库就绪策略

机器事实来自 `runtime-contract.json#testExecution.independentDatabase`。执行独立数据库集成或事务测试前，AI 必须调用：

```bash
python3 scripts/independent_test_environment.py status -g TestAgent --json
```

仅当以下三组变量均解析到非空值时状态为 `READY`：

- URL：`TEST_DB_URL`；
- 用户名：`TEST_DB_USER`；
- 密码：`TEST_DB_PASSWORD`。

缺任一组时状态为 `SKIPPED`，不执行独立测试库测试，不输出任何凭据值，也不得回退到 local/dev/生产业务库。该跳过只影响独立测试库子范围；除 `wk -min` 外，其它适用隔离测试、静态检查、构建和 Review 仍须执行。

## 2. AI 资源目录

AI 统一调用只读入口：

```bash
python3 scripts/ai_context.py resolve \
  -g {CURRENT_AGENT} \
  --task-type {TASK_TYPE} \
  --phase {PHASE} \
  --prompt "{CURRENT_REQUEST}" \
  --doc-root project_doc \
  --version {VERSION} \
  --target {TARGET_ID} \
  --json
```

该入口从运行契约、`context-routing.json`、当前 `work.md` 和 `ai/source-map.json` 组合出当前 Agent、阶段职责、允许命令、项目事实路径及最小 `resources/path/tokenBudget/readStrategy`。底层路由仍只由 `context_resolver.py` 执行，`ai/source-map.json` 只保存指针。`readStrategy=relevant_sections` 的资源同时返回 `sectionIndexCommand/readCommand/queryReadCommand`，由 `reference_reader.py` 忽略代码围栏后按稳定二级/三级标题选择性读取；该脚本不维护第二套路由。未知 task type、最小模式已跳过的 task type、缺失模式状态、路由冲突或无效章节 ID 直接阻断；其它文档不得维护第二套路由、路径表、权限表或 token 表。

## 3. 唯一生命周期

```text
requirement_confirmation
→ requirement_analysis
→ business_model
→ design
→ test_design
→ implementation_plan
→ tdd
→ development
→ code_review
→ testing
→ completion_verification
```


- 架构审查：`design` 或 `code_review` 的 Reviewer 证据；
- 测试用例：`test_design` 的主要产物；
- 实现契约：`design` 的下游产物；
<!-- common-develop-invariant: GIT_PUSH_REQUIRES_CHECKPOINT -->
- `wk -mc`、`wk -v`、`wk -min`、`wk -auto`、`wk -ar`、`wk -gc`、`wk -gp`：版本工作模式参数，不进入主生命周期；`-min` 改变适用阶段集合，`-auto` 改变确认与推进策略，`-ar` 在 `development` 内建立受 Review 隔离的骨架与具体实现 iteration，`-gc` 在阶段门禁通过后创建顺序本地 Git 检查点；`-gp` 依赖 `-gc`，在 checkpoint 后优先执行当前分支 native 非 force push；github.com 的 native 网络/认证 transport 不可用时切 GitHub Connector Git Data handoff，并在 receipt 验证 exact tree/message/equivalent parent 与 remote/可发现 PR head 后才完成。`wk -ar` 的可读规则只见 `architecture-review-development.md`。
- `wk -tp/--task-plan`：大需求任务规划辅助动作，属于 `implementation_plan` 事实闭环，不创建新生命周期阶段；任务生成后按运行契约串行 Review，finding 触发新 `TP-*` revision 和完整重审。
- `wk -l`、`wk -d`：辅助动作，不进入主生命周期。

### 3.1 可执行阶段状态机

阶段顺序不只是阅读说明。`canonicalPhases[].requiredPredecessors` 和 `minimalModePredecessors` 是机器门禁：

- `start-attempt` 只允许当前可执行阶段的任务启动；
- 所有前置阶段必须为当前 iteration 的 `PASSED`，或在 `wk -min` 下合法 `NOT_APPLICABLE/SKIPPED`；
- `depends_on` 必须已经 `PASSED/NOT_APPLICABLE`；
- 开放 P0/P1、过期 input revision、工作模式 digest 不一致都会阻断；
- 只能用 `advance-phase` 推进，用 `reopen-phase` 重开，不得直接改写 `current_phase` 绕过门禁；
- `wk -auto` 只允许 ProjectManagerAgent 在相同门禁通过后自动调用推进，不改变判断条件；
- 全流程执行策略固定为 `SEQUENTIAL`；`wk -gc` 下 `advance-phase` 必须成功创建当前阶段本地 Commit。若未启用 `-gp`，沿用本地 checkpoint 事务推进；若启用 `-gp`，必须先 commit → native push/verify 或 Connector request → receipt verify → remote checkpoint verify，任一步 pending/失败都不得写入下一阶段 transition。

### 3.2 逻辑任务、iteration 与 attempt

重复工作使用三层模型：

```text
logical task（同一功能/工作目标）
  └─ iteration（需求或 revision 变化产生的新一轮）
       └─ attempt（同一轮中的失败重试）
```

`max_attempts` 只限制单个 iteration 内的失败重试。已通过任务需要重新设计、开发或测试时，必须通过 `reopen-phase` 创建新 iteration；旧 StageOutcome、Review 和 attempt 保持不可变历史，并以 `superseded_by` 或 `review_history` 关联。

## 4. 状态域

状态按对象分别使用，完整枚举从 `runtime-contract.json#statuses` 读取：

- task：任务整体和等待/阻塞状态；
- artifact：阶段产物当前状态；
- planTask：实施任务状态；
- issue：Review 问题状态；
- review/reviewConclusion：协作 Review 状态与独立结论；
- stageOutcome：阶段执行结果；
- trace：追踪覆盖状态；
- auxiliaryAction：`wk -tp`、`wk -l`、`wk -d` 等辅助动作；
- final：向用户报告的 `DONE/PARTIAL/BLOCKED`。

不得跨对象混用状态。`PASSED` 不是 task 最终状态，`COMPLETED` 不是 Review 结论，`SKIPPED` 不等于通过。

## 5. Reviewer 来源

- `canonicalPhases[].requiredReviewerAgents`：标准模式的必需 Reviewer；
- `commandInterface.workModes.minimal.suppressedReviewerAssignments`：最小模式对标准 Reviewer 集合的唯一删减；
- `canonicalPhases[].conditionalReviewerAgents`：由追踪标志触发；
- `riskReviewerCatalog`：由 diff/设计风险触发的额外 Reviewer。

AI 必须先应用工作模式删减，再计算条件 Reviewer 和风险 Reviewer；不得因为 Profile 仍存在就把被最小模式抑制的 Reviewer 重新加入门禁。

额外 Reviewer 必须由当前 `risk.json` 或 Agent 补充语义风险产生，记录 trigger、reason 和 `evidence_ids`。只要被登记，就必须使用当前 `<phase>:<reviewerAgent>` Profile，对同一 artifact revision 完成全部 criterion，才能让阶段通过。高置信风险豁免需要专项 Reviewer 与 ProjectManagerAgent 共同批准。

## 6. Agent 最小输入/输出

分派至少包含：目标、阶段、输入 revisions、允许读取/修改范围、追踪项、关联与跨模块引用、停止条件、验证方式和下一 Reviewer。

返回至少包含：读取证据、输出 revision、修改范围、验证命令及结果、开放问题、风险和下一动作。

责任 Agent 不得自行证明自己的产物最终通过；Reviewer 必须基于同一 revision 独立审查。

## 7. 项目管理 Agent

- 长任务顶层只使用 `project_manager_agent`，当前值为 `ProjectManagerAgent`。
- 该字段表示整个长任务的项目管理责任主体，不把职责限制为协调。
- ProjectManagerAgent 的可扩展职责由 `runtime-contract.json#projectManagement.capabilities` 统一声明。
- `current_agent` 表示当前实际执行命令或阶段工作的 Agent；实施任务负责人使用 `owner_agent`；阶段结果生产者使用 `produced_by_agent`。
- 身份字段必须使用明确名称，不使用含义不清的通用字段。


## 8. 机器可信链

以下文件是长任务运行契约的组成部分，缺失时 `validate`、完成验证和归档均失败：

- `evidence/registry.jsonl`：唯一 Artifact、SHA-256、revision、phase、scope、使用 Agent 与状态；
- `evidence/reviews.jsonl`：schema v4 单一紧凑 Review 事件流；当前任务只在 `task_events.jsonl` state patch 中保存必要的 `REV-*` 引用，`cache/current_manifest.json` 由 reducer 派生；
- `acceptance.json`：自然语言验收标准的白名单机器断言；
- `risk.json`：当前 development revision 的确定性风险、Agent 补充风险和 waiver；
- `git_checkpoints.yaml`：启用 `wk -gc` 后在首次实际 checkpoint 时按需创建，保存阶段 revision 与 Git Commit Trailer 的追加式映射；Commit SHA 由 `WK-Checkpoint` Trailer 唯一解析。

Review criterion、attempt、StageOutcome、trace verification 和 archive gate 只保存 evidence ID。`expected_results` 不能直接作为通过条件；每个适用 acceptance ID 与当前任务预期都必须被结构化 assertion 覆盖。进入 `code_review` 前必须完成当前 revision 的风险扫描。详细规则见 `evidence-core.md`、`acceptance-core.md` 与 `risk-verification.md`。
