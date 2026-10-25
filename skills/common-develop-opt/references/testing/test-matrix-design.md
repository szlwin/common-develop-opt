# Test Matrix Design

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

## 1. Required dimensions

按适用性覆盖：正常路径；null/空值/格式/长度/边界/重复/非法枚举；权限、归属、状态、类型和前置流程；异常类型/业务错误码及无副作用；事务回滚与关联数据一致性；关联对象失效/删除/取消影响；跨模块超时、乱序、重复、部分失败、补偿和人工恢复；需求明确时的幂等、并发与日志。

## 2. Traceability

每个测试 Case 必须映射验收规则、Requirement、实现契约或业务流程/风险。每个契约的正常与异常分支至少映射一个 Case；适用 `IMP-*`、关键 `FLOW/STEP`、跨模块 `CMI/CMSTEP` 无法映射时不得进入实现。测试设计按需求拆分，每个已进入 `test_design` 的需求只维护：

```text
DOC_ROOT/version/{VERSION}/requirement/{REQUIREMENT_ID}/test_case.md
```

禁止继续创建版本根级共享 `test_case.md`，也禁止把多个 Requirement 的 Case 混入同一文件。

### 2.1 初始化与补全边界

使用 `case_doc.py init` 创建 Requirement 目录下的 canonical `test_case.md` 草稿。`init` 只自动填充 Requirement、Case、Test revision 等身份字段，业务字段的 `{...}` 占位符在 `draft` 阶段是预期的，不表示模板失效。AI 应在该文件中依据当前需求、设计和流程事实逐项补全，不要手工重建平行文档或复制到其他路径。

`init` 写入草稿后还会刷新派生关系图和文档门户。因此，如果命令报 `inline collections must use JSON syntax`、结构化文档解析失败或关系校验失败，直接原因是 `DOC_ROOT` 中已有的业务模型、流程或其他结构化事实，不是测试模板占位符。应根据错误定位并修复项目事实，或先运行 `artifact_relations.py validate/build` 诊断；`case_doc.py validate` 只验证 `test_case.md`，不能证明全局关系图输入有效。关系刷新失败时不得把手工创建测试文档当作该问题已解决。

补全后运行 `case_doc.py validate --stage ready`；只有此校验通过且不再有占位符时，Case 文档才可作为测试设计完成产物。`test_design` 阶段只设计 Case、断言和追踪，不执行测试代码，不修改 XML、生产模块或测试运行结果；测试实现属于 TDD，实际命令和执行 Evidence 属于 `testing`。

Case 至少包含：`case_id | requirement_ref | acceptance_ref | feature_ref | contract_ref | branch | concrete_inputs | steps | observable_outputs | verification_method | pass_criteria | exception | side_effect | test_level`。页面型 Case 还必须记录 `page_ref` 与 `page_action_ref`；流程型 Case 记录 `flow_refs`、`flow_path`、`variant_refs`、`failure_path_refs`。主路径至少覆盖一组，关键变体和 blocking failure path 必须有测试或明确的非测试验证。

输入必须是可复现的具体值，输出必须说明观察位置和精确结果，验证方式必须可执行。例如输入用户名称“张三”时，应写明“用户查询页面列表出现名称为‘张三’的记录”，并说明核对页面列表列值、接口响应字段还是数据库查询结果；“输入合法数据”“返回正确结果”均不合格。

每个 Case 按适用性链接测试数据初始化、清理脚本、自动化测试文件与方法、执行命令。无需数据脚本或无法自动化时必须写明理由和替代验证。页面设计、初始化/清理脚本、自动化测试代码按适用性反向登记同一 `CASE-*`。

## 3. Design boundary

TestDesignAgent 先基于需求、页面设计、API、契约、流程和异常定义更新测试设计，并运行 `case_doc.py validate --stage ready`；TddAgent 再把适用 Case 落成可执行测试。Case 数量不能替代语义完整性，未映射、输入输出不具体或仅覆盖实现细节的测试不计作完整覆盖。

## 4. 页面关联与通用自动化交接

涉及关联查询、选择与返回时，读取 [关联页面规范](../specialist/page-design.md#5-关联页面查询与选择返回)。Case 的关联事实登记 `PAGEREL-*`、调用页和目标页的 `PAGE/PAGEACT`，并用模板的 `TEST-CASE-PAGE-RELATIONS` 表说明本 Case 验证的分支及回填/不变断言。操作从来源页面跳到目标页面再返回，不能用接口调用成功替代页面实际回填成功。无权限、无结果、取消、选择后对象失效、重复打开及多选行为按适用性覆盖。

继续使用 Case 的 `TEST-CASE-AUTOMATION` 表作为自动化映射的唯一入口，不新建平行的用例事实源：

- TestDesignAgent 记录测试层级（页面、接口或混合）、计划脚本/方法、数据准备、清理与命令。计划路径标记 `PLANNED`；尚未实现的测试不会阻断设计阶段，也不能声明已执行。
- TddAgent 根据项目现有框架实现脚本；已实现映射标记 `IMPLEMENTED`。测试代码用项目的注释、注解或测试元数据反向引用 `CASE-*`、适用 `FLOW/STEP`、`PAGEREL-*`。重命名测试时同步更新映射。
- 页面脚本依据真实页面/前端代码选择定位器；接口脚本依据当前 OpenAPI/代码与版本增量确定请求和断言。不能从页面描述猜选择器，不能把 API changeset 当完整接口契约。明确每一步通过 UI 还是 API 执行，以及哪个观察点证明结果。
- Harness 声明框架、执行目录、脚本根目录、环境变量名、报告位置和数据隔离。独立测试仓库用项目声明的路径基准定位，不硬编码个人绝对路径，不将测试代码复制入文档目录。
- 初始化使用本次运行的数据标识，清理在失败时也执行且仅处理本次拥有的数据。存在跨测试复用或串行依赖时显式声明，禁止依赖上次运行残留状态。
- 实现 Review 核对真实文件/方法、反向 Case 映射和可发现性；TestAgent 在 testing 阶段执行已声明命令，登记 revision、结果、报告和 Evidence。跳过、未执行、环境缺失不能写为通过。

本契约指导 Agent 编写 Python、TypeScript 等项目测试代码；它不内置框架生成器或执行器。`case_doc.py` 自动检查文档结构及关联页面引用，不静态解析任意语言的方法，也不执行文档中的命令；脚本发现、业务断言正确性和实际运行结果由项目工具及 Review 验证。已有工作模式和测试阶段豁免保持有效。
