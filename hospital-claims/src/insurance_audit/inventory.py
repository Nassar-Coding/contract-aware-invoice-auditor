"""Label-free input and description evidence; no pricing conclusions."""
from collections import defaultdict
import hashlib
from pathlib import Path
from .io import Loaded, load_hospital, write_json


def inventory(data: Loaded) -> dict:
    descriptions = defaultdict(list)
    invoice_lines = defaultdict(list)
    for line in data.lines:
        descriptions[line.description].append(line)
        invoice_lines[line.invoice_id].append(line)
    return {'hospital':data.hospital,'unique_descriptions':len(descriptions),
            'descriptions':[{'description':key,'count':len(rows),'units':sorted({r.unit_basis_as_billed for r in rows}),
                             'invoice_ids':sorted({r.invoice_id for r in rows}),'line_ids':sorted({r.line_id for r in rows})}
                            for key,rows in sorted(descriptions.items())],
            'invoice_line_counts':{key:len(rows) for key,rows in sorted(invoice_lines.items())},
            'scope_status':'Descriptive inventory only; no pricing conclusions.'}


def split_h1(data: Loaded) -> dict:
    if data.hospital!='H1':
        raise ValueError('Only H1 has an evaluation split')
    parent={r.patient_id:r.patient_id for r in data.invoices}
    def find(p):
        while parent[p]!=p:
            parent[p]=parent[parent[p]];p=parent[p]
        return p
    def union(a,b):
        a,b=find(a),find(b)
        parent[max(a,b)]=min(a,b)
    by_id=defaultdict(list)
    for row in data.invoices:by_id[row.invoice_id].append(row.patient_id)
    for patients in by_id.values():
        for p in patients[1:]:union(patients[0],p)
    groups=defaultdict(list)
    for p in sorted(parent):groups[find(p)].append(p)
    patients={}
    for group in groups.values():
        signature='h1-patient-split-v1|'+'|'.join(group)
        partition='development' if int(hashlib.sha256(signature.encode()).hexdigest(),16)%10<7 else 'check'
        for p in group:patients[p]={'partition':partition,'group':group}
    invoices={k:patients[v[0]]['partition'] for k,v in sorted(by_id.items())}
    return {'algorithm':'sha256(h1-patient-split-v1|sorted connected patient IDs) mod 10; <7 development',
            'input_hashes':data.source_hashes,'patient_groups':patients,'invoice_partitions':invoices,
            'counts':{part:sum(v==part for v in invoices.values()) for part in ['development','check']},
            'label_values_accessed':False,'limitations':['Shared unlabelled contract-wide history remains; groups are not statistically independent.',
            'Label structure and counts were inspected before any label-fitted logic was written.']}


def build_inventory(project: Path) -> dict:
    reports={}
    for hospital in ['H1','H2','H3','H4','H5']:
        data=load_hospital(project/'data/source',hospital)
        reports[hospital]=data.quality()
        write_json(project/f'reports/inventory_{hospital}.json',inventory(data))
        if hospital=='H1':write_json(project/'tests/evaluation/split_manifest.json',split_h1(data))
    write_json(project/'reports/input_quality.json',reports)
    return reports
