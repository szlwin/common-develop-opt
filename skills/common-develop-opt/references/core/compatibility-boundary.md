# 兼容边界

本 Reference 是包资源别名、项目声明格式和退休布局的统一说明。项目业务数据迁移仍遵循对应项目设计，不属于这里的包布局兼容规则。

## 当前格式

- Reference 入口以 `references/reference-catalog.json` 为目录事实。
- Asset 入口以 `assets/asset-catalog.json` 为目录事实。
- 资源必须使用包内相对路径，并通过资源索引或稳定 Schema ID 解析。
- `logicalPath` 是目录声明的逻辑输入地址，不是物理文件；解析后读取 `target`。已声明别名仍受支持，不把缺少旧物理路径当作资源缺失。
- 依赖发现读取 `.artifact.json`、`.artifact.yaml`、`.artifact.yml` 显式声明。范围由 [发现 Profile](../../assets/discovery-profile.schema.json) 定义；[产物协议](../../assets/artifact-identity.schema.json) 定义身份及依赖字段。
- 权限和项目文档布局以 [Runtime Contract](../../assets/runtime/runtime-contract.json) 为准；参数通过实际 CLI `--help` 查询。

## 退休布局

新项目归档只接受 canonical 文档布局。旧版本级 `docs/`、拆分页面文档、项目级全量 API/DB 文档均被拒绝；不提供旧目录迁移预览、自动重命名或恢复入口。设计主题由当前版本文档明确声明稳定 `DESIGN-{MODULE}-{TOPIC}`，再由归档 preview/archive 生成当前快照。

依赖发现没有支持的声明时返回 `UNKNOWN`，diagnostics code 和 coverage 均为 `UNSUPPORTED_LAYOUT`。这只表示声明覆盖不足，不能据此认定原始文档已扫描完整，也不能声称存在可调用的旧布局 reader。其它资源与归档错误参见 [故障代码](../diagnostics/fault-codes.md)。

## 迁移与回滚

原包快照和迁移执行记录只读，保存在维护仓库包外 `opt/compat-retirement`；安装包不依赖该目录。包内 [迁移索引](../migration-map.json) 只是声明与审计导航，不能授权迁移写入。

包资源变更回退时，使用维护者保留的变更前副本恢复同批源文件，重新生成 resource index 和 Manifest 后验证。项目事实回退按 [归档恢复](../recovery-archive/archive-core.md) 核对原始输入及事务结果；不能只恢复 Manifest 来改变事实，也不能据本说明执行旧目录自动恢复。

## 资源退休记录

删除资源或撤销逻辑地址时，在所属 catalog 的 `retirements` 中记录 logicalPath、原 target、带时区的 retiredAt、replacement、reason 和 historyRefs。replacement 使用仍存在的同类资源物理地址；没有替代资源时显式写 null，并说明原因。记录结构只由 [Catalog Schema](../../assets/package/resource-catalog.schema.json) 定义。

historyRefs 必须是包内 JSON Pointer，指向含原身份的历史记录；该历史记录用 retirementRef 反向引用对应 catalog 退休条目。日期、原因和替代地址只在 catalog 保存，历史记录不再复制。`migration-map.json` 中的目标若已删除，也必须建立这条关联。

重新生成索引前会比较原索引和现有 Manifest；未登记删除、历史指针不匹配或退休记录被改写时拒绝生成。退休地址查询返回 `RESOURCE_RETIRED` 及替代目标，不自动改读替代资源。原索引和 Manifest 是变化比较依据，首次生成且没有旧基线时无法重建未知历史，不可用删除基线文件的方式代替退休登记。

退休记录只追加。退休后的版本回退使用经过验证的完整旧包副本，不在当前包中抹去退休记录并混用新旧索引；项目事实回退仍按归档规则处理。

## 升级链

读取协议统一见 [Reference 读取策略](../reference-catalog.json)：`ai_context` 返回一个主 Reference，升级目标和条件列在 `referenceReadingPlan.upgradeChain`；尚未请求的细节保留省略原因。`--entry` 只读物理入口，章节 alias 继续按原稳定 ID 读取；需要具体规则时通过 reader index/query 定位，最多展示三个升级目标。
