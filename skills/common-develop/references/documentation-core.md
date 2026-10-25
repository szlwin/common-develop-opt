# 研发事实与文档核心

> 本文件是 Documentation Core Reference。默认通过 `relevant_sections` 按主题章节读取；不要无选择地整篇加载。

## 事实目录与稳定标识

本文件是研发事实目录、职责分层与稳定 ID/revision 的唯一可读规则。机器路径和字段仍以运行契约及 Schema 为准。

### 原则

- 文档是当前研发事实的结构化载体，不是聊天记录的转储。
- 同一事实只有一个当前生效来源；其它文件通过稳定 ID 和 revision 引用。
- 文档按人类阅读、研发事实、运行审计三层呈现；层之间用可验证的相对 Markdown 链接跳转，不复制 authority。
- 需求、设计、代码和测试发生实质变化时，相关下游引用必须标记 `STALE`。
- 结论必须可定位到文档段落、代码、命令输出、日志或测试报告。
- 事实文档优先使用 Markdown、YAML 或 JSON；仅在 `wk -l` 或用户明确要求时生成只读 HTML 阅读副本。HTML 不作为事实源，也不得反向覆盖 Markdown/YAML。
- 不写真实密码、Token、私钥、生产账号或敏感数据。

### 三层阅读模型

| 层 | 默认入口 | 内容 | 约束 |
|---|---|---|---|
| 人类阅读层 | `docs/README.md`、`version/{VERSION}/version_summary.md` | 当前需求、模块、设计、流程、跨模块关系和版本变化的可点击导航 | 只由 `document_portal.py` 生成；可删除重建，不成为事实源 |
| 研发事实层 | `docs/**`、`version/{VERSION}/doc/**` | 当前全量事实、版本研发事实和 changeset | 同一事实一个 authority；Markdown 正文先讲结论，ID/revision/追踪放附录 |
| 运行审计层 | `version/{VERSION}/task/**` | task event、Evidence、Review、Assertion、StageOutcome 和恢复事实 | 普通读者不遍历本层；通过 `wk status/next/context` 或生成摘要访问 |

所有生成的首页、版本摘要、流程阅读视图和关系图必须使用相对链接；绝对路径、逃逸 `DOC_ROOT`、指向不存在文件或 `generated/preview` 的正式链接必须校验失败。可选文档只在真实存在时进入首页或版本摘要，不创建空文件满足导航。


### 目录按职责分层

AI **不得把目录示例当成初始化清单**。先读取当前任务路由、工作模式和已有项目事实，只创建本次阶段真实需要的文件。项目已有等价路径时沿用现状，不复制事实，不创建空模块、空设计或空 HTML 占位文件。

#### 2.1 最小固定骨架

版本建立时只保证索引、模式和事实根可定位；具体模块、需求、流程和任务目录按首次真实产出创建：

```text
DOC_ROOT/
├── docs/                              # 项目级当前事实根；可暂为空
└── version/
    ├── current_version.md
    └── {VERSION}/
        ├── requirement_list.md
        ├── project_process.md
        ├── work.md
        ├── work_record.md
        ├── doc/                       # 版本级研发事实根；按需创建子目录
        └── task/                      # 长任务触发后按需创建 {TARGET_ID}
```

`project_env.md`、`harness_env.md`、`review_knowledge.md`、`project_version_desc.md`、`project_bug.md`、`sql/` 和 `test/` 均按项目现状或当前任务需要创建，不属于无条件初始化清单。`test_case.md` 只在标准模式进入对应 Requirement 的 `test_design` 时创建，不属于版本初始化文件。

#### 2.2 按需事实产物

