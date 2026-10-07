"""Explicit H1-only metrics. Evaluation is never imported by pricing or mapping."""
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
from .io import integer,write_json
from .schema import digest
from .batch import current_run

TRUTH_FAMILIES={
 'unit_price_mismatch':'pricing','premium_omitted':'pricing','premium_incorrectly_applied':'pricing',
 'volume_discount_omitted':'pricing','volume_discount_incorrectly_applied':'pricing','bundle_not_applied':'pricing',
 'daily_cap_exceeded':'quantity','wrong_unit_basis':'unit','line_total_arithmetic':'line_arithmetic',
 'invoice_total_mismatch':'invoice_arithmetic','contract_number_mismatch':'identity','duplicate_invoice_id':'identity',
 'service_date_after_invoice_date':'date','service_date_out_of_window':'date','malformed_service_date':'date',
 'unknown_service':'mapping','cross_invoice_duplicate':'duplicate','exclusion_window_violation':'eligibility'}
# Emitted categories use the label vocabulary (plus bundle_incorrectly_applied); the older internal names are still
# accepted. line/invoice_amount_mismatch name a consequence, not a family.
PREDICTION_FAMILIES={**TRUTH_FAMILIES,'bundle_incorrectly_applied':'pricing','effective_rate_mismatch':'pricing','daily_quantity_cap':'quantity','unit_basis_mismatch':'unit',
 'line_arithmetic_mismatch':'line_arithmetic','invoice_arithmetic_mismatch':'invoice_arithmetic',
 'contract_reference_mismatch':'identity','hospital_reference_mismatch':'identity','nonbillable_service':'eligibility'}


def fraction(a,b):return a/b if b else None


def binary_metrics(truth,prediction):
    pairs=list(zip(truth,prediction));tp=sum(t==1 and p==1 for t,p in pairs);fp=sum(t==0 and p==1 for t,p in pairs)
    fn=sum(t==1 and p!=1 for t,p in pairs);tn=sum(t==0 and p==0 for t,p in pairs)
    precision=fraction(tp,tp+fp);recall=fraction(tp,tp+fn)
    return {'tp':tp,'fp':fp,'fn_including_abstentions':fn,'tn_emitted':tn,'support':sum(t==1 for t,p in pairs),
            'precision':precision,'recall':recall,'f1':fraction(2*tp,2*tp+fp+fn)}


def load_labels(path,partitions,group):
    if path.name!='hospital_1_labels.csv':raise ValueError('Only H1 label input is allowed')
    result={};seen=set()
    with path.open(newline='') as f:
        reader=csv.DictReader(f)
        if set(reader.fieldnames or [])!={'invoice_id','is_erroneous','error_categories','expected_total_cents','ambiguity_sensitive'}:
            raise ValueError('Incompatible H1 label schema')
        for row in reader:
            ident=row['invoice_id']
            if ident in seen:raise ValueError('Duplicated H1 label identity')
            seen.add(ident)
            if ident not in partitions:raise ValueError('H1 label identity absent from frozen split')
            if group!='full' and partitions[ident]!=group:continue
            value={'invoice_id':ident,'flagged':integer(row['is_erroneous'],'is_erroneous'),
                   'expected_total_cents':integer(row['expected_total_cents'],'expected_total_cents'),
                   'categories':sorted(set(filter(None,row['error_categories'].split('|')))),
                   'ambiguity_sensitive':integer(row['ambiguity_sensitive'],'ambiguity_sensitive')}
            if value['flagged'] not in {0,1}:raise ValueError('Invalid H1 truth flag')
            result[ident]=value
    if seen!=set(partitions):raise ValueError('Frozen split and label identity sets differ')
    return result


