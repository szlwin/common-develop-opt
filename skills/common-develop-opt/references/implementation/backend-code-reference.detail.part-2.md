# backend-code-reference（续）

> 本文是 [backend-code-reference.detail.md](./backend-code-reference.detail.md) 的按需续篇。
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
DOC_ROOT/version/{VERSION}/module/COM/COM_back_code.md
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
    api: DOC_ROOT/version/{VERSION}/module/MOD/MOD_api.md#MOD0001
    db: DOC_ROOT/version/{VERSION}/module/MOD/MOD_db.md

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
