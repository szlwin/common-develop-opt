# Git 阶段检查点

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

<!-- common-develop-invariant: GIT_PUSH_REQUIRES_CHECKPOINT -->
`wk -gc/--git-checkpoint` 在阶段门禁通过后创建本地 Git Commit。默认不自动 push。`wk -gp/--git-push` 是独立显式模式且必须依赖 `wk -gc`。远端 transport 顺序固定为 `NATIVE_GIT -> GITHUB_CONNECTOR`：native Git 可用时仍在有界 transport timeout 内执行当前分支非 force push 并要求 remote HEAD == local checkpoint SHA；只有 `github.com` remote 的 native 网络/认证 transport 不可用时，才允许 ProjectManagerAgent 使用 GitHub Connector Git Data adapter。任何模式都不 merge、不创建 PR、不 push tag、不发布或部署，也不降低 Review、Evidence、P0/P1 和 revision 门禁。

## 1. 执行模型

执行策略只读取 `assets/runtime/runtime-contract.json#/executionPolicy`：全流程固定为 `SEQUENTIAL`。A、B、C 可以按阶段组织，但必须按 `project_process.md` 声明顺序逐个执行；同一时刻最多一个目标或任务处于实际执行状态。

阶段推进顺序：

```text
StageOutcome PASSED
→ 无开放 P0/P1
→ `-gc` 创建 Git 检查点 Commit
→ `-gp`（如启用）优先 native push；transport 不可用时生成 Connector request → GitHub Connector exact Git Data publish/verify → `connector-complete` 校验 receipt → 验证 remote/PR head
→ 只有 remote 验证成功后才写入下一阶段 transition
→ 进入下一阶段
```

仅 `-gc` 模式下，提交失败时回滚本次尚未生效的 event ledger / checkpoint 事务，不依赖 `task_state` cache 回滚。`-gp` 模式下 checkpoint 先在当前 PASSED phase 上形成；push/remote verification 失败时保留可重试的本地 checkpoint，但不写入下一阶段 transition，因此 `current_phase` 不前移。已经通过的业务阶段不重新执行。

## 2. 唯一记录

```text
version/{VERSION}/task/{TARGET_ID}/git_checkpoints.yaml
```

该 YAML 追加保存：检查点 ID、阶段、iteration、stage revision、StageOutcome、分支、基准提交、变更路径摘要、Commit 标题、创建时间和被替代检查点。

检查点只允许在门禁通过后创建，所以不重复保存恒定的通过状态；Trailer 规则也由运行契约统一定义，不在每条记录中复制。

文件不保存自身 Commit SHA，避免内容寻址自引用。实际 SHA 通过以下 Trailer 唯一解析：

```text
WK-Checkpoint: {CHECKPOINT_ID}
```

## 3. Commit 信息

```text
{type}({scope}): complete {TARGET_ID} {phase summary}

- 最多三条关键变化

WK-Checkpoint: {CHECKPOINT_ID}
WK-Revision: {STAGE_REVISION}
```

门禁、Reviewer、Evidence、工作模式和完整文件历史保存在任务事实中，不重复写入 Commit Message。

## 4. 提交门禁

- 执行者必须是 ProjectManagerAgent；
- `work.md` 已启用 `git_checkpoint=true`；启用 push 时还必须有 `git_push=true`，且 `git_push=true` 没有 `git_checkpoint=true` 属于非法模式；
- 当前 Artifact、Review 与 StageOutcome 绑定同一 `PASSED` revision；
- 当前阶段任务均为 `PASSED/NOT_APPLICABLE`；
- 无运行中 attempt；
- 当前仓库非 detached HEAD，且不处于 merge/rebase/cherry-pick/revert 中间状态；
- 当前分支不是受保护分支；
- 执行前真实 Git index 必须没有 staged 内容且其 tree 必须等于当前 `HEAD` tree；发现 staged 内容、index tree 偏差或已有标准 `index.lock` 时必须 fail-closed，并保持调用方真实 index 不变；checkpoint 使用隔离的临时 index 组装提交，提交成功且 index digest、`HEAD` 和标准 `index.lock` 校验均未变化时，才原子刷新真实 index 到新 `HEAD`；
- 所有改动都属于当前目标、阶段任务允许范围或阶段状态迁移；
- 敏感文件、凭据、缓存和构建临时文件不得进入提交；
- `WK-Checkpoint` 在本地 Git 历史中唯一。

提交后的状态解释：`git commit` 提交的是临时 index 的快照。正常情况下，提交成功后会在标准 `index.lock` 保护下把真实 index 原子刷新到新的 `HEAD`，因此如果工作树没有新的修改，`git status` 应保持 clean。若 index digest、`HEAD` 或锁状态发生变化，则 checkpoint commit 仍保留，但真实 index 不被覆盖，结果中的 `indexSync` 会给出保留原因；若刷新本身失败，结果会返回 `COMMITTED_WITH_INDEX_REFRESH_FAILED`，也不能把它当作 clean。若随后单独执行 `advance-phase` 或其他生命周期命令，事件账本可能追加新的 `TASK_STATE_PATCHED` 等事件，因此 `git status` 再次显示 `task_events.jsonl` 未提交是正常的后续变更；它不表示前一个 checkpoint 漏提交。不要用恢复旧 index 的方式“清理”该状态，也不要把工作树必须立即干净作为 checkpoint 成功条件。

