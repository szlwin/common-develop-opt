# Test Data Isolation

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

## 1. Isolation rule

测试不得依赖执行顺序或共享可变数据。测试数据按 Requirement 隔离；需要 fixture 或辅助脚本时放在：

```text
DOC_ROOT/version/{VERSION}/test/{REQUIREMENT_ID}/
├── data/
│   ├── init.sql
│   └── cleanup.sql
└── scripts/
```

每个 Case 使用可识别、可定向清理的数据前缀，例如 `REQ_BOOKING_001_CASE_CREATE_001`。需求目录下的 `test_case.md` 必须链接实际初始化/清理脚本；脚本也必须在注释或清单中反向记录适用 `CASE-*`。

初始化应先清理同编号旧数据，再按依赖顺序创建；重复执行后的状态必须一致。清理只能删除当前功能测试数据，禁止无条件清空业务表或删除非测试数据。

## 2. Transaction and cleanup

普通增删改优先事务自动回滚；必须验证 commit 后状态时显式清理。查询测试在执行前初始化、完成后清理。事务、并发、幂等测试使用独立数据编号，不与其它测试共享可变数据。

## 3. Safety

自动化测试不得连接生产数据或未经验证的共享业务库。任何真实测试存储仍必须满足 `test-persistence` / `test-environment` 的 READY 规则。
