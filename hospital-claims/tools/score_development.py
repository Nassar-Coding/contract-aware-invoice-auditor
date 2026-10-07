"""Read-only Hospital 1 scoring against the labels; never imported by the prediction runtime."""
import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from insurance_audit.batch import current_run
from insurance_audit.evaluation import load_labels, measure
from insurance_audit.results import FIELDS

FAMILIES = ('structural', 'term_window', 'mapping', 'rule', 'pricing')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')


def read_predictions(path):
    result = {}
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(FIELDS):
            raise ValueError('Prediction columns/order differ from the results template')
        for raw in reader:
            if None in raw or any(v is None for v in raw.values()):
                raise ValueError('Malformed CSV row')
            row = dict(raw)
            ident = row['invoice_id']
            if not ident or ident in result:
                raise ValueError('Empty or duplicate prediction identity')
            for key in ('flagged', 'expected_total_cents', 'billed_total_cents'):
                if not re.fullmatch(r'\d+', row[key]):
                    raise ValueError('Expected nonnegative integer ' + key)
                row[key] = int(row[key])
            if row['flagged'] not in (0, 1) or bool(row['flagged']) != bool(row['error_category']):
                raise ValueError('Invalid flag/category')
            row['confidence'] = float(row['confidence'])
            if not math.isfinite(row['confidence']) or not 0 <= row['confidence'] <= 1:
                raise ValueError('Invalid confidence')
            result[ident] = row
    return result


def export_h1(project, path):
    directory, _ = current_run(project, ['H1'])
    opinions = json.loads((directory / 'H1.json').read_text())['opinions']
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, FIELDS, extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        writer.writerows(sorted(opinions, key=lambda r: r['invoice_id']))


def load_inputs(project, labels_path, families_path, partition):
    split = json.loads((project / 'tests/evaluation/split_manifest.json').read_text())['invoice_partitions']
    labels = load_labels(labels_path, split, partition)
    families = {}
    with families_path.open(newline='', encoding='utf-8-sig') as stream:
        for row in csv.DictReader(stream):
            if partition != 'full' and row['partition'] != partition:
                continue
            ident = row['invoice_id']
            if ident in families or ident not in labels or split[ident] != row['partition']:
                raise ValueError('Family identity/partition mismatch')
            if not labels[ident]['flagged'] or set(row['error_categories'].split('|')) != set(labels[ident]['categories']):
                raise ValueError('Family coding differs from labels')
            if row['primary_family'] not in FAMILIES:
                raise ValueError('Unknown primary family')
            families[ident] = row['primary_family']
    if set(families) != {k for k, v in labels.items() if v['flagged']}:
        raise ValueError('Family coding does not cover every labelled error')
    return labels, families


def score(labels, predictions, families):
    measured = measure(labels, list(predictions.values()))
    binary = measured['flag_metrics_all']
    details = measured['details']
    tp = [r for r in details if r['truth']['flagged'] and r['prediction'] and r['prediction']['flagged']]
    fn = [r['invoice_id'] for r in details if r['truth']['flagged'] and not (r['prediction'] and r['prediction']['flagged'])]
    fp = [{'invoice_id':r['invoice_id'], 'error_category':r['prediction']['error_category']} for r in details
          if not r['truth']['flagged'] and r['prediction'] and r['prediction']['flagged']]
    amount_errors = [abs(r['prediction']['expected_total_cents'] - r['truth']['expected_total_cents']) for r in tp]
    bins = []
    emitted = [r for r in details if r['prediction'] is not None]
    for i in range(5):
        rows = [r for r in emitted if min(int(r['prediction']['confidence'] * 5), 4) == i]
        n = len(rows)
        confidence = sum(r['prediction']['confidence'] for r in rows) / n if n else None
        accuracy = sum(r['whole_row_success'] for r in rows) / n if n else None
        bins.append({'lower':i/5, 'upper':(i+1)/5, 'upper_inclusive':i==4, 'count':n,
                     'mean_confidence':confidence, 'joint_success_rate':accuracy,
                     'absolute_gap':abs(confidence-accuracy) if n else None})
    per_family = {}; cumulative = support = 0
    detected = {r['invoice_id'] for r in tp}
    for family in FAMILIES:
        ids = {k for k, f in families.items() if f == family}
        found = len(ids & detected); cumulative += found; support += len(ids)
        per_family[family] = {'support':len(ids), 'detected':found, 'recall':found/len(ids) if ids else None,
            'cumulative_detected':cumulative, 'cumulative_support':support,
            'cumulative_recall_all_errors':cumulative/len(families) if families else None}
    return {'population':len(labels), 'erroneous':len(families), 'clean':len(labels)-len(families),
            'emitted':measured['covered'], 'withheld':measured['abstained'],
            'tp':binary['tp'], 'fn':binary['fn_including_abstentions'], 'fp':binary['fp'],
            'recall':binary['recall'], 'precision':binary['precision'],
            'fn_invoice_ids':fn, 'false_positives_on_clean':fp,
            'per_category_detection':measured['per_original_label_detection'], 'per_primary_family':per_family,
            'category_scope':'Detection on invoices bearing each label, not predicted-category accuracy; primary families are exclusive.',
            'tp_amount':{'count':len(tp), 'exact':sum(e==0 for e in amount_errors),
                'exact_match_rate':sum(e==0 for e in amount_errors)/len(tp) if tp else None,
                'mae_cents':sum(amount_errors)/len(tp) if tp else None},
            'reliability':{'event':'Correct flag AND exact expected cents; not P(error). Emitted opinions only.', 'bins':bins,
                'ece':sum(b['count']*(b['absolute_gap'] or 0) for b in bins)/len(emitted) if emitted else None}}