def measure(labels,opinions):
    ids=set(labels);pred={r['invoice_id']:r for r in opinions if r['invoice_id'] in ids}
    if len(pred)!=sum(r['invoice_id'] in ids for r in opinions):raise ValueError('Duplicate prediction identity')
    details=[];groups=defaultdict(list)
    for ident,truth in sorted(labels.items()):
        p=pred.get(ident);flag_ok=p is not None and p['flagged']==truth['flagged']
        amount_ok=p is not None and p['expected_total_cents']==truth['expected_total_cents']
        detail={'invoice_id':ident,'truth':truth,'prediction':p,'covered':p is not None,
                'flag_correct':flag_ok,'amount_exact':amount_ok,'whole_row_success':flag_ok and amount_ok}
        details.append(detail)
        if p:groups[p.get('confidence_tier',p.get('mapping_evidence','unspecified'))].append(detail)
    allcats=sorted({c for t in labels.values() for c in t['categories']})
    per_label={}
    for category in allcats:
        rows=[d for d in details if category in d['truth']['categories']]
        per_label[category]={'support':len(rows),'covered':sum(d['covered'] for d in rows),
            'flagged':sum(d['prediction'] is not None and d['prediction']['flagged']==1 for d in rows),
            'amount_exact':sum(d['amount_exact'] for d in rows),'whole_row_success':sum(d['whole_row_success'] for d in rows),
            'detection_recall_all':fraction(sum(d['prediction'] is not None and d['prediction']['flagged']==1 for d in rows),len(rows))}
    families={}
    for family in sorted(set(TRUTH_FAMILIES.values())|set(PREDICTION_FAMILIES.values())):
        truth=[int(family in {TRUTH_FAMILIES.get(c,'unmapped_truth') for c in d['truth']['categories']}) for d in details]
        guessed=[None if not d['covered'] else int(family in {PREDICTION_FAMILIES.get(c) for c in d['prediction']['error_category'].split(';')}) for d in details]
        families[family]=binary_metrics(truth,guessed)
    confidence={}
    for tier,rows in sorted(groups.items()):
        with_conf=[d for d in rows if d['prediction'].get('confidence') is not None]
        confidence[tier]={'n':len(rows),'successes':sum(d['whole_row_success'] for d in rows),
            'whole_row_accuracy':fraction(sum(d['whole_row_success'] for d in rows),len(rows)),
            'mean_confidence':fraction(sum(d['prediction']['confidence'] for d in with_conf),len(with_conf)),
            'brier_score':fraction(sum((d['prediction']['confidence']-int(d['whole_row_success']))**2 for d in with_conf),len(with_conf))}
    n=len(details);covered=len(pred)
    result={'population':n,'covered':covered,'abstained':n-covered,'coverage':fraction(covered,n),
            'flag_metrics_all':binary_metrics([d['truth']['flagged'] for d in details],[d['prediction']['flagged'] if d['covered'] else None for d in details]),
            'flag_metrics_covered':binary_metrics([d['truth']['flagged'] for d in details if d['covered']],[d['prediction']['flagged'] for d in details if d['covered']]),
            'flag_accuracy_covered':fraction(sum(d['flag_correct'] for d in details),covered),
            'amount_accuracy_covered':fraction(sum(d['amount_exact'] for d in details),covered),
            'whole_row_accuracy_covered':fraction(sum(d['whole_row_success'] for d in details),covered),
            'whole_row_success_count':sum(d['whole_row_success'] for d in details),
            'amount_mae_cents_covered':fraction(sum(abs(d['prediction']['expected_total_cents']-d['truth']['expected_total_cents']) for d in details if d['covered']),covered),
            'per_category_family':families,'per_original_label_detection':per_label,'confidence_groups':confidence,
            'ambiguity_sensitive_support':sum(t['ambiguity_sensitive'] for t in labels.values()),
            'unmapped_truth_categories':sorted(set(allcats)-set(TRUTH_FAMILIES)),
            'details':details}
    return result


def evaluate_run(root,group):
    if group not in {'development','check','full'}:raise ValueError('Unknown H1 partition')
    root=Path(root).resolve();directory,status=current_run(root,['H1'])
    split=json.loads((root/'tests/evaluation/split_manifest.json').read_text())
    labelpath=root/'data/source/labels/hospital_1_labels.csv'
    labels=load_labels(labelpath,split['invoice_partitions'],group)
    result=measure(labels,json.loads((directory/'H1.json').read_text())['opinions'])
    result['provenance']={'group':group,'run_id':status['run_id'],'attempt':status['attempt'],
        'labels_sha256':digest(labelpath),'split_sha256':digest(root/'tests/evaluation/split_manifest.json'),
        'role':'development-inclusive descriptive' if group=='full' else ('regression after reserved-check exposure; not a fresh holdout' if group=='check' else 'development'),
        'whole_row_event':'Correct binary flag AND exact expected cents. Billed cents/identity validated separately; free-text category correctness is reported by family, not included in this event.',
        'abstention_policy':'Not treated as correct negatives. Included as missed positives in all-population recall; excluded from conditional accuracy.',
        'category_policy':'Families use the explicit many-to-one crosswalk in evaluation.py; original-label table reports detection, not invented category-specific precision.',
        'confidence_scope':'H1 is development data; target transfer accuracy is not established.'}
    write_json(directory/f'H1.evaluation.{group}.json',result)
    write_json(root/f'reports/evaluation_{group}.json',result)
    print(json.dumps({k:v for k,v in result.items() if k in ['population','covered','coverage','flag_metrics_all','whole_row_accuracy_covered','confidence_groups']},indent=2))
    return result
