# 归档核心规范

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

## 1. 前置条件

只归档已验证的当前事实。应用变更前，预览合并集合、源 revision、目标路径、未解析引用和门禁状态。

## 2. 保留规则

归档操作通过声明的事务路径追加或合并，并保留源任务历史和 Git 历史。

API/DB 是版本范围内的增量：保留每个已审查 changeset 的路径、摘要、revision 转换、决策和验证 Evidence。不要将其合并为项目级完整 API/DB YAML 或 Markdown，也不要生成项目级完整 ER 视图。当前实现的 API/DB 事实从代码、OpenAPI、DDL、Migration、Schema introspection 或项目声明的等价来源解析。

Dependency impact 是项目事实，只能通过 `python3 scripts/merge_docs.py query|write` 读取或更新版本范围内的 `dependency-impact.changeset.yaml`；Agent 不直接打开或编辑该文件。稳定 Design 主题和模块页面行为作为当前 Markdown 快照归档到 `docs/module/{MODULE}/design/`；其版本源文件保留为历史。弃用通过明确状态保留历史对象，而不是删除对象。

模块枚举通过版本模块说明语义合并到 `docs/module/{MODULE}/{MODULE}_desc.md`；统一语言通过 `version/{VERSION}/union/union_language_changes.md` 语义合并到 `docs/union/union_language.md`。两类语义冲突都必须 fail-closed；API/DB/Page/Test 只引用这些权威定义。

在与项目事实相同的归档 payload 中刷新 `docs/README.md` 和 `version/{VERSION}/version_summary.md`。这些人类入口是 DERIVED 导航视图：只包含可解析的相对链接，绝不将 Review、Evidence、task event 或完整结构化目录复制到正文。

同一个原子 payload 会重建 `engineering_graph.yaml`、`traceability_matrix.md`、`dependency_graph.md`、模块设计索引和 dependency-impact 阅读视图。每个生成的链接都必须解析到已存在的文件，或解析到该 payload 中的另一个文件。

## 3. Preview

### 预览

报告选中的 changeset、API/DB 增量投影、合并后的项目事实、确定性的 `artifactMerges`、跳过项、冲突、断裂引用、源 revision 和精确 apply 命令。Preview 不得修改项目事实，也不得物化完整 API/DB 快照。普通用户不得编辑 `artifactMerges`；冲突需要修正源文件或 rebase 后重新 preview。

## 4. Apply

### 应用

只应用已审查的 preview，并在写入前立即重新验证源摘要、目标摘要、稳定 ID 和 base revision。使用事务性 staging，并在失败时恢复。重复应用已记录的 changeset 或相同的 Design snapshot 时应视为 no-op。

## 5. Review Gate

### Review 门禁

归档发布前，必需的阶段结果、阻断性 Review、验收 Assertion 和完成验证必须有效。

## 6. Reference Check

### Reference 检查

Commit 前，根据当前权威位置验证 Requirement、Feature、flow、module、枚举、统一语言、revision、Evidence、Review 和文档链接。

## 7. Recovery

### 恢复

在回滚验证完成前保留 staging 和诊断事实。恢复之前的目标状态并记录失败操作；不要删除源任务记录。
