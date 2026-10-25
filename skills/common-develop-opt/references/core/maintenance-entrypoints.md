# 维护入口和可维护性规则

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

本文只维护入口分层和维护约束，不复制 Runtime、Review 或 Evidence 的完整契约。具体字段和门禁请回到对应的 `assets/*.json`、`SKILL.md` 和 Reference。

## 1. 入口分层

v1 命令面由 [能力契约](../../assets/development/capability-pack-contract.json#/commandSurface) 维护。KEEP 从同文件的 CLI 声明生成，INTERNAL/MERGE 只保留内部实现或历史退役身份；`cli_help.py list --json` 返回全部决策及来源指针。权限仍由 Runtime Contract 决定，检查器将其与实际声明 parser 对齐。根目录 helper 的存在不代表独立 CLI 承诺，删除前仍须核对内部消费者。

`wk` 的 19 个命令只在 `scripts/document_core/wk_dispatcher.py` 注册参数。公共入口和 `runtime/wk.py` 均复用此 parser；`runtime/_core/wk_cli.py` 仅绑定旧入口回调、保留 `args.func` 和描述，不独立定义命令、参数或默认值。

`case_doc`、`work_mode`、`runtime_contract` 等文档和工作流使用的入口保留 KEEP；`document_facts` 只作为模块接口。能力包查询经 `wk packs`；禁止从旧归档 inventory 推导另一套入口或权限。

### 普通用户入口

- `scripts/wk.py`：新建用 start 预览/必要确认/apply，恢复用 begin，普通执行消费 actionPlan；ProjectManagerAgent 用 progress 推进，finish 汇总项目声明验证。
- `scripts/long_task.py`：长任务生命周期命令的公开 facade。
- `scripts/ai_context.py`：已有执行包仍缺资料或需要诊断时，按任务类型和请求补充上下文；不是 begin 之后的固定步骤。

常用入口统一见 [开发入口](../getting-started.md#常用命令入口)。`long_task.py` 是返回动作或恢复诊断要求时使用的底层公开入口，不是普通任务必须先调用的步骤。这些入口必须遵守当前任务 revision、权限和 lifecycle reducer；维护脚本不能作为绕过门禁的替代入口。

### 维护/诊断入口

普通业务任务不运行本 Skill 的测试、清理和包清单重写。以下包质量检查仅用于 Skill 源维护；只读故障诊断按需选择 check/query，不自动升级为维护写入。

- `scripts/check_skill.py`：Skill 结构、契约、路由、权限和 Manifest 检查。
- `scripts/semantic_audit.py`：语义、引用和重复规则审计。
- `scripts/quality_gate.py`：组合质量门禁编排。
- `scripts/baseline_guard.py`：基线身份和工作树偏差检查。
- `scripts/cli_help.py`：声明 CLI 的 help surface 验证。
- `scripts/checks/generate_resource_index.py`：按需重建派生资源索引，默认定位自身所属安装包；`--root` 可指定隔离包。正常包维护由质量门禁的 manifest 更新调用同一实现。
- `scripts/current_manifest.py`：Manifest 生成和查询。
- `scripts/runtime_contract.py`、`scripts/context_routing.py`、`scripts/reference_reader.py`：机器契约、路由和 Reference 查询。

## 2. 稳定 Alias

### 资源 Alias

`context-routing.json#resourceCatalog` 中的 Alias 必须满足：

1. `aliasOf` 指向存在且唯一的 canonical resource；
2. Alias 不再指向另一个 Alias，不形成链或环；
3. Alias 与目标共享真实 `path`，并使用明确的 `sectionIds`；
4. Alias 只降低按需读取成本，不复制正文、术语或门禁；
5. 新增同义 Alias 前先确认已有 canonical ID 不能满足入口需求。

资源目标即使没有 direct route，也不能因此删除；应区分 `direct route`、`alias target`、`external contract` 和真正 orphan。`check_skill` 会对目标存在性、路径和可达性执行 fail-closed 检查。

### CLI Alias

CLI Alias 只在 `assets/runtime/runtime-contract.json#scriptCommandSurfaces.aliases` 中声明。Alias 继承 canonical 命令的权限和 mutability，不在 `mutability` 表中重复声明。变更命令名时先保留稳定 Alias，再在独立迁移中移除旧名。

### 脚本兼容入口

`scripts/` 中的薄 CLI facade 保留已公开的脚本路径，不等于上面的命令 Alias，也不保存另一套业务实现。它们通过 `compat_adapter` 加载 `scripts/runtime/` 或 `scripts/checks/` 中的实现，CLI 调用缓存 namespace 中的入口函数；不会再次用 runpy 执行整个文件。Review Contract 和 Review Session 必须调用各自带错误处理的入口，不能直接替换为内部 main。

Python 导入保持既有导出；会用 API 替换 sys.modules 的底层 facade 也只初始化一次。加载缓存只对本进程有效，不支持运行中热替换源码；更新安装包后启动新进程。

包内 compat 模块间继续调用已有模块 API，不经公共 CLI 再派生进程。不要把所有裸 import 视为顶层桥接调用，必须结合实际 sys.path 与模块来源判断。新服务已有明确 API 时直接使用该 API，不引入 globals 转发或通过 Shell 调用同包 CLI。

减少默认文档入口不代表删除其他脚本。删除任何公共路径前，必须核对契约、权限发现、生成命令、恢复指引、Python 调用及外部消费者，并存在可验证的等价替代。仓库内无命中不证明外部无调用；完整门禁和独立包验证期间保留旧路径。

## 3. 硬约束分级

资源 `priorityClass` 的用户可见含义如下：

| 级别 | 语义 | 处理策略 |
| --- | --- | --- |
| `mandatory` | 当前动作不可安全执行时必须读取 | 不能因 token 预算静默省略；不足时返回 `CONTEXT_INSUFFICIENT` |
| `required-if-triggered` | 仅在已命中的条件或模式下成为必要上下文 | 命中后按 mandatory 处理，否则说明未触发 |
| `helpful` | 提升质量但不构成动作前置条件 | 可在预算不足时省略，并记录原因 |
| `example-only` | 示例或参考，不是事实源 | 默认不进入最小上下文 |

普通用户输出只需说明“缺少哪类必要上下文”和下一步；完整 `omittedResources`、digest 和诊断字段保留在 JSON/维护入口。

## 4. 并发策略

- 只读查询（status、next、context、help、contract/query、validate）可以并行执行，前提是它们读取同一稳定 revision。
- 生命周期、Evidence、Review、归档、Manifest 和 Git checkpoint 写入必须串行。
- 同一 task 同时只能有一个 writer；写入前后都要绑定 revision，失败不得用后续异常覆盖原始业务错误。
- Git checkpoint、rollback、push 和 Hook 相关操作必须串行；`git_checkpoint.py` 使用 Git metadata lock、临时 index 和 index digest 校验，避免并发写入或 Hook 改写用户 index。自动 checkpoint 提交使用 `--no-verify`，Hook 不属于 checkpoint commit 的隐式输入。
- 并行只读不能被解释为允许并行写入，也不能绕过现有 reducer 和权限契约。

## 5. Resource Catalog 生命周期

`context-routing.json#resourceCatalog` 的每个资源都必须显式声明 `status`、`owner`、`replacement` 和 `lastReviewed`。`status` 只允许 `active`、`deprecated`、`internal`、`on_demand`；废弃资源必须提供仍存在的替代资源，其余状态的 `replacement` 必须为 `null`。

资源读取优先级 `priorityClass` 与规则硬约束等级是两个独立维度，禁止互相替代。硬约束等级的唯一机器来源是 `context-routing.json#governancePolicy.hardConstraintLevels`，级别为 `HARD_GATE`、`REQUIRED`、`RECOMMENDED`、`EXAMPLE`。

## 6. 重复内容治理

- `SKILL.md` 只保留启动顺序、事实源和不可绕过门禁。
- 仓库根目录 `README.md`/`README.zh-CN.md` 提供用途、安装、参数和 Quick Start；安装后的 Skill 只保留 `SKILL.md` 作为 Agent 入口。
- `ai/README.md` 只解释 AI context payload 和读取策略。
- 详细语义放在单一 canonical Reference；摘要使用资源 Alias/section，不复制整段正文。
- `agents/openai.yaml` 只保留触发时的短启动提示，并链接上述入口。
