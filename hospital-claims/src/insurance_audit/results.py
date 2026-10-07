"""Validate complete target opinions, stage the CSV, and promote it with an atomic local pointer update.

This module never imports the evaluator or opens label files.
"""
from collections import Counter, defaultdict
import csv
import io
import json
import math
import os
from pathlib import Path
import uuid
from .batch import canonical_hash, current_run, decision_identity, verify_accounting
from .confidence import choose
from .io import integer, load_hospital, write_json
from .schema import digest

FIELDS=('invoice_id','flagged','error_category','expected_total_cents','billed_total_cents','confidence')
TARGETS=['H2','H3','H4','H5']


def ensure(condition,message):
    if not condition:raise ValueError(message)


def canonical(headers):
    """Latest-dated physical header record; the documented choice for a reused identifier."""
    return max(headers,key=lambda h:(h.invoice_date,h.source,h.source_row))


def validate_result(result,data,policy):
    """Reconcile every opinion with the original rows and its complete line trace.

    A complete audit must price every line. A finding row may leave lines
    unresolved, so it is reconciled against the physical records instead: every
    physical line of the invoice contributes exactly once to the emitted amount.
    """
    verify_accounting(result,data)
    headers=defaultdict(list);lines=defaultdict(list);raw_lines=defaultdict(list)
    for header in data.invoices:headers[header.invoice_id].append(header)
    for line in data.lines:lines[line.invoice_id].append(line)
    for record in data.dispositions:
        if record['kind']=='line_items':
            ident=record.get('invoice_id')
            if isinstance(ident,str) and ident.strip():raw_lines[ident].append(record)
    traces={t['invoice_id']:t for t in result['traces']}
    ensure(len(traces)==len(result['traces']) and set(traces)==data.invoice_identities(),'Trace identity accounting failure')
    quarantined={d.get('invoice_id') for d in data.dispositions if d['status']=='quarantined'}
    unlinked_headers=[d for d in data.quarantined_headers() if not isinstance(d.get('invoice_id'),str) or not d['invoice_id'].strip()]
    complete=findings=0
    for row in result['opinions']:
        ident=row['invoice_id'];hs=headers[ident];trace=traces[ident]
        basis=row.get('decision_basis')
        ensure(basis in {'complete_audit','finding'},'Unknown decision basis')
        ensure(hs,'Emitted invoice without an accepted header record')
        ensure(row['hospital']==data.hospital,'Cross-hospital output')
        ensure(type(row['flagged']) is int and row['flagged'] in {0,1},'Invalid flag')
        ensure(isinstance(row['error_category'],str) and bool(row['error_category'])==bool(row['flagged']),'Flag/category mismatch')
        for field in ('expected_total_cents','billed_total_cents'):
            ensure(type(row[field]) is int and abs(row[field])<=10**15,'Unsupported monetary output')
        ensure(row['expected_total_cents']>=0,'Negative corrected amount unsupported')
        ensure(row['billed_total_cents']==canonical(hs).invoice_total_cents,'Billed total differs from the canonical source record')
        ensure(trace['status']=='supported' and trace['opinion']==row,'Incomplete or mismatched invoice trace')
        if basis=='complete_audit':
            complete+=1
            ensure(len(hs)==1,'Conflicting invoice identity cannot be completely audited')
            ensure(ident not in quarantined and not unlinked_headers,'Unresolved required source record cannot be completely audited')
            raw={(l.source,l.source_row):l for l in lines[ident]}
            evidence={(l['source'],l['source_row']):l for l in trace['lines']}
            ensure(raw and len(evidence)==len(trace['lines']) and set(evidence)==set(raw),'Partial/duplicated line trace')
            total=0
            for key,line in raw.items():
                item=evidence[key]
                # A line is resolved either because one service was identified and
                # priced, or because every candidate reading of its wording reached
                # the same verdict. The second has no single service to cite, but it
                # is no less resolved.
                if item['status']=='supported_under_every_reading':
                    ensure(item.get('every_reading') and item.get('result'),'Every-reading line without its readings')
                else:
                    ensure(item['status']=='supported' and item['service_id'] and item['source_refs'],'Unresolved required line')
                for field in ('line_id','description','quantity'):
                    ensure(item[field]==getattr(line,field),'Line provenance mismatch: '+field)
                ensure(item['billed_line_total_cents']==line.line_total_cents and item['billed_unit_price_cents']==line.unit_price_cents,'Billed line provenance mismatch')
                amount=item['result']['expected_total_cents']
                ensure(type(amount) is int,'Non-integer line correction')
                total+=amount
            ensure(total==row['expected_total_cents'],'Expected invoice total does not reconcile to all lines')
        else:
            findings+=1
            ensure(row['flagged']==1 and trace['findings'] and trace['finding_evidence'],'A finding row needs named evidence')
            ensure(set(trace['findings'])<=set(row['error_category'].split(';')),'Finding absent from the emitted category')
            physical={(l.source,l.source_row) for l in lines[ident]}|{(r['source'],r['source_row']) for r in raw_lines[ident]}
            contributions=trace['amount_contributions']
            if row['amount_basis'] in {'reused_identifier_canonical_billed_total','unrecoverable_line_amount_billed_total'}:
                ensure(not contributions and row['expected_total_cents']==row['billed_total_cents'],'Unreconciled fallback amount')
            else:
                keys=[(c['source'],c['source_row']) for c in contributions]
                ensure(len(keys)==len(set(keys)),'A line may contribute only once')
                if row['amount_basis'].startswith('attributed_'):
                    # A reused identifier's lines are split between its physical
                    # records, so only the canonical record's own lines contribute.
                    ensure(set(keys)<=physical and keys,'Attributed lines must come from this identity')
                else:
                    ensure(set(keys)==physical,'Every physical line must contribute exactly once')
                ensure(all(type(c['contribution_cents']) is int for c in contributions),'Non-integer line contribution')
                ensure(sum(c['contribution_cents'] for c in contributions)==row['expected_total_cents'],'Emitted amount does not reconcile to the physical lines')
            if row['expected_total_cents']==row['billed_total_cents']:
                ensure(row['amount_unchanged_reason'],'An unchanged amount on a flagged row needs a recorded reason')
        value,tier=choose(row,policy)
        ensure(value is not None and row['confidence']==value and row['confidence_tier']==tier,'Confidence differs from frozen policy')
    return {'opinions_checked':len(result['opinions']),'traces_checked':len(traces),
            'complete_audits':complete,'finding_rows':findings,
            'supported_lines_checked':sum(len(traces[r['invoice_id']]['lines']) for r in result['opinions'])}


