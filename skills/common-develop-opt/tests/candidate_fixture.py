import hashlib, json
from pathlib import Path

def candidate(root, artifact='F', revision='R1', content='source'):
    root=Path(root); paths=[]
    for name, value in [('source.json',content),('dependency.json','dependency'),('evidence.json','evidence')]:
        p=root/name; p.write_text(value,encoding='utf-8'); paths.append(p)
    def digest(items):
        return hashlib.sha256(json.dumps(sorted(items,key=lambda x:x['path']),sort_keys=True,separators=(',',':')).encode()).hexdigest()
    groups={k:[{'path':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}] for k,p in zip(('sourceFiles','dependencyFiles','evidenceFiles'),paths)}
    values={k:digest(v) for k,v in groups.items()}
    return {'artifactId':artifact,'revision':revision,'contentDigest':values['sourceFiles'],
            'dependencyManifestDigest':values['dependencyFiles'],'evidenceDigest':values['evidenceFiles'],
            'sourceFiles':groups['sourceFiles'],'dependencyFiles':groups['dependencyFiles'],'evidenceFiles':groups['evidenceFiles'],
            'status':'READY','verification':{'contentDigest':values['sourceFiles'],'dependencyManifestDigest':values['dependencyFiles'],'evidenceDigest':values['evidenceFiles']}}
