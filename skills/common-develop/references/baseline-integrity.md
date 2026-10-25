# Skill 基线完整性

本规则只用于维护或优化 common-develop Skill 包，不替代项目任务的 Git Checkpoint。

## 1. 只读检查

```bash
python3 scripts/baseline_guard.py -g ProjectManagerAgent status --json
```

默认从 `assets/release-contract.json#baselineTag` 读取发布基线，并比较 HEAD、工作树及关键文件摘要。命令不得修改、恢复、删除或清理任何文件。

## 2. 偏差处置

- `CLEAN`：可以开始下一轮 Skill 优化；
- `RELEASE_ENVELOPE_CLEAN`：当前 HEAD 是实现基线之后唯一的发布封装提交，工作树干净且只有发布元数据文件变化，可以进入发布后的维护周期；
- `DIRTY`：先把偏差文件复制到 Skill 目录外，检查 diff，再只恢复确认属于非预期覆盖的文件；
- `DIVERGED`：先核对基线标签之后的提交，不得把已提交变更当成挂载覆盖；
- `INVALID_BASELINE`：先修复或创建发布标签，不得继续发布。

禁止使用 `git reset --hard`、`git checkout .` 或 `git clean -fd` 代替定向核对。恢复时必须保留偏差副本、记录摘要，并只处理已经确认的文件。

## 3. 关键文件

关键文件清单只由 `assets/release-contract.json#criticalBaselineFiles` 维护。版本、RC 阶段和基线标签也只读取 Release Contract，不在 `SKILL.md` 或本 Reference 复制当前值。

发布收口可使用 `baselineMode=IMPLEMENTATION_COMMIT_WITH_RELEASE_ENVELOPE`：`baselineTag` 指向已完成实现的提交，当前 HEAD 只能是其单个发布封装提交，且变化仅限 `releaseEnvelopeFiles`。这是为避免发布契约自引用提交哈希；普通提交漂移仍然返回 `DIVERGED`。
