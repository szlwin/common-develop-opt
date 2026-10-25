# 实现策略决策契约

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

本契约用于接口、方法、组件、数据访问和业务规则的实现选型。合法枚举和 `IMPL-DEC-*` 命名空间只读取 `assets/runtime/semantic-contract.json#canonicalTerms/implementationStrategyDecision`；本文解释如何作出可审查的选择。

## 1. 固定决策顺序

每个实现对象必须依次判断 `REUSE → COMPATIBLE_EXTEND → MODIFY → CREATE`。选择后一策略时，必须提供前面策略不适用的当前代码证据和理由，不能因“新建更快”直接选择 `CREATE`。

- `REUSE`：现有语义、权限、事务、副作用、异常和结果完全满足；记录真实代码位置和调用方式。
- `COMPATIBLE_EXTEND`：增加可选参数、重载、参数对象或既有扩展点即可满足；默认值必须保持旧调用行为，并验证旧、新调用路径。
- `MODIFY`：必须改变现有行为或契约；列出调用方、调整前后行为、迁移方式、回归范围与回滚影响。
- `CREATE`：现有职责或业务语义不同，继续扩展会造成分支膨胀、职责混乱或契约歧义；逐项说明为何不能复用、兼容扩展或修改，并声明新职责 Owner。

Design 中的决策使用 `IMPL-DEC-*`，代码蓝图、开发任务、必要代码注释、测试和 Review Evidence 都引用同一决策，不复制第二套结论。

## 2. CREATE 与公共逻辑

`CREATE` 还必须选择公共逻辑处置：`REUSE_SHARED / EXTRACT_EXISTING / EXTRACT_NEW / KEEP_LOCAL / SEPARATE_REFACTOR`。判断依据不是机械的出现次数，而是业务语义、Owner、输入输出、权限、事务、异常、副作用、生命周期和变化方向是否一致。

- 语义与边界一致时，公共规则只有一个可维护 Owner；优先复用或提取到拥有该规则的领域模块。
- 只有代码形态相似，或权限、事务、副作用、生命周期不同，使用 `KEEP_LOCAL` 并说明不抽取理由。
- 抽取会跨越当前允许范围或扩大回归面时使用 `SEPARATE_REFACTOR`，建立独立结构性任务；不得在当前任务中保留未登记的永久重复。
- 禁止使用大量布尔/模式参数、跨业务域 `switch` 或携带领域知识的万能 `utils` 伪造复用。

## 3. 阶段联动与返修

Design Ready 先由 `design_doc.py` 校验决策表、策略专属字段和蓝图引用；脚本只判断结构，不猜测理由是否真实。Implementation Plan 只引用已批准的决策和当前代码证据。DevelopAgent 发现代码事实与 Design 不一致时停止并更新 Design revision，不自行降级到 `CREATE`。

独立 Reviewer 分别验证：策略顺序和实现符合性、`CREATE` 的职责与 Owner、重复业务规则的唯一事实源，以及是否发生过度抽象。平行重写、破坏兼容或公共抽象造成契约混乱属于阻断性问题；修复形成新 revision 后由原 Reviewer 重新 Review。

自动模式只处理字段、链接、明确局部重复、可直接替换的平行实现和缺失兼容验证。Owner、公共契约、权限、事务或安全边界存在歧义时必须停止请求决策。

## 4. 当前契约校验

Design 必须包含当前 `Implementation strategy contract` 标记；缺失或版本不匹配时直接 fail-closed。修复应在当前 Design 文件中完成，并重新生成新的 exact revision。

缺少 `implementation_strategy_contract_version` 的 `development_task_plan.json` 直接 fail-closed。需要继续开发时，创建未批准草稿或经正式返修的新 revision，补齐当前 Design 的 `IMPL-DEC-*`、策略、代码证据、公共逻辑 Owner、验证和来源锚点。

完整正反例见 [实现策略决策正反例](implementation-strategy-example.md)。
