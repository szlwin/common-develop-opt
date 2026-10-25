"""Measure candidate file inputs; caller-provided digests are expectations only."""
from pathlib import Path
import hashlib
import posixpath
from diagnostic_io import digest_manifest, is_digest, project_path, result
from read_session import ReadSession

GROUPS = {'sourceFiles': 'contentDigest', 'dependencyFiles': 'dependencyManifestDigest',
          'evidenceFiles': 'evidenceDigest'}


def measure(root, candidate, session=None):
    if root is None or not isinstance(candidate, dict):
        return result('UNKNOWN', code='CANDIDATE_INPUT_FILES_REQUIRED')
    for key in ('artifactId', 'revision'):
        if not isinstance(candidate.get(key), str) or not candidate[key].strip():
            return result('UNKNOWN', code='CANDIDATE_INCOMPLETE')
    session = session or ReadSession()
    session.begin()
    records, digests = {}, {}
    try:
        for field, digest_field in GROUPS.items():
            items = candidate.get(field)
            if not isinstance(items, list) or (field != 'dependencyFiles' and not items):
                return result('UNKNOWN', code='CANDIDATE_INPUT_FILES_REQUIRED', field=field)
            paths, entries = set(), []
            for item in items:
                raw = item.get('path') if isinstance(item, dict) else item
                path = project_path(raw, root)
                relative = path.relative_to(Path(root).resolve()).as_posix()
                if relative in paths:
                    return result('FAILED', code='CANDIDATE_DUPLICATE_INPUT', path=relative)
                paths.add(relative)
                entries.append({'path': relative, 'sha256': hashlib.sha256(session.read_bytes(path)).hexdigest()})
            records[field] = sorted(entries, key=lambda x: x['path'])
            digests[digest_field] = digest_manifest(entries)
        session.verify()
    except (OSError, ValueError, TypeError) as exc:
        return result('UNKNOWN', code='CANDIDATE_INPUT_READ_FAILED', diagnostic=str(exc))
    for field, actual in digests.items():
        expected = candidate.get(field)
        if not is_digest(expected):
            return result('UNKNOWN', code='CANDIDATE_DIGEST_REQUIRED', field=field)
        if expected != actual:
            return result('FAILED', code='CANDIDATE_INPUT_DRIFT', field=field, expected=expected, actual=actual)
    verification = candidate.get('verification')
    if not isinstance(verification, dict) or any(verification.get(k) != v for k, v in digests.items()):
        return result('FAILED', code='CANDIDATE_VERIFICATION_MISMATCH')
    return result('PASSED', inputs=records, **digests,
                  measurements=session.metrics(), authority='DERIVED_NO_GATE')


def descriptor(candidate, *, root=None):
    """Bind identity and digests to canonical project-relative input paths."""
    import copy
    value = copy.deepcopy({key: candidate[key] for key in (
        'artifactId', 'revision', *GROUPS, *GROUPS.values(), 'verification')})
    for group in GROUPS:
        if not isinstance(value[group], list):
            raise ValueError('input paths must be a list')
        paths = []
        for item in value[group]:
            raw = item.get('path') if isinstance(item, dict) else item
            if not isinstance(raw, str) or not raw.strip() or Path(raw).is_absolute():
                raise ValueError('RELATIVE_PATH_REQUIRED')
            if root is not None:
                path = project_path(raw, root).relative_to(Path(root).resolve()).as_posix()
            else:
                path = posixpath.normpath(Path(raw).as_posix())
                if path == '..' or path.startswith('../'):
                    raise ValueError('REFERENCE_OUTSIDE_ROOT')
            if path in paths:
                raise ValueError('CANDIDATE_DUPLICATE_INPUT')
            paths.append(path)
        value[group] = sorted(paths)
    return value
