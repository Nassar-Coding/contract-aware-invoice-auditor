"""Public, standard-library command-line interface."""
import argparse
import json
from pathlib import Path
from .batch import run_audit
from .io import load_hospital,write_json
from .inventory import inventory


def main():
    parser=argparse.ArgumentParser(description='Contract-bound deterministic invoice audit')
    parser.add_argument('--project',type=Path,default=Path('.'))
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ['inventory','audit']:
        p=sub.add_parser(name);p.add_argument('--hospitals',nargs='+',choices=[f'H{i}' for i in range(1,6)],required=True)
    p=sub.add_parser('evaluate');p.add_argument('--partition',choices=['development','check','full'],required=True)
    for name in ('export','verify-results'):sub.add_parser(name)
    sub.add_parser('reproduce').add_argument('--evaluate-development',action='store_true')
    args=parser.parse_args();root=args.project.resolve()
    if args.command=='audit':
        path,status=run_audit(root,args.hospitals)
        print(json.dumps({'path':str(path),'run_id':status['run_id'],'results':status['results']},indent=2))
    elif args.command=='inventory':
        for h in args.hospitals:
            data=load_hospital(root/'data/source',h)
            write_json(root/f'reports/inventory_{h}.json',inventory(data))
            write_json(root/f'reports/input_quality_{h}.json',data.quality())
            print(h,len(data.invoices),len(data.lines))
    elif args.command=='evaluate':
        from .evaluation import evaluate_run
        evaluate_run(root,args.partition)
    elif args.command in {'export','verify-results'}:
        from .results import export_results,verify_results
        result=(export_results if args.command=='export' else verify_results)(root)
        print(json.dumps({k:result[k] for k in ('release_id','rows','results_sha256')},indent=2))
    elif args.command=='reproduce':
        from .pipeline import reproduce
        print(json.dumps(reproduce(root,evaluate_development=args.evaluate_development),indent=2))


if __name__=='__main__':main()
