# Persistence Testing

## 1. READY / SKIPPED authority

持久化测试先执行快速隔离测试，再运行 `python3 scripts/independent_test_environment.py status -g TestAgent --json`。只有 `TEST_DB_URL`、`TEST_DB_USER`、`TEST_DB_PASSWORD` 均存在并能证明目标是专用测试库时状态才可为 `READY`；缺失或无法证明身份时记录 `SKIPPED` 与未验证风险，绝不回退 local/dev/生产库，也不得把 `SKIPPED` 表述为通过。

连接信息、Profile、建表/清理方式和执行命令只记录在项目 `project_env.md` / `harness_env.md`，密码仅通过环境变量或本机私有配置提供；状态检查只输出变量名/缺失组，不输出凭据值。

## 2. Required real-store coverage

当数据访问、动态查询、分页/排序、字段/实体映射、枚举/时间/空值转换、唯一/必填/关系约束、事务提交/回滚、幂等/并发冲突、逻辑删除/状态标注/冗余联动在范围内且环境 `READY` 时，必须增加真实测试存储集成测试。不能只验证 Repository/Adapter 调用次数或内存对象。

门禁顺序：快速隔离测试 → 数据访问集成测试 → 业务事务集成测试 → 项目质量 Harness → Review；通过 Review 后才执行需要的功能数据初始化及接口/E2E。集成测试同时断言返回结果和持久化最终状态，异常还要断言无意外写入和事务回滚。

## 3. Reviewer checks

`READY` 时 Reviewer 必须确认测试确实运行于项目声明的独立测试存储，最终数据状态和回滚符合契约，初始化/清理可重复执行。`SKIPPED` 时必须存在缺失组 Evidence、没有真实读写/初始化/依赖该库的 E2E，并登记“持久化行为未验证”及后续补测或风险处理。

固定测试库可用，但必须与 local/dev/生产物理或逻辑隔离，账号仅具测试库权限。无法证明隔离时立即停止。
