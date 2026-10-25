"""Side-effect bounded application services behind thin CLI adapters."""
from artifact_identity import identity
from discover_dependencies import discover
from publish_candidate import publish
from review_submit import submit
from resource_paths import asset_path
from runtime import contract_normalization
from diagnostic_io import result


def authorize(agent, command):
    """Authorize derived candidate services against the Runtime Contract."""
    path = asset_path('assets/runtime/runtime-contract.json')
    try:
        contract = contract_normalization.load_runtime_contract(path)
        permission = contract['scriptPermissions'][f'service_cli.{command}']
        allowed = permission.get('mode') == 'STATIC' and agent in permission.get('allowedAgents', [])
        if agent not in contract['agents'] or not allowed:
            return result('FAILED', code='AGENT_NOT_AUTHORIZED', command=command, currentAgent=agent)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return result('FAILED', code='AUTHORIZATION_CONTRACT_INVALID', diagnostic=str(exc))
    return None


def inspect_artifact(spec, root, *, read_bytes=None):
    return identity(spec, root, read_bytes=read_bytes)


def discover_artifacts(root, profile, *, changed=None, expected_graph=None, session=None):
    return discover(root, profile, changed=changed, expected_graph=expected_graph, session=session)


def seal_candidate(root, candidate, archive_output, review_output, *, agent='ProjectManagerAgent'):
    denied = authorize(agent, 'seal')
    if denied:
        return denied
    return publish(root, candidate, archive_output, review_output)


def submit_review(archive, review, output, decision, findings=None, current=None, root=None, protected=(), *, agent='ProjectManagerAgent'):
    denied = authorize(agent, 'review')
    if denied:
        return denied
    return submit(archive, review, output, decision, findings, current=current, root=root, protected=protected)
