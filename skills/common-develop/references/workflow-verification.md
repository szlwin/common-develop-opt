# 研发流程横向验证门禁

本文件定义贯穿各阶段的机器验证和引导式人工确认入口。

## 机器验证横向门禁

以下门禁贯穿主流程，不创建新生命周期阶段：

1. RequirementAnalysisAgent 为验收项建立稳定 acceptance ID；
2. TestDesignAgent 将可验证预期编译为 Acceptance Assertions，无法机器化的内容转 `UNRESOLVED` 或 `MANUAL_REVIEW`；
3. 各责任 Agent 将文件、命令、测试和决策注册到 Evidence Registry；
4. development 完成后，ProjectManagerAgent 对当前 modified files 运行 `risk_detect.py scan`，随后运行 `long_task.py sync-risk-reviewers` 将未豁免高置信风险同步为 code-review 任务和阶段的强制 Reviewer；
5. `start-attempt code_review` 检查风险报告 revision/digest、risk triggers 和专项 Reviewer；
6. Review criterion 只接受 `{evidence_id, claim}`；
7. completion verification 要求所有适用 blocking assertions 为 `VERIFIED`；
8. archive 回查完整 Evidence Registry、Risk Detection 和 Acceptance Assertions。

详细命令和 Schema 按需读取 `evidence-core.md`、`review-evidence.md`、`acceptance-core.md`、`risk-verification.md` 与 `verification-integration.md`。


## 引导式人工确认

无法自动验证的 acceptance 使用 `MANUAL_REVIEW`。Reviewer 不直接编辑完整 JSON，而使用 `manual_review.py wizard`，或由 ProjectManagerAgent 生成 Markdown 确认单后 `submit`。`wk -auto` 下责任 Agent 可以 `draft`，但必须由 assertion 指定的独立 Reviewer 提交。任一 `NO` 产生 finding，任一 `UNKNOWN` 阻塞流程；全部问题和证据满足后才更新 assertion。
