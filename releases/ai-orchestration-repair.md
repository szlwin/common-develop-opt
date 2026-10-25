# AI orchestration repair — local unreleased changes

> 历史记录归属：本说明描述的是重构前的旧版包 `skills/common-develop/`（package version 1.0）。当前维护和可安装的 Skill 源已切换为 `skills/common-develop-opt/`；该重构包继承了本记录中的已知事项（CD-P0-01、CD-P2-14），后续发布说明请针对 `common-develop-opt` 编写。

The installable unit remains `skills/common-develop/`, package version 1.0. This change adds no lifecycle state machine and performs no push, merge, release or deployment.

`begin`, `progress`, `finish`, `next.actionPlan`, transactional `record-result`, structured risk depth and revision context reuse now delegate to existing runtime primitives. Existing step digests and independent Review boundaries remain authoritative. New optional selectors and observations are discoverable through CLI help. CLI JSON retains existing diagnostics while adding structured recovery; human output is concise.

Compatibility: apply-capable orchestration commands are conservatively MUTATING, previews stay read-only; result files must carry attempt/phase/revision metadata and match an exactly declared command. Use `execution_evidence.py run --attempt-id` for that binding. Candidate revision is required before validation; CLOSURE_ONLY is never reported as DONE. Existing domain errors keep their `error` object.

Known items retained: **CD-P0-01** security exception and **CD-P2-14** deferred item remain in force. This repair does not claim to close either item.

Validation and exact source digests are recorded in `project_doc/maintenance/ai-orchestration-repair/`. Real-user performance targets (30% tool-call reduction, 40% simple-task time reduction, 30% recovery-round reduction) require representative task sampling; fixture measurements are not a substitute for that acceptance study.

R02 follow-up: malformed nested risk observations are rejected before apply mutates state; malformed historical issue status remains conservatively open. Finish resolves its default validation directory from the task project, with an optional project-contained `--cwd`, and includes the resolved directory in its response and command Evidence. Follow-up validation and the outstanding real-task sampling protocol are recorded under `project_doc/maintenance/ai-orchestration-repair/r02/`.

R03 follow-up: progress and step share ProjectManagerAgent authorization; Owner plans identify both atomic actors and the phase coordinator. Generated commands bind to the installed package, and begin includes stored request conditions in cached routing. Startup documentation distinguishes creation, recovery, ordinary execution and maintenance diagnostics. Package tests and manifest writes apply only to Skill source maintenance; explicitly authorized push still requires both -gp and -gc plus existing gates. Verification is recorded under `project_doc/maintenance/ai-orchestration-repair/r03/`; real-task efficiency sampling remains pending, with CD-P0-01 and CD-P2-14 retained.
