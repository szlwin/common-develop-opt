# Evidence Registry 与可信证据

本文件定义可信证据总原则和 Evidence Registry 的唯一可读规则。

## 总原则

```text
文件、命令、测试、决策或人工审查结果
→ 注册为不可变 evidence
→ Review、StageOutcome、attempt 和验收断言只引用 evidence_id
→ 脚本回查文件、SHA-256、revision、phase、scope 和状态
→ 风险与验收全部满足后才允许完成和归档
```

Agent 不得直接手写 digest，不得用裸路径或一句“已验证”代替 evidence。旧 evidence 被新 revision 替代时保留其不可变记录并通过阶段 iteration/revision 逻辑失效；只有确需表达替代关系时才显式标记 `SUPERSEDED`，不可信或撤销的 evidence 标记 `REVOKED`，历史记录不得删除。

重开后的普通生命周期门禁采用当前 revision allow-list：只展开当前 iteration 所需的
ACTIVE Evidence，历史 revision 的条目仅保留结构化元数据和稳定 ID。旧文件缺失或 digest
漂移不会阻断新的 attempt；显式按历史 revision 查询或执行归档/完整审计时，才展开历史
对象并报告其完整性问题。这样既保留审计能力，也避免在每次需求变更时批量处理已废弃证据。

有界 `amend-phase` 不批量重写 Evidence Registry。未受影响且由 amendment 事件链明确保留的 task 可继续引用其原 revision Evidence；受影响 task 的 runtime validation Evidence 会被清空，必须针对 amendment revision 重新登记。命令参数中的 `preserve-evidence-id` 与 `invalidate-evidence-id` 是本轮影响声明：前者不改变 Evidence 条目状态，后者进入当前 Amendment 的禁止使用集合，所有当前 Attempt、Review、Acceptance、Trace、StageOutcome 和风险 Gate 均拒绝它，但不会删除或伪造 `SUPERSEDED` 状态。若失效 Evidence 仍被未受影响 PASSED/NOT_APPLICABLE task 引用，amendment 直接失败。需要改变 Registry 状态时仍显式使用 `evidence.py status`，并遵守 replacement 规则。

### 1.1 DOCUMENT_LAYOUT 前置门禁

`task_verify.py` 的所有 Profile 都包含 `DOCUMENT_LAYOUT`。发现 `version/{VERSION}/docs`、`document` 或 `documents` 时，返回 `INVALID_VERSION_DOCS_DIRECTORY`（或对应目录代码）并阻断后续阶段。Task Plan 的 `allowed_files` 只能使用 `docs/**`、`version/{VERSION}/doc/**`、`version/{VERSION}/task/**`。

直接执行 `task_verify.py verify` 属于显式完整审计，默认展开并校验全部历史 Evidence；普通生命周期门禁及 `ai_context.py` 的任务健康摘要使用 current scope，只校验当前 `IN_PROGRESS/PASSED` revision。两者不得互相替代。

普通 Evidence 查找只扫描：

- `version/{VERSION}/doc`；
- `docs`。

`version/{VERSION}/docs` 不属于当前文档布局；发现该目录时直接返回
`INVALID_VERSION_DOCS_DIRECTORY`，不能参与 digest、Review 完整性验证或归档门禁。


## Evidence Registry

每个长任务必须存在：

```text
task/{TARGET_ID}/evidence/registry.jsonl
```

证据项至少包含：

```json
{
  "evidence_id": "EVD-000001",
  "type": "test_ref",
  "ref": "evidence/test-output.json",
  "digest": "sha256...",
  "revision": "TEST-R02",
  "phase": "testing",
  "generated_by_agent": "TestAgent",
  "generated_at": "2026-07-22T10:00:00-05:00",
  "command_result_ref": "evidence/runs/{REVISION}/test.json",
  "command_result_digest": "sha256...",
  "scope_refs": ["CASE-ORDER-001"],
  "status": "ACTIVE",
  "superseded_by": "",
  "metadata": {}
}
```

注册命令：

```bash
python3 scripts/evidence.py register \
  -g TestAgent \
  --task-dir project_doc/version/{VERSION}/task/{TARGET_ID} \
  --type test_ref \
  --ref evidence/test-output.json \
  --revision TEST-R02 \
  --phase testing \
  --scope CASE-ORDER-001 \
  --command-result-ref evidence/runs/{REVISION}/{RUN_ID}.json
```

校验与状态变更：

```bash
python3 scripts/evidence.py validate -g ProjectManagerAgent --task-dir ...
python3 scripts/evidence.py show -g ProjectManagerAgent --task-dir ... --evidence-id EVD-000001
python3 scripts/evidence.py status -g ProjectManagerAgent --task-dir ... --evidence-id EVD-000001 \
  --status SUPERSEDED --replacement-id EVD-000002
```

