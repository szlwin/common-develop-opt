# Package Changes

## Cross-platform writer locks

- Replaced direct `fcntl` imports with one shared lock adapter: POSIX uses
  `fcntl.flock`, while Windows uses `msvcrt.locking` without adding a
  dependency.
- Document writers, lifecycle amendments and Git checkpoints now share the
  same non-blocking lock behavior; legacy duplicate lifecycle locking was
  removed.
- Added contention, release/reacquire and task-lock re-entry regressions, plus
  PowerShell setup instructions.

## Document semantics and readability hardening

- Enum validation now rejects bare `MODULE.ENUM` and `MODULE.ENUM.SYMBOL` uses in version Markdown unless the same line links to the Owner module description anchor. Definition tables remain the sole exception.
- Unified-language validation scans both the current version documents and the project `docs/**` baseline before archive.
- Document archive previews validate generated Markdown for duplicate headings and prohibited boilerplate before any write.
- Added regressions for missing enum links, stale baseline terms, and generated boilerplate.

## Page query, selection and return contracts

- Page design now describes caller/target relations, selection context, return
  field mapping and cancellation, including reusable cross-module pickers and
  same-page components. Reverse entries preserve the target's Owner.
- Portal and archive validation check relation identities across caller pages,
  real page/action definitions, pending documents and reciprocal links. Existing
  pages without relation tables remain readable.
- Case readiness checks bind declared relations to both pages/actions and
  concrete return assertions. Framework-neutral automation handoff distinguishes
  planned scripts, implemented tests and actual execution Evidence; no framework
  generator or runner is introduced.

## Profile-scoped manual Review context freshness

- Prepared manual Review forms now bind Evidence identity, content, validity,
  and only the current Profile's criterion scopes.
- Another independent Reviewer appending Evidence usage relationships no
  longer expires an otherwise unchanged form; artifact revision, source
  digest, current-Profile scope, and Evidence validity changes still do.

## Running attempt reopen recovery

- Reopening a phase now records cancelled attempts with the current v5 work
  record renderer, including phase and distinct target/attempt identities.
- Cancellation history from an older iteration no longer overwrites the new
  iteration's task runtime; tasks can start again after reopening or cache loss.
- Work records, including initially absent files, are prepared without writes
  and committed with lifecycle events and Acceptance updates in one transaction.
  Failed writes restore existing files and remove newly created records.

## Single wk parser

- Both wk entry points now build all 19 commands through document_core.wk_dispatcher.
  runtime/_core/wk_cli only binds callbacks and preserves the historical func
  attribute and parser description; it no longer registers arguments.
- Preserved mode conflicts, defaults, aliases and programmatic output behavior.
  Restored the runtime facade's explicit imports for its CLI error boundary.

## Five root helper relocations

- Merged service_permissions.authorize into application_services, preserving
  Runtime Contract authorization before candidate writes.
- Moved evidence_validity into document_core; consumers now import its
  canonical module.
- Moved generate_resource_index into checks, including manifest integration,
  standalone import bootstrap and the default package root.
- Moved state_safety into tests/internal with its regression coverage intact;
  the maintenance import probe now inspects the runtime modules instead.
- Removed the five old root script paths. The 39 declared v1 CLIs are unchanged;
  retired candidate IDs remain classified by the capability contract.

## First-batch command surface completion

- The capability contract owns the current 72 v1 decisions: 39 KEEP, 25 INTERNAL
  and 8 MERGE. The original 73-candidate snapshot included legacy_layout, which
  has since been retired. CLI discovery returns the current view and
  rejects conflicting or incomplete classifications. Workflow and documented
  CLIs remain public; document_facts remains an internal module interface.
- Restored argparse mutual exclusion for mode and task synchronization switches;
  repeated synonymous -wc/--no-ws flags remain valid.
- Help validation no longer injects PYTHONPATH; the relocated artifact-footprint
  CLI now bootstraps its own imports. Capability queries use wk packs.

## Stage A: Command surface source consistency

- CLI discovery and inventory export share a deterministic decision digest with
  the capability contract. Equal counts alone cannot hide changed IDs, paths,
  decisions or source pointers.
