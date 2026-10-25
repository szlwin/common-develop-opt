# 枚举规范

## 快速规则 {#enum-quick-rules}

模块业务枚举只在 `{MODULE_CODE}_desc.md` 定义一次。项目当前全量定义位于 `DOC_ROOT/docs/module/{MODULE_CODE}/{MODULE_CODE}_desc.md`；当前版本新增、扩展、修改或废弃只写入 `DOC_ROOT/version/{VERSION}/module/{MODULE_CODE}/{MODULE_CODE}_desc.md`，由 `wk -d` 语义合并。API、DB、Test Case、页面设计和代码只能引用，不得复制另一份值表。

## 定义格式

枚举编码固定为：

```text
{MODULE_CODE}.{ENUM_NAME}
```

枚举值的稳定引用写为：

```text
{MODULE_CODE}.{ENUM_NAME}.{SYMBOL}
```

每个枚举在模块说明中使用稳定 `ENUM-*` 标识和稳定锚点：

```markdown
<a id="enum-ord-order-status"></a>
### ENUM-ORD-ORDER-STATUS / 订单状态

| 属性 | 内容 |
| --- | --- |
| 枚举编码 | `ORD.ORDER_STATUS` |
| 名称 | 订单状态 |
| 枚举分类 | 状态 |
| 值类型 | INTEGER |
| 说明 | 表示订单当前所处的业务状态 |
| 状态 | ACTIVE |

| 标识 | 值 | 名称 | 业务含义 | 状态 | 引入版本 | 废弃版本 |
| --- | --- | --- | --- | --- | --- | --- |
| `CREATED` | `1` | 已创建 | 订单已创建但尚未提交 | ACTIVE | V_1.0 | - |
```

版本级定义额外声明 `变更类型`（`ADD/EXTEND/MODIFY/DEPRECATE`）和必要的变更说明。项目级归档结果不保留这些临时变更字段，只保留当前全量事实。

## 引用规则

Markdown 文档使用可点击引用：

```markdown
[ORD.ORDER_STATUS](ORD_desc.md#enum-ord-order-status)
```

跨模块时直接链接 Owner 模块，不复制定义。谁拥有业务含义，谁定义枚举。

版本需求、设计、测试和页面 Markdown 中只要出现 `MODULE.ENUM` 或
`MODULE.ENUM.SYMBOL`，同一行必须提供指向 Owner 模块说明稳定锚点的可点击链接。
`wk -d` 会阻断没有 canonical 枚举链接的使用；模块说明自身的定义表是唯一允许不带该链接的文件。

结构化 API changeset 在 endpoint 的 `enumRefs` 登记引用：

```yaml
enumRefs:
  - usedBy: request.status
    enumRef: ORD.ORDER_STATUS
    enumLink: ../ORD_desc.md#enum-ord-order-status
```

结构化 DB changeset 在 column 上登记：

```yaml
name: order_status
type: TINYINT
nullable: false
enumRef: ORD.ORDER_STATUS
enumLink: ../ORD_desc.md#enum-ord-order-status
```

Test Case、页面设计、流程和实现设计需要说明具体值时使用稳定符号，例如 `ORD.ORDER_STATUS.SUBMITTED`；可附实际持久化值帮助阅读，但业务含义仍以模块说明为准。

## 兼容与归档

- 已发布 symbol 不得改为另一个持久化值。
- 已发布 value 不得复用于另一个 symbol。
- 删除必须改为 `DEPRECATED` 并保留历史；不得静默删除后复用。
- `wk -d` 对同一枚举执行语义 upsert；冲突返回 `ENUM_ARCHIVE_CONFLICT` 并阻断归档。
- 开发期：未修改枚举引用项目基线；本版本新增/修改枚举引用版本模块说明。归档后项目级链接统一指向 `docs/module/{MODULE_CODE}/{MODULE_CODE}_desc.md`。

## 代码实现

前后端复用项目已有枚举/字典体系，不使用裸数值表达业务语义。禁止 `setStatus(1)`、`if (status == 1)` 这类魔法值；应使用项目枚举对象或稳定枚举常量。

## Review 与工具

Reviewer 至少检查定义唯一性、API/DB/Page/Test 引用一致性、是否存在魔法值，以及持久化值兼容性。

```bash
python3 scripts/enum_validator.py file --file <MODULE_desc.md> --module <MODULE> --json
python3 scripts/enum_validator.py scan --doc-root project_doc --version <VERSION> --json
```
