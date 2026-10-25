# 人工 Review 引导器

`MANUAL_REVIEW` 用于无法仅靠命令、测试、SQL、JSON 或指标自动判断的专业结论。Reviewer 只回答少量问题；脚本把答案编译成完整 criterion 结论，但不会为每个 Reviewer 复制 Evidence 或长期保存 Markdown 表单。

阶段 Owner 完成 PASSED attempt 后，ProjectManagerAgent 必须先幂等登记当前 revision 的 Review assertion：

```bash
python3 scripts/acceptance.py ensure-review \
  -g ProjectManagerAgent \
  --task-dir project_doc/version/{VERSION}/task/{TARGET_ID}
```

命令从当前 iteration 的任务 Trace 推导 Acceptance ID、全部 expected-result source ref、有效 Reviewer 和 Profile。旧 revision assertion 不复用；已存在的精确 assertion 不覆盖。`publish-artifact` 在缺少任一 required Reviewer assertion 时 fail-closed；已发布任务发生同类缺口时，`wk next` 也必须先路由回 ProjectManagerAgent 修复，不能把不可执行的 Review 分派给 Reviewer。

## 1. 三种输入模式

```text
wizard ───────────────┐
prepare → Markdown ───┼→ 标准 response → 唯一 Artifact 使用关系
Agent draft ──────────┘                  → evidence/reviews.jsonl
                                         → task_events state/issue projection
                                         → MANUAL_REVIEW assertion
```

### 1.1 交互向导

```bash
python3 scripts/manual_review.py wizard \
  -g BusinessModelReviewAgent \
  --task-dir project_doc/version/{VERSION}/task/{TARGET_ID} \
  --assertion-id ASRT-ORDER-BOUNDARY-001
```

### 1.2 Markdown 确认单

```bash
python3 scripts/manual_review.py prepare \
  -g ProjectManagerAgent \
  --task-dir project_doc/version/{VERSION}/task/{TARGET_ID} \
  --assertion-id ASRT-ORDER-BOUNDARY-001
```

未指定 `--output` 时，确认单生成到系统临时目录：

```text
/tmp/common-develop/review-forms/{TARGET_ID}/{ASSERTION_ID}.md
```

该文件是交互草稿，不属于 `project_doc`，不进入 Git。确需人工协作时可以通过 `--output` 指定其他临时位置。

由指定 Reviewer 提交：

```bash
python3 scripts/manual_review.py submit \
  -g BusinessModelReviewAgent \
  --task-dir project_doc/version/{VERSION}/task/{TARGET_ID} \
  --review-file /tmp/common-develop/review-forms/{TARGET_ID}/{ASSERTION_ID}.md
```

artifact revision、assertion revision 或 Profile 变化后，旧确认单自动失效。

### 1.3 Agent 草拟

```bash
python3 scripts/manual_review.py draft \
  -g BusinessModelAgent \
  --task-dir project_doc/version/{VERSION}/task/{TARGET_ID} \
  --assertion-id ASRT-ORDER-BOUNDARY-001 \
  --answer MRQ-BOUNDARY=YES \
  --answer MRQ-INVARIANT=YES \
  --evidence-id EVD-000121 \
  --summary "订单只维护自身状态，退款和库存释放由对应模块负责。"
```

草拟 Agent 不得等于 assertion 指定 Reviewer，草稿必须由独立 Reviewer 提交确认。

## 2. Reviewer 实际填写内容

1. 每个合并问题选择 `YES/NO/UNKNOWN/NOT_APPLICABLE`；
2. `NO/UNKNOWN` 必须说明原因；
3. 选择当前 revision 的 Evidence Artifact；
4. 写一句主要结论；
5. 可选记录不影响结论的范围说明。

Profile、criterion、revision、checked scope、finding、状态和时间均由脚本生成。

## 3. 自动结论

```text
全部问题 = YES 或合法 NOT_APPLICABLE
→ PASSED / assertion VERIFIED

任一问题 = NO
→ NEEDS_CHANGES / assertion FAILED / 创建 finding

任一问题 = UNKNOWN
→ BLOCKED / assertion BLOCKED / 创建 evidence-gap issue
```

## 4. Evidence 复用

`prepare/draft/wizard` 只推荐：

- `ACTIVE`；
- 当前 Review phase 与 artifact revision；
- evidence type 满足 Profile criterion policy；
- ref 与 digest 可回查。

提交时不再重新登记同一文件。脚本使用 `evidence.link_usage` 将 Reviewer 与 criterion scope 关联到已有 Evidence ID。一个物理 Artifact/revision/phase 始终只有一条 Evidence 记录。

## 5. Review Result 与 reducer current view

完整 Review Result 追加到：

```text
task/{TARGET_ID}/evidence/reviews.jsonl
```

每行保存一个 schema v4 紧凑 Review 事件。Reviewer、Profile、phase、revision、result、criterion 状态和 Evidence/finding 引用在同一行去重保存；读取时恢复为完整 Review Result。紧凑事件只额外保存：

- `REV-*` 与确认信息；
- criterion 状态、Evidence ID 和 finding ID；
- 不能按统一规则重建的 claim override；
- 默认答案、少量例外、摘要和备注。

标准 Evidence claim 按 `review_id + criterion_id + evidence_id` 在读取时确定性重建，不在每条 criterion 中重复保存。

`evidence/reviews.jsonl` 保存完整 Review 事件；当前 Review 摘要如需进入任务 current view，只通过 `TASK_STATE_PATCHED` event 记录最小引用，并由 reducer 生成 cache：

```json
{
  "profile_id": "design:ArchitectureReviewAgent",
  "revision": "DESIGN-R01@...",
  "conclusion": "PASSED",
  "review_result_ref": "REV-000012",
  "finding_ids": [],
  "reviewed_at": "..."
}
```

需要细节时通过 `review_result_ref` 按需物化单条 Review，不加载全部 Review 历史。finding/issue 的 lifecycle 使用 `ISSUE_*` event；`cache/current_manifest.json` 不承担 Review 或 issue authority。

## 6. 下游复核

- 当前 Review phase 尚无结论时，提交生成新的 `REV-*`；
- 已存在同 revision 的 `PASSED` 结论且流程进入下游时，复用原 `REV-*`，不复制完整结论；
- 下游复核出现 `NO/UNKNOWN` 时，不篡改旧结论，而是更新 assertion、创建 issue，并由 ProjectManagerAgent 决定是否 `reopen-phase`；
- 没有可复用结论且 Review phase 已结束时，提交被拒绝。

## 7. 查询

```bash
# 当前 assertion
python3 scripts/manual_review.py show \
  -g ProjectManagerAgent --task-dir ... --assertion-id ...

# 当前阶段 Evidence 子集
python3 scripts/evidence.py query \
  -g ArchitectureReviewAgent --task-dir ... \
  --phase design --revision DESIGN-R01@... --limit 20
```

问题模板唯一来源为 `assets/manual-review-guides.json`。合并问题只减少输入量，不减少 Profile 必检 criterion。

`acceptance.json` 是唯一 canonical Assertion 文件。历史项目中可能存在的 `acceptance_assertions.json` 是不参与运行的遗留文件，不得双写或作为 Review 修复目标。
