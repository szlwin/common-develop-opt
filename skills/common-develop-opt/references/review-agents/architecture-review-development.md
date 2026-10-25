# `wk -ar` 两阶段开发与双 Review

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

本文件是 `wk -ar` 的唯一可读规则。机器事实位于 `assets/runtime/runtime-contract.json#/commandInterface/workModes/architectureReview`；当前状态由 `task/{TARGET_ID}/task_events.jsonl` 中的 state patch 经 reducer 计算，`cache/current_manifest.json#architecture_review` 只是派生阅读视图。

## 1. 作用范围

在 `wk -auto` 中，进入本流程前必须先按 `assets/runtime/runtime-contract.json#/commandInterface/workModes/auto/architectureBoundaryPolicy` 判断当前 DEV 是否存在真实架构边界变化；只有判断为 `RUN_AR` 才执行本文件的 Skeleton Gate。fixture、test oracle、配置数据、既有 wiring、冻结 ownership 内实现和普通 CI remediation 不重复本流程。

`wk -ar` 不新增生命周期阶段，而是在现有 `development` 阶段内建立两个顺序 iteration：

1. `ARCHITECTURE_SKELETON`：先写可审查的架构代码骨架；
2. `CONCRETE_IMPLEMENTATION`：第一轮 Review 通过后再实现具体方法。

两个 iteration 不得并行。`wk -auto` 也不得跨过第一轮 Review 边界自动开始具体实现。

## 2. 第一阶段：架构代码骨架

DevelopAgent 必须提交可由编译器、静态分析或 Reviewer 读取的真实代码骨架，至少包含：

- 方法或函数名称、可见性、泛型和类型约束；
- 输入参数、返回值、错误或异常契约；
- 顶层调用顺序、依赖注入和主要协作对象；
- 关键判断、业务分支、状态转换、提前返回和失败路径；
- 事务、幂等、并发、补偿和外部副作用边界；
- 具体方法的明确未实现标记及其待实现责任。

第一阶段允许在编排方法内写调用逻辑和主要分支，但被调用的具体业务方法不得实现真实业务算法、数据库读写、网络调用、消息发送或其它外部副作用。未实现方法必须显式失败，例如语言适用的 `NotImplementedError`、`UnsupportedOperationException`、抽象方法或仅供编译的接口声明；禁止返回伪造成功、空集合、`null`、默认值或吞掉异常来隐藏缺失实现。

骨架必须能够让 Reviewer 判断完整业务主线，而不是只有空类、空方法或无分支的占位代码。

## 3. 第一轮 Review

骨架 revision 发布后，必须顺序完成：

- `development:ArchitectureReviewAgent`：检查方法边界、调用编排、主要分支、失败和副作用边界；
- `development:SpecComplianceReviewAgent`：检查需求、设计、验收和业务流程是否完整映射，且没有范围漂移。

两份 Review Result 都必须绑定同一骨架 revision 并为 `PASSED`。任一 `NEEDS_CHANGES/BLOCKED` 都停留在第一阶段，修复后以新 skeleton revision 重审。未通过时不得创建或开始具体实现 iteration。

第一轮通过后仅由 ProjectManagerAgent 执行：

```bash
python3 scripts/long_task.py advance-development-step \
  -g ProjectManagerAgent \
  --task-dir {TASK_DIR}
```

该命令保留第一轮 iteration、StageOutcome、Evidence 和 Review 历史，并为 `development` 创建新的具体实现 iteration。

## 4. 第二阶段：具体实现

DevelopAgent 在已通过的骨架上实现所有具体方法，包括适用的数据访问、外部调用、算法、状态修改、事务、幂等、并发、错误处理和补偿逻辑。

已通过 Review 的以下内容视为冻结边界：

- 公共和内部方法契约；
- 顶层调用主线；
- 主要判断与分支；
- 失败、事务、幂等和副作用边界。

仅补充方法内部实现、必要的局部私有细节和不改变语义的重构，不得偷偷改变上述冻结边界。若具体实现证明骨架不成立，必须停止当前 iteration，使用 `reopen-phase --from-phase development` 回到 `SKELETON`，说明 revision 原因并重新执行第一轮 Review。

标准模式继续遵守 TDD、当前 revision Evidence 和 development 阶段门禁；其中 TDDReviewAgent 是实现完成度门禁，不替代下一阶段的第二轮 Code Review。`wk -min` 只按其契约跳过测试，不跳过两轮 Review 或非测试验证。

## 5. 第二轮 Review

具体实现完成后：

1. 先通过当前 development iteration 的适用 Review 和 Evidence 门禁；
2. `finalize-phase` 将状态置为 `IMPLEMENTATION_READY_FOR_REVIEW`；
3. `advance-phase` 才允许进入 `code_review`；
4. 最终 Code Review（第二轮 Review）至少包含：
   - `SpecComplianceReviewAgent`；
   - `EngineeringStandardsReviewAgent`；
   - `ArchitectureReviewAgent`。

ArchitectureReviewAgent 在最终 Review 中必须核对具体实现未偏离第一轮已通过骨架；SpecComplianceReviewAgent 核对需求和设计完整性；EngineeringStandardsReviewAgent 核对实现质量、错误处理、注释和适用验证。

只有最终 Code Review 通过后，状态才置为 `IMPLEMENTATION_REVIEW_PASSED`；后续生命周期门禁全部通过后，`wk -ar` 开发才算完成。

若最终 Code Review 已通过后因 Review Finding、证据补强或最终复核需要执行 `reopen-phase --from-phase code_review`，不得重新打开已通过的 skeleton/implementation iteration。状态机必须保留 `skeleton_revision`、`skeleton_review_result_refs`、`implementation_iteration_id` 和 `implementation_revision`，仅将 `architecture_review.step` 从 `IMPLEMENTATION_REVIEW_PASSED` 回退为 `IMPLEMENTATION_READY_FOR_REVIEW`，并清空旧 `final_review_revision` / `final_review_result_refs`，随后对新的 Code Review revision 重新执行第二轮 Review。

只有从 `development` 或更早阶段重开时，才允许重建 `architecture_review` 开发状态并重新执行 skeleton Review。

## 6. 计划与上下文要求

启用 `wk -ar` 时：

- `work.md` 的“架构代码”列为 `AIW`；
- 实施计划必须把骨架、第一轮 Review、具体实现和第二轮 Review列为顺序交付；
- AI 通过 `ai_context.py resolve` 获取本文件，不得从会话自行推断当前处于哪一步；
- 恢复任务时以 event reducer 得到的 `architecture_review.step` 为准，不依赖 cache 文件是否存在；
- Work Record 的执行模式必须记录 `architecture_review`；
- 所有 Review、Artifact、StageOutcome 和 Evidence 必须绑定各自当前 iteration/revision。
