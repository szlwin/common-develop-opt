# OnePage HTML 阅读副本规范

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

本文件只定义 `$Common-develop wk -l` 的 Markdown/YAML → HTML 阅读副本。HTML 用于导航、检索、离线阅读和跨文档跳转，不是研发事实源、产品页面、视觉稿或发布站点。

## 1. 不可破坏的边界

- 事实优先级：结构化 YAML（如适用）→ Markdown → HTML。
- HTML 不得反向修改 Markdown/YAML，不改变 revision、阶段或门禁状态。
- 不因 HTML 缺失阻塞需求、设计、开发、Review 或测试。
- 只允许服务阅读的简单 CSS 和必要原生 JavaScript；禁止产品视觉规范、行为原型、真实接口和生产数据。
- 默认离线单文件，不依赖 CDN、远程字体、远程脚本或后端服务。

## 2. 常见转换

```text
DOC_ROOT/model_desc.md                                      → model_desc.html
DOC_ROOT/docs/module/{MODULE}/{MODULE}_desc.md                     → {MODULE}_desc.html
DOC_ROOT/docs/module/{MODULE}/{MODULE}_business_model.yaml → .md   → .html
DOC_ROOT/version/{VERSION}/module/{MODULE}/{MODULE}_design.md    → {MODULE}_design.html
DOC_ROOT/docs/_relations/dependency_impact.yaml → dependency_impact.md → dependency_impact.html
DOC_ROOT/version/{VERSION}/project_process.md                 → project_process.html
DOC_ROOT/version/{VERSION}/requirement/{REQUIREMENT_ID}/test_case.md  → test_case.html
DOC_ROOT/version/{VERSION}/project_bug.md                     → project_bug.html
```

`work_record.md`、长任务状态、日志和环境文档默认不进入公开导航；确需生成时先脱敏。

## 3. 命令语义

```text
$Common-develop wk -l
$Common-develop wk -l -f MOD0001
$Common-develop wk -l -b B0001

`wk -l` 的 `-f/-b` 仅接受已经解析完成的 FEATURE_ID/BUG_ID；自然语言描述必须先通过 Request Intake 分类，HTML 命令本身不创建或重分类请求。
$Common-develop wk -d -l
```

- 无筛选时刷新当前版本及关联模块；`-f/-b` 只筛选生成范围。
- `wk -d -l` 先完成研发事实增量归档，再从最终事实文档生成 HTML；API/DB 版本 changeset 不合成为全量 HTML。
- 源文件、revision 或链接目标缺失时，按 `statuses.auxiliaryAction` 记录 `PARTIAL` 或 `BLOCKED`，不得补写不存在的事实。

## 4. 最小页面结构

每个 HTML 至少包含：

1. 标题、模块/版本、源路径和源 revision；
2. 页面目录与稳定锚点；
3. 完整正文、代码块、表格和 Mermaid；
4. 实际存在的关联需求、模型、设计、API、DB、测试、Review 和模块补充文档链接；
5. 返回上级、上一页、下一页；
6. 声明：`此 HTML 由研发事实源生成，不可反向编辑`。

## 5. `project_process.html`

必须忠实转换 `project_process.md`。列结构和状态写入规则只以 `documentation-core.md#project_processmd` 与 `assets/runtime/runtime-contract.json` 为准；本文件不复制生命周期表，也不得为 HTML 单独增加阶段或状态。

不得把 `STALE`、`BLOCKED` 或任何非通过状态渲染成已完成。

## 6. 专项文档

### `test_case.html`

保留 Case ID、追踪 ID、前置条件、输入、步骤、期望、禁止副作用和状态。可链接自动化测试和报告，但不复制大段日志。

### `project_bug.html`

保留现象、复现、根因、修复 revision 和回归证据；不得改写历史，也不得暴露账号、Token、私钥或未脱敏数据。

## 7. 阅读与可访问性

- UTF-8、`lang="zh-CN"`、viewport、系统字体、连续标题层级；
- 表格有表头且可横向滚动；代码块可换行/复制；状态不只靠颜色；
- Mermaid 无法离线渲染时至少保留源码和标题；
- 相对链接从当前文件计算，跨版本链接显示版本号；不生成假链接。

这些规则只服务阅读，不构成产品视觉设计要求。

## 8. 质量门禁

生成后验证：

1. 标题、编号、状态、revision 与源文档一致；
2. 无意外覆盖，HTML 未反向改变事实源；
3. 锚点、相对链接和跨文档链接有效；
4. 无网络和后端也可打开；
5. HTML 结构无明显错误或乱码；
6. Mermaid、表格、代码块可阅读；
7. 无敏感数据；
8. `wk -l` 未推进任何研发门禁。

## 9. 结果记录

通过 `long_task.py append-work-event` 向版本级 `work_record.md` 追加转换摘要；完整生成清单和验证结果写入文件、注册为 evidence，并保存 evidence ID。结果使用 `statuses.auxiliaryAction`。
