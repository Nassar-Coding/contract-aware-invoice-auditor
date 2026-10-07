"""Observe existing decisions. No new matcher, finding, pricing or confidence policy."""
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path

from score_development import hospital_report, load_inputs, save, score, sha
from insurance_audit.batch import current_run
from insurance_audit.io import load_hospital
from insurance_audit.resolve import mapping_index, normalize
from insurance_audit.results import validate_result


def mapping_state(description, index):
    record = index.get(normalize(description or ''))
    candidates = record.get('candidates', []) if record else []
    if record and record['state'] == 'accepted':
        state = 'MATCH'
    elif len(candidates) > 1:
        state = 'TIE'
    elif candidates or not description:
        state = 'WEAK'
    else:
        state = 'NO_MATCH'
    return {'state':state, 'reviewed_candidates':candidates,
            'service_id':record.get('service_id') if state == 'MATCH' else None}


def observe(result, data, index):
    opinions = {r['invoice_id']:r for r in result['opinions']}
    raw_lines = defaultdict(list)
    for row in data.dispositions:
        if row['kind'] == 'line_items': raw_lines[row['invoice_id']].append(row)
    ids = data.invoice_identities()
    if {r['invoice_id'] for r in result['traces']} != ids or len(result['traces']) != len(ids):
        raise ValueError('Trace identity accounting failed')
    for original in result['traces']:
        ident = original['invoice_id']; opinion = opinions.get(ident)
        decision = ('flag' if opinion['flagged'] else 'clean') if opinion else 'withhold'
        reasons = original.get('reasons', [])
        # A reported invoice can still carry unresolved facts; they are recorded
        # separately from the reasons that withheld an invoice outright.
        unresolved = original.get('unresolved_facts', []) if opinion else reasons
        if decision == 'withhold' and not reasons:
            raise ValueError('Withheld invoice lacks a named reason')
        facts = [{'reason':r['reason'], 'line_ids':[r['line_id']] if r.get('line_id') else [],
                  'scope':'line' if r.get('line_id') else 'invoice', 'detail':r.get('detail')} for r in unresolved]
        actual = {(r['source'],r['source_row']):r for r in original.get('lines', [])}
        lines = []
        for raw in raw_lines[ident]:
            entry = actual.get((raw['source'],raw['source_row']), {})
            lines.append({'line_id':raw['line_id'], 'source':raw['source'], 'source_row':raw['source_row'],
                'input_status':raw['status'], 'input_reason':raw['reason'],
                'mapping':mapping_state(raw.get('raw_named',{}).get('description'),index),
                'mapping_observation':'runtime_and_lookup' if entry else 'posthoc_lookup_only',
                'execution_status':entry.get('status','not_reached'),
                'checks_fired':entry.get('result',{}).get('error_categories',[])})
            if raw['status'] == 'quarantined':
                facts.append({'reason':'quarantined_source_line','line_ids':[raw['line_id']],
                              'scope':'line','detail':raw['reason'],'source_row':raw['source_row']})
        findings = original.get('findings', [])
        checks = set(original.get('definite_error_categories', [])) | set(findings)
        if opinion: checks.update(filter(None,opinion['error_category'].split(';')))
        for line in lines: checks.update(line['checks_fired'])
        supported = sum(r['execution_status']=='supported' for r in lines)
        yield {'invoice_id':ident, 'hospital':result['hospital'], 'decision':decision,
            'checks_fired':sorted(checks), 'unresolved_facts':facts, 'lines':lines,
            'withheld_reasons':[r['reason'] for r in reasons],
            'primary_withheld_reason':reasons[0]['reason'] if reasons else None,
            'findings':findings, 'decision_basis':opinion.get('decision_basis') if opinion else None,
            'amount_basis':opinion.get('amount_basis') if opinion else None,
            'amount_status':('full' if opinion['amount_basis'] in {'full_correction'} else 'partial') if opinion
                            else ('partial' if supported else 'none'),
            'supported_line_count':supported, 'physical_line_count':len(lines),
            'confidence':opinion['confidence'] if opinion else None}


def histogram(traces):
    withheld = [r for r in traces if r['decision']=='withhold']
    if any(not r['primary_withheld_reason'] or not r['withheld_reasons'] for r in withheld):
        raise ValueError('Unnamed withheld decision')
    return {'withheld':len(withheld),
        'primary_reason':dict(sorted(Counter(r['primary_withheld_reason'] for r in withheld).items())),
        'any_reason_invoice_incidence':dict(sorted(Counter(reason for r in withheld for reason in set(r['withheld_reasons'])).items()))}


