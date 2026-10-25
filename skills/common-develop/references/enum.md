# 枚举值规范

## 0. 枚举快速规则 {#enum-quick-rules}

只有在值集合封闭且稳定、语义明确并具备兼容性处理时，才使用枚举。复用现有值和序列化规则；新增、重命名、持久化或迁移值时读取本文件。

## 定义位置

模块内的枚举值必须在以下文档中定义：

- `DOC_ROOT/docs/{MODULE_CODE}/{MODULE_CODE}_desc.md`
- `DOC_ROOT/version/{VERSION}/doc/{MODULE_CODE}/{MODULE_CODE}_desc.md`

定义后必须在以下位置统一引用：

- 前端实现契约。
- 后端实现契约。
- API 设计文档。
- 表字段设计文档。
- 前端代码。
- 后端代码。
- 流程设计文档。

## 命名规则

枚举名建议使用：

```text
{DOMAIN}.{FIELD}
```

示例：

| 枚举名 | 说明 |
|---|---|
| USER.STATUS | USER.STATUS.ENABLE=1 可用, USER.STATUS.DISABLE=2 禁用 |

## 引用规则

在设计文档、前端实现契约、后端实现契约、流程设计文档中，使用如下格式引用：

```text
@{MODULE_CODE}.{ENUM_NAME}.{ENUM_VALUE}
```

示例：

```text
@MOD.ENTITY.STATUS.ENABLE
@MOD.ENTITY.STATUS.DISABLE
```

## 代码实现

前端和后端实际代码中，应根据具体项目已有枚举代码风格实现。

要求：

- 类型、状态类字段和变量必须使用枚举，包括但不限于 `status`、`type`、`state`、`categoryType`、`enableFlag`、`deleteFlag`、`relStatus`。
- 不直接硬编码魔法值。
- 不使用裸数值或数值常量表达业务含义，例如禁止 `setStatus(1)`、`if (status == 1)`、`const ENABLED = 1` 这类写法；应使用项目枚举对象、枚举常量或枚举方法，例如 `StatusEnum.ENABLED.getCode()`。
- 如项目已有枚举基类或字典体系，应复用现有体系；如没有，应先定义枚举，再在业务代码中引用。
- 不在前端、后端、数据库文档中使用互相矛盾的枚举值。
- 新增枚举必须同步到项目级模块功能文档和当前版本的模块说明文档。
- 表结构中的枚举字段说明必须与模块功能文档中的枚举一致。

## Review 检查

Reviewer 必须检查：

- 枚举是否已定义。
- 枚举是否使用统一引用格式。
- API、表结构、实现契约、代码、流程图是否引用一致。
- 是否存在未说明的状态值、type 值或魔法数字。
- 是否存在裸数值或数值常量表达状态/类型业务含义；如存在，Review 不通过，并要求DevelopAgent 改为枚举。
