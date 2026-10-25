# 完成门禁核心规范

## 1. 决策切片 {#completion-gate-decision-slice}

首先使用 AI Context 中的 `completionGateSummary`。它报告阶段结果、验收、Evidence、Review、开放 finding、Request Intake、Git checkpoint 和归档就绪状态。

## 2. 完成规则 {#completion-gate-rules}

只有在必需阶段和 Review 通过、Evidence 绑定到当前 revision、没有开放的 P0/P1，并且所有强制最终检查成功时，才能标记完成。只有在门禁失败时才加载详细规则。


## 3. minimal/lightweight 上下文

`wk -min` 和 lightweight 模式可以跳过测试、TDD 或其他明确豁免的阶段，但不得因此丢失完成判定所需的上下文。两种模式都必须保留 `completion-gate-minimal`，其中至少包含：

- 非测试 Evidence 的存在、状态和当前 revision 绑定；
- Completion Verification 结果及其失败/未测试风险；
- `DONE`、`PARTIAL`、`BLOCKED` 的结论语义；
- `SKIPPED` 与 `NOT_APPLICABLE` 的区别；
- 尚未验证事项和不能据此宣称完成的原因。

被模式或预算省略的资源必须带有稳定 ID、来源和省略原因。minimal/lightweight 只改变明确声明的阶段和资源选择，不得把缺少测试解释为测试通过，也不得把缺少完成门禁解释为完成。
