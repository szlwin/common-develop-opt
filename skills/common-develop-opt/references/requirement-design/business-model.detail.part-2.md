# business-model（续）

> 本文是 [business-model.detail.md](./business-model.detail.md) 的按需续篇。
## 6. 与 API、DB 和模块设计的映射

业务模型不直接等同于 API 或表结构，但设计必须说明映射：

| 模型对象/规则 | API 命令或查询 | 数据对象/字段 | 负责模块 | 测试 seam |
|---|---|---|---|---|

必须检查：

- API 是否暴露了不允许绕过的不变量；
- DB 约束是否能保护关键唯一性、引用和并发条件；
- DTO/VO 是否泄漏无关内部结构；
- 跨聚合和跨模块操作是否有明确一致性、失败与补偿方案；
- 关联对象的删除、取消、退款、释放和历史保留策略是否明确；
- 状态枚举在模型、API、DB、代码和测试中是否一致；
- 错误码是否对应具体业务失败。

## 7. 变更集与 revision

业务模型变化不能整份静默覆盖。变更集至少包含：

```yaml
schemaVersion: 1
changeSet:
  id: CHG-VERSION-ORDER-001
  version: "{VERSION}"
  module: ORDER
  featureCode: ORDER-001
  reason: 取消订单禁止再次支付
  baseRevisions:
    business_model: BM-R01@old
  resultRevisions:
    business_model: BM-R02@new
  affectedTraceIds: [TR-ORDER-001]
operations:
  - artifact: business_model
    collection: invariants
    operation: update
    targetId: INV-ORDER-001
    beforeRef: business_model.yaml#INV-ORDER-001
    after:
      statement: 已取消订单不得创建支付
    reason: 与确认后的取消终态语义一致
```

规则：

- `changeSet.baseRevisions` 必须与本 changeset 实际修改的结构化产物完全一致，并等于当前有效基线；
- `resultRevisions` 只包含实际修改的产物，且每项操作使用稳定 ID；
- 删除或替代必须记录原因和替代关系；
- 变更后重新评估 API、DB、设计、计划、代码和测试；
- 受影响下游产物标记 `STALE`，旧 Review 和测试证据失效。

这里的“基线合并”只表示更新研发事实，不是发布、部署或上线流程。

### 7.1 `wk -d` 受控增量归档

版本目录只保存当前版本真实增量，项目级 YAML/Markdown 保存当前全量。正式合并执行：

```bash
python3 scripts/merge_docs.py init -g ProjectManagerAgent --doc-root project_doc --version {VERSION} --module MOD
# 复制 changes/changeset.template.yaml 为真实 *.yaml changeset，并补全 revision、操作和追踪 ID
python3 scripts/merge_docs.py validate -g IncrementalArchiveAgent --doc-root project_doc --version {VERSION} --module MOD
python3 scripts/merge_docs.py preview -g IncrementalArchiveAgent --doc-root project_doc --version {VERSION} --module MOD
# 在 generated/merge_plan.yaml 的 archiveGate 中补全门禁证据和授权结论
python3 scripts/merge_docs.py archive -g IncrementalArchiveAgent --doc-root project_doc --version {VERSION} --module MOD
```

`preview` 生成单一 `generated/merge_plan.yaml`、各结构化产物/Markdown preview 和 `merge_report.md`。merge plan 顶层固定 changeSet IDs、base/result revisions、受影响产物、追踪项、确定性 `artifactMerges`、工作模式和结果 digest；`archiveGate` 保存阶段 outcome、Review/测试证据、开放问题、授权结论和可选用户确认。

`requirement_list.md`、模块说明、稳定 Design topic、页面行为与 `dependency_impact.yaml` 均由脚本从版本源增量计算并写入 `artifactMerges` 或结构化 changeset 记录，普通归档不接受人工补写合并项。存在开放问题、旧 digest、旧 base revision、同 revision 内容漂移、悬空关系、缺少独立 Reviewer 证据或未完成门禁时禁止归档。`archive_manifest.yaml` 记录已应用 changeset、artifact merge、merge plan digest 和授权证据，保证幂等与可追溯；API/DB 仍只保存版本增量，不生成项目全量。

普通归档输出以需求、设计及适用业务文档为知识层；changeset、Traceability、Review、Acceptance、Evidence 和门禁结果保留为 `archive_manifest.yaml` 的引用及 `archive_audit/{VERSION}/{MODULE}.gate.json` 机器记录，不写入知识正文。需要完整审计交付时使用 `merge_docs.py archive --audit-bundle`（`wk document-archive --audit-bundle --apply` 亦可），额外生成审计索引。

`apply --allow-low-level-apply` 只用于受控诊断，正式 `wk -d` 禁止使用它绕过归档门禁。

## 8. BusinessModelAgent 输出

阶段结果至少包含：

- 输入 revision 与读取证据；
- 场景和术语表；
- 实体、值对象、聚合及其边界；
- 不变量和状态机；
- 服务、策略、事件及引入理由；
- 业务错误语义；
- 与需求、API、DB 和测试的映射；
- 未决问题、风险和假设；
- 输出 revision；
- 需要哪些 Reviewer。

## 9. 独立 Review

BusinessModelReviewAgent 至少检查：

- 术语是否唯一且与业务一致；
- 对象分类是否由身份、生命周期和不变量驱动；
- 聚合边界能否真正保护不变量；
- 状态机是否完整且无非法捷径；
- 业务错误是否可区分、可测试；
- 是否出现贫血模型、上帝聚合或技术模型冒充领域模型；
- 是否能映射到所有适用验收标准；
- 是否引入需求中没有的业务规则。

RequirementReviewAgent、DesignReviewAgent，以及标准模式下的 TestDesignAgent，分别从需求一致性、可实现性和可测试性独立审查。业务模型阶段的 TestDesignAgent 只检查模型是否表达足够的状态、不变量、错误和验证边界，不要求已有测试 Case；Case 覆盖由后续 `test_design` 阶段建立。`wk -min` 时不分派 TestDesignAgent，由 DesignReviewAgent 检查分支完整性和非测试可验证性。所有适用 Reviewer 必须基于同一 revision，先独立结论后讨论。

## 10. 门禁

业务模型阶段只有在以下条件全部满足时可 `PASSED`：

1. 所有适用需求和验收项均有 `business_model_refs`；
2. 术语、规则、不变量和状态转换无未决 P0/P1 冲突；
3. 状态、错误、重复调用、权限、并发和失败路径可判定；
4. API/DB/模块设计所需语义已明确；
5. 适用的关联影响策略、跨模块实现映射、失败和补偿已建立；
6. BusinessModelReview、RequirementReview、DesignReview 和 TestDesign 独立通过同一 revision；
7. 条件触发的 ImpactAnalysisReview/CrossModuleIntegrationReview 已通过或有 `NOT_APPLICABLE` 证据；
8. 无 `GAP` 或 `STALE` 追踪项；
9. 输出 revision 和 Review 证据已记录。


## 11. 与分层业务流程的映射

BusinessModelAgent 读取当前 `docs/_flows/{GROUP}_flow.yaml` 或版本预览，将 flow/step 映射到聚合、状态机、不变量、命令、事件、策略和业务错误。业务流程描述顺序、条件、变体和结果；业务模型定义这些行为由什么业务语义保证，两者不得复制。

流程业务语义变化时，从 requirement_analysis 重开；仅聚合或状态机实现语义变化时从 business_model 重开，并重新核对流程的前置、后置、失败和补偿。业务模型 changeset 可在 flow 的 `businessModelRefs` 中登记当前 revision；`wk -d` 分别合并，但门禁必须验证引用的是同一轮当前事实。
