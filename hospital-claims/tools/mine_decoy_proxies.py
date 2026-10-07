"""Mine odd-looking but correct Hospital 1 development invoices and freeze them.

Hospital 1's labels do not mark decoys (correct invoices that look wrong), so
resistance to them cannot be measured directly. This tool builds the nearest
available proxy: invoices that carry a pattern a careless check
would trip on, drawn only from invoices the labels call clean, and only from
the development partition.

Read-only. It never writes into a runtime path; the frozen set lives under
tests/ so no invoice identifier reaches prediction code.
"""
import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from insurance_audit.io import load_hospital
from insurance_audit.resolve import normalize
from insurance_audit.schema import load_bundle

PATTERNS = ('same_day_service_repeat', 'patient_with_several_invoices', 'amended_or_revised_rate',
            'contract_term_boundary_date', 'same_day_invoice_date', 'unusual_quantity',
            'rare_description', 'near_duplicate_across_invoices')


def mine(project, hospital='H1', partition='development'):
    project = Path(project).resolve()
    contract, _, _ = load_bundle(project, hospital)
    data = load_hospital(project / 'data/source', hospital)
    split = json.loads((project / 'tests/evaluation/split_manifest.json').read_text())['invoice_partitions']
    with (project / 'data/source/labels/hospital_1_labels.csv').open(newline='', encoding='utf-8-sig') as stream:
        labels = {r['invoice_id']: r for r in csv.DictReader(stream)}
    clean = {r.invoice_id for r in data.invoices
             if split.get(r.invoice_id) == partition and labels.get(r.invoice_id, {}).get('is_erroneous') == '0'}

    headers = {r.invoice_id: r for r in data.invoices}
    lines = defaultdict(list)
    for row in data.lines:
        lines[row.invoice_id].append(row)
    description_counts = Counter(normalize(r.description) for r in data.lines)
    rare = sorted(description_counts.values())[len(description_counts) // 20] if description_counts else 0
    quantities = sorted(r.quantity for r in data.lines)
    high = quantities[int(len(quantities) * 0.99)] if quantities else 0
    revised = {s['id'] for s in contract['services'] if len(s['versions']) > 1}
    revision_days = sorted({v['start'] for s in contract['services'] for v in s['versions'] if s['id'] in revised} |
                           {v['end'] for s in contract['services'] for v in s['versions'] if s['id'] in revised})
    term = contract['term']

    # One patient billed on two invoices for the same Service Day, and the same
    # patient, day and service on two invoices without being an exact repeat:
    # the near misses a duplicate rule must not report.
    by_day = defaultdict(lambda: defaultdict(set))
    groups = defaultdict(set)
    for invoice_id, rows in lines.items():
        header = headers.get(invoice_id)
        if header is None:
            continue
        for row in rows:
            by_day[header.patient_id][row.service_date].add(invoice_id)
            groups[(header.patient_id, row.service_date, normalize(row.description))].add(invoice_id)
    same_day_patient = {i for days in by_day.values() for ids in days.values() if len(ids) > 1 for i in ids}
    shared = {i for ids in groups.values() if len(ids) > 1 for i in ids}

    found = {name: [] for name in PATTERNS}
    for invoice_id in sorted(clean):
        header = headers[invoice_id]
        rows = lines.get(invoice_id, [])
        seen = Counter((normalize(r.description), r.service_date) for r in rows)
        if any(n > 1 for n in seen.values()):
            found['same_day_service_repeat'].append(invoice_id)
        if invoice_id in same_day_patient:
            found['patient_with_several_invoices'].append(invoice_id)
        resolved = {normalize(r.description) for r in rows}
        if any(abs((date.fromisoformat(r.service_date) - date.fromisoformat(d)).days) <= 1
               for r in rows for d in revision_days):
            found['amended_or_revised_rate'].append(invoice_id)
        if any(abs((date.fromisoformat(r.service_date) - date.fromisoformat(t)).days) <= 2
               for r in rows for t in term):
            found['contract_term_boundary_date'].append(invoice_id)
        if any(r.service_date == header.invoice_date for r in rows):
            found['same_day_invoice_date'].append(invoice_id)
        if any(r.quantity >= high or r.quantity <= 0 for r in rows):
            found['unusual_quantity'].append(invoice_id)
        if any(description_counts[d] <= rare for d in resolved):
            found['rare_description'].append(invoice_id)
        if invoice_id in shared:
            found['near_duplicate_across_invoices'].append(invoice_id)

    members = sorted({i for ids in found.values() for i in ids})
    return {'hospital': hospital, 'partition': partition, 'population': len(clean),
            'rare_description_threshold': rare,
            'scope': 'Invoices the labels call clean that carry a pattern a careless check could trip on. '
                     'Every check must produce zero flags on this set.',
            'unusual_quantity_threshold': high, 'patterns': found, 'members': members,
            'counts': {name: len(ids) for name, ids in found.items()}, 'member_count': len(members)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, default=ROOT / 'tests/evaluation/decoy_proxies.json')
    args = parser.parse_args()
    result = mine(args.project)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'member_count': result['member_count'], 'population': result['population'],
                      'counts': result['counts']}))


if __name__ == '__main__':
    main()
