# 编排核心规范

## 1. 最小编排闭环

读取当前任务事实，识别下一个获授权的 Owner 或 Reviewer，发出一个范围明确的任务，验证返回的 revision 和 Evidence，然后通过脚本推进或重新打开阶段。

## 2. 不变量

- 除非明确计划另有规定，否则同时只有一个任务和一个目标处于活动状态。
- Owner 产出产物；独立 Reviewer 返回独立结论。
- ProjectManagerAgent 负责协调转换，但不伪造缺失的 Owner 或 Reviewer 输出。
- 阻断的分派必须记录阻断原因和下一位负责 Agent，不得猜测结果。

## 3. Dispatch

### 分派

### 3.1 Assignment package

#### 分派包

每次分派都必须标识目标、阶段、iteration、当前输入 revision、预期输出、允许文件、验证命令和必需 Evidence。

### 3.2 Dispatch rule

#### 分派规则

只能分派给 Runtime Contract 声明的 Agent。不得在一次分派中合并 Owner 实现和独立 Review。

## 4. Independent Review

### 独立 Review

### 4.1 Review isolation

#### Review 隔离

Reviewer 读取已发布 revision 和已登记 Evidence，记录独立结果，并且不修改被审查产物。`wk next` 返回 `ISOLATED_REVIEW_SESSION_REQUIRED` 时，协调者必须创建真实的子智能体/隔离任务上下文或分派独立人工 Reviewer；把 Owner 会话的 Agent 参数切换为 Reviewer 只满足 CLI 权限标识，不满足上下文独立性。

### 4.2 Decision handling

#### 决策处理

`PASSED` 只满足被引用的 Profile 和 revision。`NEEDS_CHANGES` 或 `BLOCKED` 通过正式返工路径将控制权交还 Owner。

## 5. Retry and blocked work

### 重试与阻断工作

### 5.1 Retry boundary

#### 重试边界

失败的执行 attempt 会增加 attempt 历史。有效的业务 iteration 或上游 revision 变更会创建新的 iteration，不得消耗失败重试次数。

### 5.2 Blocked work

#### 阻断工作

记录失败命令、观察结果、Evidence 和下一步动作。绝不能直接编辑状态字段来将 attempt 标记为通过。

## 6. Handoff and recovery

### 交接与恢复

### 6.1 Handoff package

#### 交接包

交接包包含当前阶段、目标、活动 iteration 和 attempt、冻结的输入 revision、最新 Evidence 和 Review 引用、阻断项以及下一个合法命令。

### 6.2 Recovery

#### 恢复

从机器状态和 Git 事实恢复。会话摘要只能用于定位，不能替代任务记录。

## 7. Recording

### 记录

### 7.1 Required record

#### 必需记录

使用当前 Work Record 格式记录有意义的分派、命令结果、状态转换、阻断项和决策。

### 7.2 Storage rule

#### 存储规则

在 Work Record 中保存标识符和引用，不要复制完整的 Evidence、Review 或 task plan 正文。

## 8. 单 Skill 与内部能力包

`common-develop` 保持一个 Skill 入口、一个授权边界和一个原子发布单元。内部能力只按 `assets/capability-pack-contract.json` 分为：

- `document-facts`：产生或校验当前需求、设计、模型、流程与归档事实；不得修改生命周期事件。
- `task-runtime`：唯一拥有 event ledger、reducer、attempt、StageOutcome、STALE 与阶段转换。
- `review-verification`：拥有 Evidence、Review、Risk、Acceptance 与完成门禁结果；不得直接推进阶段。

跨包只传递 `assets/capability-result.schema.json` 声明的有界结果。`wk.py` 负责组合能力包，`ai_context.py` 输出当前选择；任何能力包都不得声明独立 Skill、独立版本或复制 Runtime/Review/Routing Contract。

## 9. 有界授权批处理

`wk confirm --until` 只记录 ProjectManagerAgent 对当前任务的一段有限授权意图，
不把多个生命周期门禁合并成一个可绕过的超级命令。调用流程为：先用 `--preview`
查看从当前阶段到目标阶段的 `atomicActions`，确认范围与理由后再用 `--apply` 写入
事件账本。目标可以是当前阶段（表示确认当前门禁范围），但不能倒退到更早阶段。

成功的 apply 追加 `AUTHORIZATION_BATCH_CONFIRMED` 事件，其中包含当前阶段、目标
阶段、理由、授权 Agent、原子动作和 request fingerprint，并绑定当前任务的
`taskId`、当前阶段 `iterationId`、当前阶段 `revision` 与绝对 `taskDir`；事件由
task-runtime reducer 投影到 `authorization_events`。缺少绑定字段的旧事件不得重新
消费；相同 fingerprint 的重复请求必须幂等返回 `NO_OP`。

只有正常 `advance-phase` 在既有 Gate 通过后，才按当前有效 Workflow 的**可执行阶段**
逐步消费授权，并追加 `AUTHORIZATION_BATCH_CONSUMED`。如果 `-min`、`-ws` 或历史
工作流策略合法跳过了中间阶段，消费链直接从最近的前一个可执行阶段连接到下一个
可执行阶段；跳过阶段既不要求 Gate，也不产生 `AUTHORIZATION_BATCH_CONSUMED`。
消费结果可从命令输出的 `authorization.status` 读取：成功为 `CONSUMED`，没有可用
授权为 `NOT_USED`。`PHASE_STALE`、`reopen-phase`、账本缺失/损坏或上下文绑定
变化都会使授权失效；授权消费与状态事件处于同一事务，失败时一起回滚。

`wk confirm` 的 `atomicActions` 会保留范围内的跳过阶段，但将其标为
`NOT_APPLICABLE`，命令置空，并附带 `PHASE_SKIPPED_BY_ACTIVE_WORKFLOW`。这只是
对授权范围的可审计说明，不是对跳过阶段的授权、通过或执行。

该事件代表授权意图，不代表任何阶段已通过：

- 不推进阶段，不创建或完成 Attempt；
- 不写 Evidence，不执行 Review，不创建 checkpoint；
- 不修改 Gate、Owner/Reviewer、权限或既有状态；
- 后续仍须逐个调用原生命周期命令，并由原有门禁验证真实产物和证据。

因此，批处理降低的是确认往返，不是治理强度。`wk status`、`wk next` 和 reducer
仍以运行时事件与真实验证结果为准；授权账本缺失、损坏或不一致时应保持失败关闭。
