# 代码注释规范

本文件定义接口、类型、方法和关键逻辑的注释规则。目标不是提高注释数量，而是保存代码本身难以表达的业务原因、契约、约束和风险，使后续开发与 Review Agent 能正确修改代码。

## 1. 基本原则

- 注释解释 **为什么、业务约束是什么、错误修改会造成什么后果**，不逐行翻译代码。
- 可由命名和类型清楚表达的内容优先改代码，不用注释掩盖坏命名或过度复杂结构。
- 注释必须与当前实现、需求和设计 revision 一致；行为变化时同步更新，无法确认时删除过期注释并登记问题。
- 关键业务注释同时引用稳定 ID 和来源文档。来源使用项目声明的 DOC_ROOT 相对路径或仓库可解析路径，并带稳定锚点，例如 `Source: project_doc/version/{VERSION}/requirement/REQ-USER-001/requirement.md#BR-USER-001`；实际开发时将 `{VERSION}` 替换为当前来源版本，只写 ID 而无法定位文档不合格。
- `COMPATIBLE_EXTEND`、`MODIFY`、`CREATE` 的公共契约、共享逻辑或非直观兼容分支还要引用对应 `IMPL-DEC-*` 和 Design 锚点，使后续修改者能回查为何没有采用更高优先级策略。
- 开发时将代码本身无法充分表达、但后续修改必须知道的需求信息、核心逻辑点、业务要点和影响点写在对应接口、方法或分支附近；不把整份需求复制到代码。
- 注释语言遵循项目现有约定；标识符、协议名、错误码和代码术语保持原文。
- 禁止提交被注释掉的旧代码、无 owner 的 TODO、虚假保证和与实现不一致的示例。

## 2. 必须注释的对象

### 2.1 对外接口、公共类型和公共服务

当接口承担业务边界或被模块外使用时，注释至少说明：

- 职责与不负责的范围；
- 前置条件、后置条件和关键不变量；
- 事务、幂等、并发或调用顺序要求；
- 主要副作用、可观察事件和失败语义；
- 兼容性、弃用或安全约束；
- 关联需求、规则或设计引用。
- 来源需求/设计文档路径及稳定锚点；涉及跨模块影响时同时引用 `dependency_impact.yaml` 的 `CMI/CMSTEP`。

OpenAPI、JavaDoc、KDoc、TSDoc、docstring 等应成为可由工具读取的契约，不只写“某某接口”“获取数据”。

### 2.2 公共方法和跨模块入口

方法注释按适用性覆盖：

```text
目的 / 业务语义
前置条件与权限
返回结果与后置状态
副作用、事务、幂等、并发
可预期业务错误或异常
关联规则/追踪 ID
来源文档路径与锚点
```

参数和返回值只有在类型与命名不足以表达单位、范围、空值、默认值或特殊语义时才单独解释。

### 2.3 重要逻辑和分支

以下情况必须在代码附近解释原因：

- 容易被“简化”而破坏业务规则的分支；
- 删除、取消、退款、库存释放等跨对象或跨模块处置；
- 补偿、重试、幂等、并发控制和事务边界；
- 为兼容历史数据、旧客户端或外部系统保留的非直观逻辑；
- 性能、安全、合规或数据一致性所要求的特殊实现；
- 算法、公式、位运算、时间窗口和边界值。

推荐：

```java
// BR-ORDER-014 / IMP-ORDER-003：已扣款订单取消后必须先创建退款任务，
// 不能直接释放订单终态，否则会失去退款重试与审计入口。
// Source: project_doc/version/{VERSION}/requirement/REQ-ORDER-001/requirement.md#BR-ORDER-014
// Design: IMPL-DEC-ORDER-REFUND-TASK — project_doc/version/{VERSION}/module/ORDER/ORDER_design.md#implementation-strategy-decisions
refundTaskService.createIfAbsent(order.id(), payment.captureId());
```

不推荐：

```java
// 创建退款任务
refundTaskService.createIfAbsent(order.id(), payment.captureId());
```

### 2.4 复杂查询、数据修复和迁移

说明数据选择范围、历史兼容、锁或批次策略、可重跑性、回滚方式，以及为什么不能使用更直接的写法。

## 3. 推荐格式示例

注释格式遵循语言生态，但语义字段保持一致。

### Java / Kotlin 公共方法

```java
/**
 * 取消订单，并依据支付状态触发库存释放、授权撤销或退款。
 *
 * <p>前置：调用方具有订单取消权限，订单未进入不可逆终态。
 * 后置：本地取消状态与补偿任务在同一事务内建立；跨模块补偿最终一致。
 * 幂等：同一 orderId + cancelRequestId 重复调用返回同一取消结果。
 *
 * @throws OrderStateConflictException 订单状态不允许取消
 * @implNote IMPL-DEC-ORDER-CANCEL：复用现有补偿服务并修改取消编排，不平行创建第二套补偿逻辑。
 * @see BR-ORDER-CANCEL-001
 * @see IMP-ORDER-CANCEL-REFUND
 * @see project_doc/version/{VERSION}/requirement/REQ-ORDER-001/requirement.md#BR-ORDER-CANCEL-001
 * @see project_doc/version/{VERSION}/module/ORDER/ORDER_design.md#implementation-strategy-decisions
 */
CancelResult cancelOrder(OrderId orderId, CancelRequestId cancelRequestId);
```

