# {TITLE} 技术设计

> Design revision：`{DESIGN_REVISION}`  
> Base revision：`{BASE_REVISION}`  
> Design topic：`{DESIGN_TOPIC_ID}`
> Requirement：`{REQUIREMENT_REF}`  
> Module：`{MODULE_CODE}`  
> Implementation strategy contract：`1`
> 状态：草稿

> 文档导航：[项目文档首页](../../../../docs/README.md) · [版本摘要](../../version_summary.md) · [本版本需求列表](../../requirement/requirement_list.md) · [模块说明]({MODULE_CODE}_desc.md)

> 阅读建议：产品、项目负责人和 Reviewer 先读第一部分；开发者理解方案后，再查第二部分的字段、接口和代码改动明细；追踪与审计信息统一放在附录，不打断正文。

## 第一部分：设计正文

### 1. 一页设计摘要

<!-- DESIGN-NARRATIVE-SUMMARY -->
{ONE_PARAGRAPH_SUMMARY}

#### 1.1 改造前后对照

| 关注点 | 当前情况 | 调整后 | 带来的价值 |
|---|---|---|---|
| 用户/调用方体验 | {CURRENT_USER_EXPERIENCE} | {TARGET_USER_EXPERIENCE} | {USER_VALUE} |
| 核心业务流程 | {CURRENT_MAIN_FLOW} | {TARGET_MAIN_FLOW} | {FLOW_VALUE} |
| 数据事实 | {CURRENT_DATA_FACT} | {TARGET_DATA_FACT} | {DATA_VALUE} |
| 系统边界 | {CURRENT_SYSTEM_BOUNDARY} | {TARGET_SYSTEM_BOUNDARY} | {BOUNDARY_VALUE} |

#### 1.2 六个关键结论

| 读者最关心的问题 | 当前结论 |
|---|---|
| 本次改变什么 | {CHANGE_SUMMARY} |
| 保持什么不变 | {UNCHANGED_SUMMARY} |
| 主流程如何工作 | {MAIN_FLOW_SUMMARY} |
| 失败时系统留下什么事实 | {FAILURE_FACT_SUMMARY} |
| 最关键的技术决策 | {KEY_DECISION} |
| 如何证明设计完成 | {VERIFICATION_SUMMARY} |

#### 1.3 三分钟阅读路径

| 读者 | 建议阅读 | 阅读后应能回答 |
|---|---|---|
| 产品/业务 | 1、2、5、7 | 行为如何变化，哪些保持不变 |
| 技术负责人/Reviewer | 1～7 | 边界、流程、数据、接口和风险是否闭合 |
| 开发者 | 1～8，再查 9～13 | 改哪些代码、表和接口，按什么顺序开发 |
| 测试人员 | 1、5、7、13、附录 | 哪些行为、失败和兼容场景必须验证 |

### 2. 背景、现状与设计目标

<!-- DESIGN-NARRATIVE-CURRENT -->
{CURRENT_STATE_NARRATIVE}

#### 2.1 当前实现证据

| 能力/入口 | 当前代码、配置、表或接口锚点 | 当前调用链 | 已知问题 | 本次处置 |
|---|---|---|---|---|
| {CURRENT_CAPABILITY} | {FILE_CLASS_METHOD_OR_CONFIG} | {CURRENT_CALL_CHAIN} | {CURRENT_LIMITATION} | {KEEP_ADJUST_REPLACE_REMOVE} |

#### 2.2 设计目标与非目标

- 目标：{DESIGN_GOAL}
- 非目标：{DESIGN_NON_GOAL}
- 必须保持的兼容行为：{MUST_KEEP_BEHAVIOR}

### 3. 影响范围与明确边界

#### 3.1 影响概览

| 范围 | 是否变化 | 面向人的说明 | 详细位置 |
|---|---|---|---|
| 模块/组件 | {YES_NO} | {COMPONENT_IMPACT_SUMMARY} | §4、§12 |
| 表/字段 | {YES_NO} | {DATA_IMPACT_SUMMARY} | §6、§10 |
| API/内部契约 | {YES_NO} | {API_IMPACT_SUMMARY} | §7、§11 |
| 原有功能 | {YES_NO} | {EXISTING_FEATURE_IMPACT_SUMMARY} | §8、§12 |
| 迁移/发布 | {YES_NO} | {MIGRATION_IMPACT_SUMMARY} | §8、§10 |

#### 3.2 范围外与明确不变项

- {OUT_OF_SCOPE_OR_UNCHANGED_ITEM}

### 4. 目标方案与职责边界