建议按时间点检查：

```bash
git show --stat HEAD
git diff HEAD --stat
git diff --cached --stat
```

三条命令分别用于确认 checkpoint 提交内容、checkpoint 后工作树新增内容和当前暂存区内容。正常 checkpoint 成功后，`git diff --cached --stat` 应为空；若返回 `indexSync.status != REFRESHED`，必须优先保留并检查当前 index。`git restore --staged :/` 会取消所有 staged 内容，只能在确认这些内容全部是异常残留并且不属于用户工作时使用，不能作为 checkpoint 的通用收尾动作。

## 5. 远端推送门禁

启用 `wk -gp/--git-push` 后：

- 仅 ProjectManagerAgent 可执行；
- 必须同时启用 `wk -gc`；
- 只允许当前 checkpoint commit，且 local `HEAD` 必须等于该 checkpoint SHA，禁止顺带推送未 checkpoint 的后续 commit；
- remote 按 `branch.<name>.remote` 解析，缺失时才回退 `origin`；无可解析 remote 直接阻断；
- refspec 固定为 `HEAD:refs/heads/{CURRENT_BRANCH}`；禁止 `--force`、`--force-with-lease`、tag push 和跨分支 push；
- native push 返回成功后必须用 `git ls-remote --heads` 反向确认 remote branch HEAD 与 local checkpoint SHA 完全一致；GitHub PR ref 可发现时同样校验；
- non-fast-forward/rejected/remote verification mismatch 属于硬阻断，**不得**转 Connector；只有 `native_transport_unavailable` / `native_auth_unavailable` 且 remote host 为 `github.com` 才能 handoff；
- Connector handoff 由 `scripts/git_connector_transport.py` 生成 deterministic request，存放在 Git metadata (`.git/common-develop/git-connector/`) 而不是工作树，不制造未 checkpoint 的项目文件；
- request 对每个 changed blob 给出内容、local Git object SHA 和 tree element；Connector `create_blob` 返回 SHA 必须与 local object SHA 一致，`create_tree` 返回 SHA 必须与 local checkpoint tree SHA **完全一致**；DELETE 只允许删除 request 声明路径；
- Connector 必须先证明 remote current parent 与 local base 等价：已知 remote parent SHA 时要求 exact SHA；缓存丢失时至少要求 exact base tree SHA + normalized base message SHA-256。若 remote HEAD 已经是 target checkpoint-equivalent commit，可返回 `ALREADY_PRESENT/NO_CHANGE`，不得重复提交；
- Connector 创建 remote commit 时必须使用 exact normalized checkpoint message（含 `WK-Checkpoint` / `WK-Revision` trailer）和 exact target tree，并只以 resolved equivalent parent 为 parent；因 Connector provider-owned author/committer metadata，remote commit SHA 可以与 local checkpoint SHA 不同，但必须记录 `localCommitSha + remoteCommitSha`，identityMode=`EXACT_TREE_MESSAGE_EQUIVALENT`，禁止 reset/amend/rewrite 本地历史伪造 SHA 相等；
- 更新 remote 只允许当前分支 `force=false`，或者当前分支不存在时只创建该当前分支；禁止临时 branch、PR、workflow、tag、force ref update；
- Connector 完成后必须反向读取 current branch HEAD；可发现已有 PR 时还必须验证 PR head == remote commit SHA。只有 receipt 通过 `git_checkpoint.py connector-complete` 全部字段、blob/tree/message/parent/ref/PR 约束校验后，`REMOTE_CHECKPOINTED=true`；verified receipt 仅在短 TTL 内可直接供同一次 phase advance 消费，超时必须重新 Connector 反向验证，避免恢复后把陈旧 remote 状态当成当前事实；
- `git_checkpoint.py push` 可用于重试；若返回 `CONNECTOR_REQUIRED`，`advance-phase` 必须保持当前 phase，不写 lifecycle transition。

## 6. 重开与完成阶段

阶段重开时创建新 iteration 和新检查点，通过 `supersedes_checkpoint_id` 指向同阶段旧检查点；不 amend、reset 或删除历史。

普通阶段由 `long_task.py advance-phase` 事务性提交并推进。`completion_verification` 没有下一阶段，完成后显式执行 `git_checkpoint.py commit`。

## 7. 命令

```bash
python3 scripts/git_checkpoint.py commit -g ProjectManagerAgent --task-dir {TASK_DIR}
python3 scripts/git_checkpoint.py push -g ProjectManagerAgent --task-dir {TASK_DIR}
python3 scripts/git_checkpoint.py connector-request -g ProjectManagerAgent --task-dir {TASK_DIR}
python3 scripts/git_checkpoint.py connector-complete -g ProjectManagerAgent --task-dir {TASK_DIR} --receipt-file {RECEIPT_JSON}
python3 scripts/git_checkpoint.py status -g ProjectManagerAgent --task-dir {TASK_DIR} --json
python3 scripts/git_checkpoint.py validate -g ProjectManagerAgent --task-dir {TASK_DIR} --require-resolved --json
```
