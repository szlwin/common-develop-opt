# 需求追踪最小契约

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

## 0. 追踪快速规则 {#traceability-quick-rules}

每个已确认需求和验收项都需要稳定 ID，并链接到同一个活动 revision 的设计、实现任务、测试、Evidence、Review 和 StageOutcome。只有在创建或修复详细映射时，才继续读取本文件。

## 1. 追踪关系
每个重要规则、Acceptance 或非功能约束建立稳定 `TR-*`，连接需求来源、功能、规则、业务流程、跨模块映射、业务模型、设计、Case、实现和 Evidence。引用必须真实存在，不允许只写自然语言“已覆盖”。

## 2. 同步规则
`requirement_doc.py sync-traceability` 对同一 revision 必须幂等。需求来源变化且已有下游引用时标记 `STALE`；从需求文档移除的追踪项保留审计历史并标记失效，不物理删除。

同步记录 `requirement_source_digest`，因此正文变化即使保留全部稳定 ID 也会使已有覆盖失效。旧记录首次补充摘要时需要重新核对，不把旧 `COVERED` 当作新内容的验证。功能表明确列出跨模块协作时同步影响标志，但影响说明和技术映射引用仍由 Owner 补充；模块关系消失不会自动撤销已有影响判断。

同步不会自动替换模型、设计或 Evidence 引用，也不证明这些内容已通过 Review。发布候选产物和准备/提交 Review 前会解析已声明的模型、设计和影响引用；JSON/YAML 使用真实稳定 ID 或 JSON Pointer（例如 `#/revision`），不能把 revision 字符串伪装成稳定 ID。

协作模块减少而已有跨模块判断时，`impactReviewRequired` 返回待核对的 Trace；Owner 确认影响消失后再更新标志和引用，避免自动删除来自 Flow 或人工分析的影响事实。已记录的需求分析重开允许更新追踪：更早的 revalidation 仍验证原 Review/Evidence 绑定，但不再将旧文件摘要强加给重开后的文件；当前分析仍须重新通过自己的 Trace 和 Review 门禁。

## 3. 完成条件
每个功能、规则和 Acceptance 至少被一个追踪项引用；每个追踪项至少包含一个规则和一个验收引用。业务流程或跨模块实现被要求时，相应引用不得为空。
