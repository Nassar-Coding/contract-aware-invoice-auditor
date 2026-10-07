"""Hand-computed pricing examples taken from the clauses themselves.

Every expected figure below is worked out by hand from the quoted clause, not
read back from the engine, so a change that silently alters the arithmetic
fails here rather than being absorbed.
"""
import json
import unittest
from pathlib import Path

from test_targets import fixture, price

from insurance_audit.checks import line_result, rate_difference_category
from insurance_audit.pricing import half_up

ROOT = Path(__file__).resolve().parents[1]


def service(hospital, name):
    contract = json.loads((ROOT / f'contracts/hospital_{hospital[1:]}.json').read_text())
    return next(s for s in contract['services'] if s['name'] == name)


class RoundingTests(unittest.TestCase):
    """Half-up to whole cents after each step (H1-H3 clause 3.1, H4 clause 4.2)."""

    def test_an_exact_half_rounds_up_and_a_negative_half_rounds_away_from_zero(self):
        self.assertEqual(half_up(9675, [90, 100]), 8708)      # 8707.5 -> 8708
        self.assertEqual(half_up(-9675, [90, 100]), -8708)
        self.assertEqual(half_up(157150, [85, 100]), 133578)  # 133577.5 -> 133578

    def test_rounding_after_each_step_is_not_the_same_as_rounding_once(self):
        # 101 x 0.85 x 0.90: stepwise gives 86 x 0.9 = 77.4 -> 77; combined
        # 101 x 0.765 = 77.265 -> 77. A case where they differ:
        stepwise = half_up(half_up(105, [85, 100]), [90, 100])
        combined = half_up(105, [85 * 90, 100 * 100])
        self.assertEqual((stepwise, combined), (80, 80))
        stepwise = half_up(half_up(11, [50, 100]), [50, 100])
        combined = half_up(11, [25, 100])
        self.assertEqual((stepwise, combined), (3, 3))
        # The engine rounds after every step; a multiplier of one still rounds.
        self.assertEqual(half_up(12345, [1, 1]), 12345)


class VolumeDiscountTests(unittest.TestCase):
    """Cumulative utilisation is counted up to but excluding the line priced.

    H1 clause 2.4, H2 clauses 2.7 and 3.5, H3 clause 6.1, H4 clauses 8.3-8.5 and
    H5 clause 8 all define the discount the same way: it applies to a *line
    item* whose prior cumulative utilisation exceeds the threshold, and not to
    the line on which the threshold is first crossed.
    """

    def utilisation(self, hospital, name, prior, patient='P2'):
        # H1, H2, H3 and H5 aggregate utilisation across all patients; H4's
        # clause does not say, so its history is only certain for the same
        # patient and a different one leaves the rate undetermined.
        return price(hospital, name,
                     history=[(name, '2025-01-01', prior, patient, 'F-MAIN', 'BRONZE')])['rate']

    def test_hospital_2_does_not_discount_the_line_that_crosses_the_threshold(self):
        # Clause 8.3: GBP 96.75 per visit, 10% over 60 visits, 20% over 180.
        name = 'Emergency Haematology Transport Service'
        self.assertEqual(service('H2', name)['versions'][0]['cents'], 9675)
        self.assertEqual(self.utilisation('H2', name, 59), 9675)    # below the threshold
        self.assertEqual(self.utilisation('H2', name, 60), 9675)    # the crossing line
        self.assertEqual(self.utilisation('H2', name, 61), 8708)    # 9675 x 0.90 = 8707.5
        self.assertEqual(self.utilisation('H2', name, 180), 8708)   # deeper threshold not yet passed
        self.assertEqual(self.utilisation('H2', name, 181), 7740)   # 9675 x 0.80

    def test_hospital_2_prices_a_whole_line_at_one_rate_not_unit_by_unit(self):
        # Clause 3.5 applies the discount "to a line item where cumulative
        # utilisation of the Service prior to that line item exceeds the
        # threshold", so a line that would straddle 60 is priced whole on its
        # prior utilisation. A 10-unit line with 55 prior is not split into 5
        # undiscounted and 5 discounted units.
        name = 'Emergency Haematology Transport Service'
        contract, svc, line, invoice, context, _, _ = fixture(
            'H2', name, quantity=10, history=[(name, '2025-01-01', 55, 'P2', 'F-MAIN', 'BRONZE')])
        from insurance_audit.pricing import rate_for
        rate = rate_for(line, invoice, svc, contract, context)['rate']
        self.assertEqual(rate, 9675)
        self.assertEqual(rate * 10, 96750)
        self.assertNotEqual(rate * 10, 5 * 9675 + 5 * 8708)

    def test_a_different_patient_leaves_hospital_4_utilisation_undetermined(self):
        from insurance_audit.pricing import Uncertain
        name = 'Ambulatory Musculoskeletal Ventilation Support'
        with self.assertRaises(Uncertain):
            self.utilisation('H4', name, 81, 'P2')

    def test_hospital_4_does_not_discount_the_line_that_crosses_the_threshold(self):
        # Clause 8.4 states it outright, with thresholds 80 (15%) and 240 (30%).
        name = 'Ambulatory Musculoskeletal Ventilation Support'
        self.assertEqual(service('H4', name)['versions'][0]['cents'], 157150)
        self.assertEqual(self.utilisation('H4', name, 80, 'P1'), 157150)   # crossing line, no discount
        self.assertEqual(self.utilisation('H4', name, 81, 'P1'), 133578)   # 157150 x 0.85 = 133577.5
        self.assertEqual(self.utilisation('H4', name, 240, 'P1'), 133578)  # deeper threshold not passed
        self.assertEqual(self.utilisation('H4', name, 241, 'P1'), 110005)  # 157150 x 0.70

    def test_the_deeper_discount_replaces_the_shallower_one_and_they_do_not_compound(self):
        name = 'Ambulatory Musculoskeletal Ventilation Support'
        deep = self.utilisation('H4', name, 241, 'P1')
        self.assertEqual(deep, half_up(157150, [70, 100]))
        self.assertNotEqual(deep, half_up(half_up(157150, [85, 100]), [70, 100]))


