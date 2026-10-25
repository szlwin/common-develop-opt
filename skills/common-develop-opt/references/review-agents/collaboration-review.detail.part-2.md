# collaboration-review（续）

> 本文是 [collaboration-review.detail.md](./collaboration-review.detail.md) 的按需续篇。
## 7. 条件与风险专项 Review

`canonicalPhases[].conditionalReviewerAgents` 处理需求/模型/设计阶段的结构化触发；实现风险必须先由 `risk_detect.py scan` 生成 `risk.json`，再由 ProjectManagerAgent 执行 `long_task.py sync-risk-reviewers`，依据 `runtime-contract.json#riskReviewerCatalog` 受控写入：

```json
"additional_reviewers": {
  "SecurityReviewAgent": {
    "trigger": "security",
    "reason": "新增管理接口权限和敏感字段脱敏",
    "evidence_ids": ["EVD-000031", "EVD-000032"]
  }
}
```

trigger、允许阶段、信号和 Reviewer 映射只从 `runtime-contract.json#riskReviewerCatalog` 读取，不在本文复制。需要确认当前 Reviewer 的验收范围时，使用：

```bash
python3 scripts/review_contract.py show -g SecurityReviewAgent --profile code_review:SecurityReviewAgent --json
```

规则：

- `code_review` 前必须存在当前 development revision 的 `SCANNED` 风险报告；未豁免高置信风险不得遗漏；
- 没有当前 Evidence Registry 证据不得登记额外 Reviewer；
- 被登记的 Reviewer 就成为本轮硬门禁，必须有同 revision 独立结论；高置信检测只能由专项 Reviewer 与 ProjectManagerAgent 共同批准 waiver；
- Reviewer 失败或超时不影响其它 Reviewer 继续，但阻止该阶段通过；
- 不为“流程看起来完整”创建空专项 Review。

## 8. 交叉质询

独立结论全部落盘后：

1. ProjectManagerAgent 合并重复 finding；
2. finding 提出者向责任 Agent 提问；
3. 责任 Agent 用证据回答，不做空泛同意；
4. 其它 Reviewer 可质疑证据、影响或建议；
5. ProjectManagerAgent 根据职责边界形成决议；
6. 多种有效业务结果交用户选择；
7. 技术建议冲突由对应专业 Reviewer 提供推荐和取舍；
8. 只记录有决议价值的回应。

每条 response 至少包含：`responded_by_agent`、`round`、`response`、`evidence`、`created_at`。

## 9. Review 反馈处理

收到 Review 反馈的责任 Agent：

1. 先读取问题和证据；
2. 验证反馈是否适用于当前项目；
3. 不清楚时针对具体技术点提问；
4. 同意时按严重级别逐项修复；
5. 不同意时用代码、文档或测试证据反驳；
6. 不做表演式赞同，也不盲目实现外部建议；
7. 每次修复后运行对应验证；
8. 将修复 revision 和证据交给验证 Agent。

如果反馈要求超出当前需求的“专业化功能”，先做 YAGNI 和范围检查。

## 10. 修订与关闭

状态：

- `OPEN`
- `ASSIGNED`
- `FIXED_PENDING_VERIFY`
- `CLOSED`
- `DEFERRED`
- `REJECTED`

流程使用 append-only issue event，不再原地覆盖 issue 文档：

1. 提出者追加 `ISSUE_OPENED`，current status=`OPEN`；
2. ProjectManagerAgent 追加 `ISSUE_ASSIGNED` 并指定 `owner_agent`；
3. `owner_agent` 修复后追加 `ISSUE_REMEDIATED`，登记 resolution revision/evidence，current status=`FIXED_PENDING_VERIFY`；
4. 原提出者或独立 validator 追加 `ISSUE_VERIFIED`；
5. 验证完成后追加 `ISSUE_CLOSED`，current status=`CLOSED`；
6. 误报追加 `ISSUE_REJECTED`，必须有原因/决议证据；
7. 延期追加 `ISSUE_DEFERRED`，必须有风险、责任人和验证时机；后续恢复追加 `ISSUE_REOPENED`。

通用转换使用 `python3 scripts/long_task.py issue-event ...`；Manual Review finding 的创建/替换由 `manual_review.py` 直接追加相同事件。`owner_agent` 不得等于最终 `verified_by_agent`。

## 11. 追踪矩阵

`traceability.json` 的每项至少可以关联：

- requirement refs；
- business model refs；
- design refs；
- test case IDs；
- implementation plan task IDs；
- contract refs；
- implementation refs；
- verification evidence。

状态：

- `PENDING`
- `COVERED`
- `GAP`
- `STALE`
- `NOT_APPLICABLE`

门禁：

- 需求分析后有 requirement refs 和验收标准；
- 业务模型后有 business model refs；
- 设计后有 design refs；
- 测试设计后有 test case IDs 且 `COVERED`；
- 计划后有 task IDs；
- 开发后有 implementation refs；
- 完成验证后有 verification evidence。

上游 revision 变化时，受影响项标记 `STALE`。

## 12. 分歧裁决

- 业务目标、范围和验收：RequirementConfirmationAgent 提供资料，用户决定；
- 业务语义和统一语言：BusinessModelAgent 建议，RequirementReviewAgent 验证不漂移；
- 架构和技术方案：ArchitectureReviewAgent 给出推荐，改变业务语义时退回需求；
- 测试方法：标准模式由 TestDesignAgent/TddAgent 决定，预期业务结果必须有需求依据；`wk -min` 时测试方法不适用，由需求验收条件和非测试验证约束；
- 代码规范：EngineeringStandardsReviewAgent 按项目标准判断；
- 流程、责任和证据完整性：ProjectManagerAgent 判断。

默认每阶段最多三轮。达到上限仍有 P0/P1，或同一关键问题反复打开，任务设为 `BLOCKED`，向用户提交选项、证据、影响和推荐。

## 13. Review 通过条件

- 所有必须、触发和额外 Reviewer 都存在当前 `<phase>:<reviewerAgent>` Profile；
- 每个 Reviewer 基于同一 revision 和明确 `checked_scope` 完成全部 criterion；
- 每个 `PASSED/FAILED` criterion 满足自身证据策略，且每条 evidence 使用 `{evidence_id, claim}`；Evidence Registry 中的文件、digest、revision、phase 和 type 均通过回查；
- `FAILED` criterion 已关联真实 finding，严重度不低于 criterion floor；
- 独立结论在交叉质询前已保存；
- 所有 Reviewer 的推导结论均为 `PASSED`；
- 无开放 P0/P1；
- P2 已关闭或有延期责任；
- finding 有证据、决议、修复和复核；
- 当前门禁范围无 `GAP` 或 `STALE`；
- 所需测试/Harness 对当前 revision 通过；
- stage outcome、task state 和 handoff 已更新。


## 14. Evidence Registry 与 Review 证据

Review 不直接接受裸路径。Reviewer 必须先将读取的设计、代码、命令、测试或决策结果注册到 `evidence/registry.jsonl`，再在 criterion 中引用 evidence ID。当前规则、命令和失效处理见 `review-evidence.md`。

Artifact revision 变化后，旧 evidence 和旧 Review 结论保留为历史，但不能用于当前阶段通过；需要为新 revision 重新注册或复用内容相同但 revision 正确的新 evidence。
