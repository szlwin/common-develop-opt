# 源码盘点与命令探针

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

仅在用户要求维护、诊断或评估工具源码时读取。普通研发产物校验不需要执行这些脚本。

## 可用入口

| 用途 | 现有脚本 | 输入与输出 |
|---|---|---|
| 源码与环境快照 | `scripts/maintenance/source_baseline.py` | --root 指向包含 SKILL.md 的源码目录；--output 指定报告 |
| AST 与静态引用盘点 | `scripts/maintenance/source_inventory.py` | --root 指向待分析的工具源；--output 指定报告 |
| CLI 帮助探针 | `scripts/maintenance/cli_probe.py` | --root 指向工具源；通过 --script 显式选择脚本，--output 指定报告 |

执行前读取相应 --help；使用完整脚本路径。报告写在输入目录和安装包外，避免覆盖已有文件。这些维护入口使用统一报告路径保护，拒绝覆盖源根、包根和声明的输入。

CLI 探针会实际启动指定脚本的 --help。临时工作目录不是安全沙箱；只对已核对的脚本运行，不因参数名是 help 就断言无副作用。

## 结果解释

快照描述观测时刻的文件摘要、Git 和环境信息，不是安装包重建证明。静态盘点的 command_*、import、write 调用和文本命中只证明源码位置存在；不能证明完整 CLI 注册、动态依赖、包装写入、锁和事务覆盖。

帮助探针成功只表明该入口返回成功，不证明项目行为、性能或阶段门禁有效。单项 PASSED 是局部观测结果；聚合维护报告不能授权发布、关闭问题或推进研发阶段。

## 验证维护变更

### v1 入口清单

当前入口决策以 [能力契约](../../assets/development/capability-pack-contract.json#/commandSurface) 为准。
`python3 scripts/cli_help.py list --json` 返回 CLI 与完整决策视图；
`python3 scripts/cli_help.py list --inventory --json` 导出可保存到包外的正式清单。
命令路径相对包目录，其他目录应使用脚本绝对路径。

`commandSurface.digest` 对 `{version, counts, entries}` 的规范 JSON 求 SHA-256：
条目按 script ID 排序，对象键排序，UTF-8 编码，紧凑分隔符且不转义非 ASCII 字符。
摘要不包含自身、来源路径或审计说明；修改入口声明后，应按同一导出结果更新契约摘要和包外清单。
INTERNAL/MERGE 是入口决策，不承诺对应物理脚本仍存在；退休过程另存包外审计记录。

仓库维护时给 `check_skill.py` 或 `quality_gate.py` 显式传入
`--command-surface-inventory <包外清单路径>`，校验清单内容、计数和摘要。
未传入时只检查包内权威契约及其摘要，不验证包外清单，也不依赖仓库历史。
修改完成后重建 resource index 和 Manifest，再执行 full 门禁。

### 运行验证

从包目录执行 `python3 -B -m unittest discover -s tests -v`。文件发现性能使用 `tests/benchmark_history.py --output <包外报告路径>`。原接口基线使用 `tests/run_legacy_baseline.py --legacy-source <原包源码> --output <包外报告路径>`，在临时副本运行真实解析、图发现和 Flow 预览校验，已知差异单独报告。上述结果不替代项目生命周期验收。

维护结果聚合使用 `scripts/maintenance/inspection_report.py`；只接受已知局部状态并输出 OBSERVATION_ONLY，不产生下一阶段授权。摘要教学示例不计入业务回归。
