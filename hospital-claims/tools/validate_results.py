"""Validate audit_results.csv against the output contract.

Independent of the exporter that wrote the file: it re-reads the source
invoices and checks the CSV on its own terms, so a fault in the exporter
cannot hide behind the exporter's own accounting. Read-only.

Checks, in order:

1. exact column order `invoice_id, flagged, error_category,
   expected_total_cents, billed_total_cents, confidence`;
2. `flagged` is exactly 0 or 1, and carries a category when and only when set;
3. `confidence` is a finite number between 0 and 1;
4. money is integer cents, never a decimal or a float;
5. one row per `invoice_id`, with no duplicates;
6. identifiers are drawn only from Hospitals 2 to 5;
7. `billed_total_cents` matches the source data.
"""
import argparse
import csv
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from insurance_audit.io import load_hospital

FIELDS = ('invoice_id', 'flagged', 'error_category', 'expected_total_cents', 'billed_total_cents', 'confidence')
TARGETS = ('H2', 'H3', 'H4', 'H5')
INTEGER = re.compile(r'^-?\d+$')


def source_billed(project):
    """Billed total per invoice identifier, from the source snapshot.

    Where an identifier is on more than one physical record, every one of those
    records' totals is accepted: which record is canonical is the pipeline's
    documented choice, and this validator only checks the figure came from the
    source rather than from nowhere.
    """
    totals, hospitals = {}, {}
    for hospital in TARGETS:
        for row in load_hospital(project / 'data/source', hospital).invoices:
            totals.setdefault(row.invoice_id, set()).add(row.invoice_total_cents)
            hospitals[row.invoice_id] = hospital
    return totals, hospitals


def validate(project, path):
    project, path = Path(project).resolve(), Path(path).resolve()
    failures, rows = [], []
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.reader(stream)
        try:
            header = next(reader)
        except StopIteration:
            return {'status': 'failed', 'failures': ['results file is empty'], 'rows': 0}
        if tuple(header) != FIELDS:
            failures.append(f'column order is {header!r}, expected {list(FIELDS)!r}')
        for number, values in enumerate(reader, 2):
            if len(values) != len(FIELDS):
                failures.append(f'row {number}: has {len(values)} fields, expected {len(FIELDS)}')
                continue
            rows.append(dict(zip(FIELDS, values)))

    totals, hospitals = source_billed(project)
    seen = Counter(r['invoice_id'] for r in rows)
    for identifier, count in sorted(seen.items()):
        if count > 1:
            failures.append(f'{identifier}: appears {count} times; one row per invoice_id is required')

    flagged_count = 0
    for row in rows:
        identifier = row['invoice_id']
        where = f'{identifier}'
        if identifier not in hospitals:
            failures.append(f'{where}: not an invoice of Hospitals 2 to 5')
            continue
        if row['flagged'] not in ('0', '1'):
            failures.append(f'{where}: flagged is {row["flagged"]!r}, expected 0 or 1')
        else:
            flagged_count += row['flagged'] == '1'
            if bool(row['error_category']) != (row['flagged'] == '1'):
                failures.append(f'{where}: a category is required exactly when flagged is 1')
        for field in ('expected_total_cents', 'billed_total_cents'):
            if not INTEGER.match(row[field]):
                failures.append(f'{where}: {field} is {row[field]!r}, expected integer cents')
        if INTEGER.match(row['billed_total_cents']):
            if int(row['billed_total_cents']) not in totals[identifier]:
                failures.append(f'{where}: billed_total_cents {row["billed_total_cents"]} '
                                f'is not a billed total of this invoice in the source data')
        if INTEGER.match(row['expected_total_cents']) and int(row['expected_total_cents']) < 0:
            failures.append(f'{where}: expected_total_cents is negative')
        try:
            confidence = float(row['confidence'])
        except ValueError:
            failures.append(f'{where}: confidence is {row["confidence"]!r}, expected a number')
        else:
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                failures.append(f'{where}: confidence {confidence} is outside [0, 1]')

    per_hospital = {}
    for hospital in TARGETS:
        mine = [r for r in rows if hospitals.get(r['invoice_id']) == hospital]
        per_hospital[hospital] = {'rows': len(mine), 'flags': sum(r['flagged'] == '1' for r in mine)}
    return {'status': 'passed' if not failures else 'failed',
            'failures': failures[:50], 'failure_count': len(failures),
            'rows': len(rows), 'unique_invoice_ids': len(seen), 'flagged': flagged_count,
            'per_hospital': per_hospital,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'checks': ['column order', 'flag is 0 or 1 with a matching category', 'confidence in [0,1]',
                       'integer cents', 'one row per invoice_id', 'identifiers from Hospitals 2 to 5 only',
                       'billed total matches the source data']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=ROOT)
    parser.add_argument('--results', type=Path, default=ROOT / 'audit_results.csv')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = validate(args.project, args.results)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({k: result[k] for k in ('status', 'failure_count', 'rows', 'unique_invoice_ids',
                                             'flagged', 'per_hospital', 'sha256')}, indent=2))
    for failure in result['failures']:
        print('  FAIL', failure, file=sys.stderr)
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