| 触发条件 | 允许创建的事实路径 | 禁止行为 |
|---|---|---|
| 需求确认/分析 | `version/{VERSION}/doc/{REQUIREMENT_ID}/requirement.md` | 为未确认需求预建目录或空模板 |
| 测试设计 | `version/{VERSION}/doc/{REQUIREMENT_ID}/test_case.md`；数据辅助文件位于 `version/{VERSION}/test/{REQUIREMENT_ID}/` | 创建版本根级共享 `test_case.md`；把多个 Requirement 混在同一测试文件 |
| 模块首次进入当前版本 | `version/{VERSION}/doc/{MODULE_CODE}/{MODULE_CODE}_desc.md` | 为全部模块批量生成说明文件 |
| 业务建模/设计 | 对应模块的 `*_business_model.yaml`、`*_design.md`；API/DB 变化写入 `changes/*.yaml` | 未命中阶段或无真实内容时创建占位文件；创建 API/DB 全量文档 |
| `wk -v` | `*_page_design.md` | 未启用时生成页面设计 |
| `wk -mc` | `*_web_code.md`、`*_back_code.md` | 未启用时生成伪代码文档 |
| 分层业务流程触发 | `version/{VERSION}/doc/_flows/{FLOW_GROUP}/changes/` 与 `generated/` | 直接把版本预览写入项目级 `docs/_flows/` |
| 长任务触发 | `version/{VERSION}/task/{TARGET_ID}/...` | 为短任务或未启动目标预建完整任务树 |
| `wk -d` 通过 | `docs/{MODULE_CODE}/`、`docs/_flows/`、`docs/_relations/`；API/DB 只登记版本增量引用 | 绕过 merge plan 直接覆盖项目级事实；生成或更新项目级 API/DB 全量文档 |

`merge_docs.py init`、需求/设计文档初始化和 `wk -d` 应刷新 `docs/README.md` 与当前 `version_summary.md`。项目首页和版本摘要只链接已经存在或在同一事务中生成的文档，使需求、模块、设计、流程、关系和版本历史可以快速互相跳转。

长任务目录的 canonical 文件清单只从 `assets/long-task/record-contract.json` 和 `long_task.py init` 生成，不在本文件复制第二套完整树。

#### 2.3 生成产物与阅读副本

以下内容只由对应脚本生成，可删除重建，不得作为输入事实或手工维护：

- `*.html`：仅由 `wk -l` 生成；
- `_flows/*_flow.md`、`flow_index.md`、`_relations/dependency_graph.md/html`：从结构化事实生成；
- `task/{TARGET_ID}/cache/current_manifest.json`：Git 忽略的派生 manifest，不承担 canonical authority；
- `doc/_flows/{FLOW_GROUP}/generated/`：版本预览，不是项目级正式流程。

#### 2.4 DOC_ROOT 与目录硬门禁


`DOC_ROOT` 必须始终指向项目文档根目录，例如 `project_doc`，不得指向 `project_doc/version/{VERSION}`。调用版本级命令时使用：

```bash
--doc-root project_doc --version {VERSION}
```

唯一合法职责边界：

- `docs/**`：项目级当前事实；
- `version/{VERSION}/doc/**`：版本级研发事实、changeset 与 generated preview；
- `version/{VERSION}/task/**`：版本任务状态、Evidence、Review 与 StageOutcome。

以下目录不属于当前规范布局，任何正常命令、Evidence 恢复或任务验证都必须报错：

```text
version/{VERSION}/docs/
version/{VERSION}/document/
version/{VERSION}/documents/
```

统一检查命令：

```bash
python3 scripts/doc_layout.py check -g ProjectManagerAgent \
  --doc-root project_doc --version {VERSION} --json
```

发现 `version/{VERSION}/docs`、`document` 或 `documents` 时，所有正常命令均必须立即返回布局错误；文档只能按当前 canonical 路径创建和维护。


### 文档职责

