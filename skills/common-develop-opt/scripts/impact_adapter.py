"""Export scoped observations through the generic impact adapter protocol."""
from pathlib import Path
import argparse
from diagnostic_io import finish_cli, load_json, result, write_json
from discover_dependencies import discover
from schema_protocol import validate


def build_adapter(observation, changed, base_revision, revision):
    if any(not isinstance(x, str) or not x.strip() for x in (changed, base_revision, revision)):
        raise ValueError('ADAPTER_REVISION_AND_CHANGED_REQUIRED')
    # Read a fresh discover() result in the CLI; standalone callers must not use
    # unverified historical reports as current observations.
    ready = (observation.get('status') == 'PASSED' and observation.get('coverage') == 'SCOPED_DECLARATIONS'
             and observation.get('analysis', {}).get('drift', {}).get('status') == 'CURRENT')
    nodes = {x['artifactId'] for x in observation.get('graph', {}).get('nodes', [])}
    if changed not in nodes:
        ready = False
    impacted = sorted({changed} | {x['artifactId'] for x in observation.get('analysis', {}).get('impact', [])}) if ready else []
    adapter = {'schemaVersion':1, 'baseRevision':base_revision, 'revision':revision,
               'confidence':'MEDIUM' if ready else 'UNKNOWN',
               'affectedScopes':[], 'affectedArtifacts':impacted,
               'affectedReviewCriteria':[], 'affectedProfiles':[], 'affectedTaskIds':[],
               'affectedTraceIds':[], 'affectedAcceptanceIds':[], 'requiredCapabilities':[],
               'notes':['Scoped declaration closure only; no Review criterion or permission exemption.',
                        'Derived diagnostics never authorize lifecycle transitions.']}
    return validate('impact-adapter', adapter)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('root','profile','changed','base-revision','revision','adapter-output','output'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    def operation():
        observed=discover(args.root,load_json(args.profile),args.changed)
        adapter=build_adapter(observed,args.changed,args.base_revision,args.revision)
        if observed['status'] != 'PASSED':
            # Never replace a previous adapter on a failed inspection.
            return result(observed['status'],code='ADAPTER_INPUT_NOT_CURRENT',observation=observed,adapterWritten=False)
        write_json(adapter,args.adapter_output,[args.root,args.profile,args.output])
        return result('PASSED',adapterWritten=True,adapterPath=str(Path(args.adapter_output).resolve()),
                      adapter=adapter,observation=observed,authority='DERIVED_NO_GATE')
    return finish_cli(operation,args.output,[args.root,args.profile,args.adapter_output])

if __name__=='__main__':raise SystemExit(main())
