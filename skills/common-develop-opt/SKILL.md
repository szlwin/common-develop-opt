---
name: common-develop-opt
description: 用于新功能开发、缺陷修复和多 Agent 研发协作，覆盖需求、设计、伪代码、TDD、编码、测试、独立 Review、Git checkpoint、恢复和归档。支持 wk -b、wk -f、wk -auto、wk -min、wk -review-only、wk -gc 等开发请求；不隐式执行 Git push、merge、Release 或部署。
---

# common-develop-opt

本 Skill 用于实际软件研发。先判断任务状态并选择最小命令入口，再依据命令返回的动作上下文读取对应主题规则和项目事实；不要把所有 Reference、Evidence 或历史记录一次加载到上下文。

## 先选命令，再读主题

1. 新建任务先使用 `wk start`，已有任务的恢复或普通执行先使用 `wk begin`；执行中用 `wk progress` 查看并记录进展。
2. 以命令返回的 `actionPlan`、Owner、inputs、停止条件和 resources 作为当前动作依据；返回的资源优先于固定主题表。
3. Review 失败、迁移或维护诊断同样先取得当前动作，再调用返回的定向命令或 `ai_context`。
4. 只有返回上下文不足时，才读取一个主主题和必要的辅助主题；已返回的资源不重复读取。

## 选择入口

| 当前任务 | 首先读取 | 主要动作 |
|---|---|---|
| 新建任务或恢复任务 | [开发入口](references/getting-started.md) | 确认项目、版本、目标、范围、Owner 和停止条件 |
| 需求、Model、Flow、Impact 或 Design | [需求与设计](references/requirement-and-design.md) | 建立稳定身份、行为边界、影响和验收语义 |
| 伪代码、实施计划或编码 | [实施与编码](references/implementation.md) | 选择复用/扩展/修改/新建策略，形成可实现的最小变更 |
| TDD、测试设计或测试执行 | [TDD 与测试](references/tdd-and-testing.md) | 确认 RED/GREEN/REFACTOR、测试 seam、数据和验证命令 |
| 多 Agent 协作或独立 Review | [协作与 Review](references/collaboration.md) | 分派 Owner/Reviewer，保持独立上下文和清晰停止条件 |
| Git checkpoint、完成、恢复或归档 | [交付与恢复](references/git-and-delivery.md) | 依据门禁创建 checkpoint，处理重开、归档和回退 |
| 产物身份、引用或依赖检查 | [产物一致性](references/diagnostics/artifact-consistency.md) | 检查当前 revision 的身份、引用目标和依赖闭包 |

- `PASSED`：仅按当前声明范围和返回动作继续；不得据此推断整个任务完成。
- `PARTIAL`：列明未覆盖项并补齐适用验证；不得关闭任务或宣称全部通过。
- `UNKNOWN`：补充当前输入、声明或证据后重查；不得以猜测代替事实。
- `BLOCKED`：停止受阻动作，按返回原因修复或取得必要决策；不得绕过门禁。
- `STALE`：刷新输入 revision 并重建相关验证结果；不得沿用过期 Evidence 推进。
- `DIVERGED`：对比权威事实、定位分歧并修复后重验；不得自动覆盖冲突或强行推进。

通过实际脚本的 `--help` 发现参数；机器字段以 `assets/**/*.schema.json` 和项目声明为准。资源路径以 Asset/Reference catalog 的正式 target 为准，逻辑旧地址由解析接口兼容，不要求旧物理文件存在。

[Runtime Contract](assets/runtime/runtime-contract.json) 与 [Capability Contract](assets/development/capability-pack-contract.json) 是运行规则和能力入口的机器权威；`context-routing.json` 选择资源，[Reference catalog](references/reference-catalog.json) 定义读取角色、预算与升级链。Asset/Reference catalog 拥有资源映射和退休记录，元数据从 target 查询；`assets/resource-index.json` 是派生摘要，`FILE_MANIFEST.sha256` 仅证明完整性，均不得反向定义规则。目录结构由 [Catalog Schema](assets/package/resource-catalog.schema.json) 约束；

