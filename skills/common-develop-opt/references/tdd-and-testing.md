# TDD 与测试

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](core/machine-boundaries.md)。

## 详细规则

按任务模式决定是否执行 RED、表征基线、GREEN、REFACTOR 和测试执行。测试应选择稳定 seam，使用真实集成边界验证映射、事务、序列化和状态副作用。

按需读取 `tdd-core.md`、`red-green-refactor.md`、`testing-core.md`、`test-matrix-design.md`、`test-seam.md`、`test-environment.md` 和 `test-completion.md`。

根据 `test_case.md` 编写页面、接口或混合流程脚本时，读取 [测试矩阵与自动化交接](testing/test-matrix-design.md#4-页面关联与通用自动化交接)，沿用项目测试框架与环境声明；涉及弹窗查询、选择和回填时同时核对两侧页面关联约定。
