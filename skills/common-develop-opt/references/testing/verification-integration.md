# 机器验证阶段接入与查询

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

本文件定义机器验证如何接入阶段、归档和紧凑 Evidence 查询。

## 阶段与归档接入

- `start-attempt code_review`：先校验当前风险报告、风险 Reviewer 和任务 `risk_triggers`；
- `finish-attempt PASSED`：校验 attempt evidence、command evidence 和 acceptance 关联；
- 阶段 `PASSED`：StageOutcome 和 Review criterion 只能引用当前 registry evidence；
- `completion_verification`：要求所有 blocking assertions 已验证；
- `wk -d archive`：先运行完整 `long_task.validate_task`，再回查 evidence registry、risk report、acceptance assertions、StageOutcome、Review 和 ACTIVE 决策；缺任一运行文件或存在过期引用均拒绝归档。


## 禁止做法

- Agent 手写 SHA-256；
- Review criterion 引用裸路径；
- 将旧 revision evidence 用于当前结论；
- 用自然语言 `expected_results` 直接作为机器通过条件；
- risk report 未扫描便开始 code review；
- 删除检测结果规避专项 Review；
- 把 `UNRESOLVED` 或 `MANUAL_REVIEW` 未完成项标记为完成；
- 在归档计划中填写不存在的 evidence ID。

## 紧凑 Evidence 查询

AI 不应默认加载完整 `registry.jsonl`。使用 `scripts/evidence.py query --phase {PHASE} --revision {REVISION} --limit 20` 获取有限子集；完整 Review 结果使用 `manual_review.py show --review-result-ref {REV_ID}` 按需读取，不加载整个 `reviews.jsonl` 事件流。
