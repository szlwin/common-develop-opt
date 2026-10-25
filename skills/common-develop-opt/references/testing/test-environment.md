# 测试环境与 Harness 入口

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

## 1. 事实来源
测试命令、服务启动、数据库、账号、报告位置和工具版本只从项目 `project_env.md`、`harness_env.md`、构建配置和 CI 脚本读取，不由 common-develop 猜测。

## 2. 独立测试库
只有 `TEST_DB_URL`、`TEST_DB_USER`、`TEST_DB_PASSWORD` 全部存在时，数据访问测试环境才为 READY。缺失时该子范围记录 `SKIPPED` 和风险，不得回退开发库或生产库，也不得把跳过表述为通过。

## 3. 执行记录
命令输出和报告通过 Evidence Registry 登记；Work Record 只保存执行时机、类型、结果摘要、Evidence ID、跳过原因和下一动作，不复制完整日志。


## 4. Capability Projection

项目需要让 AI 稳定读取执行能力时，继续使用现有 `project_env.md` 或 `harness_env.md`，可在其中加入唯一的结构化块；它不是新的事实文件：

```yaml common-develop-capabilities
capabilities:
  build:
    available: true
    command: ./project-build-command
  integrationValidation:
    available: false
    reason: environment not configured
```

Capability 名称由项目定义，common-develop 不内建 Maven、JUnit、pytest、npm、MySQL 等技术名。相同 capability 在两个环境文档中若声明冲突，`project_environment.py status` / `wk env` 必须 fail-closed。没有结构化块时只报告“存在人类环境文档但未声明机器 capability”，不得猜命令。

## 5. 环境与 Adapter 健康检查

`wk env --check` 只做通用结构检查：capability 声明冲突、`available` 类型、已声明 `command / adapterCommand` 是否为非空字符串；不解析具体语言、构建、测试或数据库工具。可通过 `--impact-adapter-result {FILE}` 同时验证 Impact Adapter 的标准 JSON Schema 与 confidence 字段。

健康检查不执行 Adapter，也不把环境文档变成新的生命周期事实源。项目需要更深的模块/依赖/测试发现时，应由项目本地 Adapter 执行，再把结果转换为 `assets/runtime/incremental-validity-contract.json` 定义的通用字段。
