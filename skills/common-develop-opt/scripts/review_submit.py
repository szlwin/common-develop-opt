"""Submit a Review result against the exact sealed input set."""
from __future__ import annotations
from pathlib import Path
from review_lifecycle import validate_review_inputs
from diagnostic_io import result, output_path, write_json
from candidate_inputs import measure
from read_session import ReadSession


def submit(archive, review, output, decision, findings=None, current=None, root=None, protected=()):
    if root is None or not isinstance(current, dict):
        return result('FAILED', code='CURRENT_INPUT_REQUIRED')
    session = ReadSession()
    checked = measure(root, current, session)
    if checked['status'] != 'PASSED':
        return result('FAILED', code='CURRENT_INPUT_INVALID', details=checked)
    protected = [*protected, *(Path(root) / row['path']
                             for rows in checked['inputs'].values() for row in rows)]
    try:
        output_path(output, protected)
    except (OSError, ValueError) as exc:
        return result('FAILED', code='REVIEW_OUTPUT_REJECTED', diagnostic=str(exc))
    check = validate_review_inputs(archive, review, current=current, root=root)
    if check['status'] != 'PASSED':
        return result('FAILED', code=check.get('code') or 'REVIEW_INPUT_INVALID')
    if decision not in {'APPROVED', 'NEEDS_CHANGES'}:
        return result('UNKNOWN', code='REVIEW_DECISION_INVALID')
    payload = {'schemaVersion': 1, 'kind': 'REVIEW_RESULT', 'authority': 'REVIEW',
               'artifactId': archive['artifactId'], 'revision': archive['revision'],
               'reviewInputDigest': archive['reviewInputDigest'], 'decision': decision,
               'findings': list(findings or [])}
    try:
        session.verify()
        write_json(payload, output, protected)
    except (OSError, ValueError) as exc:
        return result('UNKNOWN', code='REVIEW_WRITE_FAILED', diagnostic=str(exc))
    return result('PASSED', reviewResult=payload)