| 文档 | 责任 | 主要维护 Agent |
|---|---|---|
| `requirement.md` | 需求确认阶段创建，需求分析阶段补全；保存需求信息、目标、范围、单/多功能拆分、规则、异常、验收、职责边界、决策和追踪关系 | RequirementConfirmationAgent、RequirementAnalysisAgent |
| `version/{VERSION}/requirement_list.md` | 当前版本需求索引、需求变更关系和完成情况；需求确认通过后登记 | RequirementConfirmationAgent、ProjectManagerAgent |
| `docs/requirement_list.md` | 各版本需求列表通过 `wk -d` 按需求编码合并后的项目级索引 | IncrementalArchiveAgent |
| `*_desc.md` | 模块信息、业务能力、功能、业务信息、规则、依赖、参与业务流程索引和文档引用；不复制完整流程，不记录工作量、实现证据或缺口 | RequirementAnalysisAgent、DesignAgent、IncrementalArchiveAgent |
| `*_business_model.yaml` | 术语、场景、实体、值对象、聚合、不变量、状态机和业务错误 | BusinessModelAgent |
| `_flows/*_flow.yaml` | L0/L1/L2 项目业务流程、关键变体、失败/补偿和需求/模块/功能/规则/AC/TR 追踪 | RequirementAnalysisAgent、BusinessModelAgent、DesignAgent、IncrementalArchiveAgent |
| `_flows/*_flow.md` / `flow_index.*` | 从业务流程 YAML 生成的项目级阅读视图和索引 | DocumentRenderAgent、IncrementalArchiveAgent |
| `_relations/dependency_impact.yaml` | 需求/功能关系、关联对象处置策略和业务流程步骤的跨模块技术实现映射 | RequirementAnalysisAgent、BusinessModelAgent、DesignAgent |
| `_relations/dependency_impact.md/html` | 从 dependency_impact 生成的跨模块影响与实现映射阅读副本 | DocumentRenderAgent |
| `_relations/engineering_graph.yaml` / `traceability_matrix.md` / `dependency_graph.md` | 从需求、功能、页面、设计、流程、业务模型、跨模块与验证事实生成的 DERIVED 全量关系投影和可跳转阅读视图 | IncrementalArchiveAgent、DocumentRenderAgent |
| `version/{VERSION}/doc/{MODULE}/*_design.md` | 当前版本设计增量：当前实现证据、变更清单、模块/调用、表字段作用与读写保存、接口字段映射、原功能兼容、代码改动蓝图、事务/并发/异常/迁移与可测性设计 | DesignAgent、ArchitectureReviewAgent |
| `docs/{MODULE}/design/*.md` / `docs/{MODULE}/{MODULE}_design.md` | 按稳定 Design topic 归档的当前设计快照及人类索引；保留来源版本链接，不拼接各版本全文 | IncrementalArchiveAgent |
| `version/{VERSION}/doc/{MODULE}/changes/*.yaml#operations[artifact=api]` | 本版本 API 目标增量、错误、权限、幂等与兼容变化；不表示项目当前全量 | DesignAgent |
| `version/{VERSION}/doc/{MODULE}/changes/*.yaml#operations[artifact=db]` | 本版本表/字段/键/约束/索引/迁移增量；不表示项目当前全量 | DesignAgent |
| 代码/OpenAPI/DDL/Migration/Schema | API/DB 当前实际实现；具体权威路径由项目事实声明 | DevelopAgent、DesignReviewAgent |
| `version/{VERSION}/doc/{MODULE}/*_page_design.md` | `wk -v` 启用时的本版本页面业务字段、状态、权限和交互行为设计，不含视觉设计 | DesignAgent |
| `docs/{MODULE}/design/pages/{MODULE}_page_design.md` | 自动归档的当前页面行为快照；页面/操作稳定 ID 继续作为追踪节点 | IncrementalArchiveAgent |
| `*_web_code.md` | `wk -mc` 启用时的前端业务行为与伪代码实现契约，不含视觉设计 | DesignAgent、DevelopAgent |
| `*_back_code.md` | `wk -mc` 启用时的后端方法、调用关系、分支与伪代码实现契约 | DesignAgent、DevelopAgent |
| `version/{VERSION}/doc/{REQUIREMENT_ID}/test_case.md` | 标准模式下当前需求到 Case、具体输入/输出/验证方式、页面操作、数据脚本和自动化测试的映射；`wk -min` 时不生成或更新 | TestDesignAgent |
| `project_bug.md` | 可复现缺陷、根因、修复 revision 和复测证据 | DebugAgent、DevelopAgent、TestAgent |
| `project_process.md` | 各目标在研发阶段的当前进度和门禁结论 | ProjectManagerAgent |
| `work.md` | 当前版本的可执行工作清单和依赖 | ProjectManagerAgent |
| `work_record.md` | 版本级跨任务摘要事件与 attempt 索引；不保存长日志 | 所有执行 Agent 通过脚本追加 |
| `review_knowledge.md` | 经确认、可跨任务复用的 Review 规则 | ProjectManagerAgent、对应 Reviewer |
| `task/{TARGET_ID}/...` | 长任务状态、计划、逐次 attempt、问题、追踪、决策、阶段结果和 handoff | ProjectManagerAgent、任务责任 Agent |
| `*.html` | 对应 Markdown 的只读阅读副本，只由 `wk -l` 生成或刷新 | DocumentRenderAgent |
| `archive_manifest.yaml` | 已成功归档的 changeset、digest、revision 和批准记录索引，用于幂等与追溯 | IncrementalArchiveAgent |
| `docs/README.md` | 当前项目需求、模块、设计、流程、关系和版本历史的人类导航入口；不保存新的研发事实 | DocumentRenderAgent、IncrementalArchiveAgent |
| `version/{VERSION}/version_summary.md` | 本版本需求、模块设计、流程/API/DB 增量和任务摘要的人类导航入口；不替代任何源文档 | DocumentRenderAgent、ProjectManagerAgent |

