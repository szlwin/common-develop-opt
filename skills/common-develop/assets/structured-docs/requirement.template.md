# {TARGET_ID} {REQUIREMENT_TITLE}

> 文档职责：RequirementConfirmationAgent 在需求确认阶段创建并维护第 1～4、12、14 节；RequirementAnalysisAgent 在需求分析阶段补全第 5～13 节。需求文档描述“必须表现为什么”，不得把表名、字段名、API 路径、类名或框架方案作为需求结论；必要的现状证据可引用具体实现，但技术方案进入设计文档。

> 文档导航：[项目文档首页](../../../../docs/README.md) · [版本摘要](../../version_summary.md) · [本版本需求列表](../../requirement_list.md)

## 1. 需求信息

| 项目 | 内容 |
|---|---|
| 需求编号 | {REQUIREMENT_ID} |
| 需求名称 | {REQUIREMENT_TITLE} |
| 版本目标编号 | {TARGET_ID} |
| 需求类型 | {REQUIREMENT_TYPE} |
| 主责模块 | {PRIMARY_MODULE} |
| 协作模块 | {COLLABORATION_MODULES} |
| 受影响角色 | {AFFECTED_ROLES} |
| 当前状态 | 需求确认中 |
| 对应变更需求编号 | {CHANGE_REQUIREMENT_ID} |

### 1.1 阅读摘要

| 读者最关心的问题 | 当前结论 |
|---|---|
| 为什么要做 | {PROBLEM_1} |
| 做到什么算有价值 | {GOAL_1} |
| 本次明确不做 | {OUT_OF_SCOPE_1} |
| 如何判断完成 | 以第 9 节 `AC-{REQUIREMENT_ID}-*` 的可观察结果为准 |
| 仍需谁做决定 | 见第 12 节；没有未决事项时写“无” |

## 2. 背景与问题

### 2.1 当前行为与证据

{CURRENT_BEHAVIOR_AND_EVIDENCE}

### 2.2 需要解决的问题

1. {PROBLEM_1}
2. {PROBLEM_2}

## 3. 需求目标

1. {GOAL_1}
2. {GOAL_2}

## 4. 范围

### 4.1 范围内

- {IN_SCOPE_1}
- {IN_SCOPE_2}

### 4.2 范围外

- {OUT_OF_SCOPE_1}
- {OUT_OF_SCOPE_2}

### 4.3 约束与依赖

- {CONSTRAINT_OR_DEPENDENCY_1}

## 5. 功能列表

> 一个需求至少包含一个功能。单功能需求只保留一行；多功能需求按稳定功能编号逐行登记。功能可以跨模块，但必须明确主责模块和协作模块。

| 功能编号 | 功能名称 | 主责模块 | 协作模块 | 关联流程 | 功能说明 | 状态 |
|---|---|---|---|---|---|---|
| {REQUIREMENT_ID}-F01 | {FEATURE_1_NAME} | {PRIMARY_MODULE} | {FEATURE_1_COLLABORATION_MODULES} | 待补充 | {FEATURE_1_DESCRIPTION} | 待分析 |

## 6. 功能详细需求

> 每个功能必须有独立小节。复制下面的小节即可扩展为多功能需求；不得把多个可独立验收的功能混写在一个小节。

### 6.1 {REQUIREMENT_ID}-F01 {FEATURE_1_NAME}

#### 6.1.1 功能目标

{FEATURE_1_GOAL}

#### 6.1.2 角色与权限

- {FEATURE_1_ROLE_OR_PERMISSION}

#### 6.1.3 前置条件

- {FEATURE_1_PRECONDITION}

#### 6.1.4 正常流程

1. {FEATURE_1_NORMAL_STEP_1}
2. {FEATURE_1_NORMAL_STEP_2}

#### 6.1.5 业务规则

- BR-{REQUIREMENT_ID}-001：{BUSINESS_RULE_1}
- BR-{REQUIREMENT_ID}-002：{BUSINESS_RULE_2}

#### 6.1.6 输入与输出约束

- 输入：{FEATURE_1_INPUT_CONSTRAINT}
- 输出：{FEATURE_1_OUTPUT_CONSTRAINT}

#### 6.1.7 状态、幂等、并发与事务语义

- 状态：{FEATURE_1_STATE_RULE}
- 幂等：{FEATURE_1_IDEMPOTENCY_RULE}
- 并发：{FEATURE_1_CONCURRENCY_RULE}
- 事务/部分失败：{FEATURE_1_TRANSACTION_RULE}