def validate_csv(payload,opinions,template):
    """Independent serialization checks; parsed fields must match current opinions."""
    template_fields=next(csv.reader(io.StringIO(template)),None)
    ensure(template_fields==list(FIELDS),'Supplied template changed')
    reader=csv.DictReader(io.StringIO(payload,newline=''))
    ensure(reader.fieldnames==list(FIELDS),'Results must have exactly the template columns in order')
    expected={r['invoice_id']:r for r in opinions}
    ensure(len(expected)==len(opinions),'Duplicate/cross-hospital opinion ID')
    seen=set()
    for row in reader:
        ensure(set(row)==set(FIELDS) and all(v is not None for v in row.values()),'Ragged results row')
        ident=row['invoice_id']
        ensure(ident in expected and ident not in seen,'Unexpected, duplicate, or H1 result identity')
        seen.add(ident);opinion=expected[ident]
        ensure(opinion['hospital'] in TARGETS,'Hospital 1 is development only')
        for field in ('flagged','expected_total_cents','billed_total_cents'):
            value=integer(row[field],field)
            ensure(value==opinion[field],'Serialized field differs from current opinion: '+field)
        ensure(row['flagged'] in {'0','1'},'Flag is not exactly 0 or 1')
        ensure(row['error_category']==opinion['error_category'],'Serialized category mismatch')
        value=float(row['confidence'])
        ensure(math.isfinite(value) and 0<=value<=1 and value==opinion['confidence'],'Invalid or changed confidence')
    ensure(seen==set(expected),'Missing current complete opinions')
    return len(seen)


