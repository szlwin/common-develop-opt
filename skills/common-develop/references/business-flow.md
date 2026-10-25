# 分层业务流程规范

## 0. 业务流程快速规则 {#business-flow-quick-rules}

使用稳定的 FLOW/STEP 标识表示端到端正常流程、变体、失败、补偿、所有权、输入/输出以及跨模块步骤。根据已确认需求和当前业务事实推导流程；需要详细建模或影响分析时继续读取本文件和 `business-model.md`。

本规范描述**项目业务流程**，例如注册、商品准备、下单、支付、履约和退款。它不同于 `lifecycle-core.md` 与 `lifecycle-core.md` 定义的 common-develop 研发阶段流程。

## 1. 唯一事实与层级

项目业务流程的唯一机器事实为：

```text
DOC_ROOT/docs/_flows/{FLOW_GROUP}_flow.yaml
```

Markdown 只是脚本生成的阅读视图：

```text
DOC_ROOT/docs/_flows/{FLOW_GROUP}_flow.md
```

流程最多使用三层：

- `L0`：端到端价值场景，例如订单订购与售后；
- `L1`：稳定业务阶段，例如用户准备、商品准备、下单、支付、退款；
- `L2`：复杂节点的关键子流程，例如手机号注册、支付账号开通。

不要继续向下展开 API、数据库字段、类、方法或逐行算法。

## 2. 流程类型

- `PREPARATION`：主交易前的准备能力；
- `MAIN`：主价值链；
- `FOLLOWUP`：主流程之后的处理；
- `COMPENSATION`：失败、撤销或不一致恢复；
- `SUPPORTING`：被多个流程复用的支撑流程。

商品录入、支付账号开通等准备流程不得被错误描述成每次下单都顺序执行的主节点。

## 3. 关键字段

每个流程必须包含：

- 稳定 `FLOW-*` 编号、名称、层级、类型和父流程；
- 主责模块、参与模块和关联功能；
- 关联需求文档、业务规则、验收标准和 `TR-*`；
- 目标、触发条件、前置条件、关键步骤和成功标准；
- 关键变体；
- 阻塞失败、回退、后续和补偿路径；
- 当前状态、创建版本、最后变更版本和替代关系；
- 业务模型、影响分析、设计和测试 Case 的下游引用。

每个步骤必须包含稳定 `STEP-*`、主责模块和关联功能。变体使用 `FVAR-*`，失败路径使用 `FAIL-*`。

## 4. 与现有文档的职责边界

| 文档 | 职责 |
|---|---|
| `requirement.md` | 为什么做、功能/规则/验收是什么 |
| `*_flow.yaml` | 功能如何组成端到端业务链路 |
| `*_business_model.yaml` | 聚合、状态机、不变量、命令和事件 |
| `dependency_impact.yaml` | 关系影响、跨模块技术交互、失败与补偿实现映射 |
| `*_design.md` / API / DB | 技术实现 |
| `test_case.md` | 主路径、变体、失败和补偿覆盖 |
| implementation plan | 可独立验证的纵向任务切片 |

`dependency_impact.yaml#crossModuleImplementations` 只描述业务流程步骤的跨模块技术实现映射，必须引用 `businessFlowRef` 和 `businessStepRefs`，不得重新定义另一套业务流程。

## 5. 何时必须创建或更新

满足任一条件时，需求分析必须创建或更新结构化业务流程：

- 三个及以上关键业务节点；
- 两个及以上模块、聚合或外部系统；
- 存在状态流转；
- 存在准备、后续、回退、取消、退款或补偿；
- 存在多个身份、支付或履约变体；
- 一个功能依赖另一个功能的业务结果；
- 用户要求端到端解释业务。

简单且无状态流转的单模块 CRUD 可记录 `NOT_APPLICABLE` 及理由。

## 6. 各研发阶段职责

### 需求确认

识别是否影响现有流程组、主链路、前置、后续或补偿语义；不要求完成技术映射。

### 需求分析

RequirementAnalysisAgent 负责：

- 定义或更新 L0/L1/L2；
- 绑定需求、功能、模块、规则、验收和追踪项；
- 描述主路径、关键变体、失败出口和补偿目标；
- 执行 `business_flow.py validate`；
- 将 `business_flow_required/business_flow_refs` 同步到 `traceability.json`。

### 业务建模

BusinessModelAgent 将流程/步骤映射到聚合、状态机、不变量、命令、事件和业务错误。流程不能复制状态机定义。

### 设计

DesignAgent 将步骤映射到 API、事件、事务、幂等、并发、超时和补偿，并在 `dependency_impact.yaml` 中维护跨模块技术实现映射。

### 测试设计

TestDesignAgent 覆盖主路径、关键变体、阻塞失败路径和跨模块部分失败。无需为每个简单步骤机械创建一个 Case。

### 实施、Review、测试和完成验证

