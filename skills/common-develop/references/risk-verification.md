# 风险检测与阶段阻断

本文件定义风险扫描、风险等级和进入 Code Review 前的阻断规则。

## Risk Detection

开发完成、进入 `code_review` 前必须执行：

```bash
python3 scripts/risk_detect.py scan \
  -g ProjectManagerAgent \
  --task-dir project_doc/version/{VERSION}/task/{TARGET_ID}

python3 scripts/long_task.py sync-risk-reviewers \
  -g ProjectManagerAgent \
  --task-dir project_doc/version/{VERSION}/task/{TARGET_ID}
```

扫描输入来自当前 development revision 的实际 modified files、文件内容和 traceability 标志，输出：

```text
task/{TARGET_ID}/risk.json
```

风险发现来源：

1. `assets/risk-detection-contract.json` 的文件和内容确定性规则；
2. traceability 的 `impact_required/cross_module_implementation_required`；
3. Agent 使用 `risk_detect.py add` 补充机器无法识别的业务语义风险。

高置信检测结果自动映射到 `runtime-contract.json#riskReviewerCatalog`。进入 code review 时必须满足：

- risk report 为 `SCANNED`；
- input revision 与当前 development revision 一致；
- input digest 未过期；
- 所有未豁免高置信风险均写入 code-review task 的 `risk_triggers`；
- 对应专项 Reviewer 已登记并在当前 revision 给出独立结论。

`sync-risk-reviewers` 根据当前未豁免高置信检测结果，受控更新：

- reducer current state 中的 `collaboration_reviews.code_review.additional_reviewers`（由 task event 派生）；
- 当前 code-review task 的 `risk_triggers`；
- 当前 code-review task 的 `reviewer_agents`。

不得依靠模型手工复制这些字段。

Agent 补充语义风险：

```bash
python3 scripts/risk_detect.py add \
  -g ProjectManagerAgent --task-dir ... \
  --risk-type concurrency --confidence 8 \
  --reason "跨节点重复消费可能竞争更新同一聚合" \
  --evidence-id EVD-000010
```

高置信风险不得由单个 Agent 静默删除。豁免必须：

- 每个批准者分别产生当前 development revision 的 `decision_ref` evidence，且 evidence scope 包含 detection ID；
- 由该风险的专项 Reviewer和 ProjectManagerAgent 共同批准；
- 记录原因、时间和批准者；
- 使用 `python3 scripts/risk_detect.py waive -g ProjectManagerAgent --task-dir ...` 更新，不直接修改报告。
