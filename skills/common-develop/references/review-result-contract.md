# Review 结果最小契约

当前 Full Record、Compact Registry 和输出 Schema 的版本统一为 `1`，版本事实由
`assets/runtime-contract.json#reviewRegistrySchemaVersion` 声明，并由
`assets/long-task/record-contract.json` 对齐。三类对象仍保持不同边界：Full Record
是读取时的完整对象，Compact Registry 是 `reviews.jsonl` 的存储对象，Output Schema
是 Agent/接口输出约束。

读取到 v3/v4 历史 Compact 记录时必须拒绝继续写入或静默混用，并提示执行一次有
审计记录的显式迁移；迁移前保留原始文件及其 digest，迁移后新 registry 只能写入
v1。当前工作区没有待迁移的 Review Registry。

## 1. Criterion 状态
Criterion 使用契约允许的状态。`FAILED` 必须引用 Finding；`BLOCKED` 与 `NOT_APPLICABLE` 必须给出可核对原因。不得用总评替代逐项结果。

## 2. 总结论
总结果由 Criterion 状态和开放 Finding 推导，不由 Reviewer 主观覆盖。结果必须绑定 Review Phase、Reviewer Agent、候选 revision、证据引用和提交时间。

## 3. 模式差异
`wk -ar` 的骨架 Review 和最终 Code Review 是两个独立 revision 边界；最小模式只能移除运行契约明确排除的 Reviewer，不能降低剩余 Criterion 的证据标准。
