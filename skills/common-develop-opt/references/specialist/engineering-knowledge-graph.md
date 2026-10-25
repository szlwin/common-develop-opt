# Engineering Knowledge Graph 工程知识图

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

## 投影规则
Engineering Knowledge Graph 是 DERIVED 投影，不是新的事实来源。它将 Domain/Bounded Context 边界、Business Model 语义事实和现有 `dependency_impact.yaml` 关系投影为一个可查询图。

权威事实可以生成投影；投影绝不能覆盖权威事实。

## 关系
核心领域关系包括 `CONTAINS`、`OWNS`、`CONSUMES`、`TRANSLATES_TO`、`PROTECTED_BY`、`USES_TERM`，以及现有工程关系 `DEPENDS_ON`、`IMPLEMENTED_BY`、`VERIFIED_BY`、`AFFECTS` 和 `TRIGGERS`。

## 影响
图遍历必须返回明确的原因路径。无效或未知的图事实不能用来缩小 Review 范围；Impact Planner 必须 fail closed，或执行保守的重新验证。
