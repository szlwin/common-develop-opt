from pathlib import Path
import json


def audit(package):
    package = Path(package)
    authority = json.loads((package/'references/authority-map.json').read_text())
    errors=[]
    for key, value in authority['rules'].items():
        if value == 'assets/**/*.schema.json':
            if not [p for p in (package/'assets').rglob('*.schema.json') if not p.is_symlink()]: errors.append({'kind':'missing-authority','key':key,'value':value})
        elif key != 'lifecycle_state' and not (package/value).exists() and not value.startswith('scripts --'):
            errors.append({'kind':'missing-authority','key':key,'value':value})
    for path in [package/'SKILL.md', package/'README.md', package/'references/fault-codes.md']:
        if not path.is_file(): errors.append({'kind':'missing-doc','path':str(path)})
    return {'schemaVersion':1,'authority':'DOCUMENTATION_AUDIT','status':'PASSED' if not errors else 'FAILED','errors':errors}


if __name__ == '__main__':
    import sys
    print(json.dumps(audit(sys.argv[1]), ensure_ascii=False, indent=2))
