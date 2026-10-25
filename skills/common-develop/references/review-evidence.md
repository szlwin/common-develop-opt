# Review Evidence 核心

> 本文件是 Review Evidence Reference。默认通过 `relevant_sections` 按主题章节读取；不要无选择地整篇加载。

## Evidence 使用规范

### 1. 引用格式
Criterion Evidence 使用 `{evidence_id, claim}`；Reviewer 复用 Evidence Registry 中已有 Artifact ID，只追加使用关系，不复制文件、日志或大段输出。

### 2. 有效性
Evidence 必须绑定当前 revision、phase、digest、来源和状态。路径不存在、digest 变化、命令退出码缺失、时间早于候选 revision 或来源不可解析时，不得支持通过结论。

### 3. 最小读取
先按 Review Profile 查询所需 Evidence 类型，再按 ID 定向读取。默认不加载完整 Evidence Index、全部 Snapshot 或历史 Review；诊断缺失链时再按需加载 `verification-integration.md`、`risk-verification.md` 等当前验证 Reference 的相关章节。

## Criterion Evidence 与人工 Review

本文件定义 Review criterion 的证据引用和引导式人工 Review 边界。

### Review criterion evidence

criterion 证据结构固定为：

```json
{
  "evidence_id": "EVD-000001",
  "claim": "当前设计 revision 的事务边界已覆盖失败补偿。"
}
```

Review 门禁会回查：

- evidence ID 存在；
- evidence 为 `ACTIVE`；
- 文件 digest 未变化；
- evidence revision 等于 artifact revision；
- evidence phase 与当前 Review 阶段一致；
- evidence type 满足该 criterion 的 `evidencePolicyRef` 所指向的 Policy；
- evidence `scope_refs` 必须覆盖当前 criterion ID，防止复用无关证据；
- `FAILED` criterion 仍必须关联真实 finding。

不得再使用 `{type, ref, claim}` 裸引用格式。

### 实现策略 Evidence

`RC-SPEC-007` 至少使用当前 revision 的 Design `IMPL-DEC-*` 锚点与实现 diff/code Evidence，兼容扩展或修改还要引用旧调用和新路径的验证 Evidence。`RC-ARCH-008` 使用决策排除证据、职责 Owner 和依赖边界；`RC-MAINT-008` 使用实际代码证明同一业务规则只有一个 Owner，且没有布尔/模式参数、跨域 switch 或万能工具类伪造复用。

任务计划中的 `implementation_decision_ref` 和 `source_doc_refs` 只负责定位批准结论，不代替 Evidence Registry。Reviewer 复用这些来源时，仍要为当前 exact revision 登记对应 `design_ref / code_ref / diff_ref / test_ref`，并让 `scope_refs` 覆盖实际 Criterion ID。


### 引导式人工 Review

`MANUAL_REVIEW` 使用 `scripts/manual_review.py`，支持：

- `wizard`：Reviewer 逐题交互确认；
- `prepare → submit`：Markdown 确认单；
- `draft → submit`：责任 Agent 草拟、独立 Reviewer 确认。

三种模式共用同一提交门禁。脚本自动生成 Profile 全部 criterion、checked scope、当前 revision evidence、finding 和人工确认结果；问题模板来自 `assets/manual-review-guides.json`。没有当前证据、表单过期、草拟者自我确认或任一问题无法判断时，不得进入 `VERIFIED`。详见 `manual-review-guide.md`。

## Incremental Validity 与 Evidence 复用

新的 exact Review revision 不能继承旧 `PASSED` Review Result；Reviewer 必须对新 revision 重新产生独立 Review Result。Impact Planner 允许把未受影响 Criterion 标记为 `unaffected`，但这只缩小需要重新收集/阅读的范围，不等于新 revision 自动通过。

未受影响 Criterion 可以复用**未变化的 Evidence 来源/内容**，但旧 revision 的 Evidence ID 不能直接证明新 revision。若 exact Review revision 发生变化，必须把相同且仍有效的来源在新 revision 下重新登记或确定性重绑为 current-revision Evidence，再由 Reviewer 使用；其 digest/source scope 必须未变化、Criterion scope 必须未受影响，并继续满足当前 Evidence Policy。旧 Review Result 同样不能跨 exact revision 继承。

Impact Adapter 置信度为 `LOW/UNKNOWN`、缺失，或无法证明影响边界时，按 `assets/incremental-validity-contract.json` 的 `CONSERVATIVE_FULL_PROFILE` 策略重新验证选定 Profile 全部 Criterion。

## Review orchestration simplification

新任务使用 `Phase Gate + Core Review Bundle + Risk-triggered Review + Delta Review`：

- `assets/review-contract.json#profiles` 只保存 17 个 active Core Profile；
- `riskReviewTemplates` 保存 8 个可复用风险能力，未命中风险时只记录 applicability decision，不创建 `NOT_APPLICABLE` ReviewResult；
- `scripts/review_session.py plan` 一次冻结 phase/revision/risk/impact，并生成多个独立 Axis；每个 Axis 仍产生独立 exact-revision ReviewResult；
- `scripts/impact_analysis.py` 决定 affected criterion；unaffected criterion 不继承旧 PASSED，而是 recapture/rebind 当前 revision Evidence 后形成新 ReviewResult；
- `tdd` 不再是任务一级 phase，而是 `development` 的 `PRE_IMPLEMENTATION` / `POST_IMPLEMENTATION` 两个 TDD Axis；旧 Profile 不参与当前任务。