- check_skill accepts an explicit external inventory for repository audits;
  installed package checks do not require repository history or an opt directory.
- Source-map and Reference catalog point to the same capability authority.
  Runtime Contract keeps its canonical command-surface pointer. The 39 declared
  CLIs and their authorization boundaries are unchanged.

## 0.3.0 DEVELOPMENT

Unreleased development package. The Runtime Contract retains compatibility
version 1.0; this does not identify a published version of this package.
Upstream release metadata is preserved under `upstreamProvenance` in the
[Release Contract](assets/package/release-contract.json).

- Classified References and Assets retain the original development workflows.
- The full checker follows classified sources and preserved CLI implementations.
- Candidate services write derived observations only. Formal phase progression,
  Evidence registration and independent Review remain owned by the task runtime.
- Development integrity uses the package manifest. A formal release requires a
  prepared release contract, release materials and a verified clean Git baseline.
- Current documents, contracts and command outputs use classified resource paths.
  Catalog logical addresses remain compatible through the resolver; former flat
  filesystem aliases and their symbolic links have been removed.
- Catalog lookup uses an immutable process cache and a reproducible derived
  resource index. Full package checks validate entity digests independently.
- The complete package can be staged by directory copy or ZIP extraction without
  an adjacent original Skill. Setup and validation commands are documented in
  [the package guide](README.md); installation replacement remains a separate action.

## Breaking simplification: compat layer removed (B4-1 .. B4-8)

This package was never installed or used in production, so compatibility with
the historical flat layout was intentionally dropped instead of preserved.

- The whole `scripts/compat/` layer is gone. Its live implementations were
  renamed into canonical homes: the task state machine now lives in
  `scripts/runtime/` (`_core`, `_long_task`, `_wk`, `_evidence` and the
  lifecycle modules, moved verbatim), deterministic parsers and document
  capabilities in `scripts/document_core/`, and the package checker plus the
  five maintenance gates in `scripts/checks/`.
- `wk` is dispatched through the canonical parser/handler registry
  (`document_core.wk_dispatcher` + `wk_handlers.build_production_registry`);
  all 19 subcommands resolve before dispatch.
- `tests/compatibility/` (82 files, 1028 cases asserting legacy behavior) was
  removed with it. Behavior coverage for the canonical entry is carried by the
  curated scenario matrix in `tests/test_wk_functional.py` (command x
  normal/reject/idempotent/recovery) plus the current suite; the package gate
  now runs 223 current cases.
- The public CLI surface is unchanged: every historical script path keeps
  working as a facade over the new homes. The v1 surface inventory, retirement
  plan, audits, task log and the pre-deletion backup are archived outside the
  installable package at repository level (`opt/compat-retirement/`).
- Rollback point for the deletion: git tag `pre-compat-deletion-20260912`.

## Third-batch package convergence

## T19: Remove legacy parser aliases and provenance audit (2026-09-13)

- Removed the `_legacy` import-only package and switched remaining consumers to
  the canonical `document_core` implementations.
- Removed the obsolete reuse-provenance checker, test, and record; deleted the
  repository README fixtures that had no package consumers.
- Maintenance guidance now documents the canonical parser ownership without a
  compatibility-alias section.

## Cleanup of unused helper facades

- Removed the unused top-level helper scripts `action_plan.py`, `diagnostics.py`,
  `usage_evaluation.py`, and `pure_readonly.py`, together with their
  helper-only tests and stale fault-code references. Runtime implementations
  remain under the canonical `runtime/` and `document_core/` modules.

## Migration tooling archival

- Archived the migration-only preview implementation, tests, and project
  adaptation note under repository `opt/`; removed the installable
  `scripts/migration_preview.py` facade and its package references.

- Test-only and migration-only helpers (C06/C07 services, CLI/dual-run
  comparators and lifecycle shadow tools) now live under repository `opt/` and
  are excluded from the installable runtime surface.
- The unused quality-gate worker script builder was removed.
- Unconsumed lifecycle state-machine experiment assets are archived under
  `opt/compat-retirement/`; runtime contracts remain the only lifecycle
  authority.

Inherited limitations remain tracked as `CD-P0-01` (safety exception) and
`CD-P2-14` (deferred item); this package does not claim to close either item.