<!-- DESIGN-NARRATIVE-TARGET -->
{TARGET_SOLUTION_NARRATIVE}

#### 4.1 目标结构

```text
{TARGET_ARCHITECTURE_OVERVIEW}
```

#### 4.2 组件职责

| 组件/模块 | 当前职责 | 调整后职责 | 对外入口 | 依赖 | 不应承担的职责 |
|---|---|---|---|---|---|
| {COMPONENT} | {CURRENT_RESPONSIBILITY} | {TARGET_RESPONSIBILITY} | {ENTRYPOINT} | {DEPENDENCIES} | {FORBIDDEN_RESPONSIBILITY} |

### 5. 核心流程、状态与失败路径

<!-- DESIGN-NARRATIVE-FLOW -->
{MAIN_FLOW_NARRATIVE}

#### 5.1 主流程

```text
{HAPPY_PATH_FLOW}
```

#### 5.2 关键失败路径

```text
{FAILURE_ROLLBACK_FLOW}
```

#### 5.3 状态与步骤

| 状态/步骤 | 前置条件 | 执行动作 | 数据变化 | 外部副作用 | 成功结果 | 失败/恢复 |
|---|---|---|---|---|---|---|
| {STATE_OR_STEP} | {PRECONDITION} | {ACTION} | {DATA_CHANGE} | {SIDE_EFFECT} | {SUCCESS_RESULT} | {FAILURE_RECOVERY} |

### 6. 数据与持久化方案

<!-- DESIGN-NARRATIVE-DATA -->
{DATA_PERSISTENCE_NARRATIVE}

本节正文只解释为什么这样存、重要数据在流程中怎样变化，以及失败后保留什么事实。当前实际结构以 DDL、Migration、数据库 Schema 或项目声明的等价来源为准；本版本目标变化通过 `python3 scripts/merge_docs.py query --artifact db` 读取，并由 `merge_docs.py write` 写入 DB 增量。

#### 6.1 单次请求内的持久化生命周期

1. 读取：{LOAD_WHAT_WHEN}
2. 校验：{VALIDATE_WHAT_AGAINST_WHICH_REVISION}
3. 内存变更：{MUTATION_BEFORE_SAVE}
4. 保存：{REPOSITORY_MAPPER_SAVE_AND_FLUSH_TIMING}
5. 副作用：{EVENT_CACHE_EXTERNAL_CALL_ORDER}
6. 提交：{COMMIT_VISIBILITY}
7. 失败：{ROLLBACK_RETRY_AND_IDEMPOTENCY}

### 7. 接口、交互与兼容策略

<!-- DESIGN-NARRATIVE-COMPATIBILITY -->
{INTERFACE_COMPATIBILITY_NARRATIVE}

当前实际 wire contract 以代码、OpenAPI 或项目声明的等价来源为准；本版本目标变化通过 `python3 scripts/merge_docs.py query --artifact api` 读取，并由 `merge_docs.py write` 写入 API 增量。正文解释调用方感知、兼容窗口、错误表现和旧功能调整，开发时查阅 §11～§12 的接口、字段映射和回归明细。

#### 7.1 页面业务行为

<a id="page-behavior"></a>

`wk -v` 启用且页面行为不构成独立稳定主题时，在这里记录页面/组件、操作、权限、数据来源、流程步骤、接口反馈和失败状态；需要独立变化或独立 Review 时，按页面 Owner 拆到 `page-design/{PAGE_DOCUMENT_STEM}_design.md`，并从本节与页面文档分别以声明文件所在目录为基准建立双向相对链接。不得按旧 `{MODULE_CODE}_page_design.md` 布局新建页面文档。未启用或不涉及页面时写明“不适用”及理由。

| 页面/组件编号 | 操作编号 | 功能/流程 | 字段与数据来源 | 权限与可用条件 | 成功/失败状态 | 接口/设计引用 |
|---|---|---|---|---|---|---|
| {PAGE_OR_NA} | {PAGE_ACTION_OR_NA} | {PAGE_FEATURE_FLOW_REF} | {PAGE_DATA_SOURCE} | {PAGE_PERMISSION_CONDITION} | {PAGE_RESULT_STATE} | {PAGE_INTERFACE_DESIGN_REF} |

#### 7.2 事务、并发、幂等与一致性

