# common-develop-opt

`common-develop-opt` is a general-purpose Agent Skill for fact-based software development and multi-agent collaboration. It coordinates intake, requirements, design, implementation, testing, independent review, evidence, long-task recovery, documentation, and completion gates. It is the refactored and optimized successor to `common-develop`, and is the package maintained and installed going forward; `skills/common-develop/` is the previous pre-refactor package, kept for reference only.

The repository follows a two-layer layout inspired by `onepage-skill`:

```text
common-develop-opt/
├── README.md                       # English repository overview
├── README.zh-CN.md                 # Chinese user guide
├── AGENTS.md                       # Maintainer constraints
├── INSTALL.md                      # Install and update guide
├── releases/                       # Release notes
└── skills/
    ├── common-develop-opt/         # the current installable Skill source
    │   ├── SKILL.md                # Agent entrypoint and hard gates
    │   ├── agents/                 # UI metadata
    │   ├── ai/                     # Context source map and index
    │   ├── assets/                 # Contracts, schemas, and templates
    │   ├── references/             # Progressive detailed guidance
    │   ├── scripts/                # CLI and runtime implementation
    │   ├── tests/                  # Package regression tests
    │   ├── requirements.txt        # Declared Python dependencies
    │   ├── context-routing.json    # Context routing facts
    │   ├── CHANGELOG.md            # Package change history
    │   └── FILE_MANIFEST.sha256    # Package integrity manifest
    └── common-develop/             # previous pre-refactor package, reference only
```

Install the package directory, not the repository root.

### Global (Codex)

```bash
mkdir -p ~/.codex/skills
rm -rf ~/.codex/skills/common-develop-opt
cp -R skills/common-develop-opt ~/.codex/skills/common-develop-opt
```

### Project-level

```bash
mkdir -p .agents/skills
rm -rf .agents/skills/common-develop-opt
cp -R skills/common-develop-opt .agents/skills/common-develop-opt
```

After installation, invoke the Skill as `$common-develop-opt`. The package declares its Python dependencies in `requirements.txt` (currently `jsonschema`), preferably installed into a dedicated virtual environment; see [INSTALL.md](INSTALL.md) for update, path selection, and troubleshooting details.

The package entrypoint is `skills/common-develop-opt/SKILL.md`. Runtime contracts live in `assets/`, progressive guidance lives in `references/`, deterministic CLIs live in `scripts/`, and regression tests live in `tests/`.

Use this Skill for feature work, bug fixes, requirement and design workflows, TDD and verification, independent reviews, evidence-backed completion, cross-session task recovery, and structured documentation. It is not a general text summarizer and it does not perform Git merge, Release, deployment, or implicit push operations.

## How it differs from other development skills

Most popular development skills are playbook-style: a `SKILL.md` describes the process in natural language and relies on the model to follow it. [mattpocock/skills](https://github.com/mattpocock/skills), for example, ships small composable skills (grilling for alignment, `tdd`, `code-review`, `handoff`) with state kept in GLOSSARY/ADR/spec documents and tracker tickets; Superpowers, BMAD, and Spec-Kit drive process the same way. That style is light, model-agnostic, and well suited to an individual engineer's daily work.

`common-develop-opt` takes the opposite trade: process constraints are executable machine contracts and CLI gates, not prose the model may skip.

| Dimension | Playbook skills (e.g. mattpocock/skills) | common-develop-opt |
|---|---|---|
| Enforcement | natural-language playbooks the model may skip | Runtime Contract + schemas + deterministic CLI checks, fail-closed |
| Task state | external docs/tickets, human-maintained | append-only `task_events.jsonl` ledger rebuilt by a reducer |
| Roles | single agent or parallel subagents, no identity permissions | ProjectManager/Owner/TestDesign/Reviewer roles; permissions checked per `-g` identity |
| Review | playbook-suggested dual-channel checks | enforced Review Contract: independent Reviewer context; open high-severity issues block progress |
| Evidence | before/after notes in the PR body | Evidence/Assertion/Traceability bound to the revision, machine-queryable |
| Context | read the full spec/glossary | context routing returns the minimal resource set per phase; agents never open runtime JSON/JSONL directly |
| Git | commit per playbook | local checkpoints by default; push requires explicit `-gp` + `-gc` plus gates |
| Long tasks | handoff documents | `wk begin/status/context` cross-session recovery; bounded reopen/amend propagation |

### Where it wins: large-scale team development

- **Auditability**: every stage artifact, review, and evidence item is bound to a revision and traceable to the change that produced it — acceptance and accountability in multi-person work rest on records, not memory.
- **Consistent gates**: completion is decided by deterministic scripts (acceptance, traceability, archive gates), so every team member and every session gets the same gate behavior regardless of model or prompt.
- **Controlled cross-module impact**: impact analysis, dependency closures, and changesets (API/DB/dependency-impact) go through `merge_docs.py` with schema validation and atomic writes, so concurrent edits to version documents cannot silently overwrite each other.
- **Multi-agent division of labor**: Owner/Reviewer separation, task planning (`-tp`), and two-stage architecture review (`-ar`) directly serve decomposing and dispatching large requirements.
- **Context budget**: large repositories route a minimal resource set per task type and phase instead of loading full specs, containing context cost for long, cross-session tasks.
- **Long-horizon recovery**: the event ledger plus recovery commands preserves state across days and sessions, with bounded retry from the current attempt.

### Trade-offs

Playbook skills stay light and keep the engineer in control — mattpocock/skills explicitly argues against heavyweight process — which makes them cheaper for solo, small projects. The cost of `common-develop-opt` is initializing the `project_doc` fact structure and learning the `wk` surface; it pays off in multi-role, multi-module, evidence-and-audit-heavy projects with long cycles. The two are not exclusive: conversational pair programming is available via `-ws`, and strictness comes from mode and gate combinations rather than more prose process.

Read the [Chinese user guide](README.zh-CN.md) for the full capability map, workflow, mechanism, command parameters, output states, and operational boundaries. Maintainers should read [AGENTS.md](AGENTS.md); installation and updates are documented in [INSTALL.md](INSTALL.md).
