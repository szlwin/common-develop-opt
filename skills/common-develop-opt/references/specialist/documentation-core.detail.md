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
- 正式 Requirement、Module、Design、Test Case 与页面文档遵循 `document-style.md`：直接写业务事实、边界和失败结果，不写 Agent 思考过程或空泛“提升可维护性”措辞。
- 跨模块共享业务词先查 `docs/union/union_language.md`；需要新增/改义时只写当前版本 `union_language_changes.md`。模块有限取值集合只在 `{MODULE}_desc.md#枚举` 定义。

### 三层阅读模型

| 层 | 默认入口 | 内容 | 约束 |
|---|---|---|---|
| 人类阅读层 | `docs/README.md`、`version/{VERSION}/version_summary.md` | 当前需求、模块、设计、流程、跨模块关系和版本变化的可点击导航 | 只由 `document_portal.py` 生成；可删除重建，不成为事实源 |
| 研发事实层 | `docs/**`、`version/{VERSION}/requirement/**`、`version/{VERSION}/module/**`、`version/{VERSION}/union/**` | 项目当前全量事实、版本需求事实、版本模块增量与统一语言增量 | 同一事实一个 authority；Markdown 正文先讲结论，ID/revision/追踪放附录 |
| 运行审计层 | `version/{VERSION}/task/**` | task event、Evidence、Review、Assertion、StageOutcome 和恢复事实 | 普通读者不遍历本层；通过 `wk status/next/context` 或生成摘要访问 |

所有生成的首页、版本摘要、流程阅读视图和关系图必须使用相对链接；绝对路径、逃逸 `DOC_ROOT`、指向不存在文件或 `generated/preview` 的正式链接必须校验失败。可选文档只在真实存在时进入首页或版本摘要，不创建空文件满足导航。


### 目录按职责分层

AI **不得把目录示例当成初始化清单**。先读取当前任务路由、工作模式和已有项目事实，只创建本次阶段真实需要的文件。项目已有等价路径时沿用现状，不复制事实，不创建空模块、空设计或空 HTML 占位文件。

#### 2.1 最小固定骨架

版本建立时只保证索引、模式和事实根可定位；具体模块、需求、流程和任务目录按首次真实产出创建：

```text
DOC_ROOT/
├── docs/
│   ├── module/                        # 已归档模块当前全量事实；按需创建模块目录
│   └── union/                         # 已归档项目统一语言
└── version/
    ├── current_version.md
    └── {VERSION}/
        ├── requirement/               # 本版本需求与测试设计
        │   └── requirement_list.md
        ├── module/                    # 本版本模块设计与 changeset
        ├── union/                     # 本版本统一语言增量
        ├── project_process.md
        ├── work.md
        ├── work_record.md
        └── task/                      # 长任务触发后按需创建 {TARGET_ID}
```

`project_env.md`、`harness_env.md`、`review_knowledge.md`、`project_version_desc.md`、`project_bug.md`、`sql/` 和 `test/` 均按项目现状或当前任务需要创建，不属于无条件初始化清单。`test_case.md` 只在标准模式进入对应 Requirement 的 `test_design` 时创建，不属于版本初始化文件。

#### 2.2 按需事实产物

| 触发条件 | 允许创建的事实路径 | 禁止行为 |
|---|---|---|
| 需求确认/分析 | `version/{VERSION}/requirement/{REQUIREMENT_ID}/requirement.md` | 为未确认需求预建目录或空模板 |
| 测试设计 | `version/{VERSION}/requirement/{REQUIREMENT_ID}/test_case.md`；数据辅助文件位于 `version/{VERSION}/test/{REQUIREMENT_ID}/` | 创建版本根级共享 `test_case.md`；把多个 Requirement 混在同一测试文件 |
| 模块首次进入当前版本 | `version/{VERSION}/module/{MODULE_CODE}/{MODULE_CODE}_desc.md` | 为全部模块批量生成说明文件 |
| 业务建模/设计 | 对应模块的 `*_business_model.yaml`、`*_design.md`；API/DB 变化通过 `merge_docs.py write` 写入 `changes/*.yaml` | 未命中阶段或无真实内容时创建占位文件；创建 API/DB 全量文档 |
| `wk -v` | `version/{VERSION}/module/{MODULE_CODE}/page-design/{PAGE_DOCUMENT_STEM}_design.md` | 未启用时生成页面设计；按旧 `{MODULE_CODE}_page_design.md` 布局新建文件 |
| `wk -mc` | `*_web_code.md`、`*_back_code.md` | 未启用时生成伪代码文档 |
| 分层业务流程触发 | `version/{VERSION}/module/_flows/{FLOW_GROUP}/changes/` 与 `generated/` | 直接把版本预览写入项目级 `docs/_flows/` |
| 长任务触发 | `version/{VERSION}/task/{TARGET_ID}/...` | 为短任务或未启动目标预建完整任务树 |
| `wk -d` 通过 | `docs/module/{MODULE_CODE}/`、`docs/union/union_language.md`、`docs/_flows/`、`docs/_relations/`；API/DB 只登记版本增量引用 | 绕过 merge plan 直接覆盖项目级事实；生成或更新项目级 API/DB 全量文档 |

