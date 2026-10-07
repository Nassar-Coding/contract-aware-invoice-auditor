"""Label-free stress test: unseen invoice IDs, patient sampling and description drift.

Runs the public, unmodified reproduce command on temporary inputs for all hospitals.
It measures execution/schema survival, not detection quality.
"""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
CODE=re.compile(r'/(?:NG|RM|SA|CW|PH)-\d+',re.I)
ABBREVIATIONS={'procedure':'proc','routine':'rtn','advanced':'adv','standard':'std',
               'extended':'ext','service':'svc','visit':'vst','laboratory':'lab',
               'outpatient':'outpt','emergency':'emer','session':'sess'}
ABBREVIATIONS.update({v:k for k,v in list(ABBREVIATIONS.items())})


def read_csv(path):
    with path.open(newline='',encoding='utf-8-sig') as stream:
        reader=csv.DictReader(stream);return reader.fieldnames,list(reader)


def write_csv(path,fields,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fields,lineterminator='\n');writer.writeheader();writer.writerows(rows)


def sample_patients(headers,rng):
    """Keep reused-ID connected patients together, preserving all physical headers."""
    parent={r['patient_id']:r['patient_id'] for r in headers}
    def find(k):
        while parent[k]!=k:
            parent[k]=parent[parent[k]];k=parent[k]
        return k
    owners=defaultdict(list)
    for r in headers:owners[r['invoice_id']].append(r['patient_id'])
    for values in owners.values():
        for p in values[1:]:parent[find(p)]=find(values[0])
    groups=defaultdict(set)
    for p in parent:groups[find(p)].add(p)
    groups=sorted(groups.values(),key=lambda g:sorted(g));rng.shuffle(groups)
    target=round(len(parent)*.7);chosen=set()
    for group in groups:
        if len(chosen)+len(group)<=target:chosen.update(group)
    return chosen,len(parent),target