| 关注点 | 设计选择 | 生效边界 | 冲突/重复如何检测 | 失败与恢复 | 验证方式 |
|---|---|---|---|---|---|
| 事务 | {TRANSACTION_CHOICE} | {TRANSACTION_SCOPE} | {TRANSACTION_CONFLICT} | {TRANSACTION_FAILURE} | {TRANSACTION_TEST} |
| 并发 | {CONCURRENCY_CHOICE} | {CONCURRENCY_SCOPE} | {CONCURRENCY_DETECTION} | {CONCURRENCY_RECOVERY} | {CONCURRENCY_TEST} |
| 幂等 | {IDEMPOTENCY_CHOICE} | {IDEMPOTENCY_SCOPE} | {DUPLICATE_DETECTION} | {IDEMPOTENCY_RECOVERY} | {IDEMPOTENCY_TEST} |
| 一致性 | {CONSISTENCY_CHOICE} | {CONSISTENCY_SCOPE} | {STALE_DETECTION} | {CONSISTENCY_RECOVERY} | {CONSISTENCY_TEST} |

### 8. 开发者交接摘要

<!-- DESIGN-NARRATIVE-HANDOFF -->
{DEVELOPER_HANDOFF_NARRATIVE}

#### 8.1 建议实施顺序

1. {IMPLEMENTATION_STEP_1}
2. {IMPLEMENTATION_STEP_2}
3. {IMPLEMENTATION_STEP_3}

#### 8.2 开发开始前仍需确认

- {OPEN_IMPLEMENTATION_QUESTION_OR_NONE_WITH_REASON}

## 第二部分：开发实施明细

> 本部分供开发、Review 和测试按需查询。它是第一部分结论的展开，不应在这里重新讲一遍背景故事。

### 9. 需求映射与总变更清单

#### 9.1 功能与验收映射

| 功能/规则/AC/TR | 设计锚点 | DB 版本增量 | API 版本增量 | 验证入口 |
|---|---|---|---|---|
| {TRACE_ID} | {DESIGN_ANCHOR} | {DB_REF_OR_NA} | {API_REF_OR_NA} | {TEST_REF} |

#### 9.2 总变更清单

<!-- DESIGN-CHANGE-INVENTORY -->
| 变更对象 | 变更类型 | 当前事实与证据 | 目标变化 | 作用 | Owner | 兼容要求 |
|---|---|---|---|---|---|---|
| {CHANGE_OBJECT} | {ADD_MODIFY_REMOVE_REUSE_UNCHANGED} | {CURRENT_EVIDENCE} | {TARGET_CHANGE} | {PURPOSE} | {OWNER} | {COMPATIBILITY} |

#### 9.3 实现策略决策

<a id="implementation-strategy-decisions"></a>

每个接口、方法、组件、数据访问逻辑和业务规则按 `REUSE → COMPATIBLE_EXTEND → MODIFY → CREATE` 判断。选择后一策略时填写前序策略不适用理由；`CREATE` 必须同时明确公共逻辑处置和唯一 Owner。完整判断规则由 common-develop 的 `implementation-strategy` Reference 提供；本项目的当前结论只维护在下表，并与代码蓝图双向链接。

<!-- DESIGN-IMPLEMENTATION-DECISIONS -->
| 决策 ID | 对象 | 策略 | 现有候选与代码证据 | REUSE 不适用理由 | COMPATIBLE_EXTEND 不适用理由 | MODIFY 不适用理由 | 公共逻辑处理 | 公共逻辑 Owner | 兼容、调用方与验证 | 关联蓝图 |
|---|---|---|---|---|---|---|---|---|---|---|
| {IMPLEMENTATION_DECISION_ID} | {IMPLEMENTATION_OBJECT} | {IMPLEMENTATION_STRATEGY} | {EXISTING_CANDIDATE_EVIDENCE} | {REUSE_REJECTION_OR_NA} | {EXTEND_REJECTION_OR_NA} | {MODIFY_REJECTION_OR_NA} | {SHARED_LOGIC_DISPOSITION} | {SHARED_LOGIC_OWNER} | {COMPATIBILITY_CALLERS_VERIFICATION} | {BLUEPRINT_REF} |

### 10. 表、字段与数据读写明细

#### 10.1 表/存储对象变化

<!-- DESIGN-TABLE-CHANGES -->
| 表/存储对象 | 变更类型 | 作用与 Owner | 新增/修改内容 | 主键/唯一性 | 生命周期 | 迁移/回填 | DB 增量与实现锚点 |
|---|---|---|---|---|---|---|---|
| {TABLE_OR_STORE} | {ADD_MODIFY_REMOVE_REUSE_UNCHANGED} | {PURPOSE_AND_OWNER} | {CHANGE_DETAIL} | {IDENTITY_RULE} | {LIFECYCLE} | {MIGRATION_OR_NA} | {DB_DELTA_AND_IMPLEMENTATION_REF} |