#### 6.1.8 异常与禁止副作用

- {FEATURE_1_EXCEPTION_AND_NO_SIDE_EFFECT}

## 7. 跨功能规则

> 仅单功能且不存在共享约束时可写“无”；多功能需求必须明确共享不变量、先后顺序、依赖、触发、取消、补偿和禁止副作用。

- CR-{REQUIREMENT_ID}-001：{CROSS_FEATURE_RULE_1}

## 8. 异常与边界场景

| 场景编号 | 关联功能 | 场景 | 预期结果 | 禁止副作用 |
|---|---|---|---|---|
| EX-{REQUIREMENT_ID}-001 | {REQUIREMENT_ID}-F01 | {EXCEPTION_SCENARIO_1} | {EXPECTED_RESULT_1} | {FORBIDDEN_SIDE_EFFECT_1} |

## 9. 验收标准

> 每条验收标准必须有稳定 ID，并描述可建立的前置状态、动作、可观察结果和禁止副作用。不得以“调用某接口”“写入某表”作为业务验收结论。

### AC-{REQUIREMENT_ID}-001 {ACCEPTANCE_TITLE_1}

Given {ACCEPTANCE_GIVEN_1}  
When {ACCEPTANCE_WHEN_1}  
Then {ACCEPTANCE_THEN_1}  
And {ACCEPTANCE_NO_SIDE_EFFECT_1}

## 10. 非功能要求

### 10.1 性能

- {PERFORMANCE_REQUIREMENT_OR_NA}

### 10.2 安全与权限

- {SECURITY_REQUIREMENT_OR_NA}

### 10.3 可靠性、一致性与恢复

- {RELIABILITY_REQUIREMENT_OR_NA}

### 10.4 审计与可观测性

- {AUDIT_REQUIREMENT_OR_NA}

### 10.5 兼容与历史数据

- {COMPATIBILITY_REQUIREMENT_OR_NA}

## 11. 模块职责边界

### 11.1 {PRIMARY_MODULE} 模块

负责：

- {PRIMARY_MODULE_RESPONSIBILITY}

不负责：

- {PRIMARY_MODULE_NON_RESPONSIBILITY}

### 11.2 协作模块

| 模块 | 负责内容 | 输入/输出业务事实 | 失败责任 |
|---|---|---|---|
| {COLLABORATION_MODULE} | {COLLABORATION_RESPONSIBILITY} | {COLLABORATION_FACT} | {COLLABORATION_FAILURE_OWNER} |

### 11.3 协作边界

{CROSS_MODULE_BOUNDARY}

## 12. 待确认事项与已确认决策

### 12.1 待确认事项

| 决策编号 | 问题 | 可选方案 | 推荐方案 | 是否阻塞 | 责任人/Agent | 状态 |
|---|---|---|---|---|---|---|
| DEC-{REQUIREMENT_ID}-001 | {PENDING_DECISION_QUESTION} | {PENDING_DECISION_OPTIONS} | {PENDING_DECISION_RECOMMENDATION} | 是 | {DECISION_OWNER} | 待确认 |

### 12.2 已确认决策

| 决策编号 | 结论 | 原因 | 证据/确认来源 | supersedes |
|---|---|---|---|---|
| - | - | - | - | - |

## 13. 追踪关系

> 每个功能、每条业务规则、每条跨功能规则和每条验收标准都必须被至少一个追踪项引用；涉及三步以上、状态流转、补偿或跨模块协作时还应填写稳定 `FLOW-*`。需求分析通过后使用 `requirement_doc.py sync-traceability` 同步到长任务 `traceability.json`。

| 追踪编号 | 功能编号 | 业务规则/跨功能规则 | 验收标准 | 关联流程 | 影响分析 | 后续业务模型 | 后续设计 | 测试 Case | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| TR-{REQUIREMENT_ID}-001 | {REQUIREMENT_ID}-F01 | BR-{REQUIREMENT_ID}-001 | AC-{REQUIREMENT_ID}-001 | 待补充 | 待补充 | 待补充 | 待补充 | 待补充 | PENDING |

## 14. 变更记录

| 文档 revision | 日期 | 阶段 | 变更内容 | 责任 Agent |
|---|---|---|---|---|
| R01 | {DATE} | 需求确认 | 创建需求文档，确认目标、范围和关键决策 | RequirementConfirmationAgent |
| R02 | {DATE} | 需求分析 | 拆分功能、规则、异常、验收标准和追踪关系 | RequirementAnalysisAgent |
