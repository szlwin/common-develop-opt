# 开发执行规范

## 0. 开发快速规则 {#develop-quick-rules}

开发只能从当前已批准输入 revision、活动实现任务、允许文件集合和适用 Review profile 开始。解析当前 `IMPL-DEC-*`，按 `REUSE → COMPATIBLE_EXTEND → MODIFY → CREATE` 执行，不得静默改变策略；实现最小完整纵向变更，登记决策来源、变更文件、兼容验证和命令为 Evidence，并将候选 revision 交给独立 Reviewer。

本文件定义生产实现、范围控制、代码质量和提交前验证。标准模式在 TDD 后进入开发；`wk -min` 模式跳过测试用例、TDD 和测试执行，但仍要求已通过的实施计划、真实 diff、编译/静态检查/构建证据和独立 Code Review。

## `wk -ar` 两阶段开发

启用 `wk -ar` 时，本文件的开发规则分别应用于架构代码骨架和具体实现两个顺序 iteration。骨架允许实现调用编排、主要判断和逻辑分支，但不得实现被调用的具体业务方法；第一轮 Review 通过后只能通过 `advance-development-step` 开启具体实现。完整边界、Review 和返工规则只读取 `architecture-review-development.md`。

## 1. 开发前检查

DevelopAgent 开始前必须确认：

- 任务包、允许文件和停止条件明确；
- 输入 requirement、business model、design、plan revisions 一致；标准模式还必须包含 test case revision，`wk -min` 时该项为“无”；
- 当前任务依赖已通过；
- 无开放 P0/P1；
- 标准模式已有有效 RED，或纯重构已有 GREEN 表征基线；`wk -min` 时不要求测试基线；
- 项目环境和验证命令可用；
- 相关 Review knowledge 已读取；
- 适用的 `dependency_impact.yaml` 和跨模块实现映射 revision 已冻结；
- 公共接口或关键分支任务已读取 `code-comments.md`。

发现不一致时停止并通知 ProjectManagerAgent，不在代码中猜测。

## 2. 范围控制

- 只修改任务包允许范围；
- 发现必须修改范围外文件时停止，补充影响分析和新任务；
- 不顺便重构无关模块；
- 不加入 spec 未要求的扩展点、配置、抽象或“以后可能用到”的能力；
- 先搜索现有实现、公共方法、框架内置和项目模式；
- 如现有基础阻碍本次行为，可安排独立结构任务，避免结构和行为混在同一不可审查步骤。

### 2.1 实现策略执行顺序

1. 从已批准 Design 和当前任务读取同一个 `IMPL-DEC-*`、策略、现有代码证据、公共逻辑 Owner 与兼容验证。
2. 打开设计声明的真实实现，确认职责、签名、权限、事务、副作用和异常事实未变化。
3. 严格按 `REUSE → COMPATIBLE_EXTEND → MODIFY → CREATE` 执行；不得因实现方便跳过前序选项。
4. 代码事实与设计不一致时停止，更新 Design revision 和任务输入，不自行改变策略。
5. GREEN 后按公共逻辑处置去重复；相同业务规则只保留一个 Owner，不把不同语义强行通用化。
6. 重跑任务声明的旧调用、新调用、业务规则和影响面验证；`wk -min` 改用声明的编译、静态检查、构建或人工差异核对，不伪造测试。

新增/修改代码、验证命令和 Review Evidence 必须关联当前 `IMPL-DEC-*`。抽取会跨允许范围时使用独立 `SEPARATE_REFACTOR` 任务；Owner、权限、事务、安全或公共契约存在歧义时停止请求决策。

## 3. RED→GREEN→REFACTOR

本节只适用于未使用 `wk -min` 的标准模式。最小模式不得编写测试代码或声称存在 RED/GREEN/REFACTOR 证据，直接按已确认需求、设计和实施计划实现最小变更。

### 3.1 开发前 RED/基线

RED 或纯重构 GREEN 表征基线由当前 `development` PRE_IMPLEMENTATION gate 产生并通过 TDDReviewAgent。DevelopAgent 开始时只复核引用与 revision，不得在已经编写生产实现后补造 RED。发现测试失效、错误或不足时，停止开发并通过 ProjectManagerAgent 重开 `development` 的 PRE_IMPLEMENTATION gate。

### 3.2 GREEN

- 写最小实现使当前测试通过；
- 不提前实现后续任务；
- 不为了测试暴露无业务意义的 public 方法；
- 不 Mock 被测对象内部方法；
- 立即运行指定测试并读取结果。

### 3.3 REFACTOR

- 在持续 GREEN 下改善命名、重复、职责和结构；
- 不改变已确认行为；
- 每次小步运行测试；
- 更新附近的架构/状态图注释，避免图和代码不一致；
- 重构后运行相关测试和质量门禁。

## 4. 模块与接口

优先设计深模块：用较小、清晰的接口隐藏较多行为。

接口不仅包含类型签名，还包括：

- 不变量；
- 调用顺序；
- 错误模式；
- 配置要求；
- 性能特征；
- 幂等和事务语义。

