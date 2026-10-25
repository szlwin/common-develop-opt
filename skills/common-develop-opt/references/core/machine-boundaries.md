# 机器字段与状态边界

本文件只定义读取边界，不重复机器 Schema。

通用输入、范围、revision 和停止条件见 [Skill 入口](../../SKILL.md)。主题文档沿用该边界，不重复列出禁止项。

- 身份、引用和摘要：读取 `assets/artifact-identity.schema.json`、`assets/documents/artifact-relation.schema.json` 及对应脚本 `--help`。
- Runtime、阶段、工作模式和权限：读取 `assets/runtime/runtime-contract.json`。
- Review Profile、Criterion、Evidence Policy 和结论：读取 `assets/review/review-contract.json`、`assets/review/review-result.schema.json` 和 `assets/review/review-session.schema.json`。
- Evidence、Assertion、StageOutcome：读取 `assets/review/evidence.schema.json`、`assets/review/acceptance-assertion.schema.json` 和 `assets/review/stage-result.schema.json`。
- 任务计划、请求分类和存储：读取对应 `assets/*schema.json` 或 Contract。
- 参数、命令和组合门禁：运行实际脚本或项目入口的 `--help`。

Reference 只解释决策、边界和示例；不要在正文重新定义必填字段、状态枚举、默认值或完整 JSON/YAML 结构。示例必须标注为非权威。

维护时，`check_skill.py --json` 的 `details.assetMetadata` 返回全部 JSON Asset 的元数据观察；单个资源可通过内部 API `schema_protocol.resource_metadata(logicalPath)` 查询。返回值带 target、字段来源和声明状态；没有声明的字段为 `NOT_DECLARED`，不补造 owner 或版本。Schema 顶层版本与 `properties.schemaVersion/schema_version` 的实例约束分开报告。

目录映射、派生索引和完整性清单的权威边界由 [Catalog Schema](../../assets/package/resource-catalog.schema.json) 定义；资源删除记录与历史指针见 [兼容边界](compatibility-boundary.md)。
