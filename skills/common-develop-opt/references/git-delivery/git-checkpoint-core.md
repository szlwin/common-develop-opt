# Git Checkpoint 核心规范

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

<!-- common-develop-invariant: GIT_PUSH_REQUIRES_CHECKPOINT -->
`wk -gc` 只有在当前阶段门禁通过后才创建连续的本地 Commit。`wk -gp/--git-push` 是显式的依赖模式，始终要求先启用 `wk -gc`。

远端传输顺序为 `NATIVE_GIT -> GITHUB_CONNECTOR`。Native Git 仍是首选路径，使用有界传输超时，并要求远端分支 HEAD 等于本地 checkpoint commit SHA。只有在 `github.com` remote 不可用 Native 网络/认证传输时，ProjectManagerAgent 才能使用 Connector adapter。`git_divergence`、被拒绝或非 fast-forward 更新，以及远端验证失败，都不能触发 fallback。

Connector fallback 是确定性的 Git Data handoff，不是通用远端写入：`git_checkpoint.py` 输出精确 request，其中包含当前 repository/branch、checkpoint/base 身份、变更 Git 对象、精确目标 tree SHA 和 commit message。连接的 GitHub 工具只能读取当前 branch/commit，创建请求的 blob/tree/commit，在当前 branch 不存在时创建它，使用 `force=false` 更新当前 branch，并读回 branch/PR head。它必须返回由 `connector-complete` 校验的 receipt，校验通过后才能设置 `REMOTE_CHECKPOINTED=true`。不需要也未授权创建临时 PR/workflow/branch。

由于 GitHub Connector `create_commit` 负责 author/committer 元数据，Connector 模式产生的远端 commit SHA 可能不同于本地 checkpoint SHA。只有满足 `EXACT_TREE_MESSAGE_EQUIVALENT` 才有效：必须验证精确目标 tree SHA、精确规范化 commit message/trailers，以及精确或等价的 parent tree+message；本地和远端 SHA 分开记录，绝不改写本地历史来伪造身份。Connector receipt 失败或处于 pending 时阻断阶段推进。绝不 force-push、push tag、merge、创建 PR、改写历史、发布或部署。

本地 index 边界：checkpoint 提交的是执行时临时 index 的快照。新 checkpoint 要求调用前真实 index 没有 staged 内容且 index tree 等于 `HEAD` tree；已有 staged 内容、index tree 偏差或已有标准 `index.lock` 时必须拒绝并保持调用方真实 index 不变。提交成功后，只有在真实 index digest、`HEAD` 和标准 `index.lock` 检查均未变化时，才原子刷新真实 index 到新 `HEAD`；检测到外部变化或锁冲突时保留真实 index，并在结果中返回 `indexSync` 原因。若随后单独执行阶段推进，`task_events.jsonl` 等生命周期事实可能再次变化并显示为未提交，这表示新的时间点变更，不表示 checkpoint 漏提交。用 `git show --stat HEAD`、`git diff HEAD --stat` 和 `git diff --cached --stat` 分别核对提交内容、提交后的工作树变更和暂存区内容。