这里的 `wk` 指实际安装包内 `scripts/wk.py`，从项目目录调用时使用脚本绝对路径；不要假定项目中存在 `scripts/`。通过 `python3 scripts/wk.py help` 查询命令；命令及工作模式唯一来源为 [Runtime Contract](assets/runtime/runtime-contract.json#commandInterface)。维护诊断使用 `python3 scripts/ai_context.py resolve`；仅在修改本 Skill 源时运行 `python3 scripts/quality_gate.py`。

示例（在包目录执行）：`python3 scripts/wk.py help --script wk --command begin --json`。help 只查询参数；按其说明调用 begin 后，执行 `actionPlan` 中当前 Agent 获准的动作，按需读取 `context.contextRouting.resources`，命中停止条件立即暂停。这里的 stopWhen 语义由当前返回字段 `stopConditions` 提供；动作为空或输入缺失时先补齐，不自行推导命令。Agent 不得直接打开或扫描任何项目运行时或 Skill 契约 `.json`/`.jsonl` 文件（包括 `current_manifest.json`、`acceptance.json`、`traceability.json`、Review Contract 和人工 Review 模板）；包括修复、失败诊断和审计场景。版本 API/DB changeset（包括 `api.changeset.yaml`、`db.changeset.yaml`）和 `dependency-impact.changeset.yaml` 同样不得由 Agent 直接打开或写入，统一使用 `python3 scripts/merge_docs.py query|write`，由脚本负责范围校验、Schema 校验和原子写入。必须调用声明的查询脚本并使用其有界结果。Reducer、验证器、归档器和修复命令在进程内部读取 canonical JSON/JSONL/YAML 属于正常实现，不构成 Agent 直接读取例外。任何 Agent-facing 的 `recommendedReads`、`stateRef` 或 `semanticInputs` 都必须提供查询命令、`readMode=QUERY_ONLY` 和有限选择器，不得只返回裸文件路径。阶段上下文若选择了未声明的 owner/Reviewer，解析必须 fail-closed。

查看 [v1 公共入口生成清单](ai/v1-cli-entrypoints.json)，它由 `python3 scripts/cli_help.py list --json` 生成，并由门禁核对 [能力契约](assets/development/capability-pack-contract.json#/commandSurface)；生成清单不构成新的权威。历史 73 项清单仅是迁移审计资料，不代表当前公共入口。仅 `cliEntrypoints` 和 `supportCliEntrypoints` 声明独立 CLI；helper 及 `runtime/`、`document_core/`、`checks/` 模块不因文件存在而成为公开入口。

## 研发顺序

1. **需求**：确认范围、目标、验收语义、影响模块和未决决策。
2. **设计**：读取当前实现事实，确定 Model、Flow、Impact、数据和兼容边界。
3. **实施计划**：拆分原子变更、Owner、输入、停止条件和验证命令。
4. **TDD/测试设计**：按任务模式决定 RED、表征基线、测试 Case 和测试数据。
5. **开发**：按批准设计和计划实现最小变更，保持允许文件和公共逻辑边界。
6. **验证**：运行适用测试、静态检查、构建或集成验证，记录真实结果。
7. **独立 Review**：由独立 Reviewer 检查当前 revision；开放高严重性问题时停止推进。
8. **交付**：满足门禁后创建本地 checkpoint；push、merge、Release 和部署必须有明确授权。

Evidence、revision、Review 和阶段状态是上述动作的约束条件。它们用于确认结果是否可复核，不替代需求、设计、编码和测试本身。

## 文档事实与语言基线

- 版本需求事实写入 `version/{VERSION}/requirement/**`；版本模块设计/变更写入 `version/{VERSION}/module/**`；版本统一语言变更写入 `version/{VERSION}/union/**`。
- `docs/module/**` 与 `docs/union/union_language.md` 只保存 `wk -d` 归档后的项目当前全量事实，开发期不得双写。
- 模块业务枚举只在 `{MODULE}_desc.md#枚举` 定义，API/DB/Page/Test 使用稳定 enumRef 和可解析链接引用。跨模块共享业务术语先查 `docs/union/union_language.md`；正文中的裸 `[术语]` 必须唯一解析。
- 版本需求、设计、测试和页面 Markdown 只要使用 `MODULE.ENUM` 或 `MODULE.ENUM.SYMBOL`，同一行必须带 Owner 模块说明的 canonical 枚举链接；`wk -d` 同时检查当前版本文档和项目 `docs/**` 基线中的枚举、统一语言和生成文档表达。
- 正式项目文档首先服务于人阅读：直接说明业务事实、边界、原因和失败结果，不输出 Agent 思考过程、聊天式总结或没有可验证含义的“提升可维护性”套话。
- `wk -d` 文档侧按 `validate → preview → semantic merge → link normalize → conflict detection → manifest` 执行；枚举或统一语言语义冲突必须修复源事实后重新 preview，不能猜测合并。生命周期/工作模式 Gate 仍由现有 Runtime Contract 定义。


## Agent 边界

- `ProjectManagerAgent`：确认入口、编排阶段、推进计划和处理门禁。
- `Owner/DevelopAgent`：在允许范围内实现变更，不关闭自己产生的 Review 问题。
- `TestAgent`：执行测试设计或测试验证，报告真实命令和结果。
- `Reviewer/ArchitectureReviewer/RiskReviewer`：在独立上下文检查当前 revision，提出或关闭 Finding。
- 用户：确认范围、不可逆取舍、风险接受、跳过阶段和外部写入授权。

Owner、Reviewer、ArchitectureReviewer、RiskReviewer 是职责类别；实际 `-g` 名称以 CLI help 和 Runtime Contract 为准。仅切换 `-g` 不构成独立 Review；宿主不支持隔离上下文时，必须明确记录限制。

## 硬边界

- 不猜测需求、范围、验收语义、Owner、停止条件或不可逆取舍。
- 不以历史 Evidence、旧 revision 或派生报告证明当前代码已经通过。
- 不绕过失败的 Review、测试、依赖发现或迁移预览。
- 不使用 destructive reset、clean 或 checkout 覆盖用户变更。
- 不隐式执行 Git push、merge、Release、部署或真实项目迁移写入。

## 按需资源

- 文档事实：版本写 `version/{VERSION}/{requirement,module,union}/**`；`docs/module/**`、`docs/union/**` 仅由 `wk -d` 生成。枚举只在模块说明定义，统一语言以 `docs/union/union_language.md` 为权威；正式正文直接写事实、边界和失败语义。
- `wk -d` 文档层按 `validate → preview → semantic merge → link normalize → conflict detection → manifest` 执行；不改变工作模式或生命周期 Gate。
- Agent 交接形状：[StageResult](assets/review/stage-result.schema.json)。内部 document-facts、task-runtime、review-verification 职责由 [能力契约](assets/development/capability-pack-contract.json) 定义。
- 包身份和发布条件：[Release Contract](assets/package/release-contract.json)；`python3 scripts/baseline_guard.py -g ProjectManagerAgent status --json` 检查来源与完整性，不代表发布授权。
- 工作模式及阶段豁免的唯一规则见 [工作模式](references/core/work-mode-core.md)，不由口头指令或派生报告改变。
<!-- common-develop-invariant: LIGHTWEIGHT_TEST_ACTIONS_FORBIDDEN -->
<!-- common-develop-invariant: LIGHTWEIGHT_CLOSE_NON_RETROACTIVE -->
<!-- common-develop-invariant: LIGHTWEIGHT_ARCHIVE_AFTER_CLOSE_WITH_HISTORY -->
<!-- common-develop-invariant: GIT_PUSH_REQUIRES_CHECKPOINT -->
<!-- common-develop-invariant: WORK_MODE_APPLY_SYNCS_EXISTING_TASKS_BY_DEFAULT -->

以上 invariant 注释由检查器扫描，维护入口时保留这些机器标记。

- 按需索引：[AI 入口与来源映射](ai/README.md)、[机器协议归属](references/authority-map.json)、[故障代码](references/diagnostics/fault-codes.md)。
