"""Immutable attempt outputs and atomic success pointers, bound to decision inputs."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import time
import uuid
from .audit import audit
from .io import load_hospital, write_json
from .schema import load_bundle, digest


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def decision_identity(root, hospitals):
    paths=list((root/'src/insurance_audit').glob('*.py'))
    for h in hospitals:
        bp=root/f'contracts/{h}.bundle.json';paths.append(bp)
        bundle=json.loads(bp.read_text())
        paths.extend(root/p for p in bundle['artifacts'])
        paths.extend(root/p for p in bundle['source_hashes'])
        paths.extend((root/'data/source/invoices').glob(f'hospital_{h[1:]}_*.csv'))
    policy=root/'evaluation/confidence_policy.json'
    if policy.exists():paths.append(policy)
    return {'hospitals':sorted(hospitals),'python':platform.python_version(),
            'files':{str(p.relative_to(root)):digest(p) for p in sorted(set(paths)) if p.is_file()}}


def verify_accounting(result,data):
    a=result['accounting'];ids=data.invoice_identities()
    emitted=[r['invoice_id'] for r in result['opinions']+result['abstentions']]
    if len(emitted)!=len(set(emitted)) or set(emitted)!=ids:
        raise ValueError('Invoice accounting failure: each recoverable header identity requires exactly one disposition')
    if a['context_records']!=a['accepted_line_records']+sum(d['kind']=='line_items' and d['status']=='quarantined' for d in data.dispositions):
        raise ValueError('Context lost or multiplied raw line occurrences')
    if a['raw_records']!=a['accepted_invoice_records']+a['accepted_line_records']+a['quarantined_records']:
        raise ValueError('Raw record accounting failure')


def run_audit(root,hospitals,output_root=None,inject_failure=False):
    root=Path(root).resolve();hospitals=sorted(set(hospitals))
    output_root=Path(output_root) if output_root else root/'runs'
    attempt=uuid.uuid4().hex;directory=output_root/'attempts'/attempt
    directory.mkdir(parents=True)
    start=time.perf_counter();status={'attempt':attempt,'status':'running','hospitals':hospitals,
        'started_utc':datetime.now(timezone.utc).isoformat(),'identity':None}
    write_json(directory/'status.json',status)
    try:
        # Validate finite schemas and supported semantics; hashes are provenance only.
        bundles={h:load_bundle(root,h) for h in hospitals}
        identity=decision_identity(root,hospitals);status['identity']=identity;status['run_id']=canonical_hash(identity)
        results={}
        for h in hospitals:
            contract,maps,_=bundles[h]
            data=load_hospital(root/'data/source',h);result=audit(data,contract,maps)
            verify_accounting(result,data)
            policy_path=root/'evaluation/confidence_policy.json'
            if policy_path.exists():
                from .confidence import assign_confidence
                assign_confidence(result,json.loads(policy_path.read_text()))
            write_json(directory/f'{h}.json',result);write_json(directory/f'{h}.input_quality.json',data.quality())
            results[h]={k:result[k] for k in ['hospital','accounting']}
            if inject_failure:raise RuntimeError('Controlled failure after a staged hospital result; promotion must not happen')
        status.update(status='success',results=results,elapsed_seconds=round(time.perf_counter()-start,6),
                      finished_utc=datetime.now(timezone.utc).isoformat(),
                      outputs={p.name:digest(p) for p in sorted(directory.glob('*.json')) if p.name!='status.json'})
        write_json(directory/'status.json',status)
        key='-'.join(hospitals);pointer={'attempt':attempt,'run_id':status['run_id'],'status':'success',
                                      'path':str(directory.relative_to(output_root))}
        temp=output_root/f'.current-{key}-{attempt}.json';write_json(temp,pointer)
        os.replace(temp,output_root/f'current-{key}.json')
        return directory,status
    except Exception as error:
        status.update(status='failed',error_type=type(error).__name__,error=str(error),
                      elapsed_seconds=round(time.perf_counter()-start,6),finished_utc=datetime.now(timezone.utc).isoformat())
        write_json(directory/'status.json',status)
        raise


def current_run(root,hospitals,output_root=None):
    root=Path(root).resolve();output_root=Path(output_root) if output_root else root/'runs'
    pointer=json.loads((output_root/f"current-{'-'.join(sorted(hospitals))}.json").read_text())
    directory=output_root/pointer['path'];status=json.loads((directory/'status.json').read_text())
    if status['status']!='success' or status['hospitals']!=sorted(set(hospitals)) or pointer['attempt']!=status['attempt']:
        raise ValueError('Missing successful result for the requested hospitals; rerun audit')
    # No recorded fingerprint rejects a new batch or Python patch version.
    # Validate usable result structure; export separately reconciles current raw data.
    for hospital in hospitals:
        result=json.loads((directory/f'{hospital}.json').read_text())
        if result.get('hospital')!=hospital or not all(isinstance(result.get(k),list) for k in ('opinions','abstentions','traces')) or not isinstance(result.get('accounting'),dict):
            raise ValueError('Successful output modified or invalid: '+hospital)
    return directory,status