#### 统一关联模型与快速跳转

项目当前关系由 `artifact_relations.py` 从权威文档确定性投影，不要求人在多个 Markdown 中重复维护矩阵。核心节点为 Requirement、Feature、Page/PageAction、Design topic、Business Flow/Step、Business Model、Cross-module implementation 和 Validation；Module 只作为导航容器。需求目录下的 `test_case.md` 将 Requirement、Feature、Page/PageAction、Design 与 Flow/Step 投影为指向 Case 的 `VERIFIED_BY`。关系使用 `CONTAINS / EXPOSED_BY / HAS_ACTION / PARTICIPATES_IN / MODELED_BY / IMPLEMENTED_BY / DESIGNED_BY / VERIFIED_BY / DEPENDS_ON / AFFECTS / REFERENCES`。

`docs/_relations/traceability_matrix.md` 以需求为入口，横向跳转功能、页面、流程、模型、跨模块、设计与验证；“设计反向来源”从 Design topic 返回需求/功能。`dependency_graph.md` 提供 Mermaid 总览和逐条可点击明细。Design 节点同时登记 `#page-behavior`、`#data-write`、`#api-mapping`、`#code-blueprint` 等详细设计锚点。任何关系端点缺失、文档链接不存在、链接逃逸 DOC_ROOT 或 revision 快照过期时必须 fail-closed。

机器查询使用 `python3 scripts/artifact_relations.py query -g {AGENT} --doc-root project_doc --ref {REQ_OR_DESIGN_ID} --depth 5 --json`；结果按节点类型返回正向和反向可达事实，不要求 Agent 扫描所有 Markdown。

人类默认从 `docs/README.md` 进入当前项目，从 `version/{VERSION}/version_summary.md` 进入某个版本；模块当前设计索引链接每个稳定主题和页面行为，主题文档提供返回项目首页、模块说明和来源版本的链接。YAML、manifest、Evidence 和 task runtime 保持机器审计入口，不作为普通阅读首页。

#### P3 自动归档模型

- `business_model`、`business_flow`、`dependency_impact` 合并为项目当前事实；dependency impact 使用版本 changeset 的 add/update/deprecate 操作，deprecate 只改状态、不物理删除历史对象。
- Design 按稳定 topic 保存当前快照，页面行为按模块保存当前快照；base revision 必须等于项目当前 revision，同 revision 内容变化被视为冲突。
- `requirement_list`、模块说明、Design/page 快照进入脚本生成的 `artifactMerges`；普通归档不得人工增删。冲突处理完成后必须重新生成 preview。
- API/DB 仍只保留版本增量、source digest 和实现权威引用，不进入项目级全量归档。
- canonical facts、设计索引、关系视图、人类门户与 `archive_manifest.yaml` 在同一事务 payload 写入；写入前完成全部 schema、引用、digest 和 stale-base 校验，重复执行保持幂等。

#### P4 长任务阅读入口

长任务统一通过 `wk status`、`wk next --explain`、`wk context` 和 `cache/current_manifest.json` 恢复。Manifest 是可删除重建的派生投影，不保存新事实；完整 Trace、Evidence、Review 和历史内容必须通过查询命令按 ID 定向读取。

#### 3.1 模块补充文档引用

`version/{VERSION}/doc/{MODULE_CODE}/` 除标准的 `*_business_model`、`*_design`、`*_web_code`、`*_back_code`、`*_desc` 和 `changes/*.yaml` 外，可以按实际需要生成流程、方法契约、集成说明、前端代码规格、数据说明等补充文档。API/DB 不创建模块级全量 YAML 或全量 ER 文档。

规则：

