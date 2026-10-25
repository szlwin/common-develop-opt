# 跨 Agent 协作 Review 规范

本文件定义需求、业务模型、设计、测试用例、实施计划、代码和测试证据的独立审查、交叉质询、修订、复核和收敛。所有讨论进入结构化问题台账，不保存无结论价值的冗长对话。

## 1. 核心原则

- 所有 Reviewer 基于同一目标、范围和 artifact revision；
- 先独立审查，再读取其他意见；
- 不同 Review 轴分别报告，不能互相抵消；
- finding 必须有证据、影响和可验证建议；
- 实现者或产物责任 Agent 不得自行关闭自己的问题；
- 不确定观察与已验证问题分开；
- 用户决定业务取舍，专业 Agent 决定职责范围内的技术建议；
- ProjectManagerAgent 组织流程，不发明需求或技术事实。

## 2. Review 对象与参与

owner、必需 Reviewer 和条件 Reviewer 只从 `assets/runtime-contract.json#canonicalPhases` 读取，本文件不复制 Reviewer 名单。各对象的审查重点如下：

| 对象 | 主要问题 |
|---|---|
| 需求确认 | 目标、范围、完成标准、可测性和隐藏假设 |
| 需求分析 | 规则、验收、关系、处置策略、跨模块边界以及可建模/可设计/可测性 |
| 业务模型 | 术语、不变量、状态、聚合、关联生命周期、跨模块实现映射、错误语义和追踪 |
| 技术设计 | 需求/模型覆盖、模块/API/DB、事务、并发、补偿、可测性和可实现性 |
| 测试设计 | 覆盖、边界、预期、seam、禁止副作用和追踪 |
| 实施计划 | 粒度、依赖顺序、接口、命令和覆盖 |
| TDD | `tdd` 阶段验证 RED/表征基线；`development` 阶段验证 GREEN/REFACTOR、测试 seam 和证据新鲜度 |
| 代码 | Spec 符合性、工程规范，以及按风险登记的额外 Reviewer |
| 测试证据 | 真实命令、环境、数据、revision、最终状态和跳过项 |
| 完成验证 | 范围、追踪、任务、Review、测试、BUG 和开放问题的独立终检 |

Reviewer 列表发生变化时只修改运行契约和长任务模板，禁止在本文件另建平行矩阵。

## 3. Review 包

ProjectManagerAgent 在每轮开始时冻结：

```yaml
review_package:
  review_id: REVIEW-DESIGN-R02
  target_id: MOD0001
  phase: design
  artifact_revision: DESIGN-R02@abc123
  scope:
    included: ["订单支付状态与接口"]
    excluded: ["页面视觉设计"]
  input_revisions:
    requirement: REQ-R02@...
    business_model: BM-R01@...
  artifact_paths:
    - project_doc/version/{VERSION}/doc/order/design.md
  trace_ids:
    - TR-ORDER-001
  required_reviewers:
    - RequirementReviewAgent
    - ArchitectureReviewAgent
  questions:
    - "状态转换是否完整？"
  known_constraints:
    - "不改变旧客户端返回结构"
```

Review 包冻结后，产物变化必须开启新 revision 和新轮次。

## 4. 独立审查

每个 Reviewer 在未读取其它结论前，先使用 `scripts/review_contract.py show` 读取唯一的 `<phase>:<reviewerAgent>` Profile，并按 `review-core.md` 的 Acceptance 章节对全部 criterion 逐项输出。

```yaml
independent_review:
  profile_id: design:ArchitectureReviewAgent
  revision: DESIGN-R02@abc123
  conclusion: NEEDS_CHANGES
  checked_scope:
    - design.md#事务边界
    - design.md#模块依赖
  criteria_results:
    - criterion_id: RC-ARCH-002
      status: FAILED
      evidence:
        - evidence_id: EVD-000021
          claim: 当前设计 revision 的模块依赖证据显示 payment 反向依赖 order 内部实现。
      finding_ids:
        - RI-MOD0001-DESIGN-001
      reason: 依赖方向违反已定义模块边界。
  finding_ids:
    - RI-MOD0001-DESIGN-001
  limitations: []
  reviewed_at: 2026-07-19T10:00:00+08:00
```

没有 finding 不等于自动 `PASSED`。全部 criterion 必须有结果；`PASSED/FAILED` 必须满足 criterion 的结构化证据策略，`BLOCKED/NOT_APPLICABLE` 必须写明原因。

