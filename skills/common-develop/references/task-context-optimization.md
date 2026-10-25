# Task 验证与最小 AI 上下文

本规范定义 `task/{TARGET_ID}` 的确定性验证、当前 manifest 生成和 Token 预算。机器事实归属于 canonical 文件；`cache/current_manifest.json` 是 Git 忽略、可删除重建的派生数据。

## 1. 确定性验证

正常读取前执行：

```bash
python3 scripts/task_verify.py verify \
  -g {CURRENT_AGENT} \
  --doc-root project_doc \
  --version {VERSION} \
  --target {TARGET_ID} \
  --profile task-health \
  --level summary \
  --json
```

验证器负责：

- 文件、JSON/JSONL、Schema 和稳定 ID；
- Evidence、紧凑 Review 事件、Snapshot 和 command-result digest；
- Acceptance、Traceability、Risk、Issue 和 Long Task 状态机；
- 当前阶段、前置、下一阶段和 Reviewer 门禁。

AI 不得为这些确定性检查默认读取完整 Registry 或 SHA-256 对象。只有 `FAILED` 时使用 `--level failures`，再按 `recommendedReads` 读取最小诊断范围。`--level full` 仅用于 Skill 调试、审计和归档。

## 2. 输入绑定与缓存

验证结果必须包含 `inputDigest`、`validatorVersion` 和分项状态。输入 digest 覆盖 canonical task 文件、Evidence/Review/Snapshot 对象以及版本 `work.md/work_record.md`，但排除全部 `cache/` 派生数据。

缓存路径：

```text
task/{TARGET_ID}/cache/verification/{PROFILE}/{INPUT_DIGEST}.json
```

输入变化后旧缓存自动失效；每个 Profile 只保留当前 digest 的派生缓存，写入新缓存时删除该 Profile 的旧缓存文件，避免生命周期事件持续放大 task 目录。缓存不是正式 Evidence；归档需要将最终验证结果另行登记。

## 3. 派生投影

生成：

```bash
python3 scripts/task_layout.py sync \
  -g ProjectManagerAgent \
  --doc-root project_doc \
  --version {VERSION} \
  --target {TARGET_ID} \
  --json
```

生成内容：

```text
cache/current_manifest.json
  ├─ state
  ├─ activeRevisions
  ├─ blockers
  └─ traceabilitySummary
```

该文件必须符合 `assets/current-manifest.schema.json`，包含 `kind=CURRENT_MANIFEST`、`authority=DERIVED` 和 `sourceInputDigest`。canonical 文件仍是唯一事实源；`cache/.gitignore` 之外的 cache 文件不得被 Git 跟踪，也不得注册为业务 Evidence。

## 4. Agent 上下文

已创建或恢复的任务默认使用 `wk begin`，消费其 context.contextRouting、当前决策和动作计划；仅询问当前位置才使用 `wk status --summary`。下列 `ai_context resolve` 用于缺失资料时的定向补读或诊断，不是 begin 之后的必跑步骤；脚本路径应绑定当前安装包。

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

输出包括：

- `taskVerification`：紧凑验证结果；
- `contextView`：当前 Agent/Phase 的 revision、前置、Issue、动作和语义输入；
- `resources`：按固定资源、工作模式、精确路由和提示条件顺序选择的 Reference；
- `omittedResources`：因动态资源数、总资源数或目标 token 预算未选择的资源及原因；
- `routingLimits/budgetHeadroom`：Reference 预算上限与仍可用于任务事实的空间；
- `contextBudget`：AI Context 字段裁剪后的估算 Token 与被省略字段；
- `lazyObjectPolicy`：Evidence/Review/Snapshot 正文默认不加载。

`ai_context.py` 的 Reference 目标、任务事实预留、固定资源和基础路由预算只读取 materialized `context-routing.json#routingPolicy`；本 Reference 不复制数值。解析器按优先顺序在加入资源前检查预算；不能加入的资源必须出现在 `omittedResources`，不得静默超限或自动全文补读。

需要连续读取时增加 `--since-checkpoint`。若 authoritative 输入未变化，返回 `contextView.status=UNCHANGED`，调用方复用上一份上下文。

### 5.1 路由顺序与严格预算

Context Routing 按以下顺序确定资源：固定资源 → 精确任务路由 → 工作模式资源 → 全量命中的 Prompt Condition。`maxMatchedPromptConditions` 是兼容性/提示字段，不得作为 resolver 的硬截断；关键词只有在条件显式声明时才使用 `substring`，默认使用稳定 ID、短语或 token 边界匹配。

每个资源带有 `mandatory`、`required-if-triggered`、`helpful` 或 `example-only` 级别。显式模式的排除规则先执行，其余命中的 `required-if-triggered` 资源按 `mandatory` 处理，包括去重后的资源。任何省略的资源或条件都必须返回稳定 ID、来源和原因。`best_effort` 可以在目标预算处停止可选资源；两种预算策略下，必要资源因数量或硬 Token 上限被省略时都返回 `CONTEXT_INSUFFICIENT` 和 `insufficientMandatoryResources`。调用方必须先补读这些资源，不能把阶段可执行状态视为上下文已经齐备。`--budget-strategy strict` 还检查调用方指定的预算，并保留完成门禁和路由契约摘要。