def hospital_report(project, predictions):
    universe = {}
    for n in range(1,6):
        with (project/f'data/source/invoices/hospital_{n}_invoices.csv').open(newline='') as stream:
            universe[f'H{n}'] = {r['invoice_id'] for r in csv.DictReader(stream) if r['invoice_id']}
    if set(predictions) - set().union(*universe.values()):
        raise ValueError('Prediction identity not in the source data')
    result = {}
    for hospital, ids in universe.items():
        rows = [v for k, v in predictions.items() if k in ids]; flags = sum(r['flagged'] for r in rows)
        rate = flags/len(ids) if ids else None
        result[hospital] = {'unique_invoices':len(ids), 'rows':len(rows), 'flags':flags,
            'flag_rate_all_invoices':rate, 'withheld':len(ids)-len(rows),
            'category_mix':dict(sorted(Counter(c for r in rows for c in r['error_category'].split(';') if c).items())),
            'confidence_histogram':[sum(min(int(r['confidence']*5),4)==i for r in rows) for i in range(5)],
            'confidence_bin_bounds':[0,.2,.4,.6,.8,1],
            # a sanity band on the flag rate, for spotting a broken run; not a target
            'warning': 'below_4_percent' if rate is not None and rate<.04 else ('above_12_percent' if rate is not None and rate>.12 else None),
            'investigate_below_3_percent':rate is not None and rate<.03}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,default=ROOT)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--predictions',type=Path)
    source.add_argument('--export-current-h1',type=Path)
    parser.add_argument('--labels',type=Path)
    parser.add_argument('--families',type=Path)
    parser.add_argument('--partition',choices=['development','check','full'],default='development')
    parser.add_argument('--allow-check-partition',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--trace',type=Path,help='Observe all current hospital runs; supports .jsonl or deterministic .jsonl.gz')
    args = parser.parse_args(); project = args.project.resolve()
    if args.partition != 'development' and not args.allow_check_partition:
        parser.error('The check partition is kept out of tuning; pass --allow-check-partition to score it')
    before = sha(project/'audit_results.csv')
    if args.export_current_h1:
        if args.export_current_h1.resolve() == (project/'audit_results.csv').resolve():
            parser.error('The H1 export must not replace audit_results.csv')
        export_h1(project,args.export_current_h1)
    path = args.predictions or args.export_current_h1
    labels, families = load_inputs(project,args.labels or project/'data/source/labels/hospital_1_labels.csv',
        args.families or project/'tests/evaluation/h1_primary_family_coding.csv',args.partition)
    predictions = read_predictions(path)
    result = score(labels,predictions,families)
    result.update(partition=args.partition,hospitals_in_supplied_csv=hospital_report(project,predictions),
                  predictions_sha256=sha(path),results_sha256=before)
    if args.trace:
        from decision_trace import write_trace
        result['trace'] = write_trace(project,args.trace.resolve(),args.labels or project/'data/source/labels/hospital_1_labels.csv',
            args.families or project/'tests/evaluation/h1_primary_family_coding.csv')
    save(args.output,result)
    if sha(project/'audit_results.csv') != before:
        raise ValueError('Measurement changed audit_results.csv')
    print(json.dumps({k:result[k] for k in ['partition','tp','fn','fp','recall','precision','emitted','withheld']}))


if __name__=='__main__': main()
