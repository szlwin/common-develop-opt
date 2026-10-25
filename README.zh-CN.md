# common-develop-opt

`common-develop-opt` 是面向软件研发和多 Agent 协作的通用 Skill。它把需求、设计、开发、测试、Review、Evidence、长任务恢复和文档归档组织成一条可审计的研发流程。它是 `common-develop` 重构优化后的版本，也是今后维护、安装和发布的唯一 Skill 源；`skills/common-develop/` 是重构前的旧版包，仅作历史参考保留。

本仓库采用与 `onepage-skill` 一致的分层结构：仓库根目录承载说明和维护资料，`skills/common-develop-opt/` 是唯一可安装、可触发和可发布的 Skill 源。

## 适用场景

- 新功能、缺陷修复和跨模块变更
- 需求确认、需求分析、设计和实施计划
- TDD、测试设计、测试执行和测试 Evidence 管理
- 独立 Code Review、架构 Review 和风险 Review
- 长任务、跨会话恢复、阶段推进和失败重试
- 需求、设计、页面、流程、业务模型和跨模块影响的事实归档
- 多 Agent 串行协作、只读并行分析和写入边界控制
- 任务状态、版本、权限、引用、Schema 和交付门禁的自动校验

不适合把它当作普通聊天总结、商业计划、视觉稿工具、Git merge 工具或部署工具。它不会自动执行 `git push`、merge、Release 或部署。

## 与其他开发 Skill 的差异

