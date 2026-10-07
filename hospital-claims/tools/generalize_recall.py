"""Measure how much detection survives description drift, on development only.

`generalize.py` proves the pipeline survives unseen inputs; it deliberately
re-identifies every invoice, so its output cannot be scored against labels.
This tool perturbs *only the wording* and keeps every identifier, quantity,
date and amount, so the same development invoices can be scored before and
after and the recall drop attributed to wording alone.

It reuses `generalize.perturb`, so the drift is the same one the survival
harness applies: case changes, whitespace, abbreviation swaps in either
direction, token reordering around a separator, and a one-character typo in a
non-code token, with the service code kept on half of the perturbed lines and
removed on the other half.

Read-only with respect to the repository: it works in a temporary directory
and never rewrites a mapping, a contract or the audit results.
"""
import argparse
import csv
import json
import random
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
from generalize import perturb
from insurance_audit.audit import audit
from insurance_audit.io import load_hospital
from insurance_audit.schema import load_bundle


def perturbed_lines(source, destination, fraction, seed):
    """Copy the snapshot, rewriting a share of the line descriptions."""
    shutil.copytree(source, destination)
    path = destination / 'invoices/hospital_1_line_items.csv'
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    rng = random.Random(seed)
    chosen = sorted(rng.sample(range(len(rows)), int(len(rows) * fraction)))
    for position, index in enumerate(chosen):
        rows[index]['description'] = perturb(rows[index]['description'], position % 2 == 0, position)[0]
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    return len(chosen), len(rows)


def flagged(root, source_root):
    contract, mappings, _ = load_bundle(root, 'H1')
    result = audit(load_hospital(source_root, 'H1'), contract, mappings)
    return {row['invoice_id'] for row in result['opinions'] if row['flagged']}


def measure(project, fraction, seed):
    project = Path(project).resolve()
    split = json.loads((project / 'tests/evaluation/split_manifest.json').read_text())['invoice_partitions']
    with (project / 'data/source/labels/hospital_1_labels.csv').open(newline='', encoding='utf-8-sig') as stream:
        labels = {r['invoice_id']: r for r in csv.DictReader(stream)}
    development = {i for i, p in split.items() if p == 'development'}
    errors = {i for i in development if labels[i]['is_erroneous'] == '1'}
    clean = development - errors

    before = flagged(project, project / 'data/source')
    with tempfile.TemporaryDirectory() as folder:
        destination = Path(folder) / 'source'
        changed, total = perturbed_lines(project / 'data/source', destination, fraction, seed)
        after = flagged(project, destination)

    recall_before = len(before & errors) / len(errors)
    recall_after = len(after & errors) / len(errors)
    drop = (recall_before - recall_after) / recall_before if recall_before else 0.0
    return {'scope': 'Hospital 1 development partition. Wording is perturbed; every identifier, quantity, '
                     'date and amount is left exactly as it is, so the difference is attributable to '
                     'description drift alone.',
            'perturbed_fraction_requested': fraction, 'lines_perturbed': changed, 'lines_total': total,
            'seed': seed, 'development_errors': len(errors), 'development_clean': len(clean),
            'detected_before': len(before & errors), 'detected_after': len(after & errors),
            'recall_before': round(recall_before, 6), 'recall_after': round(recall_after, 6),
            'relative_recall_drop': round(drop, 6),
            'false_positives_before': sorted(before & clean), 'false_positives_after': sorted(after & clean),
            'new_false_positives': sorted((after & clean) - (before & clean)),
            'lost_invoice_ids': sorted((before & errors) - after),
            'gained_invoice_ids': sorted((after & errors) - before)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=ROOT)
    parser.add_argument('--fraction', type=float, default=0.30)
    parser.add_argument('--seed', type=int, default=20260919)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = measure(args.project, args.fraction, args.seed)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({k: result[k] for k in ('lines_perturbed', 'recall_before', 'recall_after',
                                             'relative_recall_drop')} |
                     {'new_false_positives': len(result['new_false_positives'])}))


if __name__ == '__main__':
    main()
