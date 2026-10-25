#!/usr/bin/env python3
"""Capture isolated legacy interfaces with anonymous files; never a production gate.

Usage: python3 -B tests/run_legacy_baseline.py --legacy-source OLD_PACKAGE --output EXTERNAL_REPORT
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import traceback

EXCLUDED = {'__pycache__', '.pytest_cache', '.git', 'project_doc'}


def snapshot(root):
    root = Path(root)
    values = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if set(relative.parts) & EXCLUDED:
            continue
        if path.is_symlink():
            values[relative.as_posix()] = {'symlink': os.readlink(path)}
        elif path.is_file():
            values[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():
            values[relative.as_posix() + '/'] = 'DIRECTORY'
    return values


def changed(before, after):
    return [key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)]


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def environment():
    try:
        schema_version = importlib.metadata.version('jsonschema')
    except importlib.metadata.PackageNotFoundError:
        schema_version = None
    return {'python': platform.python_version(), 'implementation': platform.python_implementation(),
            'executable': sys.executable, 'os': platform.system(), 'release': platform.release(),
            'machine': platform.machine(), 'jsonschema': schema_version}


def normalize(value, root):
    if isinstance(value, str):
        return value.replace(str(root), '<TEMP>')
    if isinstance(value, dict):
        return {str(k).replace(str(root), '<TEMP>'): normalize(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v, root) for v in value]
    return value


def worker(package, workspace):
    sys.path.insert(0, str(package / 'scripts'))
    import engineering_graph
    import reference_resolution
    import simple_yaml
    from legacy_fixture import relation_layout, impact_layout, flow_layout, put
    records = []
    def record(case_id, expected, actual, *, known=None, inputs=None, side_effects=None):
        status = 'PASSED' if actual == expected else ('KNOWN_DIFFERENCE' if known is not None and actual == known else 'FAILED')
        records.append({'id': case_id, 'status': status, 'expected': expected, 'actual': actual,
                        'knownLegacyBehavior': known, 'inputs': inputs or {}, 'sideEffects': side_effects or {}})
    def checked(case_id, root, expected, operation, *, known=None, inputs=None):
        before = snapshot(root)
        try:
            actual = operation()
        except Exception as exc:
            actual = {'exception': type(exc).__name__, 'message': str(exc)}
        after = snapshot(root)
        delta = changed(before, after)
        record(case_id, expected, actual, known=known, inputs=inputs,
               side_effects={'beforeDigest': canonical_digest(before), 'afterDigest': canonical_digest(after), 'changedPaths': delta})
        if delta:
            record(case_id + '-READ-ONLY', [], delta)
        return actual
    imported = {'engineering_graph': engineering_graph.__file__, 'reference_resolution': reference_resolution.__file__, 'simple_yaml': simple_yaml.__file__}

    refs = workspace / 'references'; refs.mkdir()
    put(refs, 'escaped.json', '{"id":"FLOW-\\u004fRDER","artifactType":"flow","revision":"R1"}')
    checked('REFERENCE-ESCAPED-ID', refs, 'FLOW-ORDER', lambda: reference_resolution.resolve_reference('escaped.json#FLOW-ORDER', refs).artifact_id,
            inputs={'reference': 'escaped.json#FLOW-ORDER', 'files': snapshot(refs)})
    put(refs, 'duplicate.json', [{'id': 'DUP-1'}, {'id': 'DUP-1'}])
    def duplicate():
        try:
            reference_resolution.resolve_reference('duplicate.json#DUP-1', refs)
        except reference_resolution.ReferenceResolutionError as exc:
            return 'AMBIGUOUS' if 'ambiguous' in str(exc) else str(exc)
        return 'ACCEPTED'
    checked('REFERENCE-DUPLICATE-ID', refs, 'AMBIGUOUS', duplicate, inputs={'reference': 'duplicate.json#DUP-1'})
    put(refs, 'root-only.json', {'field': 1})
    def empty_pointer():
        try:
            reference_resolution.resolve_reference('root-only.json#/', refs)
            return 'ACCEPTED_ROOT_WITHOUT_EMPTY_KEY'
        except reference_resolution.ReferenceResolutionError:
            return 'REJECTED_MISSING_EMPTY_KEY'
    checked('REFERENCE-EMPTY-KEY-POINTER', refs, 'REJECTED_MISSING_EMPTY_KEY', empty_pointer,
            known='ACCEPTED_ROOT_WITHOUT_EMPTY_KEY', inputs={'reference': 'root-only.json#/', 'document': {'field': 1}})
    put(refs, 'single.yaml', "id: 'FLOW-\\u004fRDER'\n")
    def single_quoted():
        try:
            reference_resolution.resolve_reference('single.yaml#FLOW-ORDER', refs)
            return 'DECODED_SINGLE_QUOTE_ESCAPE'
        except reference_resolution.ReferenceResolutionError:
            return 'LITERAL_BACKSLASH_NOT_MATCHED'
    checked('REFERENCE-YAML-SINGLE-QUOTE', refs, 'LITERAL_BACKSLASH_NOT_MATCHED', single_quoted,
            known='DECODED_SINGLE_QUOTE_ESCAPE', inputs={'reference': 'single.yaml#FLOW-ORDER', 'yaml': "id: 'FLOW-\\u004fRDER'"})
    put(refs, 'ghost.md', '# Actual\n')
    def filename_anchor():
        try:
            reference_resolution.resolve_reference('ghost.md#ghost', refs)
            return 'ACCEPTED_FILENAME_AS_ANCHOR'
        except reference_resolution.ReferenceResolutionError:
            return 'REJECTED_MISSING_HEADING'
    checked('REFERENCE-FILENAME-PSEUDO-ANCHOR', refs, 'REJECTED_MISSING_HEADING', filename_anchor,
            known='ACCEPTED_FILENAME_AS_ANCHOR', inputs={'reference': 'ghost.md#ghost', 'markdown': '# Actual\n'})

    discovered = workspace / 'discovery'; discovered.mkdir()
    try:
        import artifact_relations
        imported['artifact_relations'] = artifact_relations.__file__
        relation_layout(discovered, simple_yaml)
        def discover():
            relation = artifact_relations.Builder(discovered).build()
            graph = engineering_graph.build(artifact_relations=relation)
            validation = engineering_graph.validate(graph)
            relations = {(e['from'], e['to'], e['type']) for e in relation['relationships']}
            required = {('REQ-LAB-001', 'FLOW-LAB-EXAMPLE', 'PARTICIPATES_IN'),
                        ('FLOW-LAB-EXAMPLE', 'AGG-LAB', 'MODELED_BY'),
                        ('FLOW-LAB-EXAMPLE', 'CMI-LAB', 'IMPLEMENTED_BY')}
            return {'validation': validation['status'], 'requiredEdgesFound': required <= relations,
                    'derivedAuthority': graph['authority'], 'discoveryAuthority': relation['authority']}
        checked('REAL-FILE-DISCOVERY', discovered, {'validation': 'PASSED', 'requiredEdgesFound': True,
                'derivedAuthority': 'DERIVED', 'discoveryAuthority': 'DERIVED'}, discover,
                inputs={'fixtureFiles': snapshot(discovered), 'entry': 'Builder.build -> engineering_graph.build'})
    except Exception as exc:
        record('REAL-FILE-DISCOVERY-SETUP', 'READY', {'exception': type(exc).__name__, 'message': str(exc)})

    cli_root = workspace / 'graph-cli'; cli_root.mkdir()
    chain = {'schemaVersion': 1, 'nodes': [{'id': f'N-{i}', 'type': 'COMPONENT'} for i in range(13)],
             'relationships': [{'id': f'E-{i}', 'from': f'N-{i}', 'to': f'N-{i+1}', 'type': 'DEPENDS_ON',
                                'propagation': 'DEFAULT'} for i in range(12)]}
    gp = put(cli_root, 'chain.json', chain)
    api_result = engineering_graph.impact(chain, ['N-0'])
    record('GRAPH-DEFAULT-DEPTH', 13, len(api_result), known=9,
           inputs={'graph': chain, 'changedRefs': ['N-0'], 'requestedDepth': 'legacy default 8'})
    def cli_run():
        argv = [sys.executable, '-B', str(package / 'scripts/engineering_graph.py'), 'impact', '--graph', str(gp), '--changed-ref', 'N-0', '--json']
        completed = subprocess.run(argv, cwd=cli_root, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), capture_output=True, text=True, timeout=30)
        return {'exitCode': completed.returncode, 'output': json.loads(completed.stdout) if completed.returncode == 0 else completed.stdout,
                'stderr': completed.stderr}
    checked('GRAPH-ORIGINAL-CLI-SAME-INPUT', cli_root, {'exitCode': 0, 'output': api_result, 'stderr': ''}, cli_run,
            inputs={'argv': ['engineering_graph.py', 'impact', '--graph', 'chain.json', '--changed-ref', 'N-0', '--json'], 'graph': chain})

    impact_root = workspace / 'impact'; impact_root.mkdir()
    try:
        import impact_analysis
        imported['impact_analysis'] = impact_analysis.__file__
        task, graph_path = impact_layout(impact_root)
        def plan():
            value = impact_analysis.build_plan(task_dir=task, phase='code_review', base_revision='R1', revision='R2',
                change_class='SEMANTIC_AUTHORITY', graph_path=graph_path, changed_refs=['INV-LAB'], affected_task_ids=['TASK-A'])
            return {'status': value['status'], 'authority': value['authority'], 'affectedScope': value['affectedScope'],
                    'crossContext': value['graphImpact']['crossContext'], 'apiRisk': 'api_contract' in value['requiredRiskTemplates'],
                    'newReviewRequired': value['reusePolicy']['reviewResultReuseAcrossExactRevision'] is False,
                    'revalidationRequired': value['revalidationRequired']}
        checked('IMPACT-ORIGINAL-BUILD-PLAN', impact_root, {'status': 'PASSED', 'authority': 'DERIVED',
            'affectedScope': {'taskIds': ['TASK-A', 'TASK-B'], 'traceIds': ['TR-A', 'TR-B'], 'acceptanceIds': ['AC-A', 'AC-B']},
            'crossContext': True, 'apiRisk': True, 'newReviewRequired': True, 'revalidationRequired': True}, plan,
            inputs={'phase': 'code_review', 'baseRevision': 'R1', 'revision': 'R2', 'changeClass': 'SEMANTIC_AUTHORITY',
                    'changedRefs': ['INV-LAB'], 'affectedTaskIds': ['TASK-A'], 'files': snapshot(impact_root)})
    except Exception as exc:
        record('IMPACT-SETUP', 'READY', {'exception': type(exc).__name__, 'message': str(exc)})

    flow_root = workspace / 'flow'; flow_root.mkdir()
    try:
        import business_flow
        import document_facts
        from _core.review_inputs import flow_inputs
        imported['business_flow'] = business_flow.__file__
        imported['review_inputs'] = sys.modules['_core.review_inputs'].__file__
        version, group, path, changeset = flow_layout(flow_root, business_flow, document_facts, simple_yaml)
        business_flow.write_previews(flow_root, version, business_flow.simulate(flow_root, version, 'LAB'))
        checked('FLOW-GENERATED-PREVIEW-CURRENT', flow_root, True, lambda: bool(flow_inputs(flow_root, version, {'FLOW-LAB-E2E'})),
                inputs={'files': snapshot(flow_root), 'entry': 'flow_inputs', 'flowIds': ['FLOW-LAB-E2E']})
        changeset['operations'][0]['after']['successCriteria'] = ['Changed boundary, same revision']
        simple_yaml.dump(changeset, path)
        def stale():
            try:
                flow_inputs(flow_root, version, {'FLOW-LAB-E2E'})
            except ValueError as exc:
                return 'STALE_FLOW_PREVIEW' if 'STALE_FLOW_PREVIEW' in str(exc) else str(exc)
            return 'ACCEPTED_STALE_PREVIEW'
        checked('FLOW-SAME-REVISION-OLD-PREVIEW', flow_root, 'STALE_FLOW_PREVIEW', stale,
                inputs={'change': 'successCriteria changed; changeset ID and resultRevision unchanged', 'files': snapshot(flow_root)})
    except Exception as exc:
        record('FLOW-SETUP', 'READY', {'exception': type(exc).__name__, 'message': str(exc), 'traceback': traceback.format_exc(limit=3)})
    # Public command matrix: read-only doctor and parser surfaces.  No command
    # handler is executed here because command_impact and doctor --apply write
    # task/cache state.
    try:
        import doctor
        import wk
        imported['doctor'] = doctor.__file__
        imported['wk'] = wk.__file__
        doctor_root = workspace / 'doctor'; doctor_root.mkdir()
        task_root, _ = impact_layout(doctor_root)
        def doctor_inspect():
            value = doctor.inspect(doctor_root, 'V_TEST', agent='ProjectManagerAgent')
            return {'type': type(value).__name__, 'hasStatus': isinstance(value, dict) and 'status' in value,
                    'writeCommandPresent': isinstance(value, dict) and 'repairCommand' in value}
        checked('LEGACY-DOCTOR-INSPECT-READONLY', doctor_root,
                {'type': 'dict', 'hasStatus': True, 'writeCommandPresent': False}, doctor_inspect,
                inputs={'entry': 'doctor.inspect', 'apply': False, 'fix': False, 'files': snapshot(doctor_root)})
        def parser_matrix():
            parser = wk.build_parser()
            impact = parser.parse_args(['impact', '-g', 'ProjectManagerAgent', '--task-dir', str(task_root), '--revision', 'R2'])
            doctor_args = parser.parse_args(['doctor', '--doc-root', str(doctor_root), '--version', 'V_TEST', '--json'])
            return {'impactCommand': impact.command, 'impactRevision': impact.revision,
                    'doctorCommand': doctor_args.command, 'doctorJson': doctor_args.json}
        checked('LEGACY-WK-PUBLIC-PARSER-MATRIX', workspace,
                {'impactCommand': 'impact', 'impactRevision': 'R2', 'doctorCommand': 'doctor', 'doctorJson': True}, parser_matrix,
                inputs={'commands': ['impact', 'doctor'], 'handlersExecuted': False})
        def blocked_impact_missing_revision():
            value = wk.build_parser().parse_args(['impact', '-g', 'ProjectManagerAgent', '--task-dir', str(task_root)])
            return {'revision': value.revision, 'phase': value.phase, 'handlerExecuted': False}
        checked('LEGACY-WK-IMPACT-MISSING-REVISION-PARSE', workspace,
                {'revision': '', 'phase': '', 'handlerExecuted': False}, blocked_impact_missing_revision,
                inputs={'entry': 'parser only', 'commandExecuted': False})
    except Exception as exc:
        record('PUBLIC-COMMAND-MATRIX-SETUP', 'READY', {'exception': type(exc).__name__, 'message': str(exc),
                                                        'traceback': traceback.format_exc(limit=3)})
    origins_safe = all(Path(path).resolve().is_relative_to(package.resolve()) for path in imported.values())
    record('IMPORT-ISOLATION', True, origins_safe, inputs=imported)
    return {'cases': records, 'importOrigins': imported}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--worker-workspace', help=argparse.SUPPRESS)
    args = parser.parse_args()
    source = Path(args.legacy_source).resolve(); output = Path(args.output).resolve()
    own_package = Path(__file__).resolve().parents[1]
    if any(output == root or output.is_relative_to(root) for root in (source, own_package)):
        parser.error('--output must be outside both legacy and candidate packages')
    if args.worker_workspace:
        workspace = Path(args.worker_workspace).resolve()
        if source.name != 'legacy-package' or workspace.parent != source.parent or workspace.name != 'anonymous-inputs':
            parser.error('internal worker requires the isolated package and anonymous sibling workspace')
        report = worker(source, workspace)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0
    if source.name == 'project_doc' or not (source / 'scripts/reference_resolution.py').is_file():
        parser.error('--legacy-source must be the original installable package')
    before = snapshot(source)
    if any(isinstance(value, dict) for value in before.values()):
        parser.error('legacy baseline refuses symlinks; use a self-contained source package')
    with tempfile.TemporaryDirectory(prefix='legacy-baseline-') as directory:
        temporary = Path(directory).resolve(); isolated = temporary / 'legacy-package'; workspace = temporary / 'anonymous-inputs'; workspace.mkdir()
        shutil.copytree(source, isolated, ignore=shutil.ignore_patterns(*EXCLUDED))
        isolated_before = snapshot(isolated)
        child_output = temporary / 'worker.json'
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1'); env.pop('PYTHONPATH', None)
        run = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--legacy-source', str(isolated),
            '--output', str(child_output), '--worker-workspace', str(workspace)], cwd=temporary, env=env,
            capture_output=True, text=True, timeout=120)
        payload = json.loads(child_output.read_text()) if child_output.is_file() else {'cases': [], 'workerFailure': run.stderr}
        source_after = snapshot(source); isolated_after = snapshot(isolated)
        side_effects = {'originalChangedPaths': changed(before, source_after),
                        'isolatedPackageChangedPaths': changed(isolated_before, isolated_after),
                        'copyMatchesOriginal': before == isolated_before,
                        'originalBeforeDigest': canonical_digest(before), 'originalAfterDigest': canonical_digest(source_after)}
        failed = run.returncode != 0 or any(x['status'] == 'FAILED' for x in payload['cases']) or bool(side_effects['originalChangedPaths'] or side_effects['isolatedPackageChangedPaths']) or not side_effects['copyMatchesOriginal']
        report = {'schemaVersion': 1, 'kind': 'LEGACY_INTERFACE_BASELINE', 'status': 'FAILED' if failed else 'CAPTURED',
            'authority': 'OBSERVATION_ONLY', 'gateAuthorization': False, 'source': str(source), 'sourceDigest': canonical_digest(before),
            'sourceFiles': before, 'environmentMatrix': {'observed': [environment()], 'otherPythonOrOSCombinations': 'NOT_TESTED'},
            'runnerDigests': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__).resolve(), Path(__file__).with_name('legacy_fixture.py'))},
            **payload, 'sideEffectComparison': side_effects, 'workerExitCode': run.returncode,
            'workerStderr': run.stderr, 'unverified': ['real project data and history', 'wk runtime/doctor CLI integration',
                'lifecycle or Review gate transitions', 'concurrent source mutation', 'cross-platform behavior',
                'Flow archive application and real project linkage beyond anonymous fixture', 'production performance',
                'OS-wide side effects outside explicitly snapshotted roots'],
            'snapshotExclusions': sorted(EXCLUDED),
            'notes': ['Fixture construction and Flow generation intentionally write only temporary anonymous files.',
                      'Every checked reader records before/after file digests; KNOWN_DIFFERENCE is not PASSED.',
                      'This report is evidence for G0/G1 evaluation, not a claim that either gate passed.']}
        report = normalize(report, temporary)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.legacy-baseline-', dir=output.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        os.replace(name, output)
    finally:
        Path(name).unlink(missing_ok=True)
    print(json.dumps({'status': report['status'], 'report': str(output), 'caseCount': len(report['cases']),
                      'knownDifferences': [x['id'] for x in report['cases'] if x['status'] == 'KNOWN_DIFFERENCE']}))
    return 2 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
