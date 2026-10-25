"""Anonymous on-disk fixtures; never read a user's project documents."""
from pathlib import Path
import json


def put(root, relative, value):
    path = Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')
    return path


def relation_layout(root, yaml):
    """Minimal real discovery layout, adapted from legacy test_artifact_relations."""
    put(root, 'docs/README.md', '# Anonymous fixture\n')
    put(root, 'version/V_TEST/requirement/REQ-LAB-001/requirement.md', '# REQ-LAB-001\n')
    put(root, 'docs/module/LAB/LAB_desc.md',
        '# LAB\n\n## 模块功能\n\n'
        '| 功能编码 | 功能名称 | 功能说明 | 所属能力 | 关联需求 | API 引用 | DB 引用 | 功能状态 |\n'
        '| --- | --- | --- | --- | --- | --- | --- | --- |\n'
        '| REQ-LAB-001-F01 | Example | Example | CAP-LAB | REQ-LAB-001 | 无 | 无 | ACTIVE |\n')
    put(root, 'docs/module/LAB/design/example.md',
        '# Example\n> Design topic：`DESIGN-LAB-EXAMPLE`\n'
        '> Design revision：`DESIGN-R1`\n\nREQ-LAB-001 REQ-LAB-001-F01 PAGE-LAB-DETAIL\n')
    put(root, 'version/V_TEST/requirement/REQ-LAB-001/test_case.md',
        '# Test\n<!-- TEST-CASE: CASE-LAB-001 -->\n<a id="CASE-LAB-001"></a>\n'
        'REQ-LAB-001 FLOW-LAB-EXAMPLE DESIGN-LAB-EXAMPLE\n')
    model = {'artifact': 'business_model', 'revision': 'BM-R1',
             'aggregates': [{'id': 'AGG-LAB', 'name': 'Example'}],
             'traceability': [{'featureRefs': ['REQ-LAB-001-F01'], 'modelRefs': ['AGG-LAB']}]}
    yaml.dump(model, Path(root) / 'docs/module/LAB/LAB_business_model.yaml')
    relations = Path(root) / 'docs/_relations'; relations.mkdir(parents=True)
    yaml.dump({'artifact': 'dependency_impact', 'revision': 'DI-R1',
               'nodes': [], 'relationships': [],
               'crossModuleImplementations': [{'id': 'CMI-LAB', 'name': 'Example', 'steps': []}]},
              relations / 'dependency_impact.yaml')
    flow_root = Path(root) / 'docs/_flows'; flow_root.mkdir(parents=True)
    yaml.dump({'artifact': 'business_flow', 'revision': 'FLOW-R1', 'flows': [{
        'id': 'FLOW-LAB-EXAMPLE', 'name': 'Example', 'status': 'ACTIVE',
        'requirementRefs': [{'id': 'REQ-LAB-001'}],
        'featureRefs': [{'id': 'REQ-LAB-001-F01'}],
        'steps': [{'id': 'STEP-LAB-01', 'name': 'Example'}],
        'businessModelRefs': ['AGG-LAB'], 'dependencyImpactRefs': ['CMI-LAB'],
        'designRefs': ['DESIGN-LAB-EXAMPLE'], 'testCaseIds': []}]}, flow_root / 'LAB_flow.yaml')


def impact_layout(root):
    task = Path(root) / 'version/V_TEST/task/TASK-LAB'
    put(task, 'task_plan.json', {'tasks': [
        {'id': 'TASK-A', 'depends_on': [], 'acceptance_trace_ids': ['TR-A']},
        {'id': 'TASK-B', 'depends_on': ['TASK-A'], 'acceptance_trace_ids': ['TR-B']} ]})
    put(task, 'traceability.json', {'items': [
        {'id': 'TR-A', 'plan_task_ids': ['TASK-A'], 'acceptance_criteria': ['AC-A']},
        {'id': 'TR-B', 'plan_task_ids': ['TASK-B'], 'acceptance_criteria': ['AC-B']} ]})
    graph = {'schemaVersion': 1, 'authority': 'DERIVED', 'kind': 'engineering_knowledge_graph',
             'nodes': [{'id': 'INV-LAB', 'type': 'INVARIANT', 'name': 'Rule', 'sourceRefs': ['anonymous'], 'contextRef': 'BC-A'},
                       {'id': 'API-LAB', 'type': 'API', 'name': 'API', 'sourceRefs': ['anonymous'], 'contextRef': 'BC-B'}],
             'relationships': [{'id': 'REL-LAB', 'from': 'INV-LAB', 'to': 'API-LAB', 'type': 'AFFECTS',
                                'reason': 'Anonymous dependency', 'sourceRefs': ['anonymous'], 'propagation': 'DEFAULT'}]}
    graph_path = put(root, 'impact-graph.json', graph)
    return task, graph_path