class DailyCapTests(unittest.TestCase):
    """Section 8 caps the billable units per patient per Service Day."""

    def test_the_billable_quantity_is_the_cap_and_the_excess_is_not_payable(self):
        svc = service('H1', 'Comprehensive Oncology Nursing Observation')
        self.assertEqual(svc['daily_cap'], 12)
        from insurance_audit.io import Line
        line = Line('lines.csv', 2, 'H1', 'L1', 'I1', 1, '2025-01-02', svc['name'], 14,
                    svc['unit'], 8475, 14 * 8475)
        result = line_result(line, svc, {'rate': 8475, 'excluded': False, 'stages': []})
        self.assertEqual(result['billable_quantity'], 12)
        self.assertEqual(result['expected_total_cents'], 12 * 8475)
        self.assertIn('daily_cap_exceeded', result['error_categories'])

    def test_a_quantity_at_the_cap_is_payable_in_full(self):
        svc = service('H1', 'Comprehensive Oncology Nursing Observation')
        from insurance_audit.io import Line
        line = Line('lines.csv', 2, 'H1', 'L1', 'I1', 1, '2025-01-02', svc['name'], 12,
                    svc['unit'], 8475, 12 * 8475)
        result = line_result(line, svc, {'rate': 8475, 'excluded': False, 'stages': []})
        self.assertEqual(result['expected_total_cents'], 12 * 8475)
        self.assertNotIn('daily_cap_exceeded', result['error_categories'])


class AdjustmentNamingTests(unittest.TestCase):
    """A wrong rate is named after the adjustment that accounts for it."""

    def test_a_rate_billed_without_the_contract_uplift_is_a_premium_omitted(self):
        svc = service('H3', 'Specialist Psychiatric Discharge Planning')
        self.assertNotEqual(tuple(svc['weekend_multiplier']), (1, 1))
        uplifted = half_up(svc['versions'][0]['cents'], svc['weekend_multiplier'])
        self.assertEqual(rate_difference_category({'rate': uplifted, 'stages': []},
                                                  svc['versions'][0]['cents'], svc),
                         'premium_omitted')

    def test_an_uplift_the_contract_does_not_give_is_the_reverse(self):
        svc = service('H3', 'Specialist Psychiatric Discharge Planning')
        base = svc['versions'][0]['cents']
        uplifted = half_up(base, svc['weekend_multiplier'])
        self.assertEqual(rate_difference_category({'rate': base, 'stages': []}, uplifted, svc),
                         'premium_incorrectly_applied')

    def test_a_discount_the_provider_did_not_pass_on_is_named_as_omitted(self):
        svc = service('H2', 'Emergency Haematology Transport Service')
        self.assertEqual(rate_difference_category({'rate': 8708, 'stages': []}, 9675, svc),
                         'volume_discount_omitted')

    def test_a_discount_the_contract_does_not_give_is_named_as_applied(self):
        svc = service('H2', 'Emergency Haematology Transport Service')
        self.assertEqual(rate_difference_category({'rate': 9675, 'stages': []}, 8708, svc),
                         'volume_discount_incorrectly_applied')

    def test_a_bundle_the_provider_did_not_apply_is_named_as_such(self):
        svc = service('H1', 'Advanced Cardiac Recovery Room Occupancy')
        base = svc['versions'][0]['cents']
        stages = [{'operation': 'bundle', 'before': [base], 'after': [16400],
                   'base_rate': {'cents': base}}]
        self.assertEqual(rate_difference_category({'rate': 16400, 'stages': stages}, base, svc),
                         'bundle_not_applied')

    def test_a_rate_no_adjustment_explains_stays_a_unit_price_mismatch(self):
        svc = service('H1', 'Advanced Neurological Consultation')
        self.assertEqual(rate_difference_category({'rate': 500, 'stages': []}, 401, svc),
                         'unit_price_mismatch')

    def test_a_matching_rate_is_not_named_at_all(self):
        svc = service('H1', 'Advanced Neurological Consultation')
        self.assertIsNone(rate_difference_category({'rate': 500, 'stages': []}, 500, svc))


class AdjustmentOrderTests(unittest.TestCase):
    """Clause 3.2 fixes the order in all five agreements."""

    def test_every_contract_declares_the_same_order(self):
        for n in range(1, 6):
            with self.subTest(hospital=f'H{n}'):
                contract = json.loads((ROOT / f'contracts/hospital_{n}.json').read_text())
                self.assertEqual(contract['semantics']['adjustment_order'],
                                 ['bundle', 'facility', 'tier', 'daily_premium', 'weekend_uplift',
                                  'volume_discount'])
                self.assertEqual(contract['semantics']['rounding'], 'half_up_cent')

    def test_the_engine_emits_its_stages_in_the_declared_order(self):
        stages = [s['operation'] for s in price('H1', 'Advanced Neurological Consultation')['stages']]
        declared = ['bundle', 'facility', 'tier', 'daily_premium', 'weekend_uplift', 'volume_discount']
        self.assertEqual(stages, [s for s in declared if s in stages])


if __name__ == '__main__':
    unittest.main()