#### 10.2 字段变化与业务语义

<!-- DESIGN-FIELD-CHANGES -->
| 表 | 字段 | 变更类型 | 类型/长度 | 空值/默认值 | 业务语义与作用 | 值来源 | 创建时写入 | 更新条件 | 是否可变 | 索引/约束 | 敏感性 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| {TABLE} | {FIELD} | {ADD_MODIFY_REMOVE_REUSE_UNCHANGED} | {TYPE} | {NULL_DEFAULT} | {BUSINESS_MEANING} | {VALUE_SOURCE} | {CREATE_WRITE} | {UPDATE_CONDITION} | {MUTABILITY} | {INDEX_CONSTRAINT} | {SENSITIVITY} |

#### 10.3 业务动作读写矩阵

<a id="data-write"></a>

<!-- DESIGN-DATA-ACCESS -->
| 业务动作 | 触发入口 | 表/聚合 | 操作 | 字段 | 查询/更新条件 | 读取一致性/锁 | 写入时机与保存方式 | 事务 | 失败后的事实 | Trace |
|---|---|---|---|---|---|---|---|---|---|---|
| {BUSINESS_ACTION} | {TRIGGER} | {TABLE_OR_AGGREGATE} | {SELECT_INSERT_UPDATE_DELETE} | {FIELDS} | {PREDICATE} | {CONSISTENCY_LOCK} | {WRITE_TIMING_AND_SAVE} | {TRANSACTION} | {FAILURE_FACT} | {TRACE_ID} |

#### 10.4 索引、历史数据与上线顺序

- 索引与约束：{INDEX_AND_CONSTRAINT_PLAN}
- 历史数据处理：{BACKFILL_OR_NA_WITH_REASON}
- 兼容读写窗口：{COMPATIBILITY_WINDOW_OR_NA_WITH_REASON}
- DDL/数据脚本顺序与回滚：{MIGRATION_ORDER_AND_ROLLBACK}

### 11. 接口与字段映射明细

#### 11.1 接口变化

<!-- DESIGN-API-CHANGES -->
| 接口/方法 | 类型 | 调用方 | 请求变化 | 响应变化 | 校验与权限 | 幂等 | 错误码 | 事务边界 | 兼容策略 | API 增量与实现锚点 |
|---|---|---|---|---|---|---|---|---|---|---|
| {API_OR_METHOD} | {ADD_MODIFY_REMOVE_REUSE_UNCHANGED} | {CALLERS} | {REQUEST_CHANGE} | {RESPONSE_CHANGE} | {VALIDATION_AUTH} | {IDEMPOTENCY} | {ERROR_CODES} | {TRANSACTION_BOUNDARY} | {COMPATIBILITY} | {API_DELTA_AND_IMPLEMENTATION_REF} |

#### 11.2 字段端到端映射

<a id="api-mapping"></a>

| 请求/事件字段 | 归一化/校验 | 领域对象/命令字段 | 表字段/外部契约 | 响应/事件字段 | 脱敏/审计 |
|---|---|---|---|---|---|
| {INPUT_FIELD} | {NORMALIZATION_VALIDATION} | {DOMAIN_FIELD} | {PERSISTENCE_OR_EXTERNAL_FIELD} | {OUTPUT_FIELD} | {MASK_AUDIT_RULE} |

### 12. 行为调整与代码改动

#### 12.1 行为调整

<!-- DESIGN-BEHAVIOR-ADJUSTMENTS -->
| 功能/场景 | 当前行为与证据 | 调整后行为 | 受影响入口/调用方 | 数据策略 | API/UI 策略 | 回归范围 | 发布/回滚注意事项 |
|---|---|---|---|---|---|---|---|
| {EXISTING_FEATURE} | {CURRENT_BEHAVIOR_EVIDENCE} | {TARGET_BEHAVIOR} | {AFFECTED_CALLERS} | {DATA_COMPATIBILITY} | {API_UI_COMPATIBILITY} | {REGRESSION_SCOPE} | {RELEASE_ROLLBACK_NOTE} |

#### 12.2 代码改动蓝图

<a id="code-blueprint"></a>

