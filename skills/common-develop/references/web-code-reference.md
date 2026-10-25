# 前端代码规格 Frontend Code Spec（AI 可读版）

### 前端实现边界 {#frontend-implementation-boundary}

使用当前实现契约和现有组件约定。保持状态所有权、校验、可访问性、请求/错误行为以及后端契约兼容性不变；需要具体 TypeScript、React、Vue、组件、路由或浏览器行为指导时读取本文件。

> 本文件只描述前端业务行为、数据、权限、状态和代码实现，不描述颜色、排版、动效或其它视觉设计。

> 本文档定义前端实现契约的分级与 Frontend Code Spec。先判断界面业务行为和实现信息是否充分，再选择 `NONE / LITE / FULL`；避免把界面行为规格、API 和已有代码重新抄写成一份大型 JSON。

---

## 目录

- [0. 分级决策](#0-分级决策)
- [1. 当前 Frontend Code Spec](#1-总览)
- [2. AI 处理流程](#2-ai-处理流程)
- [3. FULL 模板](#3-full-模板)
- [4. 项目映射](#4-与当前前端项目的映射)
- [5. 注意事项](#5-注意事项)
- [6. 完整生成流程](#6-ai-完整生成流程)
- [7. 示例策略](#7-示例策略)

---

## 0. 分级决策

生成任何完整 JSON 前，先读取界面行为规格、需求、API、DB/ER、目标页面和相似页面；标准模式再读取测试用例并组织 DesignAgent、TestDesignAgent、DevelopAgent 评估，`wk -min` 不读取测试用例、不分派 TestDesignAgent，只由 DesignAgent、DevelopAgent 检查业务行为和非测试可验证性。

| 等级 | 规则 | 本文件使用方式 |
|---|---|---|
| `NONE` | 实现与测试信息均充分，无需额外代码规格 | 只写最小判定记录，不使用本文件完整模板 |
| `LITE` | 已有页面的局部功能变化 | 使用精简差异模板，只写变化和引用 |
| `FULL` | 新页面或复杂交互/状态/接口/权限 | 使用当前结构和 `assets/frontend-code-spec.template.json` |

`LITE` 推荐模板：

```json
{
  "level": "LITE",
  "featureCode": "MOD0001",
  "behaviorSpec": "requirement.md#MOD0001",
  "developMode": "patch",
  "targetFiles": ["{REAL_PAGE_PATH}", "{REAL_API_PATH}"],
  "implementationReferences": ["{EXISTING_SIMILAR_PAGE}"],
  "changes": ["新增企业名称查询条件"],
  "apiChanges": ["列表接口增加 enterpriseName 参数"],
  "actionRules": ["仅启用状态展示编辑按钮"],
  "validationChanges": ["企业名称最大长度与 DB/API 一致"],
  "testImpacts": ["查询参数传递", "按钮显示条件"],
  "confirmItems": []
}
```

未变化字段通过 `behaviorSpec`、`targetFiles` 和 `implementationReferences` 继承，不复制完整表格列、表单和组件配置。`NONE` 或 `LITE` 后出现新的复杂缺口时升级等级。

---

## 1. 总览

### 1.1 设计目的

- 在 `FULL` 时用 JSON 描述页面、弹窗或组件的完整交互；在 `LITE` 时只描述真实差异；
- 在编码前与用户**确认需求无歧义**；
- AI 直接基于 JSON 生成代码，无需再二次解读自然语言。

### 1.2 Frontend Code Spec 推荐结构

只有等级为 `FULL` 时使用下面的完整结构。Markdown 中可先写业务说明，再嵌入 JSON；JSON 负责机器可读和代码生成。

| 字段 | 类型 | 必填 | 说明 |
|---|---|:---:|---|
| `meta` | object | ✓ | 模块、功能编号、开发模式、目标文件、风格参考文件 |
| `componentMapping` | object | ✓ | 查询区、表格、弹窗、确认框、提示等使用的项目组件 |
| `api` | ApiSpec[] | ✓ | 前端需要调用或要求后端提供的接口 |
| `dataSources` | DataSource[] | ✓ | 页面查询、表格、表单涉及的数据来源表；多表关联时写多个 |
| `query` | QueryField[] |  | 查询条件 |
| `table` | object |  | 表格列和行内操作 |
| `forms` | FormSpec[] |  | 新增、编辑、详情等表单或弹窗 |
| `dialogs` | DialogSpec[] |  | 选择器、详情、确认之外的复杂弹窗 |
| `actions` | ActionSpec[] |  | 顶部按钮、行内按钮、弹窗按钮等交互动作 |
| `codeRules` | string[] | ✓ | 生成/修改代码时必须遵守的项目规则 |
| `confirmItems` | object[] |  | 需要用户或 ProjectManagerAgent 确认的点 |

`meta.developMode` 取值：

- `create`：新建页面或组件。
- `update`：在已有页面上做较大功能扩展。
- `patch`：只做最小必要修改，保留现有结构和风格。

`meta.targetFiles` 至少说明：

```json
{
  "page": "{project-page-path}",
  "constants": "{project-field-config-path}",
  "api": "{project-api-path}"
}
```

`meta.implementationReferences` 必须列出现有前端项目中的参考页面、常量文件或组件文件。AI 生成代码前必须先读取这些文件，复用其目录结构、组件用法、命名、导入方式、API 调用方式和样式组织方式。

### 1.4 接口判定规则

前端实现契约里的 `api[].url` 和 `api[].existence` 共同决定后端工作：

| 情况 | 判定 | 要求 |
|---|---|---|
| `url` 为空 | 无现成接口 | 必须在前端实现契约中定义接口名称、入参、出参、数据来源和期望行为，后端按新增接口处理 |
| `url` 已填写但后端不存在 | 新增接口 | 后端开发按该 URL、入参、出参和数据来源补齐接口入口、应用逻辑与数据访问实现 |
| `url` 已填写且后端存在，满足需求 | 复用接口 | 前端复用现有 API 方法；实现契约记录证据 |
| `url` 已填写且后端存在，但字段、筛选、权限、分页、状态或操作不满足 | 修改接口 | 实现契约必须写明缺口；后端按修改接口处理 |

推荐 `api` 字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|:---:|---|
| `name` | string | ✓ | 前端 API 方法名 |
| `url` | string |  | 后端 URL；空表示需要定义新接口 |
| `method` | string | ✓ | 按当前项目约定填写 GET / POST / PUT / PATCH / DELETE 等方法 |
| `existence` | enum | ✓ | `unknown`（未核查）/ `exists`（已存在）/ `missing`（确认不存在或 URL 为空） |
| `backendAction` | enum | ✓ | `reuse` / `create` / `modify` / `check` |
| `request` | object | ✓ | 入参字段和来源 |
| `response` | object | ✓ | 出参字段、分页结构和表格列对应关系 |
| `dataSources` | string[] | ✓ | 接口涉及的表；多表关联写多个 |
| `gap` | string |  | 现有接口不满足需求时的缺口 |

### 1.5 字段与数据来源补全规则

- 查询条件和表格列必须尽量写明对应表和字段；如一个字段来自多表关联或冗余字段，写入多个 `sourceTables` 或 `sourceColumns`。
- 多表关联只在前端实现契约中声明“需要哪些表和输出字段”；具体 JOIN、查询优化和权限过滤由后端根据 ER 图、DB 文档和原有代码实现。
- 如果查询条件或表格列的 `field`、`sourceTable`、`sourceColumn` 未填写，AI 先读取现有页面代码、API 文档、DB 文档、ER 图、接口实现和实体/VO 字段后自动推断。
- AI 自动推断的字段必须写入 `inferred: true` 和 `inferFrom`，并在 `confirmItems` 中标记需要确认。
- 如果无法可靠推断，不能随意编造字段；应写 `field: "待确认"`，并交由 ProjectManagerAgent 协调。
- 查询条件对应多个表时，推荐写法：

```json
{
  "label": "企业名称",
  "field": "enterpriseName",
  "component": "input",
  "match": "like",
  "sourceTables": ["t_order", "t_enterprise"],
  "sourceColumns": ["t_order.enterprise_id", "t_enterprise.enterprise_name"],
  "backendJoinHint": "根据业务表企业ID关联企业表"
}
```

---

## 2. AI 处理流程

### 2.1 解读步骤

```
1. 读 meta → 确定模块、功能、开发模式、目标文件、风格参考文件
2. 读取 implementationReferences / targetFiles → 对齐现有项目样式、组件、目录、API 调用和常量写法
3. 读 api → 判断接口复用 / 新增 / 修改 / 待检查，并把接口需求同步给后端开发依据
4. 读 dataSources → 确认页面涉及的表、字段、多表关联提示和字段推断来源
5. 读 operator/actions → 生成页面顶部按钮、行内按钮和事件方法
6. 读 query/table/forms/dialogs → 使用 `componentMapping` 与现有页面的组件组合生成查询区、表格、表单和弹窗
7. 读 actions/table.rowActions → 生成顶部、批量、行内和弹窗操作，并连接对应 API 或路由
8. 读每条 operator/action 的 condition / visibleWhen / disabledWhen → 生成 visible/disabled 逻辑和禁用提示
9. 缺失字段时按现有代码、API、DB、ER 推断；无法推断则写待确认，不编造
```

### 2.2 关键映射

| JSON | 项目代码映射规则 |
|---|---|
| `meta.targetFiles` | 按当前项目目录与命名约定定位页面、常量、API 和测试文件 |
| `componentMapping.queryForm/table/dialog/confirm/message` | 必须映射到 `implementationReferences` 或项目级 skill 中已存在的组件和调用方式 |
| `api` | 映射到当前项目的请求封装、参数位置、响应包络和异常处理，不假定 `$api`、`fetch` 或 Axios |
| `query` / `table.columns` / `forms` | 复用现有页面的字段配置、校验、分页、空状态和格式化方式 |
| `actions` / `table.rowActions` | 映射到项目按钮、权限、确认、提示、弹窗和路由机制 |
| `column.type=enum` | 使用项目枚举或字典翻译；不得写裸数值映射 |
| `column.type=money/date/dateTime` | 使用项目公共格式化规则；精度、币种和格式缺失时列为确认项 |
| `visibleWhen` / `disabledWhen` | 转换成当前框架表达式，并保留权限、状态、归属和业务前置条件 |

Vue、React、Element Plus 等框架或组件代码只可作为当前项目证据或示例，不能由本通用规范预设。

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

只有等级为 `FULL` 时读取 `assets/frontend-code-spec.template.json`。模板是机器可读骨架，不是项目事实；生成前必须用真实 `targetFiles`、`implementationReferences`、API、字段、权限、状态和测试信息替换示例值。

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

优先参考本文件 `assets/frontend-code-spec.template.json`。如项目后续需要更完整的模块级示例，可在对应版本的 `{MODULE_CODE}_web_code.md` 中追加真实功能示例，不在 skill 中维护大段项目样例。