def perturb(text,keep_code,index):
    codes=CODE.findall(text);words=CODE.sub('',text).strip();actions=[]
    # Meaning-preserving spelling changes plus deliberate non-code one-character noise.
    words=words.swapcase();actions.append('case')
    tokens=re.findall(r'[A-Za-z]+|[^A-Za-z\s]+',words)
    swapped=False
    for n,t in enumerate(tokens):
        if t.lower() in ABBREVIATIONS:
            tokens[n]=ABBREVIATIONS[t.lower()];swapped=True;break
    if swapped:actions.append('abbreviation')
    if len(tokens)>1:
        midpoint=max(1,len(tokens)//2);tokens=tokens[midpoint:]+['-']+tokens[:midpoint];actions.append('separator_token_order')
    for n,t in enumerate(tokens):
        if t.isalpha() and len(t)>2:
            at=len(t)//2;tokens[n]=t[:at]+('x' if t[at].lower()!='x' else 'z')+t[at+1:];actions.append('one_character_typo');break
    separator='   ' if index%2 else ' '
    changed=separator.join(tokens);actions.append('whitespace')
    if keep_code and codes:changed+=' '+ ' '.join(codes)
    return changed,actions,codes


def build_inputs(source,destination,seed):
    rng=random.Random(seed);summaries={}
    for folder in ['src','contracts','mappings']:
        shutil.copytree(source/folder,destination/folder,ignore=shutil.ignore_patterns('__pycache__'))
    (destination/'evaluation').mkdir()
    shutil.copy2(source/'evaluation/confidence_policy.json',destination/'evaluation/confidence_policy.json')
    shutil.copytree(source/'data/source/contracts',destination/'data/source/contracts')
    for name in ['requirements.txt','.python-version']:
        if (source/name).exists():shutil.copy2(source/name,destination/name)
    shutil.copy2(source/'data/source/submission_template.csv',destination/'data/source/submission_template.csv')
    for h in range(1,6):
        header_path=Path(f'data/source/invoices/hospital_{h}_invoices.csv');line_path=Path(f'data/source/invoices/hospital_{h}_line_items.csv')
        hf,headers=read_csv(source/header_path);lf,lines=read_csv(source/line_path)
        patients,total,target=sample_patients(headers,rng)
        kept=[r.copy() for r in headers if r['patient_id'] in patients];ids={r['invoice_id'] for r in kept}
        assert all((r['patient_id'] in patients)==(r['invoice_id'] in ids) for r in headers),'Reused-ID component was split'
        selected=[r.copy() for r in lines if r['invoice_id'] in ids]
        numbers=list(range(900000,900000+len(ids)));rng.shuffle(numbers)
        mapping={old:f'UNSEEN{h}-{number}' for old,number in zip(sorted(ids),numbers)}
        for r in kept+selected:r['invoice_id']=mapping[r['invoice_id']]
        indices=rng.sample(range(len(selected)),round(len(selected)*.3));kept_code=set(indices[:len(indices)//2]);counts=defaultdict(int);with_codes=0;removed_codes=0
        for i in indices:
            original=selected[i]['description'];new,actions,codes=perturb(original,i in kept_code,i)
            assert new!=original
            assert CODE.findall(new)==(codes if i in kept_code else [])
            selected[i]['description']=new
            for action in actions:counts[action]+=1
            with_codes+=bool(codes);removed_codes+=bool(codes) and i not in kept_code
        rng.shuffle(kept);rng.shuffle(selected)
        write_csv(destination/header_path,hf,kept);write_csv(destination/line_path,lf,selected)
        summaries[f'H{h}']={'original_patients':total,'sample_target':target,'selected_patients':len(patients),
            'sample_fraction':len(patients)/total if total else 0,'all_selected_patient_history_retained':True,
            'raw_headers':len(kept),'unique_invoice_ids':len(ids),'lines':len(selected),
            'all_invoice_ids_replaced':not bool(set(mapping.values())&ids),'line_links_valid':all(r['invoice_id'] in set(mapping.values()) for r in selected),
            'perturbed_descriptions':len(indices),'perturbed_fraction':len(indices)/len(selected) if selected else 0,
            'code_policy_keep':len(kept_code),'code_policy_remove':len(indices)-len(kept_code),
            'perturbed_originally_with_codes':with_codes,'codes_actually_removed':removed_codes,'mutation_counts':dict(counts)}
    return summaries


def run(project,python,seed,report):
    with tempfile.TemporaryDirectory(prefix='insurance-generalization-') as temporary:
        root=Path(temporary)/'project';root.mkdir();summaries=build_inputs(project,root,seed)
        command=[python,'-m','insurance_audit','--project',str(root),'reproduce']
        import os
        env={**os.environ,'PYTHONPATH':str(root/'src'),'PYTHONNOUSERSITE':'1'}
        process=subprocess.run(command,cwd=root,env=env,text=True,capture_output=True)
        report.parent.mkdir(parents=True,exist_ok=True)
        report.with_suffix('.log').write_text(process.stdout+process.stderr)
        result={'seed':seed,'python':python,'command':'python -m insurance_audit --project <temporary-project> reproduce',
            'returncode':process.returncode,'hospitals':summaries,'labels_and_split_present':False,
            'scope':'Survival/schema test. No held-out evaluation or recall tolerance measured.'}
        if process.returncode==0:
            sys.path.insert(0,str(project/'src'))
            from insurance_audit.batch import current_run
            from insurance_audit.io import load_hospital
            from insurance_audit.results import TARGETS,validate_result,verify_results
            policy=json.loads((root/'evaluation/confidence_policy.json').read_text())
            for group in [['H1'],TARGETS]:
                directory,_=current_run(root,group)
                for h in group:
                    output=json.loads((directory/f'{h}.json').read_text());validate_result(output,load_hospital(root/'data/source',h),policy)
                    summaries[h].update(schema_valid=True,opinions=len(output['opinions']),flags=sum(r['flagged'] for r in output['opinions']),withheld=len(output['abstentions']))
            manifest=verify_results(root);result.update(status='passed',result_rows=manifest['rows'],results_sha256=manifest['results_sha256'])
        else:result['status']='failed'
        report.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,default=ROOT)
    parser.add_argument('--python',default=sys.executable)
    parser.add_argument('--seed',type=int,default=20260919)
    parser.add_argument('--report',type=Path,default=ROOT/'reports/generalize.json')
    args=parser.parse_args();result=run(args.project.resolve(),args.python,args.seed,args.report.resolve())
    print(json.dumps(result,indent=2));sys.exit(0 if result['status']=='passed' else 1)