目前流行的开发 Skill 大多是提示/剧本式（playbook）：`SKILL.md` 用自然语言描述流程要求，由模型自觉执行。例如 [mattpocock/skills](https://github.com/mattpocock/skills) 把日常工程实践拆成一批小而可组合的技能（grilling 对齐需求、`tdd`、`code-review`、`handoff` 等），状态外置为 GLOSSARY、ADR、spec 和 issue tracker 里的 ticket；Superpowers、BMAD、Spec-Kit 等流程框架同样以 Markdown 剧本驱动。这类 Skill 轻、随取随用、模型无关，适合个人工程师的日常工作。

`common-develop-opt` 走的是另一条路线：把研发流程约束下沉为可执行的机器契约和 CLI 门禁，不依赖模型自觉遵守。

| 维度 | 剧本式开发 Skill（如 mattpocock/skills） | common-develop-opt |
|---|---|---|
| 约束方式 | 自然语言剧本，模型可跳过 | Runtime Contract + Schema + 确定性 CLI 校验，fail-closed |
| 任务状态 | 外置文档/ticket，靠人维护 | `task_events.jsonl` 追加式事件账本，Reducer 重建状态 |
| 角色协作 | 单 Agent 或并行子代理，无身份权限 | ProjectManager/Owner/TestDesign/Reviewer 等角色，按 `-g` 身份校验权限 |
| 独立 Review | 剧本建议双通道检查 | Review Contract 强制：Reviewer 独立上下文，开放的高严重性问题阻断推进 |
| Evidence | before/after 证据写进 PR 描述 | Evidence/Assertion/Traceability 绑定 revision，机器可回查 |
| 上下文 | 全文读取 spec/glossary | context routing 按阶段返回最小资源集；Agent 不得直接打开运行时 JSON/JSONL |
| Git | 按剧本提交 | 本地 checkpoint 为默认；push 需显式 `-gp` + `-gc` 并满足门禁 |
| 长任务 | handoff 文档交接 | `wk begin/status/context` 跨会话恢复；reopen/amend 有界传播 |

### 优势：团队的大规模项目开发

- **可审计**：每个阶段产物、Review、Evidence 和归档绑定 revision，能回查到产生它的那次变更；多人协作时的验收与追责有据可依。
- **门禁一致**：完成条件由确定性脚本判定（acceptance、traceability、archive gate），不随模型或提示差异漂移；团队每个成员、每次会话得到同样的门禁行为。
- **跨模块影响受控**：impact 分析、依赖闭包和 changeset（API/DB/依赖影响）统一经 `merge_docs.py` 做 Schema 校验和原子写入，避免多人并发修改版本文档时互相覆盖。
- **多 Agent 分工**：Owner/Reviewer 分离、任务计划（`-tp`）、两阶段架构 Review（`-ar`）直接服务大型需求的拆解、分派和串并行协作。
- **上下文预算**：大型仓库不必把 spec 全文塞进上下文，按任务类型和阶段路由最小资源集，控制长任务和跨会话的上下文成本。
- **长周期任务可恢复**：事件账本加恢复命令，跨天、跨会话的任务不丢状态，失败后从当前 attempt 有界重试。

### 取舍

mattpocock/skills 明确反对重流程、强调保留工程师的控制权，这类轻量 Skill 在个人小项目上成本更低、上手更快。`common-develop-opt` 的代价是需要初始化 `project_doc` 事实结构并学习 `wk` 命令面，单人做文档级小改动未必划算；它的收益要在多角色、多模块、需要 Evidence 和审计的长周期项目里才兑现。两者并不互斥：需要轻量协作时可用 `-ws` 对话模式，流程严格度由模式与门禁组合决定，而不是靠更多文本流程。

## 目录结构

```text
common-develop-opt/
├── README.md                          # 英文仓库概览
├── README.zh-CN.md                    # 中文用途、流程和使用手册
├── AGENTS.md                          # Agent 维护和修改约束
├── INSTALL.md                         # 安装、更新和执行路径说明
├── releases/                          # 发布说明索引
├── skills/
│   ├── common-develop-opt/            # 唯一可安装 Skill 源（重构优化版，今后以此为主）
│   │   ├── SKILL.md                   # Skill 触发入口和硬门禁
│   │   ├── agents/openai.yaml         # UI metadata 和默认调用提示
│   │   ├── ai/                        # AI context 入口索引和 source map
│   │   ├── assets/                    # Contract、Schema、模板和机器事实
│   │   ├── references/                # 按需读取的流程、领域和操作规则
│   │   ├── scripts/                   # 可执行 CLI 与内部实现
│   │   ├── tests/                     # 运行包回归测试
│   │   ├── requirements.txt           # 运行包声明的 Python 依赖
│   │   ├── context-routing.json       # 上下文资源路由
│   │   ├── CHANGELOG.md               # 包版本变更
│   │   └── FILE_MANIFEST.sha256       # 运行包完整性清单
│   └── common-develop/                # 旧版 Skill 源（重构前版本，仅历史参考）
```

| 目录或文件 | 作用 |
|---|---|
| `skills/common-develop-opt/SKILL.md` | Agent 加载的最小入口，定义触发范围、启动顺序、事实来源和不可绕过门禁。 |
| `skills/common-develop-opt/references/` | 需求、设计、生命周期、Review、测试、归档、自动模式等详细规则；按任务命中后读取。 |
| `skills/common-develop-opt/assets/` | Runtime、Review、Storage、Evidence、Stage Result、CLI 和其他机器契约。 |
| `skills/common-develop-opt/scripts/` | `wk` 统一入口、生命周期命令、Context、Review、Evidence、校验和 Git checkpoint。 |
| `skills/common-develop-opt/tests/` | 运行包回归测试；`tests/internal/` 只存 worker/helper。 |
| `skills/common-develop/` | 重构前的旧版 Skill 源，仅作历史参考；默认不修改、不安装。 |

## 安装

### Codex 全局安装

```bash
mkdir -p ~/.codex/skills
rm -rf ~/.codex/skills/common-develop-opt
cp -R skills/common-develop-opt ~/.codex/skills/common-develop-opt
```

### 项目级安装

```bash
mkdir -p .agents/skills
rm -rf .agents/skills/common-develop-opt
cp -R skills/common-develop-opt .agents/skills/common-develop-opt
```

安装后通过 `$common-develop-opt` 触发。运行包在 `requirements.txt` 中声明 Python 依赖（当前为 `jsonschema`），建议安装到独立虚拟环境；详细更新、路径选择和排障说明见 [INSTALL.md](INSTALL.md)。

## 最短使用流程

从本仓库根目录执行时，脚本路径带 `skills/common-develop-opt/` 前缀；从安装目录执行时可以去掉此前缀。`--doc-root` 指向目标项目的文档根目录（必须包含 `version/`，不能指向版本目录本身），下文示例假定该目录位于当前工作目录并命名为 `project_doc`；它不是本仓库自带的目录。

### 1. 分类请求

```bash
python3 skills/common-develop-opt/scripts/wk.py start \
  -g ProjectManagerAgent \
  --doc-root project_doc \
  --version V_1.0 \
  -f "描述要实现的功能" \
  --json
```

功能和缺陷使用同一个 `start` 子命令（默认只预览）：

```bash
python3 skills/common-develop-opt/scripts/wk.py start --doc-root project_doc --version V_1.0 -f "功能描述" --json
python3 skills/common-develop-opt/scripts/wk.py start --doc-root project_doc --version V_1.0 -b "缺陷描述" --json
```

若结果为 `NEEDS_CONFIRMATION`，必须先补充或确认信息，不能直接进入开发。

### 首次使用的最小示例

下面的示例演示预览、必要确认、创建和读取执行包。创建只写项目任务事实，不修改生产代码。从本仓库根目录设置 `WK_SCRIPT`；全局安装时将该值改为实际安装包内 `scripts/wk.py` 的绝对路径，在目标项目根目录执行。

```bash
# 1. 未确认分类时只预览；预期返回 BLOCKED / NEEDS_CONFIRMATION
WK_SCRIPT="$PWD/skills/common-develop-opt/scripts/wk.py"
python3 "$WK_SCRIPT" start \
  -g ProjectManagerAgent --doc-root project_doc --version V_1.0 \
  -f "增加订单列表按状态筛选" --json
```

先确认这是新增需求而不是既有需求变更。下面的后续步骤以用户已确认新增需求为前提；实际确认不同就使用相应分类及必要参数，不能直接照抄分类：

```bash
# 2. 使用已确认分类重新预览，预期 PREVIEW，不创建 taskDir
python3 "$WK_SCRIPT" start \
  -g ProjectManagerAgent --doc-root project_doc --version V_1.0 \
  -f "增加订单列表按状态筛选" --classification new-requirement --json

# 3. 范围与模式明确后，以同一参数 apply；从 CREATED 响应取得 taskDir
START_RESULT=$(python3 "$WK_SCRIPT" start \
  -g ProjectManagerAgent --doc-root project_doc --version V_1.0 \
  -f "增加订单列表按状态筛选" --classification new-requirement --apply --json)
TASK_DIR=$(python3 -c 'import json,sys; r=json.load(sys.stdin); assert r["status"] == "CREATED"; print(r["taskDir"])' <<< "$START_RESULT")

# 4. 任务实际创建后才读取执行包
python3 "$WK_SCRIPT" begin -g ProjectManagerAgent --task-dir "$TASK_DIR" --json
```

### 2. 读取或恢复最小上下文

```bash
python3 "$WK_SCRIPT" begin -g ProjectManagerAgent --task-dir "$TASK_DIR" --json
```

首次示例已执行 begin 时直接使用该结果。先读取 `context.contextRouting` 的命中资源和预算、`actionPlan`、责任 Agent、revision 与阻断条件。只在缺失上下文或诊断时按需调用安装包中的 `scripts/ai_context.py resolve`；不要固定重复查询，也不要全文加载所有 Contract。

### 3. 按需查询

仅询问当前位置用 `status --summary`；需要展开执行包之外的信息时，选择相应查询，不必逐条执行：

```bash
python3 "$WK_SCRIPT" status --summary --task-dir "$TASK_DIR" --json
python3 "$WK_SCRIPT" next --task-dir "$TASK_DIR" --explain --json
python3 "$WK_SCRIPT" context --task-dir "$TASK_DIR" --json
```

### 4. 执行与推进

Owner 按 `actionPlan.requiredAgent` 执行实现和登记结果；独立 Review 遵循 `executionContext`。ProjectManagerAgent 直接使用执行包中的 `progressCommand` 推进一个原子动作，无需再查询 step。需要检查完整受限序列时才使用：

```bash
python3 skills/common-develop-opt/scripts/wk.py step --preview --task-dir <TASK_DIR> --json
python3 skills/common-develop-opt/scripts/wk.py step --apply --task-dir <TASK_DIR> --plan-digest <PLAN_DIGEST> --json
```

`step --apply` 只能使用同一次预览得到的 `planDigest`，并且只调用已有的受限原子命令。

### AI 编排快捷入口

AI 可以用 `wk begin` 一次取得状态、最小上下文和动作计划，使用 `begin --apply` 启动或恢复当前 Owner 的任务。执行后的 Evidence、attempt、Assertion 和 Manifest 更新可合并为 `wk record-result`；`progress --apply` 直接消费当前返回的 `planDigest`，每次只执行一个现有原子动作。`wk finish --apply` 执行声明的验证并汇总完成条件，返回 DONE、PARTIAL 或 BLOCKED。

`progress` 只由 ProjectManagerAgent 调用，与底层 `step` 权限一致。生成的动作、推进及修复命令绑定当前安装包绝对路径，可从项目目录执行。这些入口保留 revision 和独立 Review 门禁。默认向用户显示自然语言摘要，AI 使用 `--json`；详细协议和参数含义见 [wk CLI 参考](skills/common-develop-opt/references/recovery-archive/wk-cli.md)，实际参数以 CLI help 为准。

`finish` 默认在任务所属项目根目录执行验证；模块内验证可通过 `--cwd` 指定项目内目录。无效的 `--risk-facts` 会在任何状态写入前返回结构化阻断，修正输入后可直接重试。

### 5. 交付前验证

普通业务任务执行项目与 task 声明的 validation commands，并用 `finish` 汇总验收；不会运行或修改 Skill 安装包。以下检查**仅用于修改 common-develop-opt 源的维护任务**，从本仓库根目录执行：

```bash
python3 skills/common-develop-opt/scripts/check_skill.py -g ProjectManagerAgent --json
python3 skills/common-develop-opt/scripts/semantic_audit.py -g ProjectManagerAgent --json
python3 skills/common-develop-opt/scripts/cli_help.py validate -g ProjectManagerAgent --json
python3 skills/common-develop-opt/scripts/quality_gate.py \
  -g ProjectManagerAgent --profile full --clean-transient --write-manifest --json
```

## 核心功能

### 需求和工作流编排

`request_intake.py` 和 `wk.py start` 将请求分类为缺陷、功能或其他受支持类型，并选择 Workflow Profile。分类、治理建议和显式工作模式共同决定后续阶段，不允许通过自然语言绕过分类或确认门禁。

### 需求、设计和实现计划

Skill 支持需求确认、需求分析、业务模型、设计、测试设计、实施计划和开发阶段。设计进入开发前必须通过结构化校验；实现对象要形成 `REUSE → COMPATIBLE_EXTEND → MODIFY → CREATE` 的策略决策，并保留来源和失败语义。

### 生命周期和长任务

`long_task.py` 管理 task、phase、iteration、attempt、StageOutcome 和恢复流程。`task_events.jsonl` 是状态权威；Reducer 从事件账本重建当前状态，`cache/current_manifest.json` 和 `current_context.md` 只是可删除重建的投影。

当前阶段发生边界明确的需求或设计调整时，可先用 `wk impact` 形成 PASSED 的有界影响计划，再由 ProjectManagerAgent 执行 `long_task.py amend-phase`。该命令创建当前阶段的新 iteration，但只将显式受影响且依赖闭包完整的任务置为 `REWORK`；未受影响的 PASSED 任务、Evidence、Acceptance 和 Traceability 保持不变。正文在原 canonical 文件中追加 Amendment 段落，不复制、删除或重新初始化文件。影响未知、跨阶段、跨 Bounded Context、高风险，或下游已有 PASSED StageOutcome 时仍使用 `reopen-phase` 完整传播失效。

需求、流程、业务模型、设计、开发和测试变更都沿用同一 `version/{VERSION}`；对应 canonical 文档、源码或测试资产原位修订，并追加 revision、变更、Evidence 和 Review 记录，不按返工轮次创建新版本。准备重开时，先从变更发生阶段逐级向上只读判断影响；每次拟将范围扩大到上游阶段都先与用户确认，确认后才继续检查更上游，边界判定期间不修改任何候选阶段产物。确定最早受影响阶段后，只从该阶段执行一次 `reopen-phase`，再按标准阶段顺序向下修改。未受影响的需求确认保持原 revision；只有确认阶段确实被纳入重开时才递增编号，例如 `REQCONF-P3-R06` 失效后使用 `REQCONF-P3-R07`。普通门禁只按当前 revision 读取 ACTIVE Evidence，旧 Evidence/Review 保留为历史并在显式审计或归档时处理。

新事件默认使用 JSON Pointer 增量 Patch，避免每次阶段变化重复完整状态。活动 `task_events.jsonl` 超过默认 256 KiB 时，可先运行 `long_task.py compact-events ... --json` 预览，再用返回的 `planDigest` 显式 apply；旧事件作为 hash-linked 的确定性 gzip segment 保存在任务 `history/`，当前状态由 `TASK_REDUCER_SNAPSHOT` 无损恢复。该维护命令不自动运行，也不改变生命周期结论。

运行时 JSON/JSONL 文件默认通过查询脚本读取。`task.json`、`traceability.json`、`acceptance.json`、`development_task_plan.json`、`evidence/registry.jsonl` 和 `evidence/reviews.jsonl` 不应全文加载到 AI 上下文；使用 `wk status/context`、`task_plan.py status/query`、`evidence.py query/show`、`manual_review.py show` 和 `acceptance.py verify` 按稳定 ID、阶段或 revision 定向读取。`cache/current_manifest.json` 也不得由 Agent 直接打开；需要状态、阶段或阻塞信息时使用 `wk status/next/context`，需要完整诊断时使用 `task_layout.py view`，完整扫描仅限校验、审计、归档、故障诊断脚本内部或用户明确要求。

### Review、Evidence 和完成门禁

Review 由独立 Reviewer 执行，Review、Assertion、Evidence、StageOutcome 和产物都绑定当前 revision。`evidence.py` 管理 Evidence registry；`manual_review.py` 管理 Review 结果；完成阶段前需要正式 Review、合法 Evidence 和满足验收条件。

发布与 Review 会核对已声明引用、Flow 预览及快照源文件；Review 表单还绑定输入内容摘要，复用 revision 名称不能复用已过期的审查上下文。需求正文变化后，`requirement_doc.py sync-traceability` 保留历史引用并标记下游待重验；显式跨模块协作会启用影响检查，仍需 Owner 补全真实影响引用。

### 上下文路由

`ai_context.py`、`context_resolver.py` 和 `context_routing.py` 根据任务类型、阶段、提示条件和工作模式返回最小资源集合。资源带有 `priorityClass` 和省略原因；大型文档只在触发条件命中时读取。

### 文档事实和影响分析

`requirement_doc.py`、`design_doc.py`、`business_flow.py`、`artifact_relations.py`、`engineering_graph.py` 和 `impact_analysis.py` 支持结构化事实、跨模块关系和增量影响。关系图和索引是 DERIVED projection，不能反写权威事实。

### 模式和权限

Runtime Contract 统一定义阶段、权限、命令、副作用和模式组合。`-min`、`-ws`、`-auto`、`-ar`、`-review-only` 等模式会改变允许的阶段、写入和验证行为。

### 自动验证和 Git checkpoint

自动 Evidence 只能执行当前任务精确声明的 `validation_commands`；命令默认有有限 timeout，超时不会形成成功结论。Git checkpoint 默认只做本地提交，只有显式启用 `-gp` 且满足 checkpoint 条件时才允许推送。

## 标准流程

```text
用户请求
  ↓
Request Intake：分类、治理建议、Workflow Profile
  ↓
范围冻结：输入 revision、允许范围、停止条件、工作模式
  ↓
最小上下文：executionCore、Contract 查询、按需 Reference
  ↓
需求确认 → 需求分析 → 业务模型 → 设计
  ↓
测试设计 / 实施计划 → 开发 → 独立 Review → 测试
  ↓
Evidence / Assertion / Traceability 绑定当前 revision
  ↓
Completion Verification：验收、风险、交付就绪
  ↓
文档归档、阶段收口和本地 checkpoint
```

标准模式下阶段顺序和跳过规则由 Runtime Contract 控制。若用户确需在标准模式跳过 Review 或测试，ProjectManagerAgent 先执行 `wk confirm --skip-phase code_review|testing --reason ... --apply` 记录当前会话确认，再把返回的 `requestFingerprint` 作为 `long_task.py skip-phase --confirmation-ref ...` 的授权引用；系统会记录 `SKIPPED/NOT_APPLICABLE`、用户原因和未验证风险，不能写成通过，后续恢复必须显式 `reopen-phase`。轻量模式会明确抑制测试相关阶段，不能把被跳过的阶段写成通过。自动模式遇到 P0/P1、权威事实过期或安全阻断时，会停止下游推进并进入受控 remediation。

## 机制说明

### 权威事实分层

| 事实 | 权威来源 |
|---|---|
| 包版本、发布阶段、基线和存储模型 | `assets/package/release-contract.json`、`assets/storage/storage-contract.json` |
| 生命周期、阶段、权限和命令副作用 | `assets/runtime/runtime-contract.json` |
| Review Profile、Criterion 和完成语义 | `review_contract.py show/required/criterion/policy`（脚本内部读取 `assets/review/review-contract.json`） |
| Context 资源、触发器和预算 | `context-routing.json` |
| 术语、稳定 ID 和语义约束 | `assets/runtime/semantic-contract.json` |
| 其他来源路径和文档 owner | `ai/source-map.json` |
| Task 当前状态 | `wk status/context`（脚本内部读取 `task_events.jsonl`） |

### 单写入者和 Revision 绑定

只有 `task-runtime` 能追加生命周期事件、推进阶段、重开或完成阶段。文档事实和 Review/验证能力只返回绑定 revision 的结构化结果，不创建第二套状态机。阶段输出、Review、Assertion、Evidence 和归档结果都必须能回查产生它的 revision。

### 引用和结构化输出

Markdown 引用必须指向真实 heading 或显式 anchor；JSON/YAML 使用 JSON Pointer 或稳定 ID；JSONL 使用记录 ID。公开输出从内部模型投影生成，内部诊断字段不得通过直接复制字典泄漏。

### 基线和包完整性

`FILE_MANIFEST.sha256` 校验运行包文件摘要。`baseline_guard.py` 比较实现基线、发布封装、关键文件和工作树；阶段 6 使用 `RELEASE_ENVELOPE_CLEAN` 表示实现基线之后只有一个受控发布封装提交。

## 参数说明

### 通用参数

| 参数 | 含义 |
|---|---|
| `-g` / `--agent` | 当前 Agent 身份，未声明时默认 `ProjectManagerAgent`；权限按身份校验。 |
| `--doc-root` | 项目文档根目录，例如 `project_doc`；必须包含 `version/`，不能指向版本目录。 |
| `--version` | 版本标识，例如 `V_1.0`。 |
| `--target` / `--task-dir` | 任务目标 ID 或完整任务目录。 |
| `--json` | 输出稳定机器结构；省略时输出面向用户的简洁文本。 |
| `--phase` | 当前生命周期阶段或查询阶段。 |
| `--revision` | 绑定产物、Review 或 Evidence 的内容 revision。 |

### 请求和工作模式

| 参数 | 作用和限制 |
|---|---|
| `-b` / `--bug` | 以缺陷请求进入分类流程。 |
| `-f` / `--feature` | 以功能请求进入分类流程。 |
| `-mc` | 请求模型代码相关工作。 |
| `-v` | 启用页面业务行为与交互设计；不包含视觉设计。 |
| `-min` | 最小模式；不能创建或补写测试设计。 |
| `-ws` | 对话式轻量协作；默认抑制 TDD、测试设计、Review 和测试写入。 |
| `-wc` | 关闭轻量模式；不会自动补追此前被抑制的阶段。 |
| `-auto` | 自动推进和受控 remediation。 |
| `-ar` | 在 development 内启用架构代码骨架 → 第一轮架构/规格 Review → 具体实现 → 最终 Review 的两阶段开发；`-auto` 下是否实际运行还受架构边界策略约束。 |
| `-review-only` | 仅 Review；Mutation Policy 阻断 tests、配置和 production code 写入。 |
| `-gc` | 启用本地 Git checkpoint。 |
| `-gp` | 请求 Git push；必须同时满足 checkpoint 和授权条件，不执行 force push。 |
| `-tp` / `--task-plan` | 使用任务计划相关入口。 |
| `-l` | 将 Markdown/YAML 事实文档转换为只读 HTML 阅读副本；不推进生命周期、不修改事实源。 |
| `-d` | 文档完整性和归档相关入口。 |

归档分为知识层和审计层：普通归档只呈现需求、设计及适用业务文档；changeset、Traceability、Review、Acceptance、Evidence 和门禁结果保留为版本绑定的机器引用。需要完整审计交付时，可使用 `wk document-archive --audit-bundle --apply`。

> 兼容性说明：当前版本的 `wk` CLI 和 Runtime Contract 未定义 `-q`（“每完成一个任务都等待用户确认”）参数，也没有等价的独立任务级确认模式。标准模式仍按阶段、Reviewer 和门禁要求进行必要确认；`-auto` 才会在门禁满足后自动推进。若需要恢复逐任务确认，需要另行设计并纳入 Runtime Contract，不能仅在 README 中声明一个未实现参数。

### `wk` 常用子命令

| 子命令 | 用途 |
|---|---|
| `start` | 分类请求、初始化或载入任务上下文。 |
| `status` | 查看当前状态、阶段、阻断项和 freshness。 |
| `next` | 返回下一合法动作；`--explain` 给出原因和责任 Agent。 |
| `context` | 返回当前最小任务上下文和恢复顺序。 |
| `step` | 预览或应用当前阶段的受限原子动作。 |
| `impact` | 生成 DERIVED 增量影响和最小重验计划。 |
| `reconcile` | 对齐当前任务事实、模式和派生投影。 |
| `close` | 在正式门禁满足时关闭任务；历史例外必须显式声明。 |
| `delivery` | 按项目 capability 和 delivery gate 查询交付就绪状态。 |
| `doctor` | 生成诊断和受限修复建议。 |
| `help` | 从 argparse parser 查询真实命令面和参数。 |

只想知道任务当前处于需求、设计还是开发时，不需要依次运行验证、`next` 和 `context`。使用一次紧凑状态查询：

```bash
python3 skills/common-develop-opt/scripts/wk.py status \
  -g ProjectManagerAgent \
  --task-dir project_doc/version/V_1.0/task/{TARGET_ID} \
  --summary
```

该命令只从 `task_events.jsonl` reducer 返回状态、阶段、轮次、当前 attempt、下一 Agent 和下一动作，不运行完整诊断，也不内嵌 Manifest。需要机器结果时追加 `--json`；发现状态异常后再运行普通 `wk status --json`，需要门禁原因时才运行 `wk next --explain --json`。

### 参数查询

不要根据记忆猜测参数：

```bash
python3 skills/common-develop-opt/scripts/wk.py help -g ProjectManagerAgent --json
python3 skills/common-develop-opt/scripts/wk.py help \
  -g ProjectManagerAgent --script long_task --command validate --json
python3 skills/common-develop-opt/scripts/long_task.py help \
  -g ProjectManagerAgent --command finish-attempt --json
```

以帮助返回的 `usage`、`arguments`、`permissionQuery`、`mutation` 和 `previewSupported` 为准。

## 输出和状态

- 默认文本输出：当前状态、阻断原因、下一步和必要摘要。
- `--json` 输出：脚本、审计和 Agent 间传递的结构化字段和错误码。
- 常见成功状态：`PASSED`、`VERIFIED`、`READY`、`SYNCED`、`CLEAN`、`RELEASE_ENVELOPE_CLEAN`。
- 常见非成功状态：`BLOCKED`、`FAILED`、`INVALID`、`DIRTY`、`DIVERGED`、`PARTIAL`。
- 命令超时统一记录 `COMMAND_TIMEOUT`，退出码为 `124`，并保留 partial stdout/stderr。

## 安全和操作边界

- 不绕过 Request Intake、权限、Review、Evidence、生命周期和归档门禁。
- 不把缓存、聊天内容、名称或字符串 ID 当作运行时归属证明。
- 不默认执行任意 Shell；自动 Evidence 只执行任务声明的精确验证命令。
- 不默认写入项目级 API/DB 全量文档；只记录版本增量。
- 不执行 Git merge、Release、部署或未经明确授权的 push。
- 当前发布保留 `CD-P0-01` 安全例外；`CD-P2-14` 的外部 Git index 竞争窗口按当前范围暂缓。

## 验证和维护

```bash
cd skills/common-develop-opt
python3 -m unittest discover -s tests -p 'test_*.py' -q
python3 -m unittest discover -q
```

维护约束见 [AGENTS.md](AGENTS.md)，安装更新见 [INSTALL.md](INSTALL.md)。
