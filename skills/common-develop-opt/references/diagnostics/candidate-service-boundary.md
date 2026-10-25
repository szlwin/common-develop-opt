# Candidate Service Boundary

`scripts/service_cli.py seal` and `review` are local candidate tools. They produce
`DERIVED` manifests and observations; their success cannot advance a task phase,
register formal Evidence or replace an independent Review Registry decision.

Only the ProjectManagerAgent may invoke these write services, as declared in the
[Runtime Contract](../../assets/runtime/runtime-contract.json). Both application
services check the contract before writing. CLI parameters come from `--help`.

Formal development work continues through `wk`, `long_task.publish-artifact`,
`evidence.register` and the runtime Review submission commands. Those commands
retain their task context, stage, reviewer and freshness checks. A candidate
service result is an input for inspection, never permission to skip those checks.

The candidate Review operation rechecks current identity, revision, source,
dependencies and Evidence. Rejected or stale inputs stop the operation. Recovery
classifies corrupt manifests as requiring repair; it never treats file existence
alone as a committed result.
