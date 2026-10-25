# common-develop Agent 维护说明

本仓库维护研发协作 Skill。`skills/common-develop-opt/` 是重构优化后的 Skill 源，也是今后唯一维护、可安装和可发布的目标；`skills/common-develop/` 是重构前的旧版 Skill，仅作为历史参考保留，不再是默认修改目标。仓库根目录负责说明、安装和发布资料。

## 目录职责

```text
.
├── README.md                        # 英文仓库概览
├── README.zh-CN.md                  # 中文使用手册
├── AGENTS.md                        # 本文件：维护约束
├── INSTALL.md                       # 安装和更新
├── releases/                        # 发布说明
├── skills/
│   ├── common-develop-opt/          # 当前 Skill 源（重构优化版，今后以此为主）
│   │   ├── SKILL.md                 # 入口、触发描述和硬门禁
│   │   ├── agents/openai.yaml       # UI metadata
│   │   ├── ai/                      # AI context source map/index
│   │   ├── assets/                  # Contract、Schema、模板
│   │   ├── references/              # 按需规则
│   │   ├── scripts/                 # CLI 和运行时实现
│   │   ├── tests/                   # 运行包回归
│   │   ├── requirements.txt         # 运行包声明的 Python 依赖
│   │   ├── context-routing.json     # 路由事实
│   │   ├── CHANGELOG.md             # 包版本说明
│   │   └── FILE_MANIFEST.sha256     # 包完整性清单
│   └── common-develop/              # 旧版 Skill 源（重构前版本，仅历史参考）
```

## 修改规则

- 先读取 `skills/common-develop-opt/SKILL.md` 和根目录 `README.zh-CN.md` 的相关章节。
- 修改 Skill 行为时，编辑 `skills/common-develop-opt/` 内的唯一源；不要创建根目录运行代码副本。
- `skills/common-develop/` 是重构前的旧版源，默认不修改；只有用户明确要求兼容旧行为时才动它，且不得把旧包行为回写进新版 Contract。
- 修改脚本、Contract、Schema、路由或 Reference 后，检查所有引用和唯一事实来源。
- 任务事实归档在目标项目的文档根目录（`--doc-root` 指向的 `version/` 结构），不在本仓库；不要把任务历史写回通用 Contract。
- 具体参数以 argparse 生成的 `wk help` 或对应 CLI help 为准，不在文档中猜测未验证的参数。
- 保持渐进披露：入口规则放在 `SKILL.md`，长流程和细节放在按需 `references/`，机器约束放在 `assets/`。
- 不把测试、缓存、任务 Evidence 或个人绝对路径写入运行包 Contract。
- 不使用 `reset --hard`、`checkout .` 或 `clean -fd` 覆盖用户变更。

## 运行验证

从仓库根目录运行（`common-develop-opt` 声明了 `requirements.txt` 依赖，建议在虚拟环境中安装后执行）：

```bash
python3 skills/common-develop-opt/scripts/baseline_guard.py -g ProjectManagerAgent status --json
python3 skills/common-develop-opt/scripts/check_skill.py -g ProjectManagerAgent --json
python3 skills/common-develop-opt/scripts/semantic_audit.py -g ProjectManagerAgent --json
python3 skills/common-develop-opt/scripts/cli_help.py validate -g ProjectManagerAgent --json
python3 skills/common-develop-opt/scripts/quality_gate.py -g ProjectManagerAgent --profile full --clean-transient --write-manifest --json
```

涉及 Skill 源文件的修改还应运行：

```bash
python3 /Users/shazhoulin/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/common-develop-opt
```

## 发布和权限边界

- `common-develop-opt` 是唯一安装、触发、授权和发布单元；旧包 `common-develop` 不再发布新版本。
- 默认只产生本地变更和本地 checkpoint；不得隐式执行 `git push`、merge、Release 或部署。
- `-gp` 只有在用户明确要求且 `-gc` 已启用时才允许进入推送流程。
- 阶段结果、Evidence、Review 和文档必须绑定当前 revision。
- 旧包继承的 `CD-P0-01` 安全例外和 `CD-P2-14` 延期项在新包中继续有效，必须继续出现在发布说明和阶段记录中。
