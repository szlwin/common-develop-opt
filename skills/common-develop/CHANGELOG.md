# common-develop

## Unreleased

- Bind requirement trace synchronization to source content, register explicit feature-module impact, and validate declared Review references and regenerated Flow previews before publication/Review. Review forms now bind input content digests and reject changed snapshot sources even when revision labels are reused. Historical Evidence is preserved; CD-P0-01 and CD-P2-14 remain open as documented.

- Added the ProjectManagerAgent-only `evidence.py repair-history` command to
  safely rebind active historical DIRECT Evidence to an existing verified
  content-addressed object without changing its identity or digest.

- Scoped revision-filtered `evidence.py query` validation to the requested
  revision, so historical digest drift remains auditable without blocking
  current Review evidence discovery; added regression coverage.

- Added ProjectManager-owned, idempotent `acceptance ensure-review` registration for exact phase/revision/Reviewer/Profile bindings. Artifact publication now fails closed before opening an unusable Review, lifecycle recovery routes published assertion gaps back to ProjectManagerAgent, and `manual_review.prepare` reports actionable recovery instead of a bare unknown-ID error. Legacy `acceptance_assertions.json` remains explicitly non-canonical.

- Treat triggered required context as mandatory after mode exclusions, report missing resources at every hard context limit, and invalidate revision context caches when ai/source-map.json changes. Regression coverage includes deduplication, soft budgets, explicit modes and cache reuse. CD-P0-01 and CD-P2-14 remain open as documented.

- Unified AI startup for new tasks, recovery, execution and maintenance diagnostics. Phase progress now requires ProjectManagerAgent consistently with step; generated plans bind scripts to the installed package and route stored request conditions. Executable quick-start coverage includes preview, explicit classification, apply and begin. Package maintenance checks are scoped to Skill source changes; explicit push retains the -gp/-gc gate. CD-P0-01 and CD-P2-14 remain open as documented.

- Validate nested AI risk observations before any apply mutation; malformed historical risk fields cannot prove LOW or hide open critical issues. Finish now binds validation to the task project root, supports a project-contained `--cwd`, and reports the resolved execution directory.

- Repaired AI orchestration execution paths: Owner-scoped begin/resume emits one JSON object, next/begin/progress share executable atomic plans and step digests, and finish distinguishes canonical completion from closure readiness. Independent Review execution context is preserved.
- Result registration now validates current attempt/iteration/Owner/phase/revision and exact declared commands, verifies Evidence-backed Assertions, returns the existing lifecycle Gate, and rolls back all participating files under the shared writer lock. Evidence and Acceptance writers share that lock.
- Added structured conservative risk depth, revision-bound stable-context caching with actual reuse, and unified recovery fields. Current Gate, Evidence, Review and Acceptance conclusions are never reused from the context cache. Existing CD-P0-01 security exception and CD-P2-14 deferred item remain unchanged.

- Added a minimal first-use workflow example and aligned the release baseline with the implementation revision.

- Hardened empty-Trace archive scope isolation: archive validation now prefers formal `owner_module` bindings from task plans or exact changeset `featureCode` matches, and only uses `-`/`_` separator boundaries as an unstructured fallback. It no longer reads an undefined `task.json.module` field or treats digits as implicit boundaries. Added preview-and-archive coverage for the empty-Trace path and `CAT`/`CAT2` prefix collisions.

- Scoped standard Review/testing archive waivers to tasks whose Trace IDs intersect the current changeset `affectedTraceIds` (with module-target fallback when no Trace scope exists). Archive validation now derives effective waivers independently of submitted gate status, forces active waivers to remain `NOT_APPLICABLE`, and rejects StageOutcome references from outside the current archive scope. Added regression coverage for cross-module isolation, PASSED masquerading, and the full reopen/rerun/publish/finalize/advance/archive-PASSED recovery path.

- Fixed standard-mode waiver completion after retry exhaustion: an explicitly authorized `skip-phase` may transition a current `BLOCKED` Review/testing task to `NOT_APPLICABLE` when no running attempt or current outcome remains. Archive planning/validation now projects and verifies standard waiver gates, and traceability diagnostics describe both mode skips and user waivers.

