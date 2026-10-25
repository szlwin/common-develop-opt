# common-develop AI 入口

本目录只提供**启动顺序和权威来源指针**，不复制生命周期、状态、Agent、Reviewer、工作模式、存储版本或脚本权限规则。
## 四种入口
启动协议以 `SKILL.md` 第 1 节为准：

- 新建任务：ProjectManagerAgent 使用 `wk start` 预览，补齐必要分类/范围决策后以同一参数 apply；返回 taskDir 后调用 begin。
- 恢复任务：`wk begin` 返回新鲜决策、最小上下文与 actionPlan；Owner 按需使用 begin --apply 恢复 attempt。仅查询当前位置才用 status --summary。
- 普通执行：消费 actionPlan 的 requiredAgent、executionContext、requiredInputs；结果由 record-result 登记，ProjectManagerAgent 使用 progressCommand/planDigest 推进，finish 汇总项目声明验证。无需固定重复 ai_context/status/next/context。
- 维护诊断：按 repairPlan 或缺失字段调用 help、定向 query 或 `scripts/ai_context.py resolve`。只在修改 Skill 源时运行自身 quality_gate、清理与包清单重写。

先使用 begin 的 context.contextRouting（包括当前请求命中的资源、omittedResources 和预算）；Reference 按 readStrategy 读取 1～3 个相关章节。任务事实按 ID、阶段与 revision 查询，Evidence/Review/历史按引用懒加载；`cache/current_manifest.json` 只按条件读取。BLOCKED 时按修复计划处理，不绕过原门禁。冲突顺序只读取 `ai/source-map.json`。

## Skill 实现源码边界

普通项目需求、设计、计划、开发、Review 和测试阶段只消费 `wk begin` 或按需 `ai_context resolve` 返回的机器上下文、命中的 Reference、项目事实和 CLI/query 输出。`ai/source-map.json#skillImplementationReadPolicy` 是该边界的机器事实：默认禁止读取本 Skill 的 `scripts/**/*.py` 和 `scripts/_core/**`。`design_doc.py`、`case_doc.py` 等脚本只能作为已授权命令执行；`scripts/_core/document_utils.py` 是内部实现，普通阶段不得读取。需要发现参数时先运行 `wk.py help`、`cli_help.py` 或对应 CLI 的 `--help`/query。只有 Skill 维护、CLI 故障诊断、Skill 实现 Review 或用户明确要求，才可显式登记例外。

按需诊断示例（将 scripts 路径绑定到当前安装包）：

```bash
python3 scripts/ai_context.py resolve \
  -g {CURRENT_AGENT} \
  --task-type {TASK_TYPE} \
  --phase {PHASE} \
  --prompt "{CURRENT_REQUEST}" \
  --doc-root project_doc \
  --version {VERSION} \
  --target {TARGET_ID} \
  --token-budget 12000 \
  --json
```

## 输出字段与动作

| 输出字段 | AI 动作 |
|---|---|
| `executionCore` | 读取当前 Agent、阶段、工作模式、硬门禁与权威来源摘要 |
| `taskVerification` | 决定是否允许继续写入；失败时只做定向诊断 |
| `requestIntakeContext` | 读取分类、Workflow Profile、影响评估、阶段裁剪和推荐命令 |
| `completionGateSummary` | 先读取完成门禁摘要；只对失败项补读详细规则 |
| `contextView` | 读取当前 Agent/Phase 的 revision、阻塞项、下一动作和语义输入 |
| `resources` | 按 `readStrategy` 读取预算内的最小 Reference |
| `omittedResources` / `routingLimits` | 识别因预算或数量上限未选择的资源；仅按当前任务需要定向补读，不回退为全量加载 |
| `projectFactPaths` | 读取当前版本、目标和命中业务流程的真实项目事实 |
| `allowedCommands` / `taskContextCommands` | 只执行明确授权的命令 |
| `lazyObjectPolicy` | Evidence、Review、Snapshot 按 ID 读取 |
| `contextBudget` | 字段被裁剪时按保留引用补读，不回退为全文加载 |

## 权威入口

- `ai/source-map.json`：权威事实指针和冲突顺序；
- `assets/runtime-contract.json`：normalized v2 生命周期、状态、Agent、工作模式和权限源；
- `assets/review-contract.json`：normalized v2 Review criterion、证据策略和结论推导源；
- `context-routing.json`：normalized v2 任务类型、提示条件、Reference 和 token 预算源；
- `assets/semantic-contract.json`：当前术语、稳定 ID 和废弃别名；
- `assets/long-task/record-contract.json`：长任务记录字段；
- `assets/request-intake.schema.json`：请求分类事实结构；
- `scripts/wk.py`：请求创建、状态、下一步和 `help` 参数发现入口；
- `scripts/runtime_contract.py`：按命令、阶段、权限或 JSON Pointer 查询 Runtime Contract；
- `scripts/context_routing.py`：按任务、条件、资源或 JSON Pointer 查询 Context Routing；
- `scripts/review_contract.py`：按 Profile、Criterion 或 Evidence Policy 查询 Review Contract；
- `scripts/contract_normalization.py`：只在进程内把 normalized Contract 展开为运行时视图；不产生第二份持久 Contract。
- `scripts/task_verify.py`：确定性验证；
- `scripts/task_layout.py`：生成唯一的 `cache/current_manifest.json` 派生上下文；
- `scripts/reference_reader.py`：章节索引与选择性读取；

大型 JSON 不全文进入 AI 上下文；raw v2 可省略默认/可推导字段，根节点查询被禁止且单次返回受大小限制。专题规则不在本文件展开。任务类型、提示条件和工作模式命中哪些 Reference，只由 `context-routing.json` 决定。

## 文档结构约定
主题详细文档可以包含 `0. ...快速规则` section，作为低 token 的 AI 入口；同一事实域不再维护独立摘要文件。机器资源 ID 的分段 alias 只用于按需路由，正文、术语和门禁只维护在 `aliasOf` 指向的详细文档中。
