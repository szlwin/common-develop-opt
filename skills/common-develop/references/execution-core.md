# 执行核心规范

## 1. 目的

这份紧凑启动 Reference 只包含不可协商的执行规则。当前 Agent、阶段、模式、允许命令、语义 ID 和任务事实由 `scripts/ai_context.py resolve` 返回；不要从说明文字中重新构造这些事实。

## 2. 硬门禁

- 将机器契约和当前项目事实视为权威；HTML、缓存、示例和会话摘要不是事实来源。
- 阶段、attempt、Review、Evidence、迁移、归档和工作模式状态变更必须使用声明的脚本命令。
- 不得自行批准阻断性 Review，不得复用过期 revision 证据，也不得在 P0/P1 finding 未解决时推进。
- 除非明确授权破坏性命令，否则保留任务目录、Git 历史和当前格式记录。

## 3. 按需读取的详细内容

只有在需要详细矩阵或边界说明时，才加载 `runtime-contract`、`platform-core`、`scope-core` 或 `semantic-core`。当前紧凑决策切片使用 AI Context 中的 `executionCore`。
