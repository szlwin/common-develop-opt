# Acceptance Assertion 机器验收

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

## 0. 验收快速规则 {#acceptance-quick-rules}

每个验收项必须有稳定 ID、可观察的预期结果、验证方式和 Evidence 引用。只有当前 revision 通过声明的验证路径产生预期结果时，验收项才算通过。

本文件定义自然语言验收标准到结构化 Assertion 的编译、执行和结果绑定。

## Acceptance Assertions

自然语言验收标准继续作为业务说明，但机器完成门禁只读取：

```text
task/{TARGET_ID}/acceptance.json
```

这是唯一 canonical Assertion 文件。旧任务残留的 `acceptance_assertions.json` 不参与读取、验证或 Review，不得双写。

每个 traceability acceptance ID 至少对应一个结构化 assertion：

> 机器结构示例已移出正文；字段和状态以 [机器字段与状态边界](../core/machine-boundaries.md) 指向的 Schema 为准。
实施计划中每一条自然语言 `expected_results` 也必须有稳定 source reference：

```text
{TASK_ID}#expected_results/{从 0 开始的序号}
```

例如 `TASK-DEV-001` 的第 2 条 expected result 必须被某个 assertion 的
`source_refs` 覆盖为 `TASK-DEV-001#expected_results/1`。仅创建同 acceptance ID 的其它
assertion，不能替代这条逐项映射。

支持的白名单断言：

- `TEST_CASE`：测试 Case 状态；
- `COMMAND_EXIT`：标准化命令退出码；
- `JSON_PATH`：JSON 证据字段；
- `SQL_VALUE`：已注册 SQL 查询结果；
- `METRIC_THRESHOLD`：性能或质量指标阈值；
- `FILE_PATTERN`：文件内容包含/不包含；
- `FILE_DIGEST`：文件摘要；
- `SET_EQUALS`：集合相等；
- `MANUAL_REVIEW`：无法自动判定的专业语义。

断言与证据类型也受白名单约束：`TEST_CASE` 只能使用 `test_ref`，`COMMAND_EXIT` 只能使用绑定 command-result 的 `command_ref`，`METRIC_THRESHOLD` 只能使用 `metric_ref`；`SQL_VALUE` 的 evidence scope 必须覆盖 `query_ref`，指标 evidence scope 必须覆盖指标名称。真实文件但类型或作用域不匹配，仍不得作为验收证据。

添加和验证示例：

```bash
python3 scripts/acceptance.py add \
  -g TestDesignAgent --task-dir ... \
  --assertion-id ASRT-ORDER-001 \
  --acceptance-id AC-ORDER-001 \
  --statement "重复取消不得重复退款" \
  --type TEST_CASE \
  --phase testing \
  --revision TEST-R02 \
  --blocking \
  --parameters '{"test_case_id":"CASE-ORDER-IDEMPOTENT-001","expected_status":"PASSED"}' \
  --source-ref requirement.md#AC-ORDER-001

python3 scripts/acceptance.py verify \
  -g TestAgent --task-dir ... \
  --assertion-id ASRT-ORDER-001 \
  --evidence-id EVD-000001
```

职责：

- RequirementAnalysisAgent 为自然语言验收项分配稳定 acceptance ID；
- TestDesignAgent 将可机器验证的验收项编译为白名单 assertion，并建立 Case/命令/数据映射；
- 专业 Reviewer 处理 `MANUAL_REVIEW`；
- TestAgent、TddAgent、DevelopAgent 或 Reviewer 产生注册 evidence；
- CompletionVerificationAgent 验证所有适用 blocking assertions。

无法编译的预期必须记录 `UNRESOLVED` 或转换为 `MANUAL_REVIEW`，不得默认通过。完成门禁要求：

- traceability 中每个适用 acceptance ID 都有 assertion；
- 每条当前任务 `expected_results` 至少映射到一个 `blocking=true` 且 `VERIFIED` 的 assertion；
- 所有适用 blocking assertion 都为 `VERIFIED`；
- 不存在 `UNRESOLVED/FAILED/BLOCKED`；
- `MANUAL_REVIEW` 有当前 revision 的专业 Reviewer `PASSED` 结论和 evidence。

语义编译本身还必须经过 `RC-ACPT-001` Review：Test Design 阶段由
RequirementReviewAgent 检查验收语义，Implementation Plan 阶段由 PlanReviewAgent 检查
每条任务 expected result 的断言映射。脚本验证引用、状态和证据；Reviewer 验证断言是否
弱化、遗漏或替换了原业务含义。
