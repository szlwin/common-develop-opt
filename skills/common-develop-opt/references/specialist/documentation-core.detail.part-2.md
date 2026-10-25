# documentation-core（续）
> 本文是 [documentation-core.detail.md](./documentation-core.detail.md) 的按需续篇。
### 稳定 ID 与 revision

稳定 ID 的完整机器契约位于 `assets/runtime/semantic-contract.json#idNamespaces`。项目可以选择自己的需求编号，例如 `PRD0013` 或 `REQ-ORDER-CANCEL`，但同一项目内必须保持稳定；由需求派生的功能、规则、异常、验收和追踪项使用统一后缀或前缀：

| 对象 | 当前格式 | 示例 |
|---|---|---|
| 需求 | `{REQUIREMENT_ID}` | `PRD0013` |
| 模块能力 | `CAP-{MODULE_CODE}-*` | `CAP-PRD-CATALOG` |
| 模块业务信息 | `INFO-{MODULE_CODE}-*` | `INFO-PRD-PRODUCT` |
| 功能 | `{REQUIREMENT_ID}-F{NN}` | `PRD0013-F01` |
| 业务规则 | `BR-{REQUIREMENT_ID}-{NNN}` | `BR-PRD0013-001` |
| 跨功能规则 | `CR-{REQUIREMENT_ID}-{NNN}` | `CR-PRD0013-001` |
| 异常场景 | `EX-{REQUIREMENT_ID}-{NNN}` | `EX-PRD0013-001` |
| 验收标准 | `AC-{REQUIREMENT_ID}-{NNN}` | `AC-PRD0013-001` |
| 端到端追踪项 | `TR-{REQUIREMENT_ID}-{NNN}` | `TR-PRD0013-001` |
| 测试 Case | `CASE-*` | `CASE-ORDER-PAY-001` |
| 业务模型场景 | `SCN-*` | `SCN-ORDER-CANCEL` |
| 不变量 | `INV-*` | `INV-ORDER-NET-AMOUNT` |
| 项目业务流程 | `FLOW-*` | `FLOW-ORDER-E2E` |
| 业务流程步骤 | `STEP-*` | `STEP-ORDER-E2E-03` |
| 业务流程变体 | `FVAR-*` | `FVAR-REGISTER-MOBILE` |
| 业务流程失败路径 | `FAIL-*` | `FAIL-PAYMENT-TIMEOUT` |
| 跨模块实现映射 | `CMI-*` | `CMI-ORDER-CREATE` |
| 实现映射参与者 | `CMPART-*` | `CMPART-INVENTORY` |
| 实现映射步骤 | `CMSTEP-*` | `CMSTEP-ORDER-03` |
| 实现映射失败路径 | `CMFAIL-*` | `CMFAIL-ORDER-MATERIAL` |
| 关系 / 影响策略 | `REL-*` / `IMP-*` | `REL-ORDER-PAY` |
| API / DB 版本增量对象 | `API-*` / `DB-*` | `API-ORDER-CREATE` |
| 实施任务 | `TASK-*` | `TASK-ORDER-PAY-001` |
| Review criterion | `RC-{AXIS}-{NNN}` | `RC-SEC-002` |
| Review finding | `ISSUE-*` | `ISSUE-DESIGN-001` |
| 持久决策 | `DEC-*` | `DEC-ORDER-001` |
| 阶段结果 | `SO-*` | `SO-DESIGN-001` |
| Evidence / Assertion | `EVD-*` / `ASRT-*` | `EVD-000101` |

`FLOW/STEP` 只表示业务语义；`CMI/CMSTEP` 只表示跨模块 API、事件、事务和补偿实现映射。两套命名空间不得互换。

模块说明不创建第二套功能或规则编号：`模块功能` 必须复用所属需求的 `{REQUIREMENT_ID}-F{NN}`，`规则与约束` 必须复用 `BR-*`/`CR-*`；模块编码、所属能力和关联需求分别表达模块归属。

产物 revision 使用：

```text
{ARTIFACT_KIND}-{ROUND}@{CONTENT_HASH_OR_COMMIT}
```