`merge_docs.py init`、需求/设计文档初始化和 `wk -d` 应刷新 `docs/README.md` 与当前 `version_summary.md`。项目首页和版本摘要只链接已经存在或在同一事务中生成的文档，使需求、模块、设计、流程、关系和版本历史可以快速互相跳转。

长任务目录的 canonical 文件清单只从 `assets/storage/long-task/record-contract.json` 和 `long_task.py init` 生成，不在本文件复制第二套完整树。

#### 2.3 生成产物与阅读副本

以下内容只由对应脚本生成，可删除重建，不得作为输入事实或手工维护：

- `*.html`：仅由 `wk -l` 生成；
- `_flows/*_flow.md`、`flow_index.md`、`_relations/dependency_graph.md/html`：从结构化事实生成；
- `task/{TARGET_ID}/cache/current_manifest.json`：Git 忽略的派生 manifest，不承担 canonical authority；
- `version/{VERSION}/module/_flows/{FLOW_GROUP}/generated/`：版本预览，不是项目级正式流程。

#### 2.4 DOC_ROOT 与目录硬门禁


`DOC_ROOT` 必须始终指向项目文档根目录，例如 `project_doc`，不得指向 `project_doc/version/{VERSION}`。调用版本级命令时使用：

```bash
--doc-root project_doc --version {VERSION}
```

唯一合法职责边界：

- `docs/**`：项目级当前事实；
- `version/{VERSION}/requirement/**`：版本需求与测试设计事实；
- `version/{VERSION}/module/**`：版本模块设计、changeset 与 generated preview；
- `version/{VERSION}/union/**`：版本统一语言变更；
- `version/{VERSION}/task/**`：版本任务状态、Evidence、Review 与 StageOutcome。

以下目录不属于当前规范布局，任何正常命令、Evidence 恢复或任务验证都必须报错：

```text
version/{VERSION}/doc/
version/{VERSION}/docs/
version/{VERSION}/document/
version/{VERSION}/documents/
```

统一检查命令：

```bash
python3 scripts/doc_layout.py check -g ProjectManagerAgent \
  --doc-root project_doc --version {VERSION} --json
```

发现 `version/{VERSION}/doc`、`docs`、`document` 或 `documents` 时，所有正常命令均必须立即返回布局错误；文档只能按当前 canonical 路径创建和维护。


### 文档职责

