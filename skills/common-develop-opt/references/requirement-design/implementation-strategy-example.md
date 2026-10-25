# 实现策略决策正反例

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

> 独立示例：本例使用“数字图书馆借阅”场景，只演示 `IMPL-DEC-*`、公共逻辑 Owner 和文档链接，不引用任何现有项目事实。

## 1. 正例总览

| 决策 | 对象 | 策略 | 结论 | 公共逻辑处置 | 验证 |
|---|---|---|---|---|---|
| IMPL-DEC-LIBRARY-FINE | 逾期费用计算 | REUSE | 现有 `LoanFinePolicy.calculate` 的语义、事务与错误完全满足 | REUSE_SHARED，Owner 为 Loan Domain | 既有费用表征测试与新调用集成测试 |
| IMPL-DEC-LIBRARY-NOTIFY | 到期提醒 | COMPATIBLE_EXTEND | 给现有提醒命令增加可选 `channel`，缺省仍为站内信 | REUSE_SHARED，Owner 为 Notification Policy | 旧调用默认行为与短信新路径测试 |
| IMPL-DEC-LIBRARY-RENEW | 续借规则 | MODIFY | 新规则改变最大续借次数，必须同步全部调用方 | EXTRACT_EXISTING，Owner 为 Renewal Policy | 调用方迁移、旧上限回归和新上限测试 |
| IMPL-DEC-LIBRARY-IMPORT | 馆藏盘点导入 | CREATE | 单册登记职责不同，扩展会把入口变成多模式批处理器 | KEEP_LOCAL，Owner 为 Inventory Import | 批次错误、部分失败和持久化集成测试 |

## 2. CREATE 的完整判断

### IMPL-DEC-LIBRARY-IMPORT

- `REUSE` 不适用：现有 `CatalogService.registerOne` 只处理一个已校验书目，没有批次错误和行号语义。
- `COMPATIBLE_EXTEND` 不适用：增加 `batchMode/continueOnError` 会让公共入口同时承担单册事务和批次部分失败两种契约。
- `MODIFY` 不适用：改变现有入口会破坏柜台登记调用方的原子成功语义。
- `CREATE`：新增 `InventoryImportService`，只负责文件解析、逐行校验、批次结果与已批准的分批提交。
- `KEEP_LOCAL`：行错误收集只属于导入上下文；书号规范化仍复用 `IsbnNormalizer`，不得在新服务内复制。
- 来源与跳转：在项目 Design 中，代码蓝图应使用 `[IMPL-DEC-LIBRARY-IMPORT](#implementation-strategy-decisions)` 返回策略表，策略表使用 `[BP-LIBRARY-IMPORT](#code-blueprint)` 跳到蓝图。

## 3. 不抽取与应抽取

不抽取：柜台借书和批量导入都出现“错误列表”，但前者是单请求拒绝，后者是逐行部分成功，事务和输出语义不同，应各自维护。

应抽取：借书、续借和预约都使用相同 ISBN 归一化规则，输入输出、错误和 Owner 一致，应统一调用 `IsbnNormalizer`，不能在三个服务中复制正则表达式。

## 4. 反例

### 4.1 平行新建

发现 `LoanFinePolicy.calculate` 已完全满足，却新建 `NewFineCalculator`。这违反策略顺序并产生第二事实源，`RC-SPEC-007` 与 `RC-MAINT-008` 应失败。

### 4.2 布尔参数伪复用

```java
processLoan(request, true, false, 3);
```

调用方无法理解参数语义，新增模式会继续扩大分支。应使用有名称的命令/策略；如果业务契约不同，保持独立职责。

### 4.3 跨域万能工具类

```java
switch (businessType) {
  case LOAN: /* 借阅规则 */
  case PAYMENT: /* 支付规则 */
  case NOTIFICATION: /* 通知规则 */
}
```

一个工具类拥有多个业务域真相并形成反向依赖。应把规则留在各自 Owner，通过稳定契约协作。

## 5. 计划、代码和 Review 的同一引用

```yaml
implementation:
  implementation_decision_ref: IMPL-DEC-LIBRARY-NOTIFY
  strategy: COMPATIBLE_EXTEND
  existing_code_refs:
    - library-notify/src/ReminderService.java#send
  shared_logic_disposition: REUSE_SHARED
  shared_logic_owner: NotificationPolicy
  compatibility_verifications:
    - 旧调用未传 channel 时仍发送站内信
    - 新调用指定 SMS 时发送短信且返回相同受理结果
  source_doc_refs:
    - project_doc/version/current/module/LIBRARY/LIBRARY_design.md#implementation-strategy-decisions
```

必要代码注释引用同一个决策和可解析设计锚点；Reviewer 为当前 exact revision 登记 `design_ref + diff/code_ref + test_ref`，不能把 YAML 字段本身当作通过证据。