例如 `DESIGN-R02@a31f9c2`。Reviewer、验证命令和阶段 outcome 必须引用同一 revision。

## 项目过程与工作记录

本文件定义 `project_process.md`、`work.md`、`work_record.md` 与 `task_events.jsonl` 的职责和写入边界。

### `project_process.md`

该文件记录“目标处于哪个阶段、当前门禁是否通过”，不存放详细设计或测试正文。

推荐表：

| 目标 ID | 名称 | 需求确认 | 需求分析 | 业务模型 | 设计 | 测试设计 | 实施计划 | TDD | 开发 | Code Review | 测试 | 完成验证 | 当前 revision | 阻塞/下一动作 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

阶段单元格只使用产物状态：`PENDING`、`IN_PROGRESS`、`PASSED`、`FAILED`、`STALE`、`BLOCKED`、`NOT_APPLICABLE`。

更新规则：

1. 只有阶段门禁通过才能写 `PASSED`。
2. 上游实质变化时，将受影响下游写 `STALE`，并记录新 revision。
3. `BLOCKED` 必须关联 issue、决策问题或失败证据。
4. “完成验证”通过前不得把整项目标写为完成。
5. 多个目标按表中声明顺序逐个执行；同一时刻最多一个目标处于实际执行状态。


### `work.md`

该文件是当前版本的功能工作分工与模式事实。文件必须包含机器标记：

```text
<!-- common-develop-work-mode: model_code=false page_design=false minimal=false auto=false architecture_review=false git_checkpoint=false git_push=false -->
```

固定工作表：

| 需求编号 | 需求名称 | 功能名称 | 功能编号 | 需求分析 | 页面设计 | 设计 | 架构设计 | 测试用例 | 前端伪代码 | 后端伪代码 | 开发 | 架构代码 | Review | 测试 | 归档 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

模式规则：

1. 默认未使用 `wk -mc`：所有功能行的“前端伪代码”“后端伪代码”必须填写“无”。
2. 使用 `wk -mc`：前后端伪代码进入工作范围；脚本把空值或“无”改为 `AIW`，实际完成后可按项目分工更新状态。
3. 未使用 `wk -v`：“页面设计”必须填写“无”；使用 `wk -v` 时进入工作范围并填写 `AIW`。
4. 使用 `wk -min`：“测试用例”“测试”必须填写“无”；同时不创建测试用例代码，不执行 canonical `test_design`、`testing`；Development 内嵌 TDD gate 同步设为 suppressed，不创建独立 `tdd` 阶段或 StageOutcome。
5. 使用 `wk -auto`：不改变工作表任务列；门禁通过后自动推进，不等待用户确认。
6. 使用 `wk -ar`：“架构代码”填写 `AIW`；development 分为骨架与具体实现两个顺序 iteration，第一轮 Review 通过后才能开始具体实现。完整规则只读取 `architecture-review-development.md`。
7. 使用 `wk -gc`：不改变工作表任务列；阶段门禁通过后创建本地 Git 检查点，失败时不推进；默认不 push。
<!-- common-develop-invariant: GIT_PUSH_REQUIRES_CHECKPOINT -->
8. 使用 `wk -gp`：必须同时启用 `wk -gc`；checkpoint 后优先 native 非 force push；github.com 的 native transport 不可用时只允许 GitHub Connector exact Git Data handoff，并经 receipt 校验 remote/可发现 PR HEAD；pending/失败不推进，不创建 PR、不 merge。
9. `wk -mc`、`wk -v`、`wk -min`、`wk -auto`、`wk -ar`、`wk -gc`、`wk -gp` 可组合；`wk -gp` 依赖 `wk -gc`，效果按各自契约叠加。
9. 模式变化必须先由 ProjectManagerAgent 执行 `work_mode.py apply`，再重新评估下游计划、revision 和归档门禁。
10. “页面设计”仅指界面业务字段、操作、状态和交互行为；视觉设计始终不属于本 Skill。

