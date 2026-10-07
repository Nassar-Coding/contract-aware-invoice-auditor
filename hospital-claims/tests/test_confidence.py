"""Confidence comes from the evidence tier, and never rises above the evidence."""
import copy
import unittest

from insurance_audit.confidence import choose


class ConfidenceTests(unittest.TestCase):
    def setUp(self):
        self.policy = {'version': '2', 'minimum_empirical_n': 30, 'target_discount': [9, 10],
                       'tiers': {'clean': {'n': 400, 'confidence': .99},
                                 'structural': {'n': 100, 'confidence': .80},
                                 'match_fully_priced': {'n': 7, 'confidence': .60},
                                 'ambiguous': {'n': 2, 'confidence': .09}},
                       'qualification_caps': {'outcome_invariant': .65, 'interpretation': .65,
                                              'capped_quantity_substituted_for_an_unobserved_one': .45}}
        self.row = {'hospital': 'H1', 'evidence_tier': 'clean', 'flagged': 0, 'expected_total_cents': 100}

    def test_a_well_populated_tier_is_marked_fitted_and_a_sparse_one_is_not(self):
        self.assertEqual(choose(self.row, self.policy), (.99, 'h1_fitted:clean'))
        sparse = dict(self.row, evidence_tier='match_fully_priced', flagged=1)
        self.assertEqual(choose(sparse, self.policy), (.60, 'h1_sparse:match_fully_priced'))

    def test_an_unlabelled_hospital_is_held_below_the_fitted_value(self):
        value, label = choose(dict(self.row, hospital='H5'), self.policy)
        self.assertEqual(label, 'novel_reviewed:clean')
        self.assertLess(value, self.policy['tiers']['clean']['confidence'])
        self.assertEqual(value, .89)

    def test_every_tier_stays_below_its_hospital_1_value_on_an_unlabelled_hospital(self):
        for tier, group in self.policy['tiers'].items():
            with self.subTest(tier=tier):
                fitted = choose(dict(self.row, evidence_tier=tier), self.policy)[0]
                novel = choose(dict(self.row, hospital='H4', evidence_tier=tier), self.policy)[0]
                self.assertLessEqual(novel, fitted)

    def test_an_unresolved_row_gets_no_confidence_at_all(self):
        self.assertEqual(choose(dict(self.row, unresolved=True), self.policy), (None, 'unresolved'))

    def test_an_invariant_outcome_is_capped(self):
        self.assertEqual(choose(dict(self.row, outcome_invariant_uncertainty=True), self.policy)[0], .65)

    def test_a_substituted_cap_quantity_is_penalised_further_than_other_qualifications(self):
        capped = dict(self.row, interpretation_qualifications=['capped_quantity_substituted_for_an_unobserved_one'])
        other = dict(self.row, interpretation_qualifications=['unobserved_submission_deadline_and_waiver'])
        self.assertEqual(choose(capped, self.policy)[0], .45)
        self.assertEqual(choose(other, self.policy)[0], .65)
        self.assertLess(choose(capped, self.policy)[0], choose(other, self.policy)[0])

    def test_an_unknown_tier_is_refused_rather_than_guessed(self):
        with self.assertRaises(ValueError):
            choose(dict(self.row, evidence_tier='invented'), self.policy)

    def test_nonfinite_or_invalid_confidence_fails(self):
        for value in [float('nan'), float('inf'), -1, 1.1, True]:
            policy = copy.deepcopy(self.policy)
            policy['tiers']['clean']['confidence'] = value
            with self.assertRaises(ValueError):
                choose(self.row, policy)

    def test_the_shipped_policy_is_monotone_across_the_tiers(self):
        import json
        from pathlib import Path
        order = ('ambiguous', 'partial_amount', 'no_match', 'match_fully_priced', 'structural', 'clean')
        tiers = json.loads((Path(__file__).resolve().parents[1] /
                            'evaluation/confidence_policy.json').read_text())['tiers']
        values = [tiers[name]['confidence'] for name in order]
        self.assertEqual(values, sorted(values), dict(zip(order, values)))


if __name__ == '__main__':
    unittest.main()
