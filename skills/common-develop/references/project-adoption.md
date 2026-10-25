# 存量项目采纳指南

本指南用于把已有项目逐步接入 common-develop，而不是把存量项目当作空目录重建。采纳过程必须保留历史、显式映射事实来源，并让每一个未映射项成为可追踪的待办；禁止覆盖、删除或静默迁移已有规则和证据。

## 1. 先盘点，再决定迁移深度

在创建新目录或写入 canonical storage 前，先盘点：

- 项目级规则：`AGENTS.md`、README、贡献指南、发布/安全规则和 CI 门禁；
- 计划与状态：backlog、roadmap、ADR、iteration baseline、版本计划、未完成任务；
- 研发证据：测试报告、Review 记录、变更说明、命令输出和人工批准；
- 历史归档：版本文档、发布记录、archive、已关闭问题和 lessons learned；
- 事实来源：代码、OpenAPI、DDL、Migration、配置、业务模型和领域边界。

先记录发现结果，再判断旧规则是否仍有效。规则的存在不等于规则仍是当前事实；无法确认时标记为 `ASSUMPTION` 或待确认项，不得自行选择“最新看起来合理”的版本。

## 2. 责任位置映射

| 既有项目资产 / APG 概念 | common-develop 责任位置 | 采纳动作 |
| --- | --- | --- |
| `AGENTS.md` 核心规则 | `SKILL.md` + 项目自身 Agent 规则 | 保留原文和来源；只把跨项目通用门禁映射到 Skill |
| manifest | `FILE_MANIFEST.sha256`、Storage Contract、Capability Contract | 生成新投影前保存旧 manifest，比较差异 |
| iteration baseline | `development_task_plan.json` | 将计划映射为 revision，旧 baseline 只读保留 |
| execution state | `task_events.jsonl` | 从已确认事件映射；无法证明的状态不得补写 |
| evidence | Evidence Registry | 保留原 Evidence ID，补充当前 revision 绑定 |
| lessons learned | 归档记录、残留问题、文档不变量 | 原文归档，并把可执行项转为 issue/constraint |
| docs/reference | `project_doc` 与 `references` | 先建立 source pointer，避免复制同一规则 |
| risk gates | Runtime Contract、Review Contract、项目约束 block | 通用规则进契约，项目特殊规则进入约束声明 |

## 3. 不覆盖、不删除、不静默迁移

1. 迁移前在目录外保存快照，并记录快照时间、范围和校验摘要。
2. 新 canonical 文件只能通过声明脚本创建；旧文件改名、移动或转换必须有迁移记录和 before/after 引用。
3. 旧任务计划、Evidence、Review、archive 和失败记录默认只读保留；不要用新模板覆盖它们。
4. 同一事实存在多个来源时，先登记冲突和 owner，再决定 authoritative source；迁移脚本不得把冲突压成一个无来源的值。
5. 未映射、无法验证或已失效的规则进入 `PARTIAL`/`BLOCKED` 清单，不以“迁移完成”掩盖缺口。

## 4. 最小切片采纳流程

建议按一个版本、一个模块或一个真实需求做最小切片：

1. 盘点并冻结输入 revision；
2. 建立旧资产到责任位置的映射表；
3. 只创建运行该切片所需的最小 canonical 文件；
4. 用 `wk status`、`wk doctor`、任务验证和归档预览检查映射结果；
5. 由项目负责人确认未映射项、保留项和风险；
6. 通过一次完整生命周期后，再扩大到下一模块或版本。

对存量任务，优先读取和保护现有事实；不要为了让新校验器变绿而重写历史。必要时建立“兼容读取 + 新增事实不回填旧历史”的过渡记录，并设置明确的结束条件。

## 5. 完成判定与失败处理

采纳完成至少需要：

- 映射表覆盖所有已发现的规则、计划、证据和归档入口；
- 每个映射项都有来源、责任位置、状态和验证方式；
- 当前切片的 `work.md`、任务事件、Evidence、Review 和 archive 能相互追踪；
- 未映射项已显式分类为 `PARTIAL`、`BLOCKED` 或后续任务；
- 没有未经授权的删除、覆盖、静默改写或伪造历史。

如果校验失败，保留失败输出和现场快照，修复映射或补充事实后重试；不要直接清理失败目录。采纳本身是可审计的工程变更，而不是一次性文件格式转换。