执行命令、组合参数和校验入口只读取 `work-mode-core.md` 与 `assets/runtime/runtime-contract.json#commandInterface.workModes`；本文件不复制第二套命令矩阵。
`status --json` 输出当前模式、自动确认策略、跳过阶段、被抑制 Reviewer 和必需非测试证据，供 AI 在分派前解析。细粒度长任务的静态执行规格来自 `task_plan.json`，大需求实施计划来自 `development_task_plan.json`，mutable 运行态来自 `task_events.jsonl` reducer；`work.md` 不复制任务执行状态、命令日志或 Review finding。


### `work_record.md` 与 `task_events.jsonl`

两者是不同作用域的事实源：

| 文件 | 作用域 | 内容 | 写入方式 |
|---|---|---|---|
| `version/{VERSION}/work_record.md` | 整个版本 | 跨任务摘要事件、模式变化、Review、阻塞、归档及 attempt 索引 | `long_task.py finish-attempt` 或 `append-work-event` |
| `version/{VERSION}/task/{TARGET_ID}/git_checkpoints.yaml` | 单个目标；仅 `wk -gc` 首次实际 checkpoint 时生成 | 阶段、iteration、revision、变更路径摘要和 Commit Trailer 定位；不内嵌自身 Commit SHA | `git_checkpoint.py commit` |
| `version/{VERSION}/task/{TARGET_ID}/task_events.jsonl` | 单个长任务 | 活动 append-only task runtime、attempt、StageOutcome、stale、issue lifecycle 与 auto-remediation；超阈值时由 snapshot 锚定历史 segment | `long_task.py` / `manual_review.py` / `auto_remediation.py` / 显式 `compact-events` |
| `version/{VERSION}/task/{TARGET_ID}/development_task_plan.json` | 大需求实施计划 | 具体开发任务、目标、实现方式、验收标准、依赖、Reviewer 和验证命令 | `task_plan.py status/query`（写入仍使用 init/seal/revise/finalize） |
| `version/{VERSION}/task/{TARGET_ID}/evidence/reviews.jsonl` | 大需求任务 Review | 绑定当前 `TP-*` revision 的串行 Review、finding 与覆盖任务 | `task_plan.py submit-review` |

`work_record.md` 使用 schema v4：每条记录由可读 Markdown 正文和带 digest 的紧凑隐藏元数据组成，只保留 task、attempt、iteration、revision、Evidence、Review、StageOutcome、Issue、Git checkpoint 和下一动作索引。人类直接阅读 Markdown 表格；AI 通过 `long_task.py work-events --json` 读取隐藏元数据，不再生成或保存第二份事实视图。

`work_record.md` 不复制完整命令输出、完整 Diff、异常栈和测试日志；这些内容注册到 Evidence Registry，由 `task_events.jsonl` 的 attempt payload 保存 evidence ID。一次任务执行完成后，脚本追加 event，并从 reducer 物化当前 task runtime；`task_plan.json` 与 `development_task_plan.json` 均保持静态。`cache/current_manifest.json` 仅是可删除重建的阅读 cache，不参与事务 authority。

当前格式校验与重新渲染：

```bash
python3 scripts/long_task.py validate-work-record -g CompletionVerificationAgent --doc-root project_doc --version {VERSION}
python3 scripts/long_task.py render-work-record -g ProjectManagerAgent --doc-root project_doc --version {VERSION}
```

AI 默认通过精简 JSON 读取：

```bash
python3 scripts/long_task.py task-context -g {AGENT} --task-dir ... --task-id TASK-001
python3 scripts/long_task.py latest-attempt -g {AGENT} --task-dir ... --task-id TASK-001
python3 scripts/long_task.py attempts -g {AGENT} --task-dir ... --task-id TASK-001 --status FAILED --limit 3
python3 scripts/long_task.py work-events -g {AGENT} --doc-root project_doc --version {VERSION} --task-id TASK-001 --limit 10
```

禁止 AI 为了恢复任务而默认加载整个 `work_record.md` 或全部 attempt 历史；只有分析重复失败、审计或 Review 复核时才按 ID 读取具体记录。

## 需求、设计与测试事实

本文件定义需求、设计、测试和分层业务流程文档的唯一职责边界。

### `requirement.md` 唯一结构

模板和示例来源：