### TypeScript 公共接口

```ts
/**
 * 提交取消命令。该方法只确认命令已受理，不保证退款已完成。
 * @remarks 重复使用同一 requestId 必须得到同一业务结果。
 * @remarks IMPL-DEC-ORDER-CANCEL：兼容扩展现有命令入口，旧调用默认行为不变。
 * @throws {OrderStateConflictError} 当前状态不可取消。
 * @see BR-ORDER-CANCEL-001
 * @see FLOW-ORDER-CANCEL
 * @see project_doc/version/{VERSION}/requirement/REQ-ORDER-001/requirement.md#BR-ORDER-CANCEL-001
 * @see project_doc/version/{VERSION}/module/ORDER/page-design/ORDER_cancel_design.md#pageact-order-cancel
 */
export async function cancelOrder(request: CancelOrderRequest): Promise<CancelAccepted>;
```

### Python 服务方法

```python
def create_refund(order_id: str, capture_id: str) -> RefundTask:
    """Create or return the idempotent refund task for a captured payment.

    Preconditions:
        The payment capture belongs to ``order_id`` and is refundable.
    Side effects:
        Persists a refund task; actual settlement is asynchronous.
    Raises:
        RefundNotAllowed: The capture has no refundable balance.
    Trace:
        BR-PAYMENT-REFUND-002, IMP-ORDER-CANCEL-REFUND, IMPL-DEC-PAYMENT-REFUND.
    Source:
        project_doc/version/{VERSION}/requirement/REQ-PAYMENT-001/requirement.md#BR-PAYMENT-REFUND-002
        project_doc/version/{VERSION}/module/PAYMENT/PAYMENT_design.md#code-blueprint
    """
```

这些示例是字段提示，不要求所有注释写成长篇模板。只保留适用信息，避免复制粘贴后形成错误契约。

## 4. 不应添加的注释

- 对赋值、循环、条件判断逐句复述；
- 与代码同名的空洞注释，例如“创建订单”“返回结果”；
- 用长注释解释本可拆成具名方法、类型或策略的复杂逻辑；
- 临时调试记录、聊天内容、模型推理过程；
- 没有 issue、owner 或清理条件的 `TODO/FIXME`；
- 密码、Token、客户数据或其它敏感信息。

## 5. TODO、FIXME 与弃用

统一格式：

```text
TODO(<ISSUE_ID>, owner=<ROLE_OR_TEAM>, remove-by=<CONDITION_OR_VERSION>): <remaining work>
FIXME(<ISSUE_ID>, owner=<ROLE_OR_TEAM>): <known incorrect behavior and risk>
```

弃用注释必须包含替代入口、迁移方式和计划移除条件；不得只写 `deprecated`。

## 6. 注释与图、文档的边界

- 代码注释保存局部原因和契约；完整业务规则保存在需求/业务模型中。
- 跨模块实现映射图保存在 `dependency_impact.yaml` 及其生成的 Markdown，不把整张流程复制到代码注释。
- 代码只保留必要摘要、稳定 ID 和可解析来源路径；不得引用聊天、个人机器绝对路径、临时 task attempt、Evidence snapshot 或生成预览作为业务来源。
- 来源优先指向 `requirement.md`、稳定设计、页面设计、业务流程/模型或 `dependency_impact.yaml` 的当前受审 revision；文档通过 `implementationRefs` 反向定位代码。
- 文档 revision 被后续需求或设计替代时，修改相关代码必须同步更新注释来源；不能确认新来源时登记问题并阻断交付，不保留失效链接假装可追溯。
- 附近存在状态图、决策表或算法图时，行为变化必须同步更新图或将其标记 `STALE`。

## 7. 开发门禁

DevelopAgent 在提交结果前检查：

1. 新增或修改的公共接口是否具有有效契约注释；
2. 非直观业务分支是否解释原因并关联规则/影响策略；
3. 需求信息、逻辑点、业务要点和影响点的必要注释是否同时包含稳定 ID、来源文档路径和锚点；
4. 适用的兼容扩展、修改、新建与共享逻辑注释是否关联当前 `IMPL-DEC-*`，且注释来源可打开并与当前 revision、错误语义和验证一致；
5. 是否存在被注释代码、过期 TODO、敏感信息或误导性示例；
6. 仅注释变更是否真的不改变行为；若改变契约含义，必须触发对应 Review 和适用测试；`wk -min` 时测试不适用，改为记录风险并执行非测试验证。

EngineeringStandardsReviewAgent 将注释质量作为工程规范轴；SpecComplianceReviewAgent 检查关键注释引用的规则是否与实现一致。注释缺失只有在降低正确修改能力、隐藏业务约束或违反项目规范时才作为 finding，不能按行数或覆盖率机械打分。
