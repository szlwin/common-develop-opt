# 基于 Capability 的交付就绪性

## 生命周期位置
交付就绪性是 `completion_verification` 下的通用子门禁，不创建 `release`、`deployment` 或 `production` canonical phase。

## Capability 声明
项目在现有 `common-develop-capabilities` 块中声明任意 Capability。只有包含 `deliveryGate` 声明的 Capability 才参与门禁。Skill 不推断项目类型、技术、Provider 或部署模型。

## 适用性与 Evidence
未声明或不适用的门禁只产生适用性决策，不得伪造 `NOT_APPLICABLE` ReviewResult。适用的必需门禁在完成前必须有带 Evidence 引用的明确 PASSED 验证。