def export_results(root,inject_failure=False):
    root=Path(root).resolve();directory,status=current_run(root,TARGETS)
    policy=json.loads((root/'evaluation/confidence_policy.json').read_text())
    opinions=[];checks={};counts={}
    for hospital in TARGETS:
        result=json.loads((directory/f'{hospital}.json').read_text())
        checks[hospital]=validate_result(result,load_hospital(root/'data/source',hospital),policy)
        opinions.extend(result['opinions']);counts[hospital]=result['accounting']
    opinions.sort(key=lambda r:r['invoice_id'])
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,FIELDS,lineterminator='\n',extrasaction='ignore')
    writer.writeheader();writer.writerows(opinions);payload=stream.getvalue()
    template=(root/'data/source/submission_template.csv').read_text()
    count=validate_csv(payload,opinions,template)
    identity={'target_run_id':status['run_id'],'template_sha256':digest(root/'data/source/submission_template.csv')}
    release_id=canonical_hash(identity);release=root/'runs/releases'/release_id/uuid.uuid4().hex;release.mkdir(parents=True,exist_ok=True)
    staging=release/f'.candidate-{uuid.uuid4().hex}.csv';staging.write_text(payload,encoding='utf-8',newline='')
    validate_csv(staging.read_text(),opinions,template)
    manifest={'release_id':release_id,'identity':identity,'target_attempt':status['attempt'],
              'target_pointer':'runs/current-H2-H3-H4-H5.json','results_sha256':digest(staging),
              'rows':count,'hospital_accounting':counts,'lineage_checks':checks,
              'status':'validated','label_inputs':[],'scope':'complete opinions only; no target accuracy is known'}
    if inject_failure:
        write_json(release/f'failed-{uuid.uuid4().hex}.json',{'status':'failed','reason':'Controlled failure before promotion','staged_sha256':digest(staging)})
        raise RuntimeError('Controlled export failure before promotion')
    os.replace(staging,release/'audit_results.csv');write_json(release/'manifest.json',manifest)
    temporary=root/f'.results-{uuid.uuid4().hex}.csv';temporary.write_text(payload,encoding='utf-8',newline='')
    os.replace(temporary,root/'audit_results.csv')
    pointer={'release_id':release_id,'path':str(release.relative_to(root)),'manifest_sha256':digest(release/'manifest.json')}
    temp=root/f'runs/.results-{uuid.uuid4().hex}.json';write_json(temp,pointer)
    os.replace(temp,root/'runs/current-results.json')
    return verify_results(root)


def verify_results(root):
    root=Path(root).resolve();directory,status=current_run(root,TARGETS)
    pointer=json.loads((root/'runs/current-results.json').read_text());release=root/pointer['path']
    manifest=json.loads((release/'manifest.json').read_text())
    ensure(manifest['status']=='validated' and manifest['target_attempt']==status['attempt'],'Stale/unvalidated results')
    ensure((root/'audit_results.csv').read_bytes()==(release/'audit_results.csv').read_bytes(),'Results bytes changed or interrupted promotion')
    opinions=[]
    policy=json.loads((root/'evaluation/confidence_policy.json').read_text())
    for h in TARGETS:
        result=json.loads((directory/f'{h}.json').read_text())
        validate_result(result,load_hospital(root/'data/source',h),policy)
        opinions.extend(result['opinions'])
    validate_csv((root/'audit_results.csv').read_text(),opinions,(root/'data/source/submission_template.csv').read_text())
    return manifest
