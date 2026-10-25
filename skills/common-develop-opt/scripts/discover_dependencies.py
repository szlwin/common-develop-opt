"""Discover and verify dependencies in explicitly scoped artifact declarations."""
from pathlib import Path
import argparse
from artifact_identity import identity
from dependency_graph import inspect
from diagnostic_io import canonical, finish_cli, load_json, project_path, result
from read_session import ReadSession
from reference_resolver import resolve
from schema_protocol import ProtocolError, validate

SUFFIXES = ('.artifact.json', '.artifact.yaml', '.artifact.yml')


def discover(root, profile, changed=None, expected_graph=None, session=None):
    root = Path(root).resolve()
    session = session or ReadSession()
    session.begin()
    try:
        validate('discovery-profile', profile)
    except ProtocolError as exc:
        return result('UNKNOWN', code='DISCOVERY_PROFILE_INVALID', diagnostic=str(exc))
    diagnostics, declarations, identities, locations = [], {}, {}, {}
    paths = set()
    scopes = []
    try:
        for raw in profile['roots']:
            scope = project_path(raw, root)
            if not scope.is_dir():
                raise ValueError('DISCOVERY_ROOT_MISSING: ' + raw)
            scopes.append(scope)
            # rglob does not recursively traverse symlink directories. Reject them
            # explicitly so related declarations cannot silently disappear.
            for entry in scope.rglob('*'):
                if entry.is_symlink():
                    raise ValueError('DISCOVERY_SYMLINK_UNSUPPORTED: ' + str(entry.relative_to(root)))
                if entry.is_file() and entry.name.endswith(SUFFIXES):
                    paths.add(entry.resolve())
        if len(paths) > profile.get('maxDeclarations', 10000):
            raise ValueError('DECLARATION_LIMIT_EXCEEDED')
        for path in sorted(paths):
            relative = path.relative_to(root).as_posix()
            try:
                spec = session.load(path)
                if not isinstance(spec, dict) or 'dependencies' not in spec:
                    raise ValueError('EXPLICIT_DEPENDENCIES_REQUIRED')
                observed = identity(spec, root, read_bytes=session.read_bytes)
                if observed['status'] != 'PASSED':
                    diagnostics.append({'path':relative,'code':observed.get('code'),'status':observed['status']})
                    continue
                key = spec['artifactId']
                if key in declarations:
                    raise ValueError('DUPLICATE_ARTIFACT_ID: ' + key)
                declarations[key] = spec
                identities[key] = observed['artifact']
                locations[key] = relative
            except (ValueError, TypeError, OSError) as exc:
                diagnostics.append({'path':relative,'status':'UNKNOWN','code':'DECLARATION_INVALID','message':str(exc)})
        required, found = set(profile['requiredArtifactIds']), set(declarations)
        if not paths:
            # A project may contain rich documents but no declaration files
            # understood by this read-only profile. Keep that distinct from an
            # ordinary missing-required diagnostic so callers stop safely.
            return result('UNKNOWN', code='NO_SUPPORTED_DECLARATIONS',
                          diagnostics=[{'status':'UNKNOWN',
                                        'code':'UNSUPPORTED_LAYOUT',
                                        'message':'No supported .artifact.json/.artifact.yaml/.artifact.yml declarations were found in configured roots.'}],
                          declarationCount=0, coverage='UNSUPPORTED_LAYOUT',
                          measurements=session.metrics(), authority='DERIVED_NO_GATE',
                          limitation='This profile reads explicit artifact declarations only; configured roots must provide supported declarations.')
        for key in sorted(required - found):
            diagnostics.append({'artifactId':key,'status':'UNKNOWN','code':'REQUIRED_ARTIFACT_MISSING'})
        # Unexpected declarations are included, never dropped to match a catalog.
        edges = []
        for dependent, spec in sorted(declarations.items()):
            for dep in spec['dependencies']:
                upstream = dep['artifactId']
                if upstream not in found:
                    diagnostics.append({'artifactId':dependent,'target':upstream,'status':'UNKNOWN','code':'DEPENDENCY_TARGET_MISSING'})
                    continue
                edges.append({'from':upstream,'to':dependent,'kind':dep['kind'],
                              'revision':dep['revision'],'contentDigest':dep['contentDigest']})
                if dep.get('reference'):
                    reference = resolve(root, dep['reference'], declarations[upstream]['artifactType'], dep['revision'],
                                        source=locations[dependent], read_session=session)
                    if reference['status'] != 'PASSED':
                        diagnostics.append({'artifactId':dependent,'target':upstream,'status':reference['status'],
                                            'code':'DEPENDENCY_REFERENCE_INVALID','reference':reference})
                    elif not reference.get('artifactId'):
                        diagnostics.append({'artifactId':dependent,'target':upstream,'status':'UNKNOWN','code':'DEPENDENCY_REFERENCE_ID_UNDECLARED','reference':reference})
                    elif reference['artifactId'] != upstream:
                        diagnostics.append({'artifactId':dependent,'target':upstream,'status':'FAILED','code':'DEPENDENCY_REFERENCE_ID_MISMATCH','reference':reference})
                    elif reference['path'] not in {x['path'] for x in identities[upstream]['inputs']}:
                        diagnostics.append({'artifactId':dependent,'target':upstream,'status':'FAILED','code':'DEPENDENCY_REFERENCE_WRONG_SOURCE'})
        graph = {'schemaVersion':1,'nodes':[{'artifactId':x} for x in sorted(found)],
                 'edges':sorted(edges,key=canonical)}
        if expected_graph is not None:
            validate('dependency-graph', expected_graph)
            # Validate bindings and topology, not merely the list of edge IDs.
            checked = inspect(expected_graph)
            if checked['status'] != 'PASSED':
                diagnostics.append({'status':'UNKNOWN','code':'SUPPLIED_GRAPH_INVALID','details':checked})
            actual_nodes = {x['artifactId'] for x in graph['nodes']}
            supplied_nodes = {x['artifactId'] for x in expected_graph['nodes']}
            actual_edges = {canonical(x) for x in graph['edges']}
            supplied_edges = {canonical(x) for x in expected_graph['edges']}
            if actual_nodes != supplied_nodes or actual_edges != supplied_edges:
                diagnostics.append({'status':'FAILED','code':'GRAPH_DECLARATION_MISMATCH',
                                    'missingNodes':sorted(actual_nodes-supplied_nodes),'extraNodes':sorted(supplied_nodes-actual_nodes),
                                    'missingEdges':[load_edge(x) for x in sorted(actual_edges-supplied_edges)],
                                    'extraEdges':[load_edge(x) for x in sorted(supplied_edges-actual_edges)]})
        checked = inspect(graph, changed, list(identities.values()))
        session.verify()
        # Detect declaration additions/removals while inspecting this scope.
        final_paths = {p.resolve() for scope in scopes for p in scope.rglob('*') if p.is_file() and p.name.endswith(SUFFIXES)}
        if paths != final_paths:
            raise ValueError('DECLARATION_SET_CHANGED')
    except (ValueError, TypeError, OSError) as exc:
        return result('UNKNOWN', code='DISCOVERY_INCOMPLETE', diagnostic=str(exc), diagnostics=diagnostics,
                      coverage='INCOMPLETE', measurements=session.metrics(), authority='DERIVED_NO_GATE')
    status = 'FAILED' if any(x['status']=='FAILED' for x in diagnostics) else ('UNKNOWN' if diagnostics else checked['status'])
    return result(status, graph=graph, identities=[identities[x] for x in sorted(identities)],
                  diagnostics=diagnostics, analysis=checked, measurements=session.metrics(),
                  declarationCount=len(paths), coverage='INCOMPLETE' if diagnostics else 'SCOPED_DECLARATIONS',
                  authority='DERIVED_NO_GATE',
                  limitation='Coverage applies to supported declaration files in configured roots; undeclared business relations cannot be inferred.')


def load_edge(value):
    import json
    return json.loads(value)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True);parser.add_argument('--profile',required=True)
    parser.add_argument('--graph');parser.add_argument('--changed');parser.add_argument('--output',required=True)
    args=parser.parse_args()
    protected=[args.root,args.profile]+([args.graph] if args.graph else [])
    return finish_cli(lambda:discover(args.root,load_json(args.profile),args.changed,load_json(args.graph) if args.graph else None),args.output,protected)

if __name__=='__main__':raise SystemExit(main())