1. 补充文档创建后必须进入同目录 `{MODULE_CODE}_desc.md#文档引用`；
2. `merge_docs.py validate/preview/archive` 会自动扫描该目录并补齐 `补充文档（自动）` 链接；
3. 忽略 HTML 阅读副本、隐藏文件、临时文件，以及 `changes/`、`generated/` 目录；
4. 项目级 `docs/{MODULE_CODE}/{MODULE_CODE}_desc.md` 保留版本号，并将链接改写到 `version/{VERSION}/doc/{MODULE_CODE}/`，保证归档后仍可打开；
5. 同一版本重新归档时替换该版本的自动引用，其他版本的补充文档引用继续保留；
6. 不得为了满足引用规则生成空文档或假链接。


### 稳定 ID 与 revision

稳定 ID 的完整机器契约位于 `assets/semantic-contract.json#idNamespaces`。项目可以选择自己的需求编号，例如 `PRD0013` 或 `REQ-ORDER-CANCEL`，但同一项目内必须保持稳定；由需求派生的功能、规则、异常、验收和追踪项使用统一后缀或前缀：

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

执行命令、组合参数和校验入口只读取 `work-mode-core.md` 与 `assets/runtime-contract.json#commandInterface.workModes`；本文件不复制第二套命令矩阵。
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
assets/structured-docs/requirement.template.md
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


### 设计与测试文档边界

- 需求文档写“必须表现为什么”，不锁死不必要的实现。
- 业务模型写业务语义和不变量，不写控制器、ORM 或框架细节。
- 设计文档写技术边界、契约与权衡，不重复需求全文。
- 实施计划写任务、依赖、文件边界和验证，不重新发明设计；大需求的 `development_task_plan.json` 是 `wk -tp` 唯一任务事实源。
- 测试用例只验证已确认需求、模型和设计，不扩展为视觉检查或泛 QA。
- Review 问题引用精确证据，不把偏好包装成缺陷。

### 人类阅读与 revision 历史边界

- `requirement.md` 和当前 `*_design.md` 必须在首屏提供短阅读摘要；摘要回答价值、边界、主流程/失败路径、关键决策和验证入口，详细 ID 矩阵放在后文。
- `requirement.md`、`*_design.md`、`*_page_design.md` 和模块说明在首屏提供返回项目首页、版本摘要和相邻主文档的导航；链接必须相对 `DOC_ROOT` 且可以解析。
- 当前 `*_design.md` 使用 `assets/structured-docs/design.template.md` 的两层结构：第一部分用摘要、前后对照和连续叙述讲清方案，第二部分提供表/字段、接口、原功能和代码改动明细，追踪信息放附录。开发前通过 `design_doc.py validate --stage ready`；涉及数据和接口时必须同时连接本版本 `changes/*.yaml` API/DB 增量与当前代码/OpenAPI/DDL/Migration 实现锚点，不涉及时写明“不适用”及具体理由，不创建空壳事实。
- 页面业务行为和前后端实现蓝图默认进入主设计的对应章节；只有语义独立、可单独变化或需要独立 Review 时才拆为稳定主题文件，并从主设计和版本摘要建立双向链接。
- 同一事实只保留一个当前文件。`*_R02.md`、`*_R23_t06_*.md` 等按轮次复制的需求、设计和实施计划禁止作为新产物；历史由 Git、Evidence、Review Registry、`task_events.jsonl` 与文档变更表保存。
- 语义独立的子设计可以拆文件，但文件名表达稳定主题而非 revision，并必须从当前主设计建立索引。
- Review 草稿、命令输出和证据快照不得混入 `version/{VERSION}/doc/**` 作为阅读正文。


### 变更与失效

发生以下任一情况时必须评估 `STALE`：

- 业务规则、验收标准或范围改变；
- 聚合、不变量、状态机或错误语义改变；
- API/DB/事务/幂等/并发契约改变；
- 实施任务边界或代码 revision 改变；
- 测试所依据的环境、数据或版本改变。

使用 `scripts/long_task.py reopen-phase` 创建新 iteration，并使长任务中的受影响下游产物、Review、任务和旧证据失效。



### 分层业务流程文档

项目业务流程与 common-develop 研发流程分开。项目级唯一事实位于 `docs/_flows/{FLOW_GROUP}_flow.yaml`，阅读视图和索引由 `business_flow.py` 生成；版本只维护 `version/{VERSION}/doc/_flows/{FLOW_GROUP}/changes/*.yaml`。

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
