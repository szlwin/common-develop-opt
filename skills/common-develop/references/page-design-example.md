<!-- example: common-develop/page-design-v1 -->
# BOOKING 会议室预约页面业务行为设计 Demo

> 这是独立示例，不对应任何现有项目；只演示页面业务行为、测试输入与跨文档追踪，不包含视觉稿。

> 文档导航：[页面设计规范](page-design.md#0-页面设计快速规则) · 模板位置 `assets/structured-docs/page_design.template.md` · 测试模板位置 `assets/structured-docs/test_case.template.md`

## 1. 设计信息

| 项目 | 内容 |
| --- | --- |
| 版本 | V_DEMO_1.0 |
| 模块 | BOOKING |
| Design revision | DESIGN-BOOKING-R01@demo |
| Base revision | 无（首次设计） |
| 关联需求/功能 | REQ-BOOKING-001 / BOOKING-F01 |
| 关联验收 | AC-BOOKING-001、AC-BOOKING-002、AC-BOOKING-003 |
| 关联业务流程 | FLOW-BOOKING-RESERVE |

## 2. 页面与入口

| 页面/组件编号 | 名称 | 入口 | 适用角色 | 数据权限 |
| --- | --- | --- | --- | --- |
| PAGE-BOOKING-QUERY | 会议室预约查询页 | 工作台 → 会议室预约 | 已登录员工、行政管理员 | 员工可查看本人预约；管理员可查看所属办公区全部预约 |
| PAGE-BOOKING-EDIT | 新建/编辑预约弹窗 | 查询页“新建预约”或列表“编辑” | 已登录员工 | 只能编辑本人创建且尚未开始的预约 |

## 3. 字段与数据来源

| 页面/组件编号 | 字段 | 来源 | 展示/编辑 | 默认值 | 校验 |
| --- | --- | --- | --- | --- | --- |
| PAGE-BOOKING-QUERY | 预约人名称 | 用户服务返回的 `displayName` | 展示、可作为查询条件 | 当前登录用户名称 | 最长 50 字符；去除首尾空格 |
| PAGE-BOOKING-QUERY | 会议室 | 会议室主数据 | 展示、可筛选 | 全部 | 只能选择当前办公区启用的会议室 |
| PAGE-BOOKING-EDIT | 开始时间 | 用户输入 | 必填、可编辑 | 当前时间后的下一个整点 | 必须晚于当前时间且早于结束时间 |
| PAGE-BOOKING-EDIT | 结束时间 | 用户输入 | 必填、可编辑 | 开始时间后 1 小时 | 必须晚于开始时间，单次预约不超过 4 小时 |
| PAGE-BOOKING-EDIT | 主题 | 用户输入 | 必填、可编辑 | 空 | 去除首尾空格后 1～100 字符 |

## 4. 操作与状态

| 操作编号 | 页面/组件 | 操作 | 流程编号 | 步骤编号 | 可见条件 | 可用条件 | 成功状态 | 失败状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PAGEACT-BOOKING-QUERY | PAGE-BOOKING-QUERY | 按预约人名称查询 | FLOW-BOOKING-RESERVE | STEP-BOOKING-QUERY | 页面加载成功 | 用户具有查询权限 | 列表只展示匹配条件且有权查看的预约 | 保留查询条件，列表区展示错误和“重新加载”入口 |
| PAGEACT-BOOKING-CREATE | PAGE-BOOKING-EDIT | 创建预约 | FLOW-BOOKING-RESERVE | STEP-BOOKING-CREATE | “新建预约”可见 | 字段校验通过且会议室时段可用 | 关闭弹窗、刷新列表并提示“预约成功” | 保留用户输入；字段错误就地展示，时段冲突提示冲突区间 |
| PAGEACT-BOOKING-CANCEL | PAGE-BOOKING-QUERY | 取消预约 | FLOW-BOOKING-RESERVE | STEP-BOOKING-CANCEL | 当前用户是创建人且预约未开始 | 二次确认后可执行 | 状态变为“已取消”，该时段重新可预约 | 取消失败时保持原状态并显示可重试错误 |

## 5. 查询与分页

| 页面/组件 | 查询条件 | 排序 | 分页 | 空数据行为 |
| --- | --- | --- | --- | --- |
| PAGE-BOOKING-QUERY | 预约人名称、会议室、预约日期、预约状态 | 开始时间降序 | 默认每页 20 条，支持 20/50/100 | 展示“没有符合条件的预约”，保留当前查询条件并提供清空条件入口 |

## 6. 交互流程

1. 用户进入查询页，系统按权限加载默认范围内的预约。
2. 用户输入预约人名称并查询；页面重置到第一页并展示匹配结果。
3. 用户点击“新建预约”，填写会议室、时间和主题。
4. 页面先做必填与格式校验，再提交创建接口。
5. 创建成功后关闭弹窗并刷新列表；业务冲突时保留输入并展示冲突时间段。

## 7. 接口与错误语义

| 操作编号 | 接口契约引用 | 请求来源 | 成功反馈 | 业务错误 | 系统错误 | 重试策略 |
| --- | --- | --- | --- | --- | --- | --- |
| PAGEACT-BOOKING-QUERY | API-BOOKING-QUERY | 查询表单和分页状态 | 更新列表、总数和分页 | 无权限时展示“无权查看该范围” | 列表区错误提示，不清空查询条件 | 用户点击“重新加载”后使用原条件重试 |
| PAGEACT-BOOKING-CREATE | API-BOOKING-CREATE | 编辑弹窗字段 | Toast“预约成功”并刷新列表 | `ROOM_TIME_CONFLICT` 显示冲突区间；字段错误定位到字段 | 弹窗顶部显示系统错误，保留输入 | 创建请求携带幂等请求号；用户可重试一次 |
| PAGEACT-BOOKING-CANCEL | API-BOOKING-CANCEL | 列表行预约编号 | 行状态更新为“已取消” | `BOOKING_ALREADY_STARTED` 提示不可取消并刷新该行 | 保持原行状态并提示重试 | 使用预约编号和操作请求号幂等重试 |

## 8. 禁止副作用与验收

| 追踪编号 | 验收编号 | 可观察结果 | 禁止副作用 |
| --- | --- | --- | --- |
| TR-BOOKING-001 | AC-BOOKING-001 | 输入预约人名称后只展示名称匹配且有权查看的记录 | 不得返回其它办公区或无权查看人员的预约 |
| TR-BOOKING-002 | AC-BOOKING-002 | 创建成功后列表出现新预约，会议室和时间与输入一致 | 冲突失败时不得创建预约或占用时段 |
| TR-BOOKING-003 | AC-BOOKING-003 | 取消成功后状态显示“已取消”，时段可再次预约 | 不得删除预约历史或覆盖创建人信息 |

## 9. 追踪关系

| 页面/组件编号 | 功能编号 | 流程编号 | 步骤编号 | 规则编号 | 验收编号 | 设计引用 | 测试 Case |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PAGE-BOOKING-QUERY | BOOKING-F01 | FLOW-BOOKING-RESERVE | STEP-BOOKING-QUERY | BR-BOOKING-001 | AC-BOOKING-001 | DESIGN-BOOKING-QUERY | CASE-BOOKING-QUERY-NAME-001 |
| PAGE-BOOKING-EDIT | BOOKING-F01 | FLOW-BOOKING-RESERVE | STEP-BOOKING-CREATE | BR-BOOKING-002 | AC-BOOKING-002 | DESIGN-BOOKING-CREATE | CASE-BOOKING-CREATE-001 |
| PAGE-BOOKING-QUERY | BOOKING-F01 | FLOW-BOOKING-RESERVE | STEP-BOOKING-CANCEL | BR-BOOKING-003 | AC-BOOKING-003 | DESIGN-BOOKING-CANCEL | CASE-BOOKING-CANCEL-001 |

## 10. 测试设计输入

| 页面/操作 | 具体输入示例 | 可观察输出 | 推荐验证方式 | 测试 Case |
| --- | --- | --- | --- | --- |
| PAGE-BOOKING-QUERY / PAGEACT-BOOKING-QUERY | 预约人名称输入“张三”，其它条件为空 | 用户查询列表中至少出现一条预约人名称为“张三”的记录，且不出现名称不匹配的记录 | 在页面列表逐行读取“预约人名称”列；同时核对查询接口响应中每条记录的 `displayName` 等于“张三” | CASE-BOOKING-QUERY-NAME-001 |
| PAGE-BOOKING-EDIT / PAGEACT-BOOKING-CREATE | 会议室“海棠”、开始时间“2026-09-01 10:00”、结束时间“2026-09-01 11:00”、主题“迭代计划会” | 弹窗关闭并提示“预约成功”；列表出现会议室、时间和主题完全一致的新记录 | 核对成功提示、列表行和创建接口响应；集成测试再验证持久化记录 | CASE-BOOKING-CREATE-001 |
| PAGE-BOOKING-QUERY / PAGEACT-BOOKING-CANCEL | 对尚未开始的本人预约点击“取消”并确认 | 对应列表行状态变为“已取消” | 核对页面状态文本和取消接口响应；再次查询确认状态保持“已取消” | CASE-BOOKING-CANCEL-001 |

## Demo 使用说明

- 实际项目复制 `assets/structured-docs/page_design.template.md`，不要直接复制本 Demo 的编号和业务内容。
- 页面设计给出用户可观察事实；测试人员在需求目录的 `test_case.md` 中补齐前置数据、初始化/清理脚本、自动化测试文件、执行命令和精确通过标准。
- 每个 `PAGEACT-*` 至少映射一个适用 Case；成功、失败、权限和校验分支是否拆成多个 Case，由实际风险决定。
