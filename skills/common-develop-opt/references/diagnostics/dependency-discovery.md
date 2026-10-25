# 有界依赖发现与现有影响接口

## 快速规则

先确认当前任务的输入、revision、允许范围和停止条件。机器字段和参数边界统一见 [机器字段与状态边界](../core/machine-boundaries.md)。

## 详细规则

## 输入范围

`discover_dependencies.py --root ROOT --profile PROFILE.json --output REPORT.json [--changed ID] [--graph GRAPH.json]`

Profile 的机器定义见 [discovery-profile.schema.json](../../assets/discovery-profile.schema.json)。它包含 schemaVersion=1、相对目录 roots、非空 requiredArtifactIds，以及可选 maxDeclarations。目录必须存在，不能越界；发现过程拒绝 symlink，并限制声明数量。

每个目录递归发现 `.artifact.json`、`.artifact.yaml`、`.artifact.yml` 文件。声明遵循 [产物协议](artifact-consistency.md)，并且必须显式提供 dependencies；没有依赖时提供空数组。输入源 paths 相对 root，依赖 reference 相对当前声明文件。

这是已知格式上的发现器，不扫描所有 Markdown 文本猜测依赖。Requirement/Model/Flow 文件可作为声明的 inputs；本工具不会改写它们或自动生成缺失的身份声明。未显式声明的关系不能直接当作完整依赖事实。

没有支持的声明文件时，按 [兼容边界](../core/compatibility-boundary.md) 处理覆盖不足与诊断；不能把 required ID 缺失误解为旧文档已被完整发现。

requiredArtifactIds 是本次检查预期存在的范围，用于识别整份声明丢失；不是生命周期状态表。额外发现的声明同样检查，不为匹配列表而丢弃。roots 外的历史文件不加载；related roots 中的损坏声明不能被当作无关草稿忽略。

## 校验结果

发现器逐项计算真实源摘要，解析可选依赖 reference，并从 dependencies 构造上游→下游图。reference 指向的文件必须属于目标产物 inputs，目标类型和 revision 必须匹配。供比对的 GRAPH 若漏节点、漏边、增加边或绑定不同，返回 GRAPH_DECLARATION_MISMATCH。

缺声明、缺目标、重复 ID、不支持格式和范围不完整返回 UNKNOWN；引用冲突或明确图不一致返回 FAILED；当前源变化导致旧依赖失效时返回 STALE。输入声明本身不能证明业务没有漏写依赖，因此 SCOPED_DECLARATIONS 只表示支持格式和配置范围内的覆盖。

报告包含 graph、identities、diagnostics、analysis、coverage 和读取指标。失效解释沿用 [产物协议](artifact-consistency.md)；报告不决定生命周期。

## 读取一致性和复用

每次发现调用固定已读取文件的 inode、大小、mtime、ctime；同一次调用再次读取或结束复核时发生变化，返回 UNKNOWN。声明集合在检查期间增删也返回 UNKNOWN。该检查缩小读取期间混用新旧内容的风险，不是跨文件锁或发布事务。

ReadSession 可在调用间复用未变化的字节缓存；新调用重新记录观测签名，已变化文件重读。Schema、引用和图检查每次执行，不缓存“通过”结论。metrics 记录实际读取文件数、字节数和缓存命中。

## 现有影响接口适配

`impact_adapter.py --root ROOT --profile PROFILE.json --changed ID --base-revision REV --revision REV --adapter-output ADAPTER.json --output REPORT.json`

该命令先执行新鲜的发现检查，再导出 [通用 impact adapter 协议](../../assets/impact-adapter.schema.json)；`wk impact --adapter-result` 和 `wk env --check --impact-adapter-result` 可读取 ADAPTER.json。它输出受影响 artifact ID，置信度为 MEDIUM；不猜测 Review criterion/profile、任务/Trace/Acceptance 映射或权限。

REPORT.json 保留诊断；ADAPTER.json 只含正式协议允许字段，不能把整个发现报告作为适配结果传入。检查失败不覆盖已有 adapter；输出路径先预检。两份报告不构成跨文件发布事务，IO 故障后需核对实际输出，不据单个文件授权推进。

适配文件不是自动执行影响计划或生命周期命令的授权。本包只导出文件，不调用写路径；报告中的当前性不能代替原入口的 revision、权限和正式门禁。
