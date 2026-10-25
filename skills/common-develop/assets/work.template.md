<!-- template: common-develop/work-v4 -->
<!-- common-develop-work-mode: model_code=false page_design=false minimal=false auto=false architecture_review=false git_checkpoint=false git_push=false lightweight=false -->
<!-- common-develop-mutation-policy: review_only=false -->
# {VERSION} 工作分工

> 工作模式（wk）：前后端伪代码=关闭；页面设计=关闭；最小流程=关闭；自动执行=关闭；两阶段开发Review=关闭；Git阶段提交=关闭；Git自动推送=关闭；轻量对话开发=关闭。

| 需求编号 | 需求名称 | 功能名称 | 功能编号 | 需求分析 | 页面设计 | 设计 | 架构设计 | 测试用例 | 前端伪代码 | 后端伪代码 | 开发 | 架构代码 | Review | 测试 | 归档 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## 使用说明

- 未使用 `wk -mc` 时，“前端伪代码”和“后端伪代码”统一填写为“无”。
- 使用 `wk -mc` 时，前后端伪代码进入工作范围，新增或原为“无”的单元格填写为 `AIW`。
- 未使用 `wk -v` 时，“页面设计”统一填写为“无”。
- 使用 `wk -v` 时，“页面设计”进入工作范围，新增或原为“无”的单元格填写为 `AIW`。
- 使用 `wk -min` 时，“测试用例”“测试”统一填写为“无”，并跳过 `test_design`、`testing` 两个测试阶段；Development 内嵌 TDD Gate 同步抑制。
<!-- common-develop-invariant: LIGHTWEIGHT_TEST_ACTIONS_FORBIDDEN -->
- 使用 `wk -ws` 时进入对话式结对开发；需求确认、分析、设计和开发允许交错。轻量模式开启期间不创建或执行 TDD、TestDesign、Review、测试代码、测试用例或测试产物；如需执行这些动作，先关闭 `wk -ws`/`wk -wc`，再按标准生命周期重开对应阶段。完整规则见 `references/work-mode-core.md`。
<!-- common-develop-invariant: LIGHTWEIGHT_CLOSE_NON_RETROACTIVE -->
- 使用 `wk -auto` 时，后续阶段在全部必需门禁、Review、证据和阻塞检查通过后自动推进，不再等待用户确认。
<!-- common-develop-invariant: LIGHTWEIGHT_ARCHIVE_AFTER_CLOSE_WITH_HISTORY -->
- 轻量模式关闭后，若当前版本任务运行时保留轻量历史，`wk -wd` 仍可按历史豁免归档；详情以 `references/work-mode-core.md` 为准。
- 使用 `wk -ar` 时，“架构代码”进入工作范围；开发阶段先提交方法签名、参数、返回值、调用编排、主要判断和逻辑分支，第一轮 Review 通过后才能开始具体方法实现，完成后再进入最终 Review。
- 未使用 `wk -ar` 时，“架构代码”统一填写为“无”。
- 使用 `wk -gc/--git-checkpoint` 时，每个阶段门禁通过后由 ProjectManagerAgent 创建本地 Git 检查点；默认不自动 push 或 merge。
<!-- common-develop-invariant: GIT_PUSH_REQUIRES_CHECKPOINT -->
- 使用 `wk -gp/--git-push` 时必须同时启用 `wk -gc`；checkpoint 成功后只同步当前分支：优先 native 非 force push；github.com 的 native 网络/认证 transport 不可用时可使用 GitHub Connector exact-tree/message adapter，并经 receipt 校验 remote/可发现 PR HEAD；不创建/合并 PR、不建临时 branch/workflow。任何 transport 未验证时不进入下一阶段。
- `wk -review-only` 时只允许 Task Facts 与项目文档范围的写入，测试、配置和生产代码修改由机器策略拒绝。
<!-- common-develop-invariant: WORK_MODE_APPLY_SYNCS_EXISTING_TASKS_BY_DEFAULT -->
- 修改已有 `work.md` 时，`work_mode.py apply/init` 默认同步已有任务运行时；需要跳过时使用 `--no-sync-existing-tasks`。
- `wk -mc`、`wk -v`、`wk -min`、`wk -ws`、`wk -auto`、`wk -ar`、`wk -gc`、`wk -gp`、`wk -review-only` 可以组合；`wk -ws` 只与需求、设计和开发范围组合，不能在保持开启时恢复 TDD、TestDesign、Review 或测试；`wk -gp` 仅在同时启用 `wk -gc` 时合法；`wk -v -min` 表示执行页面业务行为设计但跳过测试阶段。自动执行不绕过 Agent 权限、Review、证据、P0/P1 阻塞或安全边界。多个需求始终按声明顺序逐个执行。
- “页面设计”仅指页面业务字段、操作、状态和交互行为设计，不包含视觉风格、高保真原型或像素级设计。