```text
assets/documents/structured-docs/requirement.template.md
tests/fixtures/structured-docs/requirement.multi-feature.example.md
```

文件生命周期：

```text
RequirementConfirmationAgent 创建并完成确认章节
→ 用户或 wk -auto 确认当前 revision
→ RequirementAnalysisAgent 在同一文件补全功能、规则、异常、验收和追踪
→ 后续阶段只引用稳定 ID
```

固定一级章节为：

```text
1. 需求信息
2. 背景与问题
3. 需求目标
4. 范围
5. 功能列表
6. 功能详细需求
7. 跨功能规则
8. 异常与边界场景
9. 验收标准
10. 非功能要求
11. 模块职责边界
12. 待确认事项与已确认决策
13. 追踪关系
14. 变更记录
```

一个需求可以包含一个或多个功能。单功能只保留一行和一个详细小节；多功能继续使用同一文件，并为每个功能分配稳定功能编号。每个功能至少关联一条业务规则或跨功能规则、一个验收标准和一个追踪项。不得把可独立变更的多个业务目标强行包装为一个需求。

初始化和校验：

```bash
python3 scripts/requirement_doc.py init -g RequirementConfirmationAgent ...
python3 scripts/requirement_doc.py validate -g RequirementConfirmationAgent --stage confirmation ...
python3 scripts/requirement_doc.py validate -g RequirementAnalysisAgent --stage analysis ...
python3 scripts/requirement_doc.py sync-list -g RequirementConfirmationAgent --file ... --description "需求摘要"
python3 scripts/requirement_doc.py status -g ProjectManagerAgent --file ... --json
python3 scripts/requirement_doc.py sync-traceability -g RequirementAnalysisAgent --file ... --task-dir ...
```

需求文档写“必须表现为什么”，不锁死不必要的实现。现状证据可以引用代码、表或接口，但新表名、字段名、API 路径、类名、框架、事务方案和消息方案属于业务模型或设计产物。每条功能、每条 `BR/CR` 和每条验收标准必须进入第 13 节追踪关系；分析完成后必须通过 `sync-traceability` 同步到长任务 `traceability.json`，后续阶段只扩展动态引用，不反向污染需求事实。



### 统一语言与枚举引用

- 跨需求、模块和角色共享的业务术语以 `docs/union/union_language.md` 为当前权威；版本中新增/修改/废弃只写 `version/{VERSION}/union/union_language_changes.md`。正文裸 `[术语]` 必须唯一解析，语义冲突不得由 Agent 猜测合并。
- 模块枚举以 `docs/module/{MODULE}/{MODULE}_desc.md#枚举` 为项目当前权威；版本增量写 `version/{VERSION}/module/{MODULE}/{MODULE}_desc.md#枚举`。API/DB/Page/Test 只保存 enumRef/链接，不复制值表。
- 正式文档首先服务于人阅读；机器字段集中在固定表、front matter 或隐藏元数据。Review 按 `document-style.md` 检查术语稳定、边界明确、无工作过程措辞和空泛总结。

### 设计与测试文档边界

- 需求文档写“必须表现为什么”，不锁死不必要的实现。
- 业务模型写业务语义和不变量，不写控制器、ORM 或框架细节。
- 设计文档写技术边界、契约与权衡，不重复需求全文。
- 实施计划写任务、依赖、文件边界和验证，不重新发明设计；大需求的 `development_task_plan.json` 是 `wk -tp` 唯一任务事实源。
- 测试用例只验证已确认需求、模型和设计，不扩展为视觉检查或泛 QA。
- Review 问题引用精确证据，不把偏好包装成缺陷。

### 人类阅读与 revision 历史边界

