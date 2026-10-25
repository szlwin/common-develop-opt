"""Typed, derived projection over the existing engineering graph query."""
from __future__ import annotations
import argparse
from diagnostic_io import EDGE_KINDS, canonical, digest_manifest, digest_value, finish_cli, is_digest, load_json, result
from document_core import engineering_graph as legacy
from schema_protocol import ProtocolError, validate


def inspect(graph, changed=None, artifacts=None):
    if not isinstance(graph, dict) or not isinstance(graph.get('nodes'), list) or not isinstance(graph.get('edges'), list):
        return result('FAILED', code='GRAPH_COLLECTIONS_REQUIRED')
    if type(graph.get('schemaVersion')) is not int or graph['schemaVersion'] != 1:
        return result('UNKNOWN', code='GRAPH_SCHEMA_UNKNOWN')
    nodes, edges = graph['nodes'], graph['edges']
    if any(not isinstance(n, dict) or not isinstance(n.get('artifactId'), str) or not n['artifactId'] for n in nodes):
        return result('FAILED', code='NODE_ID_REQUIRED')
    ids = [n['artifactId'] for n in nodes]
    if len(ids) != len(set(ids)):
        return result('FAILED', code='DUPLICATE_NODE_ID')
    keys = set()
    for edge in edges:
        if not isinstance(edge, dict) or not isinstance(edge.get('kind'), str) or edge['kind'] not in EDGE_KINDS or edge.get('from') not in ids or edge.get('to') not in ids:
            return result('FAILED', code='INVALID_EDGE')
        key = (edge['from'], edge['to'], edge['kind'])
        if key in keys:
            return result('FAILED', code='DUPLICATE_EDGE')
        keys.add(key)
        if edge['kind'] not in {'contextual', 'supersedes'} and (not isinstance(edge.get('revision'), str) or not edge['revision'] or not is_digest(edge.get('contentDigest'))):
            return result('UNKNOWN', code='EDGE_INPUT_IDENTITY_REQUIRED')
    try:
        validate('dependency-graph', graph)
    except ProtocolError as exc:
        return result('FAILED', code='GRAPH_PROTOCOL_INVALID', diagnostic=str(exc))
    # All edge directions are explicitly upstream -> dependent; contextual and
    # supersedes are navigation/history edges, never invalidation authority.
    projection = {'nodes': [{'id': x} for x in ids], 'relationships': [
        {'id': digest_value([e['from'], e['to'], e['kind']]), 'from': e['from'], 'to': e['to'],
         'type': e['kind'], 'propagation': 'NO_PROPAGATION' if e['kind'] in {'contextual', 'supersedes'} else 'DEFAULT'} for e in edges]}
    paths = []
    if changed is not None:
        if changed not in ids:
            return result('UNKNOWN', code='CHANGED_NODE_NOT_FOUND')
        by_edge = {e['id']: e for e in projection['relationships']}
        for entry in legacy.impact(projection, [changed], max_depth=len(ids)):
            if entry['ref'] == changed:
                continue
            chain = [by_edge[x] for x in entry['reasonPath']]
            paths.append({'artifactId': entry['ref'], 'path': [changed] + [x['to'] for x in chain],
                          'via': [x['type'] for x in chain], 'reasonEdges': entry['reasonPath']})
    drift, missing = [], []
    freshness = 'UNKNOWN'
    if artifacts is not None:
        if not isinstance(artifacts, list) or any(not isinstance(x, dict) or not isinstance(x.get('artifactId'), str) for x in artifacts):
            return result('FAILED', code='ARTIFACT_LIST_INVALID')
        current_ids = [x['artifactId'] for x in artifacts]
        if len(set(current_ids)) != len(current_ids):
            return result('FAILED', code='DUPLICATE_ARTIFACT_ID')
        by_id = {x['artifactId']: x for x in artifacts}
        missing = [x for x in ids if x not in by_id or not is_digest(by_id[x].get('contentDigest')) or not isinstance(by_id[x].get('revision'), str) or not by_id[x]['revision'].strip()]
        for e in edges:
            if e['kind'] in {'contextual', 'supersedes'} or e['from'] in missing:
                continue
            actual = by_id[e['from']]
            if actual['revision'] != e['revision'] or actual['contentDigest'] != e['contentDigest']:
                drift.append({'code': 'EDGE_STALE_INPUT', 'from': e['from'], 'to': e['to'], 'kind': e['kind'],
                              'expectedRevision': e['revision'], 'currentRevision': actual['revision'],
                              'expectedDigest': e['contentDigest'], 'currentDigest': actual['contentDigest']})
        freshness = 'UNKNOWN' if missing else ('STALE' if drift else 'CURRENT')
    return result(freshness if artifacts is not None and freshness != 'CURRENT' else 'PASSED',
                  graph={'nodeCount': len(ids), 'edgeCount': len(edges), 'dependencyManifestDigest': digest_manifest(edges)},
                  impact=paths, drift={'status': freshness, 'items': drift, 'missingIdentities': missing},
                  coverage='DECLARED_ONLY', validationScope='CONTENT_AND_STRUCTURE' if artifacts is not None else 'STRUCTURE_ONLY',
                  authority='DERIVED_NO_GATE', legacyProjection=projection)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--graph', required=True); ap.add_argument('--changed'); ap.add_argument('--artifacts'); ap.add_argument('--output', required=True)
    args = ap.parse_args()
    return finish_cli(lambda: inspect(load_json(args.graph), args.changed, load_json(args.artifacts) if args.artifacts else None),
                      args.output, [args.graph] + ([args.artifacts] if args.artifacts else []))

if __name__ == '__main__':
    raise SystemExit(main())
