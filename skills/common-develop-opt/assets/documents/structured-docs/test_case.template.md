<!-- template: common-develop/test-case-v1 -->
# {REQUIREMENT_ID} 测试设计

> Test revision：`{TEST_REVISION}`  
> Requirement：`{REQUIREMENT_ID}`  
> Requirement revision：`{REQUIREMENT_REVISION}`  
> 状态：草稿

> 文档导航：[项目文档首页](../../../../docs/README.md) · [版本摘要](../../version_summary.md) · [需求文档](requirement.md) · [关联设计]({DESIGN_DOC_REF}) · [关联页面设计]({PAGE_DESIGN_REF})

> 编写原则：每个 Case 必须写出可复现的具体输入、可观察的具体输出和可执行的验证方式。不要只写“输入合法数据”“返回正确结果”。例如输入用户名称“张三”，预期输出应写成“用户查询页面列表出现名称为‘张三’的记录”，并说明通过页面文本、接口响应或数据库查询中的哪一种方式验证。

## 1. 测试范围与环境

- 测试目标：{TEST_OBJECTIVE}
- 范围内：{IN_SCOPE}
- 范围外：{OUT_OF_SCOPE}
- 环境与账号：{ENVIRONMENT_AND_ACCOUNT}
- 共用前置条件：{SHARED_PRECONDITIONS}

## 2. Case 索引

| Case ID | 名称 | 功能/验收 | 页面/操作 | 流程/步骤 | 测试层级 | 自动化状态 |
|---|---|---|---|---|---|---|
| {CASE_ID} | {CASE_NAME} | {FEATURE_ACCEPTANCE_REFS} | {PAGE_ACTION_REFS} | {FLOW_STEP_REFS} | {TEST_LEVEL} | {AUTOMATION_STATUS} |

<!-- TEST-CASE: {CASE_ID} -->
<a id="{CASE_ID}"></a>
## {CASE_ID} {CASE_NAME}

### 关联事实

<!-- TEST-CASE-TRACE -->
| 对象 | 稳定引用 | 来源文档 |
|---|---|---|
| 需求/验收 | {REQUIREMENT_ACCEPTANCE_REFS} | [需求文档](requirement.md#{REQUIREMENT_ANCHOR}) |
| 设计/契约 | {DESIGN_CONTRACT_REFS} | [关联设计]({DESIGN_DOC_REF}#{DESIGN_ANCHOR}) |
| 页面/操作 | {PAGE_ACTION_REFS} | [页面设计]({PAGE_DESIGN_REF}#{PAGE_ANCHOR}) |
| 流程/步骤 | {FLOW_STEP_REFS} | {FLOW_DOC_REF_OR_REASONED_NA} |

### 关联页面验证（按适用性填写）

> 涉及选择返回时，在上表登记 PAGEREL 编号、两侧 PAGE/PAGEACT 和来源文档，并填写下表。来源页面用 `[PAGE-*](相对路径)`；验证场景写明确认/取消/异常等分支，返回断言写字段精确值或不变条件。无关联时保留空表或删除本节。一个 Case 不必覆盖所有分支。

<!-- TEST-CASE-PAGE-RELATIONS -->
| 关联编号 | 来源页面 | 验证场景 | 返回断言 |
| --- | --- | --- | --- |

### 前置条件和具体输入

<!-- TEST-CASE-INPUT -->
| 输入项 | 输入值 | 输入方式 | 来源/约束 |
|---|---|---|---|
| {INPUT_NAME} | {EXACT_INPUT_VALUE} | {INPUT_METHOD} | {INPUT_SOURCE_OR_CONSTRAINT} |

### 执行步骤

<!-- TEST-CASE-STEPS -->
| 序号 | 操作 | 页面/接口 | 预期中间结果 |
|---:|---|---|---|
| 1 | {ACTION} | {PAGE_OR_API} | {INTERMEDIATE_RESULT} |

### 预期输出与验证方式

<!-- TEST-CASE-OUTPUT -->
| 输出位置 | 预期输出 | 验证方式 | 通过标准 |
|---|---|---|---|
| {OUTPUT_LOCATION} | {EXACT_EXPECTED_OUTPUT} | {VERIFICATION_METHOD} | {PASS_CRITERIA} |

### 禁止副作用与清理

- 禁止副作用：{FORBIDDEN_SIDE_EFFECTS}
- Case 完成后的状态：{POSTCONDITION}
- 清理要求：{CLEANUP_REQUIREMENT}

### 测试数据与自动化映射

> 沿用项目已声明的框架和 Harness。设计阶段可记录计划中的相对脚本路径/方法并标注 PLANNED；脚本实现后改为 IMPLEMENTED，实际运行结果通过 Evidence 记录，不以文件存在代表 PASSED。脚本反向引用本 Case、适用 FLOW/STEP 和 PAGEREL；路径基准、执行目录、环境变量名、报告位置见项目环境文档。初始化/清理只处理本次测试拥有的数据。具体交接要求见 Skill 的测试矩阵规范。

<!-- TEST-CASE-AUTOMATION -->
| 测试数据初始化 | 清理脚本 | 自动化测试文件与方法 | 执行命令 | 状态/不自动化理由 |
|---|---|---|---|---|
| {FIXTURE_REF_OR_REASONED_NA} | {CLEANUP_REF_OR_REASONED_NA} | {AUTOMATION_TEST_REF_OR_REASONED_NA} | {EXECUTION_COMMAND_OR_REASONED_NA} | {AUTOMATION_STATUS_OR_REASON} |

## 4. 完成门禁

- [ ] 每个 Case 都绑定本需求的 Requirement、Feature 和 Acceptance ID。
- [ ] 页面型 Case 同时绑定 `PAGE-*` 与 `PAGEACT-*`，页面设计反向登记 Case ID。
- [ ] 每个 Case 都写明具体输入值、具体预期输出、输出观察位置、验证方式和通过标准。
- [ ] 需要数据准备的 Case 链接初始化与清理脚本；不需要时写明理由。
- [ ] 可自动化 Case 链接测试文件、测试方法和执行命令；不能自动化时写明理由及替代验证。
- [ ] 初始化脚本、测试代码和流程事实按适用性反向引用 Case ID。
- [ ] 正常、边界、异常、权限、事务、幂等、并发、兼容和禁止副作用已按适用性覆盖。

## 枚举引用

仅登记本需求测试实际使用的模块枚举；枚举值含义不得在本文件重新定义。

| 枚举 | 用途 |
| --- | --- |