- `requirement.md` 和当前 `*_design.md` 必须在首屏提供短阅读摘要；摘要回答价值、边界、主流程/失败路径、关键决策和验证入口，详细 ID 矩阵放在后文。
- `requirement.md`、`*_design.md`、`page-design/*_design.md` 和模块说明在首屏提供返回项目首页、版本摘要和相邻主文档的导航；Markdown 链接必须从声明链接的文件所在目录计算相对路径，解析后仍位于 `DOC_ROOT` 且目标真实存在。
- 当前 `*_design.md` 使用 `assets/documents/structured-docs/design.template.md` 的两层结构：第一部分用摘要、前后对照和连续叙述讲清方案，第二部分提供表/字段、接口、原功能和代码改动明细，追踪信息放附录。开发前通过 `design_doc.py validate --stage ready`；涉及数据和接口时必须通过 `merge_docs.py query` 取得本版本 API/DB 增量，并同时连接当前代码/OpenAPI/DDL/Migration 实现锚点，不涉及时写明“不适用”及具体理由，不创建空壳事实。
- 页面业务行为和前后端实现蓝图默认进入主设计的对应章节；只有语义独立、可单独变化或需要独立 Review 时，页面行为才拆到所属模块的 `page-design/{PAGE_DOCUMENT_STEM}_design.md`，并从主设计、模块说明和版本摘要建立链接。每个页面文件只声明一个主 `PAGE-*`；跨模块总览只引用 Owner 模块页面，不复制正文。
- 同一事实只保留一个当前文件。`*_R02.md`、`*_R23_t06_*.md` 等按轮次复制的需求、设计和实施计划禁止作为新产物；历史由 Git、Evidence、Review Registry、`task_events.jsonl` 与文档变更表保存。
- 语义独立的子设计可以拆文件，但文件名表达稳定主题而非 revision，并必须从当前主设计建立索引。
- Review 草稿、命令输出和证据快照不得混入 `version/{VERSION}/requirement/**`、`version/{VERSION}/module/**` 或 `version/{VERSION}/union/**` 作为阅读正文。


### 变更与失效

发生以下任一情况时必须评估 `STALE`：

- 业务规则、验收标准或范围改变；
- 聚合、不变量、状态机或错误语义改变；
- API/DB/事务/幂等/并发契约改变；
- 实施任务边界或代码 revision 改变；
- 测试所依据的环境、数据或版本改变。

使用 `scripts/long_task.py reopen-phase` 创建新 iteration，并使长任务中的受影响下游产物、Review、任务和旧证据失效。



### 分层业务流程文档

项目业务流程与 common-develop 研发流程分开。项目级唯一事实位于 `docs/_flows/{FLOW_GROUP}_flow.yaml`，阅读视图和索引由 `business_flow.py` 生成；版本只维护 `version/{VERSION}/module/_flows/{FLOW_GROUP}/changes/*.yaml`。

流程 YAML 使用稳定 FLOW/STEP/FVAR/FAIL ID，并关联需求文档、模块、功能、规则、AC 和 TR。模块 `{MODULE_CODE}_desc.md#参与业务流程` 只保存索引，不复制完整流程。`traceability.json` 使用 `business_flow_required/business_flow_refs`，`cross_module_implementation_refs` 只指向 `dependency_impact.yaml` 的跨模块技术实现映射。

版本预览与项目归档目录不同，所有 Markdown 链接由脚本按输出目录动态生成并验证。`wk -d` 必须在同一原子 payload 中写入流程 YAML、流程 Markdown、`flow_index.*`、受影响模块说明和 manifest；模块退出流程时必须删除旧索引，流程废弃时保留 DEPRECATED/SUPERSEDED 历史。详细规则见 `business-flow.md`。

## 文档阅读副本

本文件只定义 `wk -l` 只读 HTML 阅读副本的输入、输出和事实隔离边界。

### `wk -l` HTML 阅读副本

- 转换方向固定为 YAML（如适用）→ Markdown → HTML。
- Markdown/YAML 是事实源，HTML 是可删除、可重建的阅读副本。
- `wk -l` 不推进需求、设计、开发、Review、测试或 BUG 状态。
- `wk -l -b/-f` 只接受已解析的 BUG_ID/FEATURE_ID；描述型输入必须先由 `request_intake.py` 或 `wk.py start` 分类。
- `dependency_graph.md` 和跨模块 Mermaid 必须由 `scripts/render_relationships.py` 从 YAML 生成，再转换 HTML。
- 生成规则、链接、离线阅读和一致性检查见 `onepage-html.md`。
