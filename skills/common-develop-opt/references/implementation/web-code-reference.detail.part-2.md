# web-code-reference（续）

> 本文是 [web-code-reference.detail.md](./web-code-reference.detail.md) 的按需续篇。
### 2.3 代码生成模板

```text
1. 读取 meta.targetFiles 与 meta.implementationReferences。
2. 从参考文件提取：导入方式、状态管理、请求封装、响应包络、查询/表格/弹窗组件、权限与消息机制。
3. 按 componentMapping 生成页面骨架；不存在可靠映射时写入 confirmItems，不虚构组件。
4. 按 api 生成或修改请求方法，保留项目错误处理、取消请求、分页和加载状态模式。
5. 按 query/table/forms/dialogs 生成字段与校验；字段长度不得超过 DB 定义。
6. 按 actions 生成 visible/disabled/confirm/success/error 流程，并在后端再次验证业务前置条件。
7. 运行项目现有格式化、类型检查和测试，再把实际文件与接口证据回写实现契约。
```

---

## 3. FULL 模板

只有等级为 `FULL` 时读取 `assets/package/frontend-code-spec.template.json`。模板是机器可读骨架，不是项目事实；生成前必须用真实 `targetFiles`、`implementationReferences`、API、字段、权限、状态和测试信息替换示例值。

最小必备顶层字段：`meta`、`componentMapping`、`api`、`dataSources`、`query/table/forms/dialogs/actions`、`codeRules`、`confirmItems`。

## 4. 与当前前端项目的映射

下表第三列必须在生成代码前根据项目级 skill、`implementationReferences`、`targetFiles` 和实际代码填写，不得使用通用默认值。

| 概念 | 本规范 | 当前项目实现 |
|---|---|---|
| 模块编号 | `meta.module` | 项目模块与权限命名规则 |
| 功能编号 | `meta.featureCode` / action `funcCode` | 项目权限点或功能编号映射 |
| 后端 API | `api[].url` | 请求封装、参数绑定、错误处理和取消机制 |
| 后端响应包络 | `api[].response` | 成功标识、数据、分页、错误码的实际路径 |
| 查询表单 | `query[]` | `componentMapping.queryForm` |
| 数据表格 | `table.columns[]` | `componentMapping.table` |
| 操作按钮 | `actions` / `table.rowActions` | 项目按钮和权限组件 |
| 二次确认 | action `confirm` | `componentMapping.confirm` |
| 弹窗 | `forms` / `dialogs` | `componentMapping.dialog` |
| 跳转 | action `route` | 项目路由机制 |
| 枚举列 | `column.enumRef` | 项目枚举或字典翻译方式 |
| 显示/禁用 | `visibleWhen` / `disabledWhen` | 当前框架条件表达式与提示机制 |

---

## 5. 注意事项

1. **先确认等级**：`FULL` 缺 `meta.module` / `meta.featureCode` / `meta.targetFiles` 时必须补齐；`LITE` 使用精简差异字段；`NONE` 只保留判定记录。
2. **`url` 遵循当前项目路由规范**；为空表示需要定义新增接口，填写后必须检查后端是否已存在且是否满足需求。
3. **前端差异作为后端输入**：`LITE/FULL` 中新增或修改的 `api`、字段、数据来源和操作由后端读取；`NONE` 时以后端设计、API 和界面行为规格为依据，不要求空白规格。
4. **字段缺失时先推断再标记**：查询条件或表格列字段缺失时，AI 可根据现有页面、API/DB 版本增量、当前 OpenAPI/代码、DDL/Migration、实体/VO 推断，但必须写 `inferred: true` 和 `inferFrom`；无法推断则写“待确认”。
5. **多表关联只给后端提示**：前端实现契约列出 `sourceTables`、`sourceColumns`、`backendJoinHint`；具体 JOIN、索引、权限过滤以本版本 DB 增量和当前 DDL/Migration/代码为准。
6. **`funcCode` 与当前项目权限系统对齐**：没有权限模型时可为空；存在权限控制时必须记录真实映射，不能假定某个固定权限模块。
7. **推荐结构的条件表达式统一使用 `&&`、`||`、`!`**（如 `status == @MOD.STATUS.ENABLED && type == @MOD.TYPE.NORMAL`）；AI 转成目标框架表达式时必须使用项目枚举对象、枚举常量或字典方法，不得翻译成裸数值判断。输入必须直接使用当前表达式语法，不解析其它运算符写法。
8. **`rel-param` 字段名必须存在于列、表单字段或响应字段中**，否则 AI 找不到取值来源，需补字段或标记待确认。
9. **`type=enum` 的列需要配套字典翻译函数或项目枚举**，AI 应在生成代码时复用已有枚举；缺少枚举时先定义枚举，不直接写 `1:启用, 0:禁用` 这类裸数值映射。
10. **操作按钮必须检查前置条件**：按钮是否显示、是否可点、禁用提示是什么，都必须由 `condition`、`visibleWhen`、`disabledWhen`、权限码、状态枚举或现有代码推导；不得因为前端能点就把失败留给后端。
11. **不做视觉设计**：本规范不描述颜色、间距、复杂布局；AI 必须通过 `implementationReferences` 读取现有代码，复用项目组件、状态管理和编码模式，不推导视觉风格。

---

## 6. AI 完整生成流程

```
输入：需求 + 界面行为规格 + API/DB 版本增量 + 当前实现证据 + 目标代码
        ↓
[Step 1] Design/Test/Develop 评估实现与测试信息充分性
        ↓
[Step 2] 选择 NONE / LITE / FULL
        ↓
[Step 3] NONE 写最小记录；LITE 写差异；FULL 写完整 JSON
        ↓
[Step 4] 用户或获授权 PM 确认等级与内容
        ↓
[Step 5] AI 读取界面行为规格、项目级 skill、implementationReferences 和 targetFiles 后生成或修改代码
        ↓
[Step 6] 后端只读取新增/修改的接口与字段差异
        ↓
[Step 7] 构建、浏览器/组件/E2E 验证 → 必要时升级等级或反向同步
```

---

## 7. 示例策略

优先参考本文件 `assets/package/frontend-code-spec.template.json`。如项目后续需要更完整的模块级示例，可在对应版本的 `{MODULE_CODE}_web_code.md` 中追加真实功能示例，不在 skill 中维护大段项目样例。
