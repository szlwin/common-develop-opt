"""Anonymous declaration and source fixtures shared by behavior and I/O tests."""
import json
from pathlib import Path
from artifact_identity import identity


def make_project(root, count=4, history=0):
    root = Path(root)
    (root/'current').mkdir(parents=True,exist_ok=True)
    (root/'data').mkdir(parents=True,exist_ok=True)
    records=[]
    for index in range(count):
        key=f'ART-{index:05d}'
        kind=['requirement','model','flow','impact'][index % 4]
        (root/'data'/f'{index}.json').write_text(json.dumps({'id':key,'artifactType':kind,'revision':'R1','text':'anonymous source'}))
        spec={'artifactId':key,'artifactType':kind,'revision':'R1','category':'WORKING_SOURCE',
              'lifecycleBinding':{'task':'TASK-EXAMPLE','phase':'analysis','iteration':'ITERATION-1'},
              'inputs':[{'path':f'data/{index}.json'}], 'dependencies':[]}
        if index:
            spec['dependencies']=[{'artifactId':records[-1]['artifactId'],'kind':'consumes','revision':'R1',
                                   'contentDigest':records[-1]['contentDigest'], 'reference':f'../data/{index-1}.json#{records[-1]["artifactId"]}'}]
        (root/'current'/f'{index}.artifact.json').write_text(json.dumps(spec))
        records.append(identity(spec,root)['artifact'])
    if history:
        (root/'history').mkdir(exist_ok=True)
        for index in range(history):
            (root/'history'/f'{index}.artifact.json').write_text('{"invalid old history":'+'x'*4096)
    return {'schemaVersion':1,'roots':['current'],'requiredArtifactIds':[r['artifactId'] for r in records]}, records
