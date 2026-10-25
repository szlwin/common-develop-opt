# 产物一致性诊断

## 快速规则

这是维护和校验工具，不是普通编码入口。用于确认 Requirement、Model、Flow、Impact、Design、Test、Review 和 Evidence 的身份、引用、依赖和当前 revision 是否一致。

机器字段、必填项、状态、摘要算法和类型枚举只读取：

- `assets/artifact-identity.schema.json`
- `assets/documents/artifact-relation.schema.json`
- `scripts/artifact_identity.py --help`
- `scripts/reference_resolver.py --help`
- `scripts/dependency_graph.py --help`

## 使用边界

诊断报告是派生观察，不推进任务状态、不关闭 Review、不发布候选、不创建 Git checkpoint。缺少声明、身份或当前输入时返回 `UNKNOWN`、`PARTIAL` 或 `BLOCKED`，不能推断为当前有效。

## 诊断入口

- `artifact_identity.py`：检查声明与实际源文件的身份绑定；
- `reference_resolver.py`：检查 JSON Pointer、Markdown 锚点、稳定 ID 和引用范围；
- `dependency_graph.py`：检查图结构、边绑定和当前身份漂移；

具体参数以脚本 `--help` 为准。报告必须写到包目录和输入源之外。

## 结果解释

相同 ID 的内容、依赖或绑定发生变化时，需要生成新 revision 并重新验证。图或摘要只证明声明范围内的观察结果，不证明项目没有遗漏业务关系。正式封存、Review、生命周期推进和历史迁移不由这些诊断脚本执行。

## 相关诊断

- [依赖发现](dependency-discovery.md)
- [故障代码](fault-codes.md)
- [机器字段边界](../core/machine-boundaries.md)
