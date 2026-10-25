# 后端实现契约语法

## 0. 后端实现边界 {#backend-implementation-boundary}

选择框架模式前，先使用当前实现契约和项目约定。保持事务边界、授权、持久化语义、错误契约和可观察副作用不变；需要具体 Java/Spring、数据库、API、并发或 Repository 指导时读取本文件。

本文件定义后端实现契约的轻量 DSL。目标是在正式编码前确认业务逻辑、减少歧义和降低 token 消耗；不要把实现契约写成完整代码或大段自然语言。

## 目录

- [核心原则](#核心原则)
- [文件与读取优先级](#文件与读取优先级)
- [复杂度分级](#复杂度分级)
- [变量前缀](#变量前缀)
- [功能方法定义](#功能方法定义)
- [固定段落](#固定段落)
- [方法契约与调用关系](#方法契约与调用关系)
- [测试矩阵与复杂度预算](#测试矩阵与复杂度预算)
- [表达式与校验](#表达式与校验)
- [数据操作](#数据操作)
- [计算与公共方法](#计算与公共方法)
- [枚举引用](#枚举引用)
- [最小示例](#最小示例)
- [Review 检查](#review-检查)

## 核心原则

- 实现契约源文档必须使用 Markdown、YAML、JSON 或纯文本代码块；HTML 仅是可重建阅读副本，不得作为实现输入。
- 每个功能只写“会影响实现、测试或用户确认”的逻辑；项目默认规则用引用代替重复描述。
- 简单 CRUD 不展开所有固定字段；复杂状态流转、金额计算、多表联动、权限差异必须写完整流程。
- 条件表达式统一使用类 Java 语法：`==`、`!=`、`&&`、`||`、`in`、`not in`、`is empty`、`not empty`。
- 枚举必须使用 `@{MODULE_CODE}.{ENUM_NAME}.{ENUM_VALUE}`，不写魔法值。
- 同一功能的实现契约必须能被 DevelopAgent、Reviewer 和 TestAgent 直接定位到功能编号。

## 文件与读取优先级

后端实现契约源文档固定维护：

```text
DOC_ROOT/version/{VERSION}/doc/{MODULE_CODE}/{MODULE_CODE}_back_code.md
```

读取优先级：

1. AI 开发、Review、测试优先读取 `{MODULE_CODE}_back_code.md`。
2. 缺少 Markdown/YAML/JSON 或纯文本源契约时立即阻塞并补齐事实源；HTML 不得充当实现契约输入。

## 复杂度分级

每个功能必须标记复杂度，用于控制输出粒度。

| 复杂度 | 适用场景 | 写法 |
|---|---|---|
| `simple` | 单表 CRUD、无特殊状态流转 | 写 `meta`、`refs`、关键 `validate`、关键 `save/return` |
| `normal` | 有权限、枚举、事务、少量多表读取 | 写完整固定段落，但只展开关键字段 |
| `complex` | 状态机、金额计算、批处理、多表联动、外部接口、强一致性 | 写完整流程、确认点和异常分支 |

## 变量前缀

统一使用语义前缀，避免 `#`、`*`、`##` 在 Markdown 中产生歧义。

| 前缀 | 含义 | 是否可改 | 示例 |
|---|---|---:|---|
| `in.` | 入参 / 请求 DTO | 否 | `in.mobile` |
| `tmp.` | 临时变量 / 中间计算结果 | 是 | `tmp.member` |
| `db.` | 从数据库、缓存或外部系统读取的数据 | 否 | `db.member` |
| `out.` | 返回 VO / 输出结构 | 是 | `out.memberId` |
| `ctx.` | 上下文，如登录用户、租户、权限 | 否 | `ctx.userId` |

数组使用 `[]` 表示：

```text
db.memberList[]
out.rows[]
```

## 功能方法定义

基本格式：

```text
define mf {功能编号} {方法名}(in: {DTO}) -> {VO|Void|List<VO>|Page<VO>} {
  ...
}
```

要求：

- `{功能编号}` 必须与需求、设计、API、测试用例中的功能编号一致。
- `{方法名}` 使用业务语义，不要求等同最终代码方法名。
- 输入输出 DTO/VO 引用本版本 API changeset 增量和当前代码/OpenAPI；不要在实现契约中重复全字段。
- 无返回值使用 `Void`。

## 固定段落

按以下顺序书写；无内容的段落可省略，但不要改名。

```text
define mf {FUNCTION_ID} {METHOD_NAME}(in: {INPUT_TYPE}) -> {OUTPUT_TYPE} {
  meta:
    module: {MODULE_CODE}
    feature: {FEATURE_NAME}
    complexity: {simple|normal|complex}

  refs:
    requirement: DOC_ROOT/version/{VERSION}/doc/{TARGET_ID}/requirement.md#{FEATURE_ID}
    api_delta: DOC_ROOT/version/{VERSION}/doc/{MODULE_CODE}/changes/{CHANGESET}.yaml#{API_ID}
    api_implementation: {OPENAPI_OR_CONTROLLER_DTO_REF}
    db_delta: DOC_ROOT/version/{VERSION}/doc/{MODULE_CODE}/changes/{CHANGESET}.yaml#{DB_ID}
    db_implementation: {DDL_MIGRATION_OR_SCHEMA_REF}
    enum: DOC_ROOT/docs/{MODULE_CODE}/{MODULE_CODE}_desc.md#enum

  defaults:
    auth: project-default
    tx: auto-if-write
    error: project-default
    audit: project-default

  contract:
    id: {CONTRACT_ID}
    signature: {METHOD_NAME}({INPUT_TYPE}) -> {OUTPUT_TYPE}
    preconditions: [...]
    postconditions: [...]
    errors: [...]
    transaction: {required|read-only|none}

  calls:
    {METHOD_NAME} -> ...

  test_matrix:
    {CASE_ID}: ...

  complexity_budget:
    source: DOC_ROOT/harness_env.md

  validate:
    ...

  load:
    ...

  biz:
    ...

  save:
    ...

  return:
    ...

  confirm:
    ...
}
```

段落含义：

| 段落 | 用途 |
|---|---|
| `meta` | 模块、功能名、复杂度 |
| `refs` | 需求、API、DB、枚举、流程等引用 |
| `defaults` | 项目默认权限、事务、异常、审计、日志等规则 |
| `contract` | 主要方法签名、输入输出、前置/后置条件、异常、事务、幂等和副作用 |
| `calls` | 公共方法、业务协作边界和关键调用顺序；不固定无须稳定的 private 方法 |
| `test_matrix` | 方法契约到 `test_case.md` Case ID、正常/边界/异常分支的映射 |
| `complexity_budget` | 引用 `harness_env.md` 中已确认的覆盖率和复杂度规则，不在实现契约中复制项目阈值 |
| `validate` | 参数校验、权限校验、业务前置校验 |
| `load` | 数据存储、缓存或外部接口等读取 |
| `biz` | 业务判断、状态流转、字段映射、计算 |
| `save` | 新增、更新、删除、缓存写入、事件发布 |
| `return` | 输出组装 |
| `confirm` | 用户或 ProjectManagerAgent 需确认的问题 |

## 方法契约与调用关系

新增或修改后端业务逻辑时，`contract` 和 `calls` 是 TDD 的前置输入：

```text
contract:
  id: MOD0001-C01
  signature: bindPart(BindPartCommand) -> BindPartResult
  inputs: [mainDeviceId required, partDeviceIds required not-empty]
  preconditions: [主设备存在且为主件, 配件均未绑定]
  postconditions: [关联已创建, 配件状态已更新]
  errors:
    MAIN_DEVICE_NOT_FOUND: BusinessException
    PART_ALREADY_BOUND: BusinessException
  sideEffects: [insert relation, update part status, biz log]
  transaction: required
  idempotency: duplicate-active-relation forbidden

calls:
  bindPart -> loadMainDevice -> loadPartDevices -> validateBindable
  bindPart -> saveRelations -> updatePartStatus -> writeBizLog
```

只定义需要被用户、架构、测试和调用方稳定理解的入口与协作端口。不要为 TDD 提前设计全部 private 方法，也不要让测试依赖不属于业务契约的内部调用顺序。

## 测试矩阵与复杂度预算

`test_matrix` 使用 `test_case.md` 的 Case ID，不复制完整用例：

```text
test_matrix:
  CASE-MOD0001-001: success -> relation-created, part-bound
  CASE-MOD0001-002: main-missing -> MAIN_DEVICE_NOT_FOUND, no-write
  CASE-MOD0001-003: part-bound -> PART_ALREADY_BOUND, no-write
  CASE-MOD0001-004: save-failed -> rollback

complexity_budget:
  source: DOC_ROOT/harness_env.md
  scope: changed-methods
```

每个正常、边界和异常分支至少映射一个 Case。项目的覆盖率、复杂度阈值、命令和报告路径只引用 `harness_env.md`，不写死在通用实现契约。

## 表达式与校验

校验统一使用：

```text
check(条件, throw "错误信息")
check(条件, throw ERROR_CODE "错误信息")
check(条件, error "日志信息")
```

示例：

```text
check(in.mobile not empty, throw MOBILE_REQUIRED "手机号不能为空")
check(in.mobile matches MOBILE, throw "手机号格式错误")
check(ctx.hasPerm("MOD_CREATE"), throw "无新增会员权限")
check(db.member == null, throw "手机号已存在")
```

规则：

- 条件表达式统一使用 `==`、`!=`、`&&`、`||`。
- 不使用单等号 `=` 表示判断。
- 错误信息必须面向用户或日志可理解。
- 项目存在稳定业务错误码时，异常校验必须使用 `throw ERROR_CODE "错误信息"`，并与 `contract.errors`、API 文档和测试断言一致；没有错误码体系时按项目现有异常约定处理。

## 数据操作

数据操作使用短方法表达，不展开最终 ORM 或 SQL 细节。

```text
db.member = db.get(t_member, mobile == in.mobile)
db.memberList[] = db.query(t_member, status == @MOD.STATUS.ENABLED)
db.page = db.page(t_member, in.page, in.size, status == in.status)

db.insert(t_member, tmp.member)
db.update(t_member, tmp.member, id == in.id)
db.delete(t_member, id == in.id)          // 仅需求和项目数据生命周期明确物理删除时使用
db.logicDelete(t_member, id == in.id)     // 项目采用逻辑删除时使用
db.batchInsert(t_member_role, tmp.roles[])
db.batchUpdate(t_member, tmp.members[], id in tmp.ids[])
```

多数据源时在表名前加数据源别名：

```text
db.member = db.get(mysqlMain.t_member, id == in.id)
tmp.session = cache.get(redisMain, "session:" + in.token)
```

复杂多表查询可写 `querySQL`，但必须先写业务意图：

```text
// 查询会员及企业名称；示例要求保留企业信息缺失的会员，因此使用 left join
db.rows[] = db.querySQL("
  select m.id, m.name, e.name as enterpriseName
  from t_member m
  left join t_enterprise e on e.id = m.enterprise_id
  where m.status = :status
", { status: in.status })
```

实际 JOIN 类型必须按业务保留语义、关联基数和空值处理确定，不得照搬示例。

## 计算与公共方法

简单计算直接写表达式：

```text
tmp.totalAmount = round(in.price * in.count, 2)
tmp.enabledCount = count(db.memberList[], item.status == @MOD.STATUS.ENABLED)
```

复杂聚合使用 `computeSQL`，并写明意图：

```text
// 按会员类型统计数量，用于统计卡片
tmp.stats[] = computeSQL("
  select member_type, count(*) as count
  from db.memberList
  group by member_type
")
```

可复用复杂逻辑写入公共实现契约：

```text
DOC_ROOT/version/{VERSION}/doc/COM/COM_back_code.md
```

公共方法调用使用 `$$` 前缀：

```text
tmp.allowed = $$verifyDataScope(ctx.userId, in.enterpriseId)
```

## 枚举引用

枚举统一引用 `enum.md` 的格式：

```text
@{MODULE_CODE}.{ENUM_NAME}.{ENUM_VALUE}
```

示例：

```text
tmp.member.status = @MOD.STATUS.ENABLED
check(in.memberType in [@MOD.TYPE.PERSONAL, @MOD.TYPE.ENTERPRISE], throw "会员类型错误")
```

## 最小示例

```text
define mf MOD0001 createMember(in: MemberCreateDTO) -> MemberVO {
  meta:
    module: MOD
    feature: 新增会员
    complexity: normal

  refs:
    api: DOC_ROOT/version/{VERSION}/doc/MOD/MOD_api.md#MOD0001
    db: DOC_ROOT/version/{VERSION}/doc/MOD/MOD_db.md

  defaults:
    auth: project-default
    tx: auto-if-write
    error: project-default
    audit: project-default

  contract:
    id: MOD0001-C01
    signature: createMember(MemberCreateDTO) -> MemberVO
    preconditions: [名称有效, 手机号未占用]
    postconditions: [会员已创建]
    errors: [NAME_INVALID, MOBILE_EXISTS]

  calls:
    createMember -> validateInput -> loadExisting -> insertMember

  test_matrix:
    CASE-MOD0001-001: success
    CASE-MOD0001-002: invalid-name -> NAME_INVALID
    CASE-MOD0001-003: duplicate-mobile -> MOBILE_EXISTS

  complexity_budget:
    source: DOC_ROOT/harness_env.md

  validate:
    check(ctx.hasPerm("MOD_CREATE"), throw "无新增会员权限")
    check(in.name not empty, throw NAME_INVALID "会员名称不能为空")
    check(in.mobile matches MOBILE, throw "手机号格式错误")

  load:
    db.exists = db.get(t_member, mobile == in.mobile)

  biz:
    check(db.exists == null, throw MOBILE_EXISTS "手机号已存在")
    tmp.member = map in -> t_member
    tmp.member.status = @MOD.STATUS.ENABLED

  save:
    db.insert(t_member, tmp.member)

  return:
    out = map tmp.member -> MemberVO
    out.id = tmp.member.id

  confirm:
    C001: 手机号是否全局唯一？default: 是
}
```

## Review 检查

Reviewer 检查后端实现契约时至少确认：

- 功能编号、API、DB、枚举、测试用例是否可追溯。
- `complexity` 是否合理；复杂功能是否展开关键状态、金额、多表和异常分支。
- `contract` 是否明确方法参数、返回值、前置/后置条件、异常、事务和副作用；`calls` 是否只固定必要边界。
- `test_matrix` 是否覆盖正常、边界、异常和无副作用逻辑，并能追溯到 `test_case.md`。
- `complexity_budget` 是否引用项目 `harness_env.md`，实际代码是否通过覆盖率和复杂度门禁。
- 是否只使用 `in/tmp/db/out/ctx` 当前前缀。
- 是否引用枚举而不是写魔法值。
- 是否把用户确认点集中写在 `confirm`。
- 是否存在大段重复默认规则；如有，应改为 `defaults` 或 `refs`。
