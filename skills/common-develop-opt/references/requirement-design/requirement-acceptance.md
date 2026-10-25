# 需求验收与完成门禁

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

## 1. 验收表达
每条 Acceptance 必须有稳定 ID，并描述可建立的前置状态、用户或系统动作、可观察结果和禁止副作用。错误路径写明错误类型、状态、返回和数据结果；事务、幂等、并发写明最终状态。

## 2. 功能覆盖
每个功能、业务规则和跨功能规则至少关联一个 Acceptance。验收不得依赖未确认的实现细节，应定义可观察结果和后续验证方式，并可在后续阶段映射到测试 Case；需求确认阶段不要求已有测试文件、测试 Case 或测试执行结果。`wk -min` 虽不创建测试任务，仍必须保留可核对条件、风险和禁止副作用。

## 3. 文档完成
需求分析必须补全功能、规则、异常、验收、非功能要求、模块边界、追踪和当前 revision。推荐执行：

```bash
python3 scripts/requirement_doc.py finalize-requirement-analysis -g RequirementAnalysisAgent --file {REQUIREMENT_FILE} --task-dir {TASK_DIR} --json
```

该命令必须原子完成 analysis 校验和追踪同步。若只预检，使用 `validate --stage analysis` 后再执行 `sync-traceability`。

## 4. 阶段门禁
当前 revision 的需求确认与需求分析 Review 均通过；无开放 P0/P1；强制的影响策略和跨模块映射可回查；每个适用 Acceptance 均已定义可观察结果及允许的验证方式，才允许进入下一阶段。测试 Case 映射与实际执行结果由后续 `test_design` / `testing` 阶段负责。