`evidence.py query` is a bounded ordinary read: pass `--revision` for a
current-revision query so only that revision's source bytes are expanded and
validated. Historical entries remain visible as metadata but their old
mutable references are not revalidated on this scoped path. An unscoped query
retains full-registry validation; use the explicit `evidence.py validate`
command when a full historical audit is required without filtering results.

When an active historical `DIRECT` record points at a canonical file that has
since changed, but its original digest object is still present under
`evidence/objects/`, a ProjectManagerAgent may run
`evidence.py repair-history --evidence-id ...`. The command is locked and
idempotent: it keeps the Evidence ID, revision, digest, timestamps, and all
existing references, changes only the capture mode to `SNAPSHOT`, stores the
canonical path in `source_ref`, and points `ref` at the verified immutable
object. It fails closed when the object is missing, duplicated, or its bytes
do not match the historical digest. It must not be used to rewrite a digest,
alter an event ledger, or supersede an evidence record.

硬门禁：

- 文件必须存在且 SHA-256 与注册值一致；
- evidence revision 必须等于被验证对象的当前 revision；
- criterion 还要求 evidence phase 和 type 符合 Profile；
- command evidence 必须绑定真实 command-result；
- command-result 文件自身也必须匹配注册时的 `command_result_digest`，不能只校验其输出文件；
- 当前 Review、阶段结果、完成验证和归档只能使用 `ACTIVE` evidence；
- StageOutcome、attempt、Review criterion、风险报告、Acceptance Assertion 和 archive gate 均通过 evidence ID 引用同一注册表。

`task_plan.json`、`development_task_plan.json`、`task_events.jsonl` 中 attempt/StageOutcome payload 的 Evidence ID、`traceability.json` 和 `merge_plan.yaml#archiveGate.evidenceIds` 均是当前 schema 的 evidence ID 字段，不接受任意路径字符串。


### 2.1 多源 Evidence Bundle

同一 Revision 需要冻结两个及以上文件时，使用 `bundle-register`，将多种 Evidence role 合并到一个内容寻址归档；Review 通过 `used_as_types` 判断该 Bundle 可承担的证据角色。逐文件 Snapshot 只用于单个可变 canonical 文件或确有独立生命周期的 Artifact。

### 2.2 单文件 command-result

一条命令默认只产生一个 schema v2 JSON：

```json
{
  "schema_version": 2,
  "kind": "command_result",
  "status": "PASSED",
  "error_code": "",
  "command": "python3 validation/test_contract.py",
  "exit_code": 0,
  "executed_at": "2026-07-26T10:00:00-05:00",
  "completed_at": "2026-07-26T10:00:01-05:00",
  "timeout_seconds": 300,
  "duration_ms": 1000,
  "revision": "REQCONF-R04@hash",
  "output": "5/5 PASSED
",
  "output_digest": "sha256-of-inline-output"
}
```

- UTF-8 输出不超过 16 KiB：使用 `output`，不得再创建配套 `.output.txt`；
- 大输出或二进制输出：省略 `output`，使用 `output_ref + output_digest`；
- `output` 与 `output_ref` 互斥；
- 命令结果统一使用 schema v2。
- 新生成的 schema v2 command-result 必须记录 `status`、`error_code`、`completed_at`、`timeout_seconds` 和 `duration_ms`；历史 v2 结果读取时保留兼容，但不会补写缺失字段；
- 命令因 timeout 失败时固定使用 `exit_code=124`、`error_code=COMMAND_TIMEOUT`，并在 `metadata.timed_out=true` 中标记；其他非零退出使用 `COMMAND_EXIT_NONZERO`。


### 2.3 通用 Execution Evidence Collector

common-develop 提供技术栈无关的执行采集器：

```bash
python3 scripts/execution_evidence.py run \
  -g {CURRENT_AGENT} \
  --task-dir project_doc/version/{VERSION}/task/{TARGET_ID} \
  --phase {PHASE} --revision {EXACT_REVISION} \
  --command "{PROJECT_DECLARED_COMMAND}" \
  --cwd {PROJECT_ROOT} --mutation-scope taskFacts --json
```

内核只捕获 command、cwd、开始/结束时间、实际 timeout、运行时长、exit code、revision 和 phase，并登记 `command_ref` Evidence；通用 `execution_evidence.py` 将 stdout/stderr 合并到 `output`，而 `finish-attempt --auto-evidence` 额外分别保留 stdout 与 stderr。内核不解析具体测试框架、构建工具、数据库或报告格式。项目若需要额外 metrics，应由项目本地 adapter 产生新的结构化 Evidence，不能把技术适配逻辑写进 common-develop 内核。