避免：

- 调用者必须理解大量内部细节；
- 数据到处被直接读取和拼装；
- 同一判断在多个文件重复；
- 过长消息链；
- 只做转发的中间层；
- 用 primitive 表达重要领域概念；
- 同一状态 switch 在多处重复。

## 5. 业务约束

- 权限、状态、数据归属和前置条件必须在正确边界验证；
- 状态、类型、类别和标志使用枚举或明确类型；
- 接口字符串长度不能超过 DB 字段；
- 关键操作记录可定位日志，不记录敏感明文；
- 事务、幂等、重试和并发按设计实现；
- 冗余字段有明确来源、更新时机和一致性规则；
- 数据结构变更追加迁移，不篡改已执行历史；
- 错误类型和返回符合设计，不吞异常或返回模糊成功；
- 删除、取消、退款、释放等行为严格执行影响策略，不凭外键默认级联；
- 跨模块步骤、事件、补偿和幂等与流程事实一致。


## 6. 代码注释

按 `code-comments.md` 执行：

- 对外接口和公共服务说明职责边界、前后置条件、失败、副作用、事务/幂等/并发和追踪 ID；
- 重要业务分支、补偿、兼容、算法和数据一致性逻辑解释 why、错误修改风险及影响点；
- 上述必要注释必须同时写稳定需求/规则/设计 ID 和可解析的来源文档路径、锚点；不得只写 ID、聊天引用、个人机器绝对路径或临时任务文件；
- 对 `COMPATIBLE_EXTEND`、`MODIFY`、`CREATE` 的公共接口、共享逻辑和非直观分支，必要注释还必须引用对应 `IMPL-DEC-*` 及其设计锚点；
- 需求或设计 revision 改变时同步核对并更新来源注释，代码与来源不一致必须阻断交付；
- 不逐行翻译代码，不提交被注释旧代码，不用注释替代命名、拆分或类型；
- TODO/FIXME 必须有 issue、owner 和清理条件；
- 行为变化时同步更新注释、关系图和附近状态/流程图。

## 7. 测试分层

本节只适用于标准模式。使用 `wk -min` 时不执行以下测试，也不创建替代测试任务。

按任务需要执行：

1. 快速隔离测试；
2. 数据访问集成测试；
3. 业务事务集成测试；
4. API/端到端行为测试；
5. 项目 Harness。

涉及 SQL、事务、约束或最终数据状态时，Mock 不能证明通过。环境缺失时标记 `SKIPPED/BLOCKED`，不回退到非测试环境。

## 8. 自测与非测试验证

标准模式每个任务完成前运行：

- 任务指定测试；
- 受影响模块测试；
- 静态检查/类型检查；
- 构建；
- 必要集成测试；
- Harness。

记录命令、时间、revision、退出码和关键结果。旧结果不能证明新改动。

`wk -min` 时只执行适用的编译、类型检查、静态分析和构建，不执行任何测试命令；若这些非测试验证也无法运行，必须记录 `PARTIAL/BLOCKED`，不得以 Code Review 替代可执行性证据。

## 9. Agent 结果包

DevelopAgent 输出：

```yaml
development_result:
  task_id: TASK-01
  input_revisions: {...}
  output_revision: CODE-R01@abc
  changed_files: [...]
  scope_deviations: []
  red_evidence: [...]
  green_evidence: [...]
  refactor_evidence: [...]
  validation:
    - command: ...
      exit_code: 0
      summary: ...
  open_risks: []
  next_agent: SpecComplianceReviewAgent
```

ProjectManagerAgent 必须核对实际 diff 和命令输出。

## 10. Review 修复

收到 finding 后：

- 先验证问题和当前 revision；
- 不清楚时向提出者询问具体证据；
- 按 P0→P1→P2 处理；
- 每项修复独立验证；
- 记录修复 revision；
- 交原 Reviewer 复核；
- 不自行关闭；
- finding 若改变需求或设计，停止并回上游。

## 11. BUG 修复

BUG 使用 `debugging.md`：

- 建立反馈环；
- 复现和最小化；
- 单假设验证；
- 修复根因；
- 回归测试 RED→GREEN；
- 检查同类代码；
- 更新 review knowledge 候选。

禁止直接在无法复现时提交猜测修复。

## 12. 完成条件

- 任务范围内行为实现；
- 标准模式具有上一 TDD iteration 的真实 RED/基线，以及当前 development iteration 的 GREEN/REFACTOR 证据；`wk -min` 时明确记录不适用；
- 标准模式自测和必要集成测试来自当前 revision；`wk -min` 时至少有当前 revision 的编译/静态检查/构建证据；
- 无范围漂移和未记录文件；
- Spec Review、工程 Review 和必要专项 Review 通过；
- 必要需求/逻辑/业务/影响注释带来源文档并可解析，且注释、关联影响和跨模块实现与当前事实一致；
- finding 由独立 Reviewer 关闭；
- task plan、traceability、stage outcome 和 handoff 已更新。
