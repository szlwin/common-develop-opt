# 独立 Review 核心

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

> 本文件是 Review Core Reference。默认通过 `relevant_sections` 按主题章节读取；不要无选择地整篇加载。

## Review 执行

### 1. Profile 选择
Reviewer 通过 `scripts/review_contract.py show` 获取当前 `<phase>:<reviewerAgent>` Profile。Criterion、Evidence Policy 和结论推导由脚本内部从 `assets/review/review-contract.json` 查询；Agent 不直接打开该 JSON，Reference 不复制机器矩阵。

### 2. 独立性
Reviewer 必须读取与 Owner 相同的候选 revision，并使用独立上下文验证。宿主具有子智能体、隔离 task 或等价独立会话能力时，ProjectManagerAgent 必须实际分派该上下文；仅在 Owner 的同一上下文中改用 Reviewer `-g` 不构成独立 Review。宿主确实不具备隔离执行能力时，只允许明确的独立人工 Reviewer，并在 Review limitations 中记录该执行边界，不能静默降级。Owner 不得关闭自己修复的阻塞 Finding；需要返修时生成新 revision，再由原 Reviewer 或授权独立 Reviewer 复核。

### 2.1 子智能体分派、握手与等待

协调者不能依赖宿主是否把子智能体 Prompt 展示在 UI 中。创建 Reviewer 前后必须在主会话主动输出一条可见的分派回执，至少包含 Reviewer、phase、artifact revision、Assertion/Profile、只读/允许写入范围和停止条件；子智能体 Prompt 必须携带同一组字段。宿主支持进度消息时，Reviewer 先返回简短 `ACK` 再开始读取；宿主不支持流式子智能体消息时，由主会话明确说明“已分派，等待独立结果”，不能让用户从无输出推断执行状态。

等待和交互必须使用宿主原生的子智能体 `wait/status/send-input/interrupt` 能力。禁止为保持会话、等待 Reviewer 或轮询状态而执行 `sleep`、`echo true`、空循环、无输出 shell 或重复 no-op 命令；业务验证命令自身明确包含的 timeout/sleep 不受此限制。主会话在等待期间应先完成不冲突的协调工作；确实被结果阻断时只做有界原生等待，并在用户发来新消息时停止等待、先处理用户输入，再通过原生状态入口恢复。

多个 Reviewer 可以并行只读检查同一 revision，但 `manual_review.py submit`、Issue 生命周期和 `task_events.jsonl` 写入必须串行。主会话不得替子智能体编造中间进度，也不得用主会话重做同一 Review；超时、用量限制或子智能体失败必须返回真实状态和可恢复入口。

### 3. 执行顺序
确认输入 revision 和范围，逐 Criterion 读取证据，记录状态与 Claim，创建或引用 Finding，推导总结果并提交 Review Registry。开放 P0/P1、缺失必需证据或 revision 不一致必须阻断。

Review 上下文同时绑定当前文档、追踪引用和流程预览的内容摘要；准备草稿后输入变化必须重新准备，不能复用仅 revision 标签相同的旧草稿。当前 revision 的 Snapshot Evidence 必须对应其当前源内容。修订已发布模型或 Flow 时生成新 revision，原 Review 只证明原快照；检查不会自动重写证据或将下游返工产物判为通过。

## 4. 设计/Review 问题的详细会话回答

当用户要求解释一个设计或 Review finding，或给出一条类似 `DEC-*` 的决策记录时，Reviewer/Owner 必须先用会话给出可核对的详细说明，再写入或修改 Finding。回答至少覆盖：

- 当前决策的精确定义、适用范围和不适用范围；
- 相关对象的概念拆分，尤其是编译事实、运行 generation、owner/binding、scope/session/handle、结构相等和值相等；
- 正常组合与错误组合的调用/数据流，以及错误应在副作用前还是副作用后暴露；
- 为什么现有检查不能证明安全，不能用 `equals()`、名称、字符串 ID、包可见性或“同 JVM”替代 owner identity；
- 推荐的唯一绑定边界、不可伪造身份来源和生命周期失效规则；
- 可执行不变量、拒绝条件、影响范围和具体验证场景；
- 尚未确认的前提、候选取舍和需要用户确认的事项。

解释必须引用当前 revision 的代码、设计、契约或 Evidence 锚点；没有直接证据时标记为 `UNVERIFIED`，不得把推测写成事实。用户确认后再生成新的决策 revision 或关闭 finding，不能把聊天中的临时回答当成最终 Review 结论。

## Finding 生命周期

### 1. 创建
失败 Criterion 创建稳定 Finding，记录严重度、置信度、受影响产物、当前 revision、证据、责任 Agent 和期望修复结果。P0/P1 默认阻断阶段推进。

### 2. 修复与复核
Owner 在新 revision 中修复并引用 Finding；独立 Reviewer 重新验证受影响范围和必要回归。只有复核证据当前且结论满足契约时，Finding 才能关闭。

### 3. 延期与知识沉淀
允许延期的 P2 必须记录责任人、风险和后续入口。可复用经验进入 Review Knowledge 时应去除项目私有事实、标记适用范围和失效条件；知识库不是当前 Review 结论来源。
