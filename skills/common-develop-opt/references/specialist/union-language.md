# DDD 统一语言

## 快速规则

项目共享业务术语的当前权威是 `DOC_ROOT/docs/union/union_language.md`；当前版本新增、修改和废弃只写入 `DOC_ROOT/version/{VERSION}/union/union_language_changes.md`，通过 `wk -d` 语义合并。正文中的裸 `[术语]` 表示统一语言引用，必须能在“项目基线 + 当前版本变更”中唯一解析。

扫描范围包括当前版本的 requirement/module 文档和 `DOC_ROOT/docs/**` 项目基线文档。基线中已经存在的悬空术语也会阻断归档，不能等到该文档再次发生版本变更时才处理。

## 边界

统一语言回答“这个业务词是什么意思”，模块枚举回答“一个有限取值集合有哪些值”。两者不得互相替代。Bounded Context 内部需要不同语义时，应使用明确限定的统一名称或显式 translation，不允许同一个裸术语在项目中同时对应两个定义。

每个术语至少包含稳定 `UL-*` ID、统一名称、英文/代码名称、领域、Owner、定义、业务边界、首次引入版本和状态。别名只用于解析历史叫法；如果别名产生歧义，归档必须阻断。

## 版本变更

`union_language_changes.md` 中每项变更必须声明 `ADD`、`MODIFY` 或 `DEPRECATE` 以及具体变更原因。开发期不得直接修改项目级 `union_language.md`；`wk -d` 在冲突检查通过后生成新的项目全量事实。

## 工具

```bash
python3 scripts/union_language.py validate --doc-root project_doc --version {VERSION} --scan --json
python3 scripts/union_language.py resolve --doc-root project_doc --version {VERSION} --term 登录 --json
```
