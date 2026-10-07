"""The frozen decoy proxies must never be flagged.

Hospital 1's labels do not mark decoys, so this set is the nearest available
stand-in: development invoices the labels call clean that nevertheless carry a
pattern a careless check could trip on. A flag on any of them is a false
positive that would very likely repeat on real decoys in unlabelled data.
"""
import json
import sys
import unittest
from pathlib import Path

from insurance_audit.audit import audit
from insurance_audit.io import load_hospital
from insurance_audit.schema import load_bundle

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

FROZEN = ROOT / 'tests/evaluation/decoy_proxies.json'


class DecoyProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen = json.loads(FROZEN.read_text())
        contract, maps, _ = load_bundle(ROOT, 'H1')
        data = load_hospital(ROOT / 'data/source', 'H1')
        result = audit(data, contract, maps)
        cls.flagged = {r['invoice_id'] for r in result['opinions'] if r['flagged']}
        cls.emitted = {r['invoice_id'] for r in result['opinions']}

    def test_the_frozen_set_is_not_empty_and_covers_several_patterns(self):
        self.assertGreater(self.frozen['member_count'], 100)
        populated = [n for n, ids in self.frozen['patterns'].items() if ids]
        self.assertGreaterEqual(len(populated), 4, self.frozen['counts'])

    def test_no_check_flags_a_decoy_proxy(self):
        hits = sorted(set(self.frozen['members']) & self.flagged)
        self.assertEqual(hits, [], f'{len(hits)} decoy proxies were flagged')

    def test_no_single_pattern_produces_a_flag(self):
        for name, ids in sorted(self.frozen['patterns'].items()):
            with self.subTest(pattern=name):
                self.assertEqual(sorted(set(ids) & self.flagged), [])

    def test_the_frozen_set_still_matches_the_current_snapshot(self):
        if not (ROOT / 'data/source/labels/hospital_1_labels.csv').is_file():
            self.skipTest('Labels are not present in this checkout')
        from mine_decoy_proxies import mine
        self.assertEqual(mine(ROOT)['members'], self.frozen['members'])


if __name__ == '__main__':
    unittest.main()
