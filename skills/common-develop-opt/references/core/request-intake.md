# wk -b / wk -f 请求入口

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

## 0. Request Intake 快速规则 {#request-intake-quick-rules}

将 `wk -b/-f` 输入解析为稳定目标 ID、输入类型、分类、工作流 Profile、相关事实、影响评估和阻断原因。使用 Request Intake JSON 作为机器事实；如果分类为 `NEEDS_CONFIRMATION`，则阻断开发。

本文件是请求入口的唯一可读规则；机器事实源为 `assets/development/request-intake.schema.json` 和 `assets/runtime/runtime-contract.json#workflowProfiles`。

## 1. 分类规则 {#request-intake-classification-rules}

`wk -b` 后的非 ID 值始终视为 Bug 描述，并进入 Bug 工作流。根据项目事实，将 `wk -f` 后的非 ID 值分类为新需求、需求变更、simple optimization 或需要确认；不要只根据措辞分类。

## wk -b

- 参数符合 BUG_ID 时沿用该 ID。
- 参数不是 BUG_ID 时，完整文本就是 BUG 描述，并生成稳定 `BUG-DESC-{HASH}`。
- `wk -b` 的描述不得重新分类为 FEATURE、需求或简单优化，始终进入 `BUG_FLOW`。

## wk -f

FEATURE_ID 沿用原 ID。描述必须明确分类为 `NEW_REQUIREMENT`、`REQUIREMENT_CHANGE` 或 `SIMPLE_OPTIMIZATION`；事实不足时写入 `NEEDS_CONFIRMATION` 并阻断开发，禁止按关键词猜测。

`SIMPLE_OPTIMIZATION` 只允许展示层微调，必须关联已有 FEATURE，且业务逻辑、数据模型、API、权限、流程、持久化、外部副作用、迁移和依赖九项影响全部为 `NONE`。其流程为 `development → code_review → testing → completion_verification`，跳过阶段仍写入 `SKIPPED` StageOutcome。

<!-- common-develop-invariant: GIT_PUSH_REQUIRES_CHECKPOINT -->
简单优化与 `wk -min` 不兼容；可与 `wk -ar`、`wk -auto`、`wk -gc` 和依赖 `-gc` 的 `wk -gp` 组合，但不得绕过两轮 Review、测试和完成验证。

`wk -l -b/-f` 只接受已经解析完成的 ID，不执行请求分类。

## 2. Simple Optimization {#request-intake-simple-optimization}

请求必须关联已有 Feature，并且对业务逻辑、数据、API、权限、工作流、持久化、副作用、迁移和依赖声明的所有影响都必须为 `NONE`。执行 development、Code Review、Testing 和 Completion Verification；Minimal mode 不兼容，启用时 architecture Review mode 仍保持活动。

## 3. Request 重新分类 {#request-intake-reclassification}

当实现发现当前 Intake 未表示的行为、数据、API、权限、工作流、持久化、副作用、迁移或依赖变更时，必须重新分类。保留之前的 Intake revision 和开发历史，重新打开需求确认，使受影响的下游结果失效，并且不消耗失败 attempt。跨阶段重分类前必须先用 `wk confirm --reopen-from requirement_confirmation` 记录用户确认；此时 `request_intake.py reclassify --confirmation-ref` 必须消费该既有引用，不得自行生成或伪装 `wk.confirm` 授权事件。同阶段重分类不需要该参数，若提供则拒绝，避免把未校验字符串误认为有效确认。确认写入与整个重分类事务均使用统一 task writer lock。事务失败时必须同时恢复 Intake、状态、事件账本和本次历史 payload；只有 `request_intake_history/` 由本事务创建且回滚后为空时才删除该目录，既有目录及其中的其他历史记录必须保留。

## 4. 面向非专家的沟通协议

请求入口先读取项目和当前版本事实，再向用户解释选择；不要先抛出模式、Agent 或契约术语让用户盲选。需要用户确认时，固定按以下顺序输出：

```text
当前事实：<已确认的项目、版本、目标和影响>
实际风险：<会被保护的事实、可能的副作用和阻断条件>
建议选择：<推荐的流程/Profile/模式>
为什么：<根据事实给出的简短理由>
会发生什么：<将创建、校验或修改的内容>
不会改变什么：<保留的历史、权限和未涉及的状态>
下一步命令：<可执行的 preview 或查询命令>
```

先给一个推荐并说明可覆盖点；只有在候选方案确实不可由当前事实区分时才提问。创建文件前说明创建什么、保留什么、解决什么，并从最小可用切片开始。`wk start` 返回的 `governanceProfile`/`decisionGuide` 是严格公共投影和 advisory 建议，不包含图影响、输入 digest 或 override 内部明细，也不会替用户改变实际工作模式。
完整内部推荐快照同时保存在 `request_intake.json.governanceRecommendation`，任务状态只保存
其 Profile、输入 digest 和 override 标志；因此推荐可回查、可验证，但不会成为运行时
状态机或 Evidence 的替代权威。
