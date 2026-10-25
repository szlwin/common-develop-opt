"""Thin CLI adapter that consumes application_services functions."""
import argparse, json
from application_services import seal_candidate, submit_review
from diagnostic_io import load_json

def build_parser():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='command',required=True)
    seal=sub.add_parser('seal'); seal.add_argument('--root',required=True); seal.add_argument('--candidate',required=True); seal.add_argument('--archive',required=True); seal.add_argument('--review',required=True)
    rev=sub.add_parser('review'); rev.add_argument('--archive',required=True); rev.add_argument('--input',required=True); rev.add_argument('--current',required=True); rev.add_argument('--root',required=True); rev.add_argument('--output',required=True); rev.add_argument('--decision',required=True)
    for command in (seal, rev):
        command.add_argument('-g', '--agent', default='ProjectManagerAgent')
    return p

def main():
    a=build_parser().parse_args()
    if a.command=='seal': value=seal_candidate(a.root,load_json(a.candidate),a.archive,a.review, agent=a.agent)
    else: value=submit_review(load_json(a.archive),load_json(a.input),a.output,a.decision, current=load_json(a.current), root=a.root, protected=[a.archive, a.input, a.current], agent=a.agent)
    print(json.dumps(value,ensure_ascii=False,indent=2)); return 0 if value.get('status')=='PASSED' else 2
if __name__=='__main__': raise SystemExit(main())