def flow_layout(root, flow_api, document_facts, yaml):
    """Real Flow schema, links, changeset and generated preview inputs."""
    version = 'V_1.0'; target = 'V_1.0_0001'; group = 'LAB_LIFECYCLE'
    requirement_ref = f'version/{version}/requirement/{target}/requirement.md'
    put(root, requirement_ref, '# Anonymous requirement\nLAB0001\nLAB0001-F01\nBR-LAB0001-001\nAC-LAB0001-001\nTR-LAB0001-001\n')
    text = document_facts.module_desc_template('LAB', version, project=True)
    lines = text.splitlines()
    start, end, _ = document_facts.find_table(lines, '## 模块功能', document_facts.FEATURE_HEADERS)
    lines[start:end] = document_facts.render_table(document_facts.FEATURE_HEADERS, [[
        'LAB0001-F01', 'Example', 'Example', 'CAP-LAB-CORE', 'LAB0001', '无', '无', 'ACTIVE']])
    start, end, _ = document_facts.find_table(lines, '## 业务能力', document_facts.CAPABILITY_HEADERS)
    lines[start:end] = document_facts.render_table(document_facts.CAPABILITY_HEADERS, [[
        'CAP-LAB-CORE', 'Example', 'Example', 'Boundary', 'ACTIVE']])
    put(root, 'docs/module/LAB/LAB_desc.md', '\n'.join(lines) + '\n')
    flow = {'id': 'FLOW-LAB-E2E', 'name': 'Example', 'level': 'L0', 'type': 'MAIN',
            'ownerModule': 'LAB', 'participantModules': ['LAB'], 'goal': 'Example', 'trigger': 'Request',
            'requirementRefs': [{'id': 'LAB0001', 'documentRef': requirement_ref}],
            'featureRefs': [{'id': 'LAB0001-F01', 'module': 'LAB'}],
            'ruleRefs': ['BR-LAB0001-001'], 'acceptanceRefs': ['AC-LAB0001-001'], 'traceIds': ['TR-LAB0001-001'],
            'preconditions': ['Valid request'], 'steps': [{'id': 'STEP-LAB-01', 'order': 1, 'name': 'Execute',
            'ownerModule': 'LAB', 'featureRef': 'LAB0001-F01', 'preconditions': [], 'postconditions': ['Completed']}],
            'variants': [], 'failurePaths': [{'id': 'FAIL-LAB-001', 'atStep': 'STEP-LAB-01', 'condition': 'Failure',
            'result': 'Unchanged', 'compensation': 'None', 'blocking': True, 'acceptanceRefs': ['AC-LAB0001-001']}],
            'successCriteria': ['Old boundary'], 'businessModelRefs': [], 'dependencyImpactRefs': [], 'designRefs': [],
            'testCaseIds': [], 'status': 'ACTIVE', 'createdVersion': version, 'lastChangedVersion': version, 'supersedes': []}
    changeset = {'schemaVersion': 1, 'artifact': 'business_flow_changeset', 'changeSet': {
        'id': 'FLOW-CHG-LAB-001', 'version': version, 'module': 'LAB', 'flowGroup': group, 'reason': 'Anonymous example',
        'baseRevision': flow_api.INITIAL_REVISION, 'resultRevision': 'FLOW-R01@test', 'affectedTraceIds': ['TR-LAB0001-001']},
        'operations': [{'operation': 'add', 'targetId': flow['id'], 'reason': 'Add', 'after': flow}]}
    changes = flow_api.changes_root(Path(root), version, group); changes.mkdir(parents=True)
    path = changes / 'example.yaml'; yaml.dump(changeset, path)
    return version, group, path, changeset