revision/profile 上下文缓存同时绑定 `ai/source-map.json` 摘要；读取边界、权威入口或冲突顺序发生变化，以及该文件新增或删除时，旧缓存失效。缓存只保存稳定上下文，不保存 Evidence、Review 或 Acceptance 的最终结论。

恢复缓存必须校验 schema/version、输入 digest、事件 digest、validator 版本和 Contract 映射；不兼容缓存必须拒绝并从 canonical events 重建。

## 5. 读取规则

正常流程：

```text
task_verify summary
→ ai_context compact view
→ 读取当前语义 Artifact
→ 按 EVD/REV ID 懒加载具体对象
```

跨会话恢复时，先执行 `wk status`、`wk next --explain`、`wk context` 并读取当前 revision 文档；只有需要紧凑恢复摘要时，才条件读取 `cache/current_manifest.json#/traceabilitySummary`。`cache/current_impact.json` 只由 `wk next` 间接消费，不作为直接读取输入。需要判断某一条追踪或验收映射时，再执行：

```bash
python3 scripts/wk.py context -g {CURRENT_AGENT} --task-dir {TASK_DIR} --trace-id {TRACE_ID}
python3 scripts/wk.py context -g {CURRENT_AGENT} --task-dir {TASK_DIR} --acceptance-id {ACCEPTANCE_ID}
```

`cache/current_manifest.json` 是可删除重建的派生缓存，仅按条件读取；完整 `traceability.json`、Evidence、Review 和历史文件均为按需读取。摘要包含 canonical 源文件 digest，digest 变化会使 current manifest 变旧，但不会产生第二份追踪事实。

### 5.1 运行文件定向读取矩阵

以下规则约束 Agent 的上下文读取，不限制验证器、reducer 或归档脚本为了确定性校验而在进程内扫描 canonical 文件：

| 文件 | 权威/性质 | Agent 默认入口 | 允许的选择器 | 全文读取 |
|---|---|---|---|---|
| `task.json` | canonical 身份与 workflow binding | `wk status --summary`、`wk context` | task/version/target 身份字段 | 仅初始化、校验、审计或用户明确要求 |
| `cache/current_manifest.json` | DERIVED，可删除重建 | `wk status`、`wk next`、`wk context` | `/state`、`/activeRevisions`、`/blockers`、`/traceabilitySummary` | 仅缓存诊断；不得作为 authority |
| `traceability.json` | canonical 追踪事实 | `wk context --trace-id` 或 `--acceptance-id` | 稳定 Trace/Acceptance ID | 仅完整审计或失败诊断 |
| `acceptance.json` | canonical Acceptance Assertion | `acceptance.py verify --assertion-id`、`manual_review.py show --assertion-id` | assertion/acceptance ID | 仅编译、validate、归档或审计 |
| `evidence/registry.jsonl` | Evidence 索引 | `evidence.py query`、`evidence.py show --evidence-id` | phase、revision、type、scope、status、limit、Evidence ID | 默认禁止 |
| `evidence/reviews.jsonl` | Review 历史事件 | `manual_review.py show --review-result-ref` 或 `--assertion-id` | Review/Assertion ID | 默认禁止；历史审计须显式指定 |
| `development_task_plan.json` | canonical 大需求开发计划 | `task_plan.py status/query` | `task_id`、`field`、`max_bytes` | 仅显式审计或校验可全文读取 |

查询结果应保留稳定 ID、source path、revision、digest/freshness 和省略数量。不得用 `cat`、全文 `read_text()`、无界 `jq` 或自行扫描日志替代查询入口；需要详细对象时先用摘要中的引用再按 ID 展开。`task_verify --level summary`、完成门禁和归档校验可以在脚本内部读取完整输入，但对 Agent 只返回紧凑摘要和 `recommendedReads`。

禁止：

- 默认读取完整 `registry.jsonl`、全部 `reviews.jsonl` 事件或所有 Evidence Object；
- 用 AI 自行重复计算 digest、ID 完整性和状态机门禁；
- 将派生投影作为 canonical 事实源；
- 为降低 Token 使用不透明短字段或 YAML 状态机。
## 6. Reference 章节选择

`context-routing.json` 中 `readStrategy=relevant_sections` 的资源不得直接全文读取。`ai_context.py resolve` 会为这类资源返回：

- `sectionIndexCommand`：列出忽略代码围栏后生成的稳定二级/三级章节 ID；
- `readCommand`：按明确章节 ID 读取；
- `queryReadCommand`：按主题最多读取 3 个匹配章节。

示例：

```bash
python3 scripts/reference_reader.py index \
  -g DesignAgent --resource design-core --json
python3 scripts/reference_reader.py read \
  -g DesignAgent --resource design-core \
  --section 1-设计核心
```

`readStrategy=full` 的小型 Reference 可直接读取；大型 Reference 只有在失败诊断、Skill 审计或用户明确要求全文时才显式使用 `--full`。章节索引从当前 Markdown 标题实时生成，不是第二套规则。



## 7. 物理文件预算

`task_layout.py sync` 的稳定输出固定为一个忽略的物理文件：

```text
cache/current_manifest.json
```

读取某 Agent View：

```bash
python3 scripts/task_layout.py view   -g {CURRENT_AGENT}   --task-dir project_doc/version/{VERSION}/task/{TARGET_ID}   --view-agent {TARGET_AGENT}   --json
```

禁止为每个 Agent 重新创建独立 JSON。当前 manifest 是唯一派生任务视图，不需要注册为业务 Evidence。