- Completed standard-mode explicit Review/testing skips: `wk confirm --skip-phase` records an exact task/phase/iteration/revision/reason authorization and `long_task.py skip-phase --confirmation-ref` consumes it once under the shared writer lock. The durable waiver is validated against both ledger events and consumed by ordinary gates; `reopen-phase` clears it for the reopened phase and downstream phases. Skips remain `SKIPPED/NOT_APPLICABLE`, expose explicit-reopen status in the current Manifest, preserve the unverified risk, and never become pass evidence.

- Fixed empty current-revision handling so ordinary gates keep an explicit empty set (structure-only) instead of falling back to full historical Evidence validation. Reopen boundary confirmation now consumes a real, prior `AUTHORIZATION_BATCH_CONFIRMED` event bound to task, phases, source revision, and reason; Request Intake reclassification must receive that existing reference and cannot mint one internally. Mutating `wk confirm` and the full reclassification transaction now share the task writer lock; same-phase reclassification neither requires nor accepts a confirmation reference. Reclassification rollback now restores or removes its exact Intake history payload and removes a history directory only when the failed transaction created it and left it empty. Stale records validate confirmation/consumption source, ProjectManagerAgent ownership, exact atomic actions, strict consumption-to-stale adjacency, event ID/digest, and every cross-event binding field. Task Verify `current` fingerprints exclude only dedicated historical Evidence payloads under `task/evidence`; canonical files remain fingerprinted even when historical Evidence references them. `full` remains the explicit historical audit scope.

- Clarified same-version revisions for requirement, flow, business-model, design, development, and testing. Reopen scope is now discovered read-only from the changed phase toward upstream phases, with user confirmation before every scope expansion; after the earliest affected phase is fixed, one reopen propagates downstream and only actually reopened phases advance their non-reusable revision numbers. Ordinary lifecycle gates lazily validate current-iteration/current-revision Evidence while retaining historical metadata for explicit audit.

- Added audited `long_task.py revalidate-traceability` recovery for legacy synchronization that incorrectly leaves an already re-confirmed Trace in `STALE/GAP`. The command is ProjectManagerAgent-only, single-Trace and fail-closed; it requires same-revision ACTIVE Evidence, an independent PASSED Review and VERIFIED Acceptance, then atomically records the canonical Trace, `TRACEABILITY_REVALIDATED` event and Manifest digest. Ordinary validation detects later canonical tampering, and the recovery does not replace phase finalization or downstream rework.

