"""Replay reviewed artifacts without model calls or implicit label evaluation."""
import contextlib
import io
from pathlib import Path
import platform
import time
import warnings
from .batch import run_audit
from .evaluation import evaluate_run
from .io import write_json
from .reporting import render_reports
from .results import TARGETS, export_results


def reproduce(root, evaluate_development=False):
    root=Path(root).resolve();start=time.perf_counter()
    if platform.python_version()!='3.12.13':
        warnings.warn(f'Reference runtime is Python 3.12.13; running {platform.python_version()}. Supported range: >=3.12,<3.13.',RuntimeWarning)
    h1,h1_status=run_audit(root,['H1'])
    target,target_status=run_audit(root,TARGETS)
    manifest=export_results(root)
    evaluations={};evaluation={'status':'not_requested','check_partition_read':False}
    if evaluate_development:
        if not (root/'data/source/labels/hospital_1_labels.csv').is_file() or not (root/'tests/evaluation/split_manifest.json').is_file():
            evaluation['status']='skipped_missing_labels_or_manifest'
        else:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    evaluations['development']=evaluate_run(root,'development')
                evaluation['status']='development_completed'
            except Exception as exc:
                warnings.warn(f'Optional development evaluation failed: {type(exc).__name__}: {exc}',RuntimeWarning)
                evaluation.update(status='failed_nonfatal',error=f'{type(exc).__name__}: {exc}')
    workload=render_reports(root,evaluations)
    execution={'status':'success','python':platform.python_version(),'elapsed_seconds':round(time.perf_counter()-start,6),
        'h1_attempt':h1_status['attempt'],'target_attempt':target_status['attempt'],
        'hospital_opinions':{h:w['opinions'] for h,w in workload.items()},
        'result_rows':manifest['rows'],'results_sha256':manifest['results_sha256'],
        'network_calls':0,'model_calls':0,'evaluation':evaluation,
        'scope':'prediction replay; optional development-only evaluation; no fresh LLM extraction'}
    write_json(root/'reports/execution.json',execution)
    return execution
