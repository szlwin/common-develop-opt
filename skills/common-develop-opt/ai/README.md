# common-develop-opt AI 入口

本目录只定义启动顺序、读取预算和权威来源指针；具体规则按主题 Reference 读取。

## 启动顺序

1. 识别新建、恢复、执行、Review 失败、迁移或维护诊断状态。
2. 新建先用 `wk start`，恢复或执行先用 `wk begin`；以返回的 actionPlan、Owner、inputs、stopWhen 和 resources 为准。
3. 只有返回上下文不足时，才读取 `source-map.json` 中的一个主主题和必要的一个辅助主题。
4. 再通过脚本 `--help` 或项目声明确认参数，不猜命令；先消费必需输入和停止条件，再决定是否允许写入。
5. 结果绑定当前 revision；新 revision 需要重新验证受影响的设计、测试和 Review。

## 主题路由

- 新任务/恢复：`references/getting-started.md`
- 需求/设计：`references/requirement-and-design.md`
- 实施/编码：`references/implementation.md`
- TDD/测试：`references/tdd-and-testing.md`
- Agent/Review：`references/collaboration.md`
- Git/交付/恢复：`references/git-and-delivery.md`

默认只读 1～3 个主题文档。Evidence、Review、历史和大型 Contract 按 ID、章节或查询结果懒加载；不得全文加载。

## 事实优先级

用户当前明确指令和项目真实事实 > 当前 revision 的已确认事实 > 正式机器 Contract > 命中的主题 Reference > 兼容文档。无法消解的冲突登记为阻断或待决策，不自行选低优先级规则。

## 代码实现边界

当前任务投影为 `version/{VERSION}/task/{TARGET_ID}/cache/current_manifest.json`；它可由事件重建，不替代事件、Evidence 或 Review 的权威记录。

普通开发阶段消费主题规则、项目事实、CLI/query 输出和测试结果，不默认读取 Skill 内部脚本源码。只有维护本 Skill、诊断 CLI、实现 Review 或用户明确要求时才读取脚本实现。
