"""Development identity and release readiness are different assertions."""
import json 
from pathlib import Path


def validate_identity(root, *, profile='development'):
    errors = []
    try:
        package = json.loads((root / 'assets/package.json').read_text())
        release = json.loads((root / 'assets/package/release-contract.json').read_text())
        runtime = json.loads((root / 'assets/runtime/runtime-contract.json').read_text())
        if release.get('packageName') != package.get('name') or release.get('packageVersion') != package.get('version'):
            errors.append('PACKAGE_IDENTITY_MISMATCH')
        if release.get('runtimeCompatibilityVersion') != runtime.get('packageVersion'):
            errors.append('RUNTIME_COMPATIBILITY_VERSION_MISMATCH')
        stage = release.get('releaseStage')
        if stage not in ('DEVELOPMENT', 'STABLE'):
            errors.append('INVALID_RELEASE_STAGE')
        if stage == 'DEVELOPMENT' and (release.get('baselineTag') is not None or release.get('baselineMode') != 'PACKAGE_MANIFEST'):
            errors.append('DEVELOPMENT_BASELINE_INVALID')
        if stage == 'STABLE' and not release.get('baselineTag'):
            errors.append('RELEASE_BASELINE_REQUIRED')
        changelog = root / 'CHANGELOG.md'
        if not changelog.is_file() or f"## {package['version']} {stage}" not in changelog.read_text():
            errors.append('PACKAGE_CHANGELOG_MISSING')
        if profile == 'release' and stage != 'STABLE':
            errors.append('RELEASE_NOT_PREPARED')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f'PACKAGE_IDENTITY_INVALID: {exc}')
    return errors
