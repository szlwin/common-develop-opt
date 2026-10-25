# Task 验证与最小 AI 上下文

## 快速规则

- 适用：本规范定义 `task/{TARGET_ID}` 的确定性验证、当前 manifest 生成和 Token 预算。机器事实归属于 canonical 文件；`cache/current_manifest.json` 是 Git 忽略、可删除重建的派生数据。
- 通用输入、状态、权限和停止条件见 [机器字段与状态边界](../core/machine-boundaries.md)；这里仅选择本主题所需的具体规则。

## 详细规则

完整规则见 [task-context-optimization.detail.md](task-context-optimization.detail.md)。需要执行具体流程时按章节读取，不要全文加载。