## 5. Finding 标准

### 5.0 设计/Review 问题的详细会话解释

当 finding 的核心争议涉及对象归属、生命周期、并发、边界或身份校验时，主报告前必须先给出面向用户的详细解释。解释应依次说明问题定义、概念拆分、合法/非法组合、失败时序、现有检查为何不足、推荐的绑定边界、不变量和验证场景。尤其要区分编译上下文与 runtime generation，区分结构相等与 owner/object identity；相同规则、名称、字符串 ID、包可见性或同 JVM 都不能单独证明运行时对象属于同一 root。没有当前 revision 的代码/设计/Evidence 支持时必须标记 `UNVERIFIED`，用户确认前不得把解释当作已关闭的 finding 或最终决策。

每个 finding 至少包含：

```json
{
  "id": "RI-MOD0001-DESIGN-001",
  "phase": "design",
  "round": "DESIGN-R02",
  "axis": "ARCHITECTURE",
  "severity": "P1",
  "confidence": 9,
  "status": "OPEN",
  "issue_type": "INCONSISTENCY",
  "artifact_revision": "DESIGN-R02@abc123",
  "raised_by_agent": "ArchitectureReviewAgent",
  "question_to": ["DesignAgent"],
  "owner_agent": "DesignAgent",
  "title": "支付写入与订单状态不在同一事务边界",
  "description": "设计允许支付记录成功但订单状态未更新。",
  "motivating_evidence": [
    "design.md#支付服务：先写支付记录，再异步修改订单"
  ],
  "impact": "可能产生不可恢复的不一致订单",
  "question": "失败时最终状态和补偿机制是什么？",
  "recommendation": "在同一事务中更新，或定义可验证补偿状态机。",
  "affected_artifacts": ["design", "test_design", "implementation_plan"],
  "affected_trace_ids": ["TR-ORDER-008"],
  "responses": [],
  "decision": "",
  "resolution_revision": "",
  "resolution_evidence": "",
  "verified_by_agent": "ArchitectureReviewAgent"
}
```

### 5.1 严重级别

- `P0`：安全、数据损坏、核心路径错误或任务完全阻断；
- `P1`：当前任务必须修复，否则不满足需求或风险不可接受；
- `P2`：可延期，必须记录风险、owner 和验证时机。

### 5.2 置信度

- `9-10`：已读取具体代码/产物并可展示直接问题；
- `7-8`：高可信模式，证据充分；
- `5-6`：可能是问题，需要确认；
- `3-4`：低可信观察，只进附录或讨论；
- `1-2`：推测，除非潜在 P0，否则不报告。

严重级别和置信度独立：高影响但低置信的问题应先验证，不能直接定罪。

### 5.3 预发出证据门禁

任何进入主报告的 finding 必须引用触发它的精确证据：

- 代码 finding：`file:line` 和触发行；
- 字段不存在：引用定义字段的模型、Schema、迁移或框架元构造；
- 竞态：同时引用两个竞争路径；
- Spec 缺失：引用 spec/追踪项和相关实现；
- 测试证据问题：引用命令、输出和 revision。

如果无法引用触发证据：

- confidence 最高为 5；
- 不得作为主报告阻塞项；
- 标记 `UNVERIFIED`，进入附录或待确认问题。

禁止以搜索不到字符串证明框架生成字段不存在。

## 6. Review 轴分离

### 6.1 Spec 符合性轴

检查：

- 需求、模型、设计、Case 是否完整实现；
- 是否缺失、部分实现或行为错误；
- 是否有 spec 未要求的功能和范围漂移；
- 代码和测试是否对应追踪项。

### 6.2 工程规范轴

检查：

- 项目标准、模块边界、技术栈和命名；
- 重复、复杂度、隐藏副作用、浅模块和过度抽象；
- `IMPL-DEC-*` 是否遵循复用优先顺序，`CREATE` 是否真实需要，以及公共逻辑是否同时避免多事实源和伪复用；
- 错误、事务、幂等、日志、枚举和数据一致性；
- 公共契约与关键分支注释是否准确、必要且未过期，需求/逻辑/业务/影响注释是否同时包含稳定 ID 与可解析来源文档路径/锚点；
- 测试质量与 Mock 边界。

两个轴分别列结论和最严重问题。不得合成一个模糊“总体通过”。

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
