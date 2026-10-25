# TDD Role Boundary

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

## 1. Ownership

TddAgent 把已确认 Case 落为测试代码，并在开发前取得有效 RED 或纯重构 GREEN 基线。DevelopAgent 负责最小生产实现、GREEN 和 REFACTOR；实现者不能作为唯一测试证据 Reviewer。

## 2. Independent review

`development:TDDReviewAgent` 的 PRE_IMPLEMENTATION 轴 验证 RED/基线真实性、稳定 seam、Case 映射和 Evidence；`development:TDDReviewAgent` 验证 GREEN 是否最小、REFACTOR 是否受测试保护，以及 BUG 回归是否完整经历 RED→GREEN。TestEvidenceReviewAgent 在测试阶段独立检查环境、命令、数据、事务和 Evidence 新鲜度。

## 3. Phase boundary

ProjectManagerAgent 只能在真实命令结果可回查、要求的 Review/Evidence 满足后登记 StageOutcome 并推进。TDD Gate 不替代 Code Review、Testing 或 Completion Verification。
