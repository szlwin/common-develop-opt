# 语义契约最小摘要

## 1. 唯一来源
稳定术语、ID 命名空间、语义分离和废弃别名以 `assets/semantic-contract.json` 为唯一机器来源。本摘要只提醒 AI 使用稳定 ID，不复制完整词典。

## 2. 使用规则
- 需求、功能、业务规则、验收、追踪、流程、跨模块步骤、Evidence、Review 和 Finding 使用各自命名空间，不能混用。
- 遇到陌生别名或疑似旧术语时，不猜测映射；按需加载 `semantic-contract` 并执行语义审计。
- 新增术语先确认归属和稳定 ID，再写入项目事实；示例名称不得成为当前项目事实。


## 3. Canonical artifact 引用
- 结构化事实之间必须引用可唯一定位的 artifact：Markdown fragment 绑定 heading 或显式 anchor，JSON/YAML fragment 使用 RFC 6901 JSON Pointer，JSONL 使用记录稳定 ID。
- `id`、`stableId`、`stable_id` 之外的普通文本不参与稳定 ID 索引；同一稳定 ID 出现多次时引用必须拒绝，不能按文件顺序猜测。
- 所有相对路径均相对于声明引用的文档目录解析，并且必须位于项目根内。公开治理输出遵循严格投影模型，内部推荐诊断字段不属于对外 Schema。
