"""Measure real-file scoped discovery with cold and warm application caches."""
from pathlib import Path
import argparse
import hashlib
import json
import statistics
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from consistency_fixture import make_project
from discover_dependencies import discover
from read_session import ReadSession
from diagnostic_io import finish_cli, result


def semantic(observation):
    return {key:observation[key] for key in ('status','graph','identities','diagnostics','analysis','coverage')}


def benchmark(count,history,repetitions):
    samples=[]
    with tempfile.TemporaryDirectory(prefix='consistency-history-') as directory:
        root=Path(directory).resolve()
        profile,records=make_project(root,count,history)
        expected_files=count*2
        expected_bytes=sum(p.stat().st_size for d in ('current','data') for p in (root/d).iterdir())
        history_bytes=sum(p.stat().st_size for p in (root/'history').iterdir()) if history else 0
        baseline=None
        for iteration in range(repetitions):
            session=ReadSession()
            started=time.perf_counter();cold=discover(root,profile,records[0]['artifactId'],session=session);cold_ms=(time.perf_counter()-started)*1000
            previous=session.metrics()
            started=time.perf_counter();warm=discover(root,profile,records[0]['artifactId'],session=session);warm_ms=(time.perf_counter()-started)*1000
            previous_value=semantic(cold)
            if baseline is None:baseline=previous_value
            correct=(previous_value==baseline==semantic(warm) and cold['status']=='PASSED'
                     and len(cold['analysis']['impact'])==count-1
                     and previous['filesRead']==expected_files and previous['bytesRead']==expected_bytes
                     and session.files_read==previous['filesRead'] and session.bytes_read==previous['bytesRead'])
            samples.append({'iteration':iteration+1,'status':'PASSED' if correct else 'FAILED',
                            'coldMs':round(cold_ms,3),'warmMs':round(warm_ms,3),
                            'coldReads':previous,'warmAdditionalFilesRead':session.files_read-previous['filesRead'],
                            'warmAdditionalBytesRead':session.bytes_read-previous['bytesRead']})
        cold_median=statistics.median(x['coldMs'] for x in samples)
        warm_median=statistics.median(x['warmMs'] for x in samples)
        ratio=round(warm_median/cold_median,3) if cold_median else None
        timing='REQUIRES_INVESTIGATION' if ratio and ratio>2 else 'NO_LARGE_WARM_REGRESSION_OBSERVED'
        package=Path(__file__).resolve().parents[1]
        return result('PASSED' if all(x['status']=='PASSED' for x in samples) else 'FAILED',
                      authority='OBSERVATION_ONLY',samples=samples,
                      files={'activeArtifacts':count,'activeFiles':expected_files,'activeBytes':expected_bytes,
                             'excludedHistoryFiles':history,'excludedHistoryBytes':history_bytes},
                      timing={'coldMedianMs':cold_median,'warmMedianMs':warm_median,'warmColdRatio':ratio,'assessment':timing},
                      sourceDigests={p.relative_to(package).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                                     for folder in ('scripts','assets','tests') for p in sorted((package/folder).rglob('*'))
                                     if p.is_file() and '__pycache__' not in p.parts},
                      limitations=['Cold means empty application read cache, not flushed OS disk cache.',
                                   'Both passes perform schema/reference/graph checks; warm reuse skips only unchanged byte reads.',
                                   'Scoped declaration profile excludes history; this is not a benchmark of arbitrary legacy layout discovery.',
                                   'Timing is observational; fixed correctness and read-count assertions do not depend on measured latency.'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts',type=int,default=400)
    parser.add_argument('--history',type=int,default=2000)
    parser.add_argument('--repetitions',type=int,default=3)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if not 2<=args.artifacts<=3000 or not 0<=args.history<=10000 or not 1<=args.repetitions<=10:
        parser.error('artifacts 2..3000, history 0..10000, repetitions 1..10')
    return finish_cli(lambda:benchmark(args.artifacts,args.history,args.repetitions),args.output,[])

if __name__=='__main__':raise SystemExit(main())
