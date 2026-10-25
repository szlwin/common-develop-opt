# Domain / Bounded Context 语义治理

## 启用与范围
Domain governance 是可选且与项目无关的能力。存在 `docs/_domain/domain_context_map.yaml` 时启用严格校验；没有该文件的项目继续使用现有 Module/Feature 生命周期。

## 权威分工
- `domain_context_map.yaml` 负责 System/Domain/Bounded Context 边界、Context 关系和术语翻译声明。
- `business_model.yaml` 仍是 ubiquitous language、实体/值对象、聚合、不变量、状态和领域行为的权威来源。
- 语义索引和 Engineering Graph 是派生投影，绝不能覆盖权威产物。

## Context 语言
允许使用 Context 范围内的术语。跨 Context 语言必须使用显式翻译，不能假设整个系统存在一个统一模型。
