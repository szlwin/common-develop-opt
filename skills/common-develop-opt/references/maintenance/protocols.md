# 机器协议与运行环境

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

运行需要 Python 3.9 或更高版本，以及 [requirements.txt](../../requirements.txt) 中的 jsonschema。需要配置依赖时使用 `python3 -m pip install -r <包目录>/requirements.txt`，遵循当前环境的依赖管理方式。具体测试过的 Python/OS 组合记录在维护验证报告；最低语法要求不代表所有平台已经验收。

工具版本及摘要算法标识由 [package.json](../../assets/package.json) 提供，Schema 版本与工具版本独立。历史报告保留生成时的版本和摘要，不随升级改写。

| 资产 | 实际消费者 | 机器约束 |
|---|---|---|
| [artifact-identity.schema.json](../../assets/artifact-identity.schema.json) | artifact_identity、diagnostic_io | 声明/观察结构、类型与状态枚举、依赖绑定字段 |
| [dependency-graph.schema.json](../../assets/dependency-graph.schema.json) | dependency_graph、discover_dependencies | 节点/边结构与传播边的 revision/digest |
| [diagnostic-result.schema.json](../../assets/diagnostic-result.schema.json) | result 与 finish_cli | 输出状态、版本及嵌套产物形状 |
| [discovery-profile.schema.json](../../assets/discovery-profile.schema.json) | discover_dependencies | 有界扫描配置、预期身份和数量限制 |
| [impact-adapter.schema.json](../../assets/impact-adapter.schema.json) | impact_adapter | 通用影响适配器交换的字段白名单 |

schema_protocol 用 JSON Schema 2020-12 校验并在本地 registry 解析包内引用，不加载网络 Schema。Schema 管结构，服务管文件真实性、摘要自洽、路径约束、唯一身份、引用与闭包语义；结构校验通过不能推导业务有效。

修改字段时必须同步机器 Schema、服务消费者和正反例；文档解释使用语义，不另建枚举或参数权威。诊断协议没有生命周期授权字段，未知不能提升为当前或允许推进。
