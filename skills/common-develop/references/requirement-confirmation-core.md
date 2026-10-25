# 需求确认核心规范

## 1. 确认目标

在开始分析前，确认目标、用户可见结果、范围内行为、排除行为、约束和未解决问题。

## 2. 退出门禁

需求 revision 必须明确、可审查，并链接到 Request Intake 或既有需求事实。会改变范围的歧义会阻断阶段完成。

需求确认阶段只验证需求语义是否足够清晰、可观察和可测试：包括范围边界、用户可见结果、验收标准、失败语义、禁止副作用和未决策事项。此阶段不要求已有测试文件、测试 Case、测试命令或测试执行结果，也不能使用 `test_ref` 替代需求本身对验收和失败行为的定义。

验收标准到测试 Case 的映射、正常/边界/失败路径覆盖以及测试证据采集方案，由 `requirement_analysis` 和 `test_design` 阶段负责；真实测试执行结果和测试 Evidence，由 `testing` 阶段负责。

## 3. Authoritative Fact Location

### 权威事实位置

使用 AI Context 引用的项目需求产物和 Request Intake。不要把聊天文本、HTML 渲染结果或生成摘要当作 canonical requirement。

需求变更必须发布为新的 revision，并通过声明的脚本更新追踪关系。

## 4. Stable ID and Revision

### 稳定 ID 与 Revision

保留目标 Requirement、Feature 或 Bug ID。生成的描述 ID 对规范化后的原始输入保持稳定。

每个下游阶段都必须绑定到精确的已确认需求 revision。后续需求变更应重新打开受影响阶段，而不是覆盖历史。

## 5. Scope Lock and Change Control

### 范围锁定与变更控制

记录纳入行为、排除行为、依赖、假设和验收边界。

对业务行为、API、数据、权限、工作流、持久化、副作用、迁移或依赖的变更，都需要进行影响分析，并可能需要重新进行 Request Intake 分类。

## 6. 面向人阅读的需求契约

`requirement.md` 首屏必须包含 `1.1 阅读摘要`，用直白语言回答：为什么需要这次变更、预期产生什么有价值的结果、明确排除什么、如何观察完成情况，以及是否仍有未决策事项。保留稳定 ID 和 revision 元数据，但不要让读者通过 ID、Review 历史或追踪表自行还原需求目的。

只维护一个当前的 `requirement.md`。revision 历史属于 Git、Evidence、Review 记录和变更表；不要创建 `requirement_R02.md` 之类的同级文件。
