"""Daily caps and exclusion windows: the readings taken, and the evidence needed."""
import json
import unittest
from pathlib import Path

from insurance_audit.audit import contract_rule_findings
from insurance_audit.context import Context
from insurance_audit.io import load_hospital
from insurance_audit.schema import load_bundle, validate_rules

ROOT = Path(__file__).resolve().parents[1]


class ReviewedRuleFileTests(unittest.TestCase):
    def test_every_hospital_ships_a_rule_file_that_matches_its_contract(self):
        for hospital in ('H1', 'H2', 'H3', 'H4', 'H5'):
            with self.subTest(hospital=hospital):
                contract, _, _ = load_bundle(ROOT, hospital)
                rules = contract['rules']
                self.assertIsNotNone(rules)
                validate_rules(rules, contract)
                self.assertTrue(rules['daily_caps'] or rules['exclusion_windows'])
                for row in rules['daily_caps'] + rules['exclusion_windows']:
                    self.assertTrue(row['refs'], 'every rule needs a clause citation')
                    self.assertEqual((row['effective_from'], row['effective_to']), tuple(contract['term']))

    def test_the_boundary_and_direction_readings_are_recorded(self):
        for hospital in ('H1', 'H2', 'H3', 'H4', 'H5'):
            with self.subTest(hospital=hospital):
                rules = load_bundle(ROOT, hospital)[0]['rules']
                self.assertEqual(rules['exclusion_boundary'], 'inclusive_at_equal')
                self.assertIn(rules['exclusion_direction'],
                              {'both', 'uncertain_before_or_both', 'uncertain_direction'})
                self.assertIn('within', rules['boundary_reading'])
                self.assertTrue(rules['direction_reading'])

    def test_hospital_3_records_that_its_amendment_leaves_the_rules_alone(self):
        rules = load_bundle(ROOT, 'H3')[0]['rules']
        self.assertIn('A1.4.1', rules['amendment_note'])

    def test_a_cap_that_disagrees_with_the_contract_is_rejected(self):
        contract, _, _ = load_bundle(ROOT, 'H1')
        rules = json.loads(json.dumps(contract['rules']))
        rules['daily_caps'][0]['maximum_units_per_patient_per_service_day'] += 1
        with self.assertRaises(Exception):
            validate_rules(rules, contract)

    def test_dropping_an_exclusion_window_is_rejected(self):
        contract, _, _ = load_bundle(ROOT, 'H1')
        rules = json.loads(json.dumps(contract['rules']))
        rules['exclusion_windows'].pop()
        with self.assertRaises(Exception):
            validate_rules(rules, contract)


class CapAndExclusionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract, cls.maps, _ = load_bundle(ROOT, 'H1')
        cls.data = load_hospital(ROOT / 'data/source', 'H1')
        cls.context = Context(cls.data, cls.contract, cls.maps)
        cls.services = {s['id']: s for s in cls.contract['services']}
        cls.headers = {r.invoice_id: r for r in cls.data.invoices}
        cls.lines = {r.line_id: r for r in cls.data.lines}

    def findings(self, line_id, service_id):
        line = self.lines[line_id]
        invoice = self.headers[line.invoice_id]
        return contract_rule_findings(line, invoice, self.services[service_id], self.contract, self.context)

    def test_a_quantity_over_the_cap_is_reported_with_the_service_day_total(self):
        found = [f for f in self.findings('H1-L00049-04', 'H1-S027') if f['finding'] == 'daily_cap_exceeded']
        self.assertEqual(len(found), 1)
        self.assertGreater(found[0]['billed_this_service_day'],
                           found[0]['maximum_units_per_patient_per_service_day'])
        self.assertTrue(found[0]['refs'])

    def test_a_quantity_within_the_cap_reports_nothing(self):
        for service_id, service in self.services.items():
            if service['daily_cap'] is None:
                continue
            for line in self.data.lines:
                invoice = self.headers.get(line.invoice_id)
                if invoice is None or line.quantity > service['daily_cap']:
                    continue
                total = self.context.service_day_total(service_id, invoice.patient_id, line.service_date)
                if total['lower'] is not None and total['lower'] <= service['daily_cap']:
                    self.assertEqual([f for f in contract_rule_findings(line, invoice, service, self.contract,
                                                                        self.context)
                                      if f['finding'] == 'daily_cap_exceeded'], [])
                    return
        self.skipTest('no within-cap line available')

    def test_an_anchor_exactly_the_window_away_is_inside_the_window(self):
        found = [f for f in self.findings('H1-L00646-09', 'H1-S004')
                 if f['finding'] == 'exclusion_window_violation']
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['anchor_service_id'], 'H1-S100')
        self.assertEqual(found[0]['window_days'], 7)
        self.assertTrue(found[0]['refs'])

    def test_the_service_day_total_sums_across_invoices(self):
        line = self.lines['H1-L00049-04']
        invoice = self.headers[line.invoice_id]
        total = self.context.service_day_total('H1-S027', invoice.patient_id, line.service_date)
        self.assertEqual(total['query']['day'], line.service_date)
        self.assertGreaterEqual(total['lower'], line.quantity)

    def test_the_service_day_total_uses_the_calendar_date_on_hospital_2(self):
        contract, maps, _ = load_bundle(ROOT, 'H2')
        self.assertEqual(contract['semantics']['service_day'], 'seven_am_unobserved')
        data = load_hospital(ROOT / 'data/source', 'H2')
        context = Context(data, contract, maps)
        capped = next(s for s in contract['services'] if s['daily_cap'] is not None)
        header = {r.invoice_id: r for r in data.invoices}
        for line in data.lines:
            invoice = header.get(line.invoice_id)
            if invoice is None:
                continue
            total = context.service_day_total(capped['id'], invoice.patient_id, line.service_date)
            self.assertEqual(total['query']['day'], line.service_date)
            return


if __name__ == '__main__':
    unittest.main()