任务、Review finding、测试 Case 和 Evidence 使用稳定 flow/step 引用。CompletionVerificationAgent 检查受影响流程 revision、下游映射和验证证据是否仍为当前事实。

## 7. 版本增量目录

版本开发只写 changeset：

```text
DOC_ROOT/version/{VERSION}/doc/_flows/{FLOW_GROUP}/changes/*.yaml
```

预览由脚本生成：

```text
DOC_ROOT/version/{VERSION}/doc/_flows/{FLOW_GROUP}/generated/
├── {FLOW_GROUP}_flow.preview.yaml
└── {FLOW_GROUP}_flow.preview.md
```

项目级全量事实只由 `wk -d` 写入：

```text
DOC_ROOT/docs/_flows/
├── flow_index.yaml
├── flow_index.md
├── {FLOW_GROUP}_flow.yaml
└── {FLOW_GROUP}_flow.md
```

`business_flow.py init` 只能创建版本 changeset 模板，不得提前创建或修改项目级流程事实。

## 8. 命令

```bash
python3 scripts/business_flow.py init \
  -g RequirementAnalysisAgent \
  --doc-root project_doc \
  --version {VERSION} \
  --module {MODULE_CODE} \
  --group {FLOW_GROUP} \
  --name "{流程组名称}"

python3 scripts/business_flow.py validate \
  -g RequirementAnalysisAgent \
  --doc-root project_doc \
  --file {CHANGESET_OR_FLOW_YAML}

python3 scripts/business_flow.py impact \
  -g RequirementAnalysisAgent \
  --doc-root project_doc \
  --ref {REQUIREMENT_OR_FEATURE_OR_RULE_OR_AC_OR_TRACE_OR_FLOW_ID} \
  --json
```

预览和正式归档仍通过 `merge_docs.py preview/archive`，不得绕过 `wk -d` 直接修改项目级 YAML/Markdown。

`validate` 的结构通过不代表预览已同步。关联流程进入产物发布或 Review 时，检查会重放未归档 changeset，比较当前预览 YAML 内容与生成 Markdown；缺失或过期的预览必须重新生成。比较内容而非只比较 `resultRevision`，因此同一编号下的语义变化也会被发现。流程语义修订仍须使用新的 revision 并保留历史证据。

## 9. `wk -d` 链接改写

YAML 中的文档引用统一保存为 DOC_ROOT 相对路径，例如：

```text
version/{VERSION}/doc/{TARGET_ID}/requirement.md
```

Markdown 链接不能从版本预览复制到项目级文件。生成器必须依据输出文件所在目录动态计算：

```text
版本预览：
version/{VERSION}/doc/_flows/{FLOW_GROUP}/generated/{FLOW_GROUP}_flow.preview.md
→ ../../../{TARGET_ID}/requirement.md

项目归档：
docs/_flows/{FLOW_GROUP}_flow.md
→ ../../version/{VERSION}/doc/{TARGET_ID}/requirement.md
```

模块说明中的项目级流程链接固定由归档脚本生成，例如：

```text
docs/{MODULE_CODE}/{MODULE_CODE}_desc.md
→ ../_flows/{FLOW_GROUP}_flow.md#flow-payment
```

归档前必须验证所有生成链接：

- 不能是绝对路径或逃逸 DOC_ROOT；
- 目标必须已存在或在同一原子归档 payload 中生成；
- 版本预览和项目归档必须分别计算；
- 模块参与关系变化时，旧模块流程索引必须删除或替换；
- deprecated/superseded 流程保留历史状态，不物理删除事实。

## 10. 变更传播

- 需求目标、功能、规则、AC 或流程主语义变化：从 `requirement_analysis` 重开，受影响流程标记 `STALE`；
- 聚合、状态机或业务事件变化：从 `business_model` 重开，并重新核对流程前后置条件；
- 仅 API、DB 或类实现变化且业务语义不变：流程 revision 不变，只更新设计/影响分析；
- 功能废弃：流程步骤标记 `DEPRECATED` 或由新流程 `SUPERSEDED`，不得删除历史；
- 模块拆分/合并：稳定流程 ID 原则上不变，更新 owner/participants/featureRefs 和模块索引。

ProjectManagerAgent 根据 `business_flow.py impact` 结果执行 `long_task.py reopen-phase`，禁止直接改状态。

## 11. 归档门禁

`wk -d archive` 必须验证：

- changeset revision 连续且未重复应用；
- 父子层级、步骤顺序和引用无环；
- 模块、功能、需求文档、规则、AC 和 TR 引用真实；
- `business_flow_required=true` 的 trace 有 `business_flow_refs`；
- 跨模块技术映射引用真实 flow/step；
- 受影响的业务模型、设计、测试和完成验证已重新通过；
- 预览 digest、当前 facts 和归档计划一致；
- 链接经目录变换后仍可解析。

成功后原子写入流程 YAML、阅读 Markdown、流程索引、模块说明流程索引和 `archive_manifest.yaml`。
