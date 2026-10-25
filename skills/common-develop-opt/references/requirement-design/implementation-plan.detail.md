# 可执行实施计划规范

## 0. 实施计划快速规则 {#implementation-plan-quick-rules}

创建按依赖排序的纵向任务，每个任务都产出可审查的 revision 和可执行验证。每个任务引用一个已批准的 `IMPL-DEC-*`，记录策略、现有代码引用、共享逻辑处置方式/Owner、源文档锚点、兼容性验证、输入、允许范围、验收、Evidence、Reviewer 和阻断依赖；不要重复 Design 依据。使用 `task_plan.py` 维护当前 `development_task_plan.json`。

实施计划是设计与编码之间的硬门禁。标准模式把已确认需求、业务模型、设计和测试用例转成任务图；`wk -min` 模式不依赖测试用例，改用需求验收条件、设计分支、编译/静态检查和 Code Review 形成最小可执行计划。

## 1. 目标

高质量计划应让一个不了解当前会话的 Agent 能够：

- 知道为什么做、做什么和不做什么；
- 找到需要修改的文件和可复用入口；
- 知道任务依赖和接口；
- 先写哪个失败测试；
- 运行什么命令、预期看到什么；
- 在什么情况下停止；
- 把结果交给哪个 Reviewer。

计划不得依赖隐含会话记忆。

## 2. 计划前置条件

只有以下条件满足才可生成实施计划：

- 需求确认、需求分析、业务模型和设计已通过；
- 标准模式下测试用例和测试 seam 已确认；`wk -min` 时该条件由已确认的需求验收条件、设计分支和非测试验证命令替代；
- 无开放 P0/P1；
- 关键接口、类型、字段、状态和错误语义已稳定；
- 输入 revision 已冻结。

若 spec 覆盖多个独立子系统，应先拆分为多个计划，每个计划单独产生可工作的软件增量。

## 3. 文件结构图

计划开始先列出创建、修改和测试文件及职责：

```markdown
## File Map

- Create `src/order/payment/policy.py` — 支付资格规则，隐藏状态判断细节。
- Modify `src/order/payment/service.py` — 调用资格规则，不拥有状态定义。
- Test `tests/order/payment/test_policy.py` — 通过公共接口验证规则。
```

原则：

- 一个文件应有清晰责任；
- 一起变化的内容放在一起；
- 优先遵守现有代码结构；
- 不为计划本身强制大规模重构；
- 若当前文件过大直接阻碍本次修改，可先安排独立结构任务，再做行为任务。

## 4. 任务粒度

一个任务是“最小的、可独立测试、值得独立 Review 的交付单元”。

合适任务：

- 完成一个用户可观察行为的纵向切片；
- 有自己的 RED→GREEN→REFACTOR；
- 完成后代码可构建、可测试；
- Reviewer 可以接受或拒绝该任务而不必同时决定邻近任务。

不合适任务：

- “创建所有表”；
- “完成后端”；
- “补充错误处理”；
- “写相关测试”；
- 只做脚手架且没有服务于同任务交付；
- 把所有测试留到最后。

## 5. 纵向切片与依赖

默认按业务行为切片，而非按技术层：

```text
错误：DB → Repository → Service → Controller → Test

推荐：
TASK-01 创建取消订单支付的失败测试和最小实现
TASK-02 创建重复支付幂等的失败测试和最小实现
TASK-03 集成两项行为并运行事务测试
```

每项任务声明 `depends_on`。依赖必须是真正阻塞关系，不是“看起来顺序更整齐”；`start-attempt` 会阻止依赖未完成的任务。每项任务同时声明稳定 `logical_task_id` 和当前 `iteration_id/iteration_no`：正常 revision 变化由 `reopen-phase` 建立新 iteration，`max_attempts` 仅限制当前 iteration 内的失败重试。

跨大范围迁移推荐：

```text
expand → migrate batches → contract → integration verification
```

每个中间任务尽量保持代码可运行、兼容和可回滚。

## 6. 标准计划头

```markdown
# {FEATURE} Implementation Plan

**Goal:** 一句话说明最终业务能力。

**Architecture:** 2-3 句说明主要边界和数据流。

**Input Revisions:**
- Requirement: ...
- Business Model: ...
- Dependency Impact: ...
- Design: ...
- Test Case: ...（`wk -min` 时写“无”）
- Work Mode: `work.md@sha256`

**Global Constraints:**
- 约束 1
- 约束 2

**Out of Scope:**
- 明确排除项

**Completion Evidence:**
- 必须通过的适用测试、构建和追踪门禁；`wk -min` 时测试写“无”，改列编译、类型检查、静态分析或构建门禁
```

## 7. 标准任务格式

```markdown
### TASK-01: 已取消订单禁止支付

**Objective:** 从用户视角说明交付行为。

**Files:**
- Modify: `src/order/payment/service.py`
- Test: `tests/order/payment/test_cancelled.py`

**Inputs:**
- AC-ORDER-007
- DESIGN-R03#payment-policy

**Interfaces:**
- Consumes: `Order.status`
- Produces: `PaymentNotAllowedError`

**Implementation Strategy:**
- Decision: `IMPL-DEC-ORDER-PAYMENT-GUARD`（链接到当前 Design 的稳定锚点）
- Strategy: `MODIFY`
- Existing code refs: `src/order/payment/service.py#pay`
- Shared logic: `REUSE_SHARED`，Owner 为 `OrderPaymentPolicy`
- Compatibility verification: 旧支付调用回归、新禁止路径测试；`wk -min` 时改列非测试验证

**Relationship/Flow Refs:**
- `IMP-ORDER-CANCEL-REFUND`
- `FLOW-ORDER-CANCEL`

**Depends On:** None

**Allowed Scope:**
- `src/order/payment/*`
- `tests/order/payment/*`

**Stop Conditions:**
- 需要改变订单取消语义
- 需要修改范围外模块

- [ ] Step 1: 写针对 AC-ORDER-007 的失败测试
- [ ] Step 2: 运行指定测试并确认因目标行为缺失而失败
- [ ] Step 3: 写最小实现
- [ ] Step 4: 运行测试并确认通过
- [ ] Step 5: 在持续测试下重构并更新必要契约/关键分支注释
- [ ] Step 6: 运行关联影响、跨模块补偿、相关测试、静态检查和构建
- [ ] Step 7: 交给 SpecComplianceReviewAgent
- [ ] Step 8: 交给 EngineeringStandardsReviewAgent

**Commands:**
- `pytest tests/order/payment/test_cancelled.py -q`

**Expected:**
- RED: 当前取消订单仍进入支付路径
- GREEN: 抛出 `PaymentNotAllowedError`，无支付记录副作用

**Produces:**
- implementation revision
- RED/GREEN/REFACTOR evidence
```


> 校验或 Review 时按需读取 [计划校验与 Review](./implementation-plan.detail.part-2.md)；主题选择见 [主入口](implementation-plan.md)。
