"""Mapping-free findings: what each check reports, and what it refuses to report."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from insurance_audit.audit import audit
from insurance_audit.findings import Findings, order, quarantine_category
from insurance_audit.io import INVOICE_COLUMNS, LINE_COLUMNS, load_hospital
from insurance_audit.schema import load_bundle

ROOT = Path(__file__).resolve().parents[1]


class Snapshot:
    """A minimal hospital snapshot written to disk and read back by the loader."""

    def __init__(self, hospital='H1'):
        self.hospital = hospital
        self.contract, self.maps, _ = load_bundle(ROOT, hospital)
        self.services = {s['id']: s for s in self.contract['services']}
        self.alias = {r['service_id']: r['raw_descriptions'][0] for r in self.maps['records']
                      if r['state'] == 'accepted' and r['grade'] == 'explicit'}

    def header(self, ident='I1', patient='P1', total=0, **changes):
        row = dict(invoice_id=ident, hospital_id=self.hospital, contract_number=self.contract['contract_number'],
                   invoice_date='2025-06-01', patient_id=patient, facility_code='F-MAIN', plan_tier='BRONZE',
                   admission_date='2025-05-01', discharge_date='2025-05-02', invoice_total_cents=total)
        row.update(changes)
        return row

    def line(self, ident='I1', lid='L1', sid='H1-S001', quantity=1, **changes):
        service = self.services[sid]
        rate = service['versions'][0]['cents']
        row = dict(line_id=lid, invoice_id=ident, line_no=int(lid[1:]), service_date='2025-05-01',
                   description=self.alias[sid], quantity=quantity, unit_basis_as_billed=service['unit'],
                   unit_price_cents=rate, line_total_cents=rate * quantity)
        row.update(changes)
        return row

    def load(self, headers, lines):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'invoices').mkdir()
            for kind, columns, rows in [('invoices', INVOICE_COLUMNS, headers), ('line_items', LINE_COLUMNS, lines)]:
                path = root / f'invoices/hospital_{self.hospital[1:]}_{kind}.csv'
                with path.open('w', newline='') as stream:
                    writer = csv.DictWriter(stream, columns)
                    writer.writeheader()
                    writer.writerows(rows)
            return load_hospital(root, self.hospital)

    def assess(self, headers, lines, invoice_id='I1'):
        data = self.load(headers, lines)
        return Findings(data, self.contract, self.maps).assess(invoice_id)

    def balanced(self, lines):
        return sum(l['line_total_cents'] for l in lines)


class QuarantineClassificationTests(unittest.TestCase):
    def test_a_malformed_value_is_named_but_an_unattributable_row_is_not(self):
        self.assertEqual(quarantine_category('service_date: day is out of range for month'), 'malformed_service_date')
        self.assertEqual(quarantine_category('service_date: expected YYYY-MM-DD'), 'malformed_service_date')
        self.assertEqual(quarantine_category('discharge_date: month must be in 1..12'), 'malformed_invoice_date')
        self.assertEqual(quarantine_category('quantity: not an exact integer'), 'malformed_amount')
        self.assertIsNone(quarantine_category('invoice_id: missing identifier'))
        self.assertIsNone(quarantine_category('ragged row'))

    def test_a_field_name_survives_an_impossible_calendar_date(self):
        f = Snapshot()
        lines = [f.line(service_date='2025-02-30')]
        data = f.load([f.header(total=0)], lines)
        reason = [d for d in data.dispositions if d['status'] == 'quarantined'][0]['reason']
        self.assertTrue(reason.startswith('service_date:'), reason)
        self.assertEqual(quarantine_category(reason), 'malformed_service_date')

    def test_a_malformed_date_is_reported_although_the_line_cannot_be_priced(self):
        f = Snapshot()
        lines = [f.line(), f.line(lid='L2', service_date='2025-02-30')]
        facts = f.assess([f.header(total=f.balanced(lines))], lines)
        self.assertIn('malformed_service_date', facts['categories'])
        self.assertEqual(facts['amount_affecting'], [])


class StructuralFindingTests(unittest.TestCase):
    def setUp(self):
        self.f = Snapshot()

    def test_a_clean_invoice_reports_nothing(self):
        lines = [self.f.line(), self.f.line(lid='L2', sid='H1-S002')]
        facts = self.f.assess([self.f.header(total=self.f.balanced(lines))], lines)
        self.assertEqual(facts['categories'], [])

    def test_a_reused_identifier_is_reported_and_the_latest_record_is_canonical(self):
        lines = [self.f.line()]
        headers = [self.f.header(total=100, invoice_date='2025-06-01'),
                   self.f.header(total=200, patient='P2', invoice_date='2025-07-01')]
        facts = self.f.assess(headers, lines)
        self.assertEqual(facts['categories'], ['duplicate_invoice_id'])
        self.assertEqual(facts['canonical_header']['invoice_total_cents'], 200)

    def test_the_contract_number_and_hospital_of_record_are_checked(self):
        lines = [self.f.line()]
        total = self.f.balanced(lines)
        facts = self.f.assess([self.f.header(total=total, contract_number='INS-H4-2024-2049')], lines)
        self.assertEqual(facts['categories'], ['contract_number_mismatch'])
        facts = self.f.assess([self.f.header(total=total, hospital_id='H4')], lines)
        self.assertEqual(facts['categories'], ['hospital_reference_mismatch'])

    def test_arithmetic_is_checked_on_the_line_and_on_the_header(self):
        lines = [self.f.line(line_total_cents=1)]
        facts = self.f.assess([self.f.header(total=1)], lines)
        self.assertEqual(facts['categories'], ['line_total_arithmetic'])
        lines = [self.f.line()]
        facts = self.f.assess([self.f.header(total=self.f.balanced(lines) + 1)], lines)
        self.assertEqual(facts['categories'], ['invoice_total_mismatch'])

    def test_a_service_date_outside_the_term_or_after_the_invoice_is_reported(self):
        lines = [self.f.line(service_date='2026-05-01')]
        facts = self.f.assess([self.f.header(total=self.f.balanced(lines), invoice_date='2026-06-01')], lines)
        self.assertEqual(facts['categories'], ['service_date_out_of_window'])
        lines = [self.f.line(service_date='2025-06-02')]
        facts = self.f.assess([self.f.header(total=self.f.balanced(lines), invoice_date='2025-06-01')], lines)
        self.assertEqual(facts['categories'], ['service_date_after_invoice_date'])

    def test_a_reused_identifier_uses_the_latest_invoice_date_for_the_after_check(self):
        lines = [self.f.line(service_date='2025-06-15')]
        headers = [self.f.header(total=1, invoice_date='2025-06-01'),
                   self.f.header(total=2, invoice_date='2025-07-01')]
        self.assertEqual(self.f.assess(headers, lines)['categories'], ['duplicate_invoice_id'])

    def test_naming_precedence_puts_the_term_window_first_then_the_reused_identifier(self):
        self.assertEqual(order({'service_date_after_invoice_date', 'duplicate_invoice_id',
                                'service_date_out_of_window'}),
                         ['service_date_out_of_window', 'duplicate_invoice_id', 'service_date_after_invoice_date'])


class CrossInvoiceDuplicateTests(unittest.TestCase):
    def duplicate_pair(self, hospital, **changes):
        f = Snapshot(hospital)
        sid = next(iter(f.alias))
        first = [f.line('I1', 'L1', sid)]
        second = [dict(f.line('I2', 'L2', sid), **changes)]
        headers = [f.header('I1', 'P1', total=f.balanced(first), invoice_date='2025-06-01'),
                   f.header('I2', 'P1', total=f.balanced(second), invoice_date='2025-06-02')]
        data = f.load(headers, first + second)
        found = Findings(data, f.contract, f.maps)
        return {i: found.assess(i)['categories'] for i in ('I1', 'I2')}

    def test_the_later_invoice_is_reported_and_the_earlier_one_is_not(self):
        result = self.duplicate_pair('H1')
        self.assertEqual(result['I1'], [])
        self.assertEqual(result['I2'], ['cross_invoice_duplicate'])

    def test_a_different_day_quantity_or_service_is_not_a_duplicate(self):
        for changes in [{'service_date': '2025-05-02'}, {'quantity': 2, 'line_total_cents': None},
                        {'description': 'Continuous Wnd Care'}]:
            with self.subTest(changes=changes):
                changes = dict(changes)
                if changes.get('line_total_cents') is None and 'quantity' in changes:
                    changes.pop('line_total_cents')
                    f = Snapshot('H1')
                    sid = next(iter(f.alias))
                    rate = f.services[sid]['versions'][0]['cents']
                    changes['line_total_cents'] = rate * changes['quantity']
                self.assertEqual(self.duplicate_pair('H1', **changes)['I2'], [])

    def test_every_hospital_reports_the_repeat_including_the_one_with_no_clause(self):
        # H1, H3, H4 and H5 each state that the same Service may not be billed
        # twice for one Patient and Service Date. H2's agreement states neither a
        # prohibition nor a permission, and silence is not evidence that a repeat
        # is legitimate, so the check runs there too.
        for hospital in ('H1', 'H2', 'H3', 'H4', 'H5'):
            with self.subTest(hospital=hospital):
                result = self.duplicate_pair(hospital)
                self.assertEqual(result['I1'], [])
                self.assertEqual(result['I2'], ['cross_invoice_duplicate'])


class DecisionLayerTests(unittest.TestCase):
    def test_a_finding_is_reported_although_another_line_is_unmapped(self):
        f = Snapshot()
        lines = [f.line(), f.line(lid='L2', description='Continuous Wnd Care')]
        headers = [f.header(total=f.balanced(lines), contract_number='INS-H4-2024-2049')]
        result = audit(f.load(headers, lines), f.contract, f.maps)
        opinion = result['opinions'][0]
        self.assertEqual(opinion['flagged'], 1)
        self.assertIn('contract_number_mismatch', opinion['error_category'].split(';'))
        self.assertEqual(opinion['decision_basis'], 'finding')
        # The ambiguous line is still recorded as unresolved on the trace even
        # though the contract-number finding is reported without it.
        self.assertTrue(result['traces'][0]['unresolved_facts'])

    def test_an_unpriceable_line_keeps_its_billed_amount_in_a_partial_correction(self):
        # A non-positive quantity is unsupported under every candidate reading,
        # so the line stays unpriced and contributes what was billed.
        f = Snapshot()
        lines = [f.line(), f.line(lid='L2', description='Continuous Wnd Care', quantity=0, line_total_cents=0)]
        headers = [f.header(total=f.balanced(lines) + 500)]
        opinion = audit(f.load(headers, lines), f.contract, f.maps)['opinions'][0]
        self.assertEqual(opinion['error_category'], 'invoice_total_mismatch')
        self.assertEqual(opinion['amount_basis'], 'partial_correction')
        self.assertEqual(opinion['expected_total_cents'], f.balanced(lines))

    def test_an_arithmetic_line_is_recomputed_from_its_own_quantity_and_price(self):
        f = Snapshot()
        lines = [f.line(description='Continuous Wnd Care', quantity=3, line_total_cents=7)]
        opinion = audit(f.load([f.header(total=7)], lines), f.contract, f.maps)['opinions'][0]
        self.assertIn('line_total_arithmetic', opinion['error_category'].split(';'))
        self.assertEqual(opinion['expected_total_cents'], 3 * lines[0]['unit_price_cents'])

    def test_a_flagged_row_states_why_its_amount_is_unchanged(self):
        f = Snapshot()
        lines = [f.line()]
        headers = [f.header(total=f.balanced(lines), contract_number='INS-H4-2024-2049')]
        opinion = audit(f.load(headers, lines), f.contract, f.maps)['opinions'][0]
        self.assertEqual(opinion['expected_total_cents'], opinion['billed_total_cents'])
        self.assertEqual(opinion['amount_unchanged_reason'], 'finding_does_not_affect_the_payable_amount')

    def test_every_withheld_invoice_still_carries_a_named_reason(self):
        f = Snapshot()
        lines = [f.line(description='Continuous Wnd Care', quantity=0, line_total_cents=0)]
        result = audit(f.load([f.header(total=f.balanced(lines))], lines), f.contract, f.maps)
        self.assertEqual(result['opinions'], [])
        self.assertTrue(all(a['reasons'] for a in result['abstentions']))


class RealSnapshotTests(unittest.TestCase):
    def test_every_quarantined_record_in_the_snapshot_is_a_named_structural_error(self):
        for hospital in ('H1', 'H2', 'H3', 'H4', 'H5'):
            with self.subTest(hospital=hospital):
                data = load_hospital(ROOT / 'data/source', hospital)
                reasons = [d['reason'] for d in data.dispositions if d['status'] == 'quarantined']
                self.assertTrue(reasons)
                self.assertTrue(all(quarantine_category(r) for r in reasons), reasons)


if __name__ == '__main__':
    unittest.main()
