# 测试完成与最终声明

## 1. 声明链
完成声明必须连接当前 revision、测试命令、构建或 Harness、Review、Acceptance Assertion 和开放风险。发生代码、配置、依赖或 Schema 变化后，相关旧证据失效。

## 2. 状态
只有范围内追踪、任务、Review、测试和 blocking Assertion 全部满足时才可报告 `DONE`。部分环境缺失、未执行必要回归或存在非阻塞未验证项时报告 `PARTIAL`；关键证据缺失或门禁失败时报告 `BLOCKED`。

## 3. 最终核对
CompletionVerificationAgent 使用当前 `work.md` 模式、Task Verification、Evidence、Review、Risk、Trace 和 StageOutcome 进行最终判断；完整 Gate 规则按需读取 `completion-gate-core` 的相关章节。