- Git checkpoint now atomically refreshes the real index after a successful isolated commit when the clean index and `HEAD` remain unchanged. Concurrent index changes and lock conflicts preserve the caller index and return explicit synchronization diagnostics.
- Added `long_task.py amend-phase` for bounded, append-only current-phase requirement and design changes. It preserves unaffected passed tasks and Evidence across amendment chains, reworks only a dependency-closed affected set, and falls back to `reopen-phase` for unknown, broad, cross-phase, or high-risk impact.
- Hardened amendments: `--canonical-file` is appended atomically with the lifecycle event and an immutable Impact Plan snapshot; revisions are SHA-256-bound, Amendment history is validated on every task validation, Impact Plan task/trace/Acceptance scopes must match exactly, invalidated Evidence is rejected by current Gates, and `PROCESS_ONLY` is excluded from semantic amendments.
- Closed the remaining Amendment integrity gaps: affected tasks now require an exact reverse Trace/Acceptance closure in both command and history validation; canonical blocks are matched bidirectionally and exactly to their events; canonical ownership fails closed; and a task-scoped non-blocking writer lock prevents concurrent amendment overwrite.
- Hardened the Amendment audit and lifecycle writer boundary: every Markdown file in the current version `doc/` tree is scanned for rogue blocks; command/history/active Amendment records share strict semantic and Impact Plan snapshot validation; and all task-lifecycle event writers share the same non-blocking task lock.
- Closed remaining Amendment fail-open cases: `doc/` and `history/amendments/` reject roots and descendants that are symbolic links (including dangling links), external symlink loops become structured errors, bounded Impact Plans require an actually affected Review criterion or validation capability and receive strict safety-field and cross-field validation in both command and history paths, dynamic risk Review Profiles are resolved and checked against the current phase, protected trees are scanned once per history validation, and an active Amendment without history becomes a structured validation error instead of a crash.
- Closed impact-analysis consistency gaps: bounded plans reject unknown or phase-ineligible risk templates, Graph-triggered dynamic risk Review Profiles are materialized before affected criteria are derived, and `PROCESS_ONLY` remains free of semantic revalidation even when an Adapter or Graph is supplied.
- Aligned `wk impact` output with the bounded-plan validator: optional `derivedOutputPath` transport metadata is accepted, every required risk template must retain its complete affected Review Profile, and phase-ineligible Graph risks are emitted as structured `BLOCKED` results with a `reopen-phase` fallback.
- Prevented stale impact projections after analysis failure: `wk impact` now atomically supersedes the default derived cache with the current `BLOCKED` result and synchronizes the Manifest, including when a separate output path is requested, so `wk next` cannot continue consuming an older plan.
- Closed the remaining Impact Analysis recovery gaps: `wk next` now emits a `wk.impact` command that is consistent with the `BLOCKED` decision's allowed actions and execution metadata, while invalid provenance inputs are converted into a current structured `BLOCKED` projection so the default cache and Manifest supersede stale plans.
- Completed Impact cache invalidation coverage: normal `BLOCKED` results now supersede the default cache before writing a custom `--output`, and Impact enum/tri-state values are validated inside the cache-safe command path so argparse errors cannot leave an older successful projection available to `wk next`.
- Completed Impact required-input coverage: missing `--revision` and `--change-class` values now enter the same structured `BLOCKED` path and invalidate the task's default cache; `--task-dir` remains the only parser-level required locator because it is needed to identify the cache safely.
- Closed the remaining Impact input gap: explicit phases are now checked against the Runtime Contract before planning; an unknown phase produces a structured `BLOCKED` result and supersedes both default and custom derived projections without leaving a false `PASSED` plan.
- Closed the Impact output alias gap: equivalent custom output paths are now compared after path resolution, preventing a second write from invalidating the freshly synchronized default Manifest.
- Closed the Impact output path failure gap: output directory and symlink-loop resolution errors now produce structured `BLOCKED` results, invalidate the default cache, and never preserve an older successful projection.
- Completed Impact projection consistency: successful plans from a custom `--output` now also replace the implicit default cache; any later output-write failure converts that projection to `BLOCKED` and refreshes its Manifest.
- Documented that `--output` is an optional, non-authoritative transport path that may be outside the task directory; only the default cache is consumed implicitly, and callers own external-path permissions and cleanup.

## 1.0 STABLE

Initial release of the common-develop skill.

- Defines one canonical lifecycle, task runtime, Evidence registry, Review registry, and document model.
- Uses `task_events.jsonl` as runtime authority and `cache/current_manifest.json` as the only task recovery projection.
- Provides deterministic context routing, structured contracts, review gates, evidence validation, and sequential Git checkpoints.
- Adds `wk status --summary` for a single reducer-only lifecycle-position query and consistent `wk context --json` output.
- Requires Review dispatch to use an isolated Agent session (or an explicitly limited independent human fallback), rather than an Owner-context `-g` switch.
- Defines reopen as logical stale propagation only: canonical downstream documents such as `test_case.md` are preserved.
- Uses nested JSON-Pointer state patches after the initial event seed and retains only the current verification cache per profile to limit task-directory growth.
- Adds a per-file runtime read policy: task identity, traceability, acceptance, development task plan, Evidence, and Review JSON/JSONL are queried by stable ID or bounded filters; the derived manifest is read only through conditional selectors.
- Adds bounded `task_plan.py query` output for development-plan indexes and selected task fields, with a hard response-size limit.
- Adds a visible Reviewer dispatch/handshake protocol and forbids shell `sleep`/`echo true`/no-op polling for subagent coordination.
- Adds explicit preview/apply event-ledger rollover with deterministic gzip history segments and a hash-linked reducer snapshot for oversized `task_events.jsonl` files.
- Final release identity is anchored to the completed implementation baseline; `CD-P0-01` remains an accepted security exception and `CD-P2-14` remains deferred by scope.