| 文档 | 责任 | 主要维护 Agent |
|---|---|---|
| `requirement.md` | 需求确认阶段创建，需求分析阶段补全；保存需求信息、目标、范围、单/多功能拆分、规则、异常、验收、职责边界、决策和追踪关系 | RequirementConfirmationAgent、RequirementAnalysisAgent |
| `version/{VERSION}/requirement/requirement_list.md` | 当前版本需求索引、需求变更关系和完成情况；需求确认通过后登记 | RequirementConfirmationAgent、ProjectManagerAgent |
| `docs/requirement_list.md` | 各版本需求列表通过 `wk -d` 按需求编码合并后的项目级索引 | IncrementalArchiveAgent |
| `*_desc.md` | 模块信息、业务能力、功能、业务信息、规则、依赖、参与业务流程索引和文档引用；不复制完整流程，不记录工作量、实现证据或缺口 | RequirementAnalysisAgent、DesignAgent、IncrementalArchiveAgent |
| `{MODULE}_desc.md#枚举` | 模块业务枚举的唯一业务定义；版本文件记录增量，项目文件保存归档后的当前全量；API/DB/Page/Test 只引用 | RequirementAnalysisAgent、DesignAgent、IncrementalArchiveAgent |
| `docs/union/union_language.md` / `version/{VERSION}/union/union_language_changes.md` | 项目共享 DDD 统一语言当前事实与版本增量；裸 `[术语]` 必须唯一解析 | RequirementAnalysisAgent、BusinessModelAgent、DesignAgent、IncrementalArchiveAgent |
| `*_business_model.yaml` | 术语、场景、实体、值对象、聚合、不变量、状态机和业务错误 | BusinessModelAgent |
| `_flows/*_flow.yaml` | L0/L1/L2 项目业务流程、关键变体、失败/补偿和需求/模块/功能/规则/AC/TR 追踪 | RequirementAnalysisAgent、BusinessModelAgent、DesignAgent、IncrementalArchiveAgent |
| `_flows/*_flow.md` / `flow_index.*` | 从业务流程 YAML 生成的项目级阅读视图和索引 | DocumentRenderAgent、IncrementalArchiveAgent |
| `_relations/dependency_impact.yaml` | 需求/功能关系、关联对象处置策略和业务流程步骤的跨模块技术实现映射 | RequirementAnalysisAgent、BusinessModelAgent、DesignAgent |
| `_relations/dependency_impact.md/html` | 从 dependency_impact 生成的跨模块影响与实现映射阅读副本 | DocumentRenderAgent |
| `_relations/engineering_graph.yaml` / `traceability_matrix.md` / `dependency_graph.md` | 从需求、功能、页面、设计、流程、业务模型、跨模块与验证事实生成的 DERIVED 全量关系投影和可跳转阅读视图 | IncrementalArchiveAgent、DocumentRenderAgent |
| `version/{VERSION}/module/{MODULE}/*_design.md` | 当前版本设计增量：当前实现证据、变更清单、模块/调用、表字段作用与读写保存、接口字段映射、原功能兼容、代码改动蓝图、事务/并发/异常/迁移与可测性设计 | DesignAgent、ArchitectureReviewAgent |
| `docs/module/{MODULE}/design/*.md` / `docs/module/{MODULE}/{MODULE}_design.md` | 按稳定 Design topic 归档的当前设计快照及人类索引；保留来源版本链接，不拼接各版本全文 | IncrementalArchiveAgent |
| `merge_docs.py query --artifact api` 的结果 | 本版本 API 目标增量、错误、权限、幂等与兼容变化；不表示项目当前全量 | DesignAgent |
| `merge_docs.py query --artifact db` 的结果 | 本版本表/字段/键/约束/索引/迁移增量；不表示项目当前全量 | DesignAgent |
| `merge_docs.py query --artifact dependency-impact` 的结果 | 本版本跨模块依赖影响增量；不表示项目当前全量 | Impact/DesignAgent |
| 代码/OpenAPI/DDL/Migration/Schema | API/DB 当前实际实现；具体权威路径由项目事实声明 | DevelopAgent、DesignReviewAgent |
| `version/{VERSION}/module/{MODULE}/page-design/{PAGE_DOCUMENT_STEM}_design.md` | `wk -v` 启用时按 Owner 模块和稳定页面主题拆分的本版本页面业务字段、状态、权限和交互行为设计，不含视觉设计；使用 `page_design.template.md`，从当前文件建立相对链接并链接具体 API、原型、功能、流程、步骤和 AC anchor；旧 `{MODULE}_page_design.md` 仅作迁移期只读兼容 | DesignAgent |
| `docs/module/{MODULE}/design/pages/{PAGE_DOCUMENT_STEM}_design.md` | 按主 `PAGE-*` 独立归档的当前页面行为快照；页面/操作稳定 ID 继续作为追踪节点 | IncrementalArchiveAgent |
| `*_web_code.md` | `wk -mc` 启用时的前端业务行为与伪代码实现契约，不含视觉设计 | DesignAgent、DevelopAgent |
| `*_back_code.md` | `wk -mc` 启用时的后端方法、调用关系、分支与伪代码实现契约 | DesignAgent、DevelopAgent |
| `version/{VERSION}/requirement/{REQUIREMENT_ID}/test_case.md` | 标准模式下当前需求到 Case、具体输入/输出/验证方式、页面操作、数据脚本和自动化测试的映射；`wk -min` 时不生成或更新 | TestDesignAgent |
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

`version/{VERSION}/module/{MODULE_CODE}/` 除标准的 `*_business_model`、`*_design`、`*_web_code`、`*_back_code`、`*_desc` 和 `changes/*.yaml` 外，可以按实际需要生成流程、方法契约、集成说明、前端代码规格、数据说明等补充文档。API/DB changeset 只能由 `merge_docs.py query/write` 读取或写入；不创建模块级全量 YAML 或全量 ER 文档。

规则：

1. 补充文档创建后必须进入同目录 `{MODULE_CODE}_desc.md#文档引用`；
2. `merge_docs.py validate/preview/archive` 会自动扫描该目录并补齐 `补充文档（自动）` 链接；
3. 忽略 HTML 阅读副本、隐藏文件、临时文件，以及 `changes/`、`generated/` 目录；
4. 项目级 `docs/module/{MODULE_CODE}/{MODULE_CODE}_desc.md` 保留版本号，并将链接改写到 `version/{VERSION}/module/{MODULE_CODE}/`，保证归档后仍可打开；
5. 同一版本重新归档时替换该版本的自动引用，其他版本的补充文档引用继续保留；
6. 不得为了满足引用规则生成空文档或假链接。



> 后续规则见 [documentation-core.detail.part-2.md](./documentation-core.detail.part-2.md)。
