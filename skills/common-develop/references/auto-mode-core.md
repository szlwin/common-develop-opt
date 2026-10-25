# Automatic Mode Core

`wk -auto` 是受 Runtime Contract 约束的自治执行模式，不只是“门禁通过后自动点下一步”。它必须持续执行当前合法任务，并在可确定、可安全修复的问题上自动进入 remediation loop；它不删除授权、独立 Review、测试、Evidence、exact revision 或安全门禁。

## 1. 两级停止语义

`confirmationPolicy.stopWhen` 只冻结 **batch-forward**：CI/P0 failure、开放 P0/P1、failed/blocked Review、authority stale、safety block 等出现时，不得进入下一 DEV/阶段，但 `-auto` 本身继续运行 remediation。真正停止整个 auto batch 的条件只读取 `remediationStopWhen`：authority 冲突、业务/架构决策歧义、安全绕过才可修、未授权外部动作、或同一失败在无新证据/无进展时重复达到收敛阈值。

P1 的固定语义是：`batchForward=FROZEN`、`autoRemediation=ACTIVE`。修复必须留在当前 DEV；代码、配置或测试发生变化时形成新的 exact code revision，重新执行该 revision 的 P0，并重新执行受影响的 P1 Review；只有开放 P1=0 才恢复 forward。

## 2. 自动诊断与生产修改前检查

先运行 `python3 scripts/auto_remediation.py diagnose-log ...` 对日志做保守分类，再按固定顺序排除：authority stale → test oracle 错误 → fixture 不代表真实契约 → 配置语义错误 → 环境/CI 瞬态问题 → production implementation。只有 `preflight` 五项全部通过才允许把 production code 作为首要修复对象；禁止为过测试而放宽 Guard、fail-closed、权限、provenance 或其它安全边界。

CI/基础设施瞬态失败可在同一 exact revision 上 rerun；任何代码/配置/测试修复都必须形成新 exact revision。authority stale 只有在最新 authority 唯一且可确定性 reconcile 时自动修；safety block 只允许保持安全约束的根因修复。

## 3. 条件架构 Review

`-auto` 在 DEV 前先使用 `auto_remediation.py architecture` 判断是否存在真实架构边界变化。只有模块依赖方向、公共 API ownership、trust/transaction/persistence/security boundary、runtime composition owner、authority owner 或既有 architecture invariant 变化时才执行 `-ar`。fixture/test/config 修正、既有 wiring 修复、冻结 ownership 内实现、docs-only closure 和普通 CI remediation 不重复 Skeleton Gate。

## 4. 收敛与最终 HEAD 门禁

自动修复不按普通 revision 次数粗暴停止。只要旧失败被消除、失败推进到更深层或获得新的确定性证据，就继续 remediation；同一失败签名在无任何进展时重复达到机器阈值才停止为 `NON_CONVERGING_REMEDIATION`。

最后一个 Development task 关闭后，必须解析真实 PR HEAD 并执行一次最终 P0。DEV 自己的 P0 不能替代 final HEAD P0；如果 closure/doc commit 改变 HEAD，必须在新 HEAD 上重新验证。

机器事实：`assets/runtime-contract.json#/commandInterface/workModes/auto`；确定性决策入口：`scripts/auto_remediation.py`。
