<!-- template: common-develop/page-design-v2 -->
# {PAGE_NAME}页面业务行为设计

> 仅在 `wk -v` 启用时使用；不包含视觉设计。

> Canonical 位置：`version/{VERSION}/module/{MODULE_CODE}/page-design/{PAGE_DOCUMENT_STEM}_design.md`。

> 文档导航：[项目文档首页](../../../../../docs/README.md) · [版本摘要](../../../version_summary.md) · [本版本需求列表](../../../requirement/requirement_list.md) · [模块说明](../{MODULE_CODE}_desc.md) · [模块设计](../{MODULE_CODE}_design.md)

> 所有链接从当前页面文件所在目录计算相对路径。稳定编号不随目录或文件名变化；页面使用 `PAGE-{MODULE_CODE}-*`，操作使用 `PAGEACT-{MODULE_CODE}-*`，版本号和 Review 轮次只进入 revision。

## 1. 设计信息

| 项目 | 内容 |
| --- | --- |
| 版本 | {VERSION} |
| 模块 | {MODULE_CODE} |
| 页面文档主题 | {PAGE_DOCUMENT_STEM} |
| 主页面编号 | {PRIMARY_PAGE_ID} |
| 页面中文名称 | {PAGE_NAME} |
| 原型图 | {PROTOTYPE_LINK_OR_NA} |
| Design revision | {DESIGN_REVISION} |
| Base revision | {BASE_REVISION} |
| 关联需求/功能 | {REQUIREMENT_FEATURE_REFS} |
| 关联验收 | {ACCEPTANCE_REFS} |
| 关联业务流程 | {FLOW_REFS} |

> 本文档必须且只能声明一个主 `PAGE-*`。原型存在时填写指向真实文件的相对链接；不存在时填写“不适用”及原因。

## 2. 页面与入口

| 类型 | 页面/组件编号 | 中文名称 | 入口 | 适用角色 | 数据权限 |
| --- | --- | --- | --- | --- | --- |
| 主页面 | {PRIMARY_PAGE_ID} | {PAGE_NAME} | {PAGE_ENTRY} | {APPLICABLE_ROLES} | {DATA_PERMISSION} |

## 3. 字段与数据来源

| 页面/组件编号 | 页面区域 | 字段 | 来源 | 展示/编辑 | 默认值 | 校验 |
| --- | --- | --- | --- | --- | --- | --- |

## 4. 操作与状态

> 流程编号和步骤编号必须链接到各自 canonical 定义；一个单元格只填写一个编号。

| 操作编号 | 页面/组件 | 操作中文名称 | 详细操作 | 流程编号 | 步骤编号 | 可见条件 | 可用条件 | 成功状态 | 失败状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## 5. 查询与分页

| 页面/组件 | 查询条件 | 排序 | 分页 | 空数据行为 |
| --- | --- | --- | --- | --- |

## 6. 交互流程

1. {INTERACTION_STEP}

### 6.1 关联页面与选择返回（按适用性填写）

> 来源操作、目标操作必须在各自页面的操作表定义。关联编号使用 `PAGEREL-{来源模块}-{稳定主题}`；目标页面使用 `[PAGE-*](相对路径)`。展示方式为 MODAL/DRAWER/ROUTE/INLINE，选择方式为 SINGLE/MULTIPLE/NONE。无关联时保留空表或删除本节，不创建占位关系。专用子组件可链接本页，可复用选择页链接其 Owner 文档。

<!-- PAGE-RELATIONS -->
| 关联编号 | 来源操作 | 目标页面 | 目标操作 | 展示方式 | 输入上下文 | 选择方式 | 返回与回填 | 取消行为 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

> 回填需说明稳定 ID、显示字段、替换/追加、去重和持久化时机；取消需说明原值和未保存输入如何保留。查询权限、空结果、失效选项、重新打开和多选跨页行为放在操作/状态及测试输入中。

### 6.2 被其他页面调用（反向入口）

> 每个调用方一行，只引用调用方的关联编号与来源操作。多个调用方不改变目标页面 Owner；本页子组件也登记指向本页的入口。

<!-- PAGE-CALLERS -->
| 关联编号 | 来源页面 | 来源操作 |
| --- | --- | --- |

## 7. 接口与错误语义

> 接口契约引用必须链接到 API 文档中的具体接口；接口中文名称与 API 文档保持一致，“业务语义”描述页面为何及何时调用该接口。

| 操作编号 | 接口中文名称 | 接口契约引用 | 业务语义 | 请求来源 | 成功反馈 | 业务错误 | 系统错误 | 重试策略 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| {PAGE_ACTION_REF} | {API_CHINESE_NAME} | [{API_CONTRACT_ID}]({API_RELATIVE_LINK}#{API_ANCHOR}) | {API_BUSINESS_SEMANTICS} | {REQUEST_SOURCE} | {SUCCESS_FEEDBACK} | {BUSINESS_ERROR} | {SYSTEM_ERROR} | {RETRY_POLICY} |

## 8. 禁止副作用与验收

> 追踪编号保持纯文本；验收编号必须链接到 Requirement 文档中同名的显式 `AC-*` anchor。

| 追踪编号 | 验收编号 | 可观察结果 | 禁止副作用 |
| --- | --- | --- | --- |
| {TRACE_ID} | [{ACCEPTANCE_ID}]({REQUIREMENT_RELATIVE_LINK}#{ACCEPTANCE_ANCHOR}) | {OBSERVABLE_RESULT} | {FORBIDDEN_SIDE_EFFECT} |

## 9. 追踪关系

> 每行只表达一个“功能—流程—步骤—验收”映射。功能中文名称必须与 Requirement 文档一致；功能、流程、步骤和验收编号必须分别链接到其 canonical 定义。

| 页面/组件编号 | 功能编号 | 功能中文名称 | 流程编号 | 步骤编号 | 规则编号 | 验收编号 | 设计引用 | 测试 Case |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| {PRIMARY_PAGE_ID} | [{FEATURE_ID}]({FEATURE_RELATIVE_LINK}) | [{FEATURE_CHINESE_NAME}]({FEATURE_RELATIVE_LINK}) | [{FLOW_ID}]({FLOW_RELATIVE_LINK}) | [{STEP_ID}]({STEP_RELATIVE_LINK}) | {RULE_REF} | [{ACCEPTANCE_ID}]({REQUIREMENT_RELATIVE_LINK}#{ACCEPTANCE_ANCHOR}) | {DESIGN_REF} | {TEST_CASE_REF} |

## 10. 测试设计输入

> 本节为需求目录下的 `test_case.md` 提供页面事实，不替代测试设计。测试 Case 必须反向引用页面和操作编号，并自行补齐前置数据、清理、自动化脚本与执行命令。

| 页面/操作 | 具体输入示例 | 可观察输出 | 推荐验证方式 | 测试 Case |
| --- | --- | --- | --- | --- |
| {PAGE_ACTION_REF} | {EXACT_PAGE_INPUT_EXAMPLE} | {OBSERVABLE_PAGE_OUTPUT} | {PAGE_VERIFICATION_METHOD} | {TEST_CASE_REF} |

## 枚举引用

页面展示、筛选、按钮条件涉及枚举时，只引用模块说明中的权威定义，不在页面文档复制值表。

| 枚举 | 使用位置 |
| --- | --- |