def cross_tab(misses, families, traces):
    index = {r['invoice_id']:r for r in traces}; exclusive=Counter(); incidence=Counter(); details=[]
    for ident in misses:
        row=index[ident]
        primary=row['primary_withheld_reason'] if row['decision']=='withhold' else 'emitted_clean'
        family=families[ident];exclusive[family,primary]+=1
        for reason in set(row['withheld_reasons']) or {'emitted_clean'}: incidence[family,reason]+=1
        details.append({'invoice_id':ident,'primary_family':family,'withheld_reason':primary})
    records=lambda counter:[{'primary_family':f,'withheld_reason':r,'count':n} for (f,r),n in sorted(counter.items())]
    return {'missed':len(misses),'exclusive_total':sum(exclusive.values()),
            'primary_reason_crosstab':records(exclusive),'any_reason_crosstab':records(incidence),'details':details}


def write_trace(project, path, labels_path, families_path):
    before=sha(project/'audit_results.csv'); traces=[]; predictions={}; hashes={}; linked_count=0
    policy=json.loads((project/'evaluation/confidence_policy.json').read_text())
    for group in (['H1'],['H2','H3','H4','H5']):
        directory,_=current_run(project,group)
        for h in group:
            result=json.loads((directory/f'{h}.json').read_text()); data=load_hospital(project/'data/source',h)
            validate_result(result,data,policy)
            index=mapping_index(json.loads((project/f'mappings/hospital_{h[1:]}.json').read_text()))
            traces.extend(observe(result,data,index));predictions.update({r['invoice_id']:r for r in result['opinions']})
            ids=data.invoice_identities()
            linked_count+=sum(r['kind']=='line_items' and r['invoice_id'] in ids for r in data.dispositions)
            for suffix in ('.json','.input_quality.json'):hashes[h+suffix]=sha(directory/(h+suffix))
    if sum(r['physical_line_count'] for r in traces)!=linked_count:
        raise ValueError('Physical linked lines missing from trace')
    traces.sort(key=lambda r:(r['hospital'],r['invoice_id']))
    path.parent.mkdir(parents=True,exist_ok=True)
    content=''.join(json.dumps(r,sort_keys=True,allow_nan=False)+'\n' for r in traces).encode()
    path.write_bytes(gzip.compress(content,mtime=0) if str(path).endswith('.gz') else content)
    labels,families=load_inputs(project,labels_path,families_path,'development')
    dev=score(labels,predictions,families)
    dev_traces=[r for r in traces if r['hospital']=='H1' and r['invoice_id'] in labels]
    hist={'H1_development':histogram(dev_traces)}
    hist.update({h:histogram([r for r in traces if r['hospital']==h]) for h in ['H2','H3','H4','H5']})
    cross=cross_tab(dev['fn_invoice_ids'],families,dev_traces)
    out=path.parent
    save(out/'withheld_reasons.json',hist)
    save(out/'missed_development_crosstab.json',cross)
    hospitals=hospital_report(project,predictions)
    save(out/'hospital_distributions.json',hospitals)
    summary={'invoices':len(traces),'by_hospital':dict(Counter(r['hospital'] for r in traces)),
        'decisions':dict(Counter(r['decision'] for r in traces)),
        'every_withheld_invoice_has_named_reason':all(r['decision']!='withhold' or bool(r['withheld_reasons']) for r in traces),
        'physical_linked_lines':linked_count,'all_physical_linked_lines_represented':True,
        'amount_status':dict(Counter(r['amount_status'] for r in traces)),
        'mapping_states':dict(Counter(l['mapping']['state'] for r in traces for l in r['lines'])),
        'runtime_output_sha256':hashes,'trace_sha256':sha(path),'results_sha256':before,
        'mapping_scope':'Frozen reviewed records only: MATCH accepted; TIE multiple candidates; WEAK one unresolved candidate or absent description; NO_MATCH no reviewed candidate. No scored matcher or confident unknown_service finding is introduced.',
        'amount_scope':'Partial means diagnostic supported-line pricing, never a partial emitted correction. Full means an emitted total.',
        'histogram_scope':'Primary reason is the first runtime reason, exclusive; any-reason incidence counts distinct invoices and overlaps.',
        'orphans':'Lines without a recoverable header identity remain in original input-quality accounting.'}
    # Measurement must never touch the audit results.
    if sha(project/'audit_results.csv')!=before:
        raise ValueError('Measurement changed audit_results.csv')
    save(out/'trace_summary.json',summary)
    return {'rows':len(traces),'path':str(path.relative_to(project)) if path.is_relative_to(project) else str(path),
            'sha256':sha(path),'every_withheld_has_named_reason':summary['every_withheld_invoice_has_named_reason']}