<!-- DESIGN-CODE-BLUEPRINT -->
| 蓝图 ID | 顺序 | 仓库/模块 | 文件、类或配置 | 变更类型 | 方法/符号 | 决策 ID | 具体改动 | 作用 | 公共逻辑 Owner | 来源文档 | 依赖 | 对应测试 |
|---|---:|---|---|---|---|---|---|---|---|---|---|---|
| {BLUEPRINT_ID} | {ORDER} | {REPOSITORY_MODULE} | {FILE_CLASS_CONFIG} | {ADD_MODIFY_REMOVE} | {METHOD_SYMBOL} | {IMPLEMENTATION_DECISION_REF} | {CONCRETE_CHANGE} | {PURPOSE} | {BLUEPRINT_SHARED_LOGIC_OWNER} | {SOURCE_DOCUMENT_MARKDOWN_LINKS} | {DEPENDENCY} | {TEST} |

尚不存在的文件或类必须标记“建议新增”；不能把建议路径写成当前事实。

`wk -mc` 启用时，前后端伪代码优先作为本蓝图对应行的补充说明；只有实现规格可以独立复用、独立变化或需要独立 Review 时才拆出 `*_web_code.md` / `*_back_code.md`，并从对应蓝图行建立双向链接。

### 13. 异常、安全、观测与验证明细

#### 13.1 异常、安全与可观测性

| 场景 | 稳定错误/告警 | 对外表现 | 日志/指标/Trace | 敏感数据处理 | 数据与状态结果 | 人工处置 |
|---|---|---|---|---|---|---|
| {FAILURE_SCENARIO} | {ERROR_OR_ALERT} | {EXTERNAL_BEHAVIOR} | {OBSERVABILITY} | {SECURITY_HANDLING} | {DATA_STATE_RESULT} | {MANUAL_ACTION_OR_NA} |

#### 13.2 验证计划

<!-- DESIGN-VERIFICATION -->
| 验证项 | 对应需求/风险 | 测试层级 | 前置数据 | 操作 | 可观察结果 | 失败注入/边界 | 自动化命令或证据入口 |
|---|---|---|---|---|---|---|---|
| {VERIFICATION_ITEM} | {REQUIREMENT_OR_RISK} | {TEST_LEVEL} | {PRECONDITION_DATA} | {ACTION} | {OBSERVABLE_RESULT} | {FAILURE_OR_BOUNDARY} | {COMMAND_OR_EVIDENCE} |

#### 13.3 开发就绪门禁

- [ ] 第一部分可以独立说明现状、目标方案、主流程、失败路径和关键决策。
- [ ] 每个范围内功能都有设计锚点和验证入口。
- [ ] 每项表/字段变化都有作用、来源、读写时机、更新条件、约束和迁移说明。
- [ ] 每项接口变化都有调用方、字段映射、权限、错误、幂等和兼容说明。
- [ ] 每个行为调整都有当前证据、目标行为和回归范围。
- [ ] 每个代码改动都有定位、职责、依赖、实施顺序和测试。
- [ ] 每个实现对象都按四级顺序形成 `IMPL-DEC-*`；`CREATE` 已逐项排除前序策略并说明公共逻辑处置与 Owner。
- [ ] 每个代码蓝图都双向关联实现决策、来源需求/设计锚点和验证入口。
- [ ] 事务、并发、失败回滚、可观测性和安全不存在未决实现语义。
- [ ] API/DB 版本 changeset 增量、当前代码/OpenAPI/DDL/Migration 实现证据、`dependency_impact.yaml` 与本文引用的 revision/锚点一致，或明确不适用及理由。

## 附录：追踪、决策与未决项

### A.1 追踪矩阵

| TR/AC | 设计章节 | 表/字段 | API/内部契约 | 代码蓝图 | 验证项 | 状态 |
|---|---|---|---|---|---|---|
| {TRACE_OR_ACCEPTANCE_ID} | {DESIGN_SECTION} | {TABLE_FIELD_REF_OR_NA} | {API_REF_OR_NA} | {CODE_BLUEPRINT_ROW} | {VERIFICATION_REF} | {COVERED_GAP} |

### A.2 已确认决策

| 决策 ID | 问题 | 选择 | 理由 | 替代方案与放弃原因 | 决策人/证据 |
|---|---|---|---|---|---|
| {DECISION_ID} | {QUESTION} | {CHOICE} | {RATIONALE} | {ALTERNATIVES} | {AUTHORITY_OR_EVIDENCE} |

### A.3 未决问题

| 问题 | 为什么阻断开发 | 需要谁决定 | 截止条件 | 影响章节 |
|---|---|---|---|---|
| {OPEN_QUESTION_OR_NONE_WITH_REASON} | {BLOCKING_REASON} | {DECISION_OWNER} | {DEADLINE_OR_GATE} | {AFFECTED_SECTION} |
