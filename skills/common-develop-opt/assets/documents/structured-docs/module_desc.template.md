<!-- template: common-develop/module-desc-v2 -->
# {MODULE_CODE} 模块说明

> 版本级文件记录本版本新增或调整的模块事实；`wk -d` 会自动发现同目录补充文档并维护“文档引用”。

> 文档导航：[项目文档首页](../../../../docs/README.md) · [版本摘要](../../version_summary.md) · [本版本需求列表](../../requirement/requirement_list.md)

## 模块信息

| 字段 | 内容 |
| --- | --- |
| 模块编码 | {MODULE_CODE} |
| 模块名称 | 待补充 |
| 模块说明 | 待补充 |
| 职责边界 | 待补充 |
| 最后合并版本 | {VERSION} |

## 业务能力

| 能力编码 | 能力名称 | 能力说明 | 能力边界 | 状态 |
| --- | --- | --- | --- | --- |

## 模块功能

| 功能编码 | 功能名称 | 功能说明 | 所属能力 | 关联需求 | API 引用 | DB 引用 | 功能状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |

## 业务信息

| 信息编码 | 信息名称 | 信息说明 | DB引用 |
| --- | --- | --- | --- |

## 枚举

> 本节是本模块枚举的唯一业务定义。API、DB、测试用例和页面设计只引用枚举编码与锚点，不重复定义值含义。版本级文件只写本版本新增或调整的枚举和值。

<a id="enum-{MODULE_CODE}-example"></a>

### ENUM-{MODULE_CODE}-EXAMPLE / 示例枚举

| 属性 | 内容 |
| --- | --- |
| 枚举编码 | `{MODULE_CODE}.EXAMPLE_TYPE` |
| 名称 | 示例类型 |
| 枚举分类 | 类型 |
| 值类型 | INTEGER |
| 说明 | 示例；实际使用时替换为真实业务枚举 |
| 状态 | ACTIVE |
| 变更类型 | ADD |
| 变更说明 | 首次定义 |

| 标识 | 值 | 名称 | 业务含义 | 状态 | 引入版本 | 废弃版本 |
| --- | --- | --- | --- | --- | --- | --- |
| `EXAMPLE` | `1` | 示例 | 示例值；实际使用时删除本行并填写真实值 | ACTIVE | {VERSION} | - |

## 规则与约束

| 规则编码 | 规则摘要 | 关联需求 | 关联功能 |
| --- | --- | --- | --- |

## 模块依赖

| 依赖模块 | 依赖类型 | 依赖说明 | 关联功能 | 接口引用 |
| --- | --- | --- | --- | --- |

## 参与业务流程

| 流程编号 | 流程名称 | 模块角色 | 关联功能 | 流程层级 | 状态 | 流程文档 |
| --- | --- | --- | --- | --- | --- | --- |

## 页面设计

> 仅登记属于本模块的页面 Owner 文档。页面设计链接从本文件所在目录计算，逐页指向 `page-design/{PAGE_DOCUMENT_STEM}_design.md`；不得链接旧 `{MODULE_CODE}_page_design.md` 或复制页面正文。

| 页面编号 | 页面中文名称 | 页面设计 | 原型图 | 关联功能 | 状态 |
| --- | --- | --- | --- | --- | --- |
| {PRIMARY_PAGE_ID} | {PAGE_NAME} | [打开](page-design/{PAGE_DOCUMENT_STEM}_design.md) | {PROTOTYPE_LINK_OR_NA} | {FEATURE_REFS} | {PAGE_STATUS} |

## 文档引用

- 需求列表：[打开](../../requirement/requirement_list.md)
- 业务模型：[打开]({MODULE_CODE}_business_model.yaml)
- API/DB 版本增量：[打开目录](changes/)
- API/DB 当前实现：代码、OpenAPI、DDL、Migration 或项目声明的等价来源
- 模块设计：[打开]({MODULE_CODE}_design.md)
- 页面设计：见上方“页面设计”逐页入口
- 依赖关系：[项目关联事实](../../../../docs/_relations/dependency_impact.yaml)
