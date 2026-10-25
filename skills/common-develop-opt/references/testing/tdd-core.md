# TDD Core

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

## 1. Applicability

标准模式下，新增或修改业务逻辑、BUG 修复，以及权限、状态流转、数据一致性、幂等、事务、金额、库存、关联关系和具有分支/异常行为的公共服务默认执行 TDD。TDD 是 Development 内嵌的 pre/post gate，不再是一级 canonical phase。`wk -min` 明确跳过 canonical `test_design`、`testing` 并 suppress Development 内嵌 TDD gate，不得把跳过表述为通过。

仅文档/注释、无行为变化的格式化、生成代码或静态资源调整可以判定 TDD 不适用，但 ProjectManagerAgent 必须记录原因和未验证风险。缺少 Harness 不构成绕过理由：先建立最低可用 Harness，或把任务设为 `BLOCKED`。

纯重构不人为制造 RED：先取得 GREEN 表征基线，再在测试保护下重构。新行为和 BUG 修复必须先取得有效 RED。

## 2. Contract first

测试从当前需求、验收、实现契约、状态/错误语义和可观察副作用推导。公共入口、应用/领域服务和外部协作端口先稳定；不要为了测试提前固定 private 方法。关键调用顺序只有在它属于业务契约时才可被测试锁定。

前端已有稳定组件/单元 Harness 时使用同一 TDD 循环；未配置时按实现契约等级和现有项目 Harness 执行类型检查、组件测试或 E2E，不能因此削弱后端 TDD。

## 3. Execution rule

使用可复现命令、隔离数据和注册 Evidence。Mock、skip 或内存替身不能证明真实运行时行为，除非测试契约本身就是验证该边界。测试阶段的 Case 设计不能关闭 DevelopAgent 对代码级 TDD 的职责。
