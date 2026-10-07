import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from insurance_audit.results import FIELDS, TARGETS, validate_csv, export_results, verify_results

ROOT=Path(__file__).resolve().parents[1]
TEMPLATE=','.join(FIELDS)+'\n'


class ResultsTests(unittest.TestCase):
    def setUp(self):
        self.row=dict(invoice_id='INV-H3-1',hospital='H3',flagged=1,error_category='unit_basis_mismatch',
                      expected_total_cents=100,billed_total_cents=100,confidence=.65)

    def payload(self,row=None,fields=FIELDS):
        s=io.StringIO();w=csv.DictWriter(s,fields,lineterminator='\n',extrasaction='ignore');w.writeheader();w.writerow(row or self.row)
        return s.getvalue()

    def test_exact_format_and_unchanged_amount_error(self):
        self.assertEqual(validate_csv(self.payload(),[self.row],TEMPLATE),1)

    def test_invalid_numbers_identity_partial_and_columns_rejected(self):
        for field,value in [('confidence','NaN'),('confidence','inf'),('confidence','1.01'),('expected_total_cents','100.0'),('billed_total_cents',101),('flagged',2),('invoice_id','INV-H1-1')]:
            with self.subTest(field=field,value=value):
                row=copy.deepcopy(self.row);row[field]=value
                with self.assertRaises(ValueError):validate_csv(self.payload(row),[self.row],TEMPLATE)
        with self.assertRaises(ValueError):validate_csv(TEMPLATE,[self.row],TEMPLATE)
        with self.assertRaises(ValueError):validate_csv(self.payload(fields=FIELDS+('extra',)),[self.row],TEMPLATE)
        with self.assertRaises(ValueError):validate_csv(self.payload()+self.payload().split('\n',1)[1],[self.row],TEMPLATE)
        row=copy.deepcopy(self.row);row['hospital']='H1'
        with self.assertRaises(ValueError):validate_csv(self.payload(),[row],TEMPLATE)

    def test_failure_before_promotion_and_stale_release(self):
        # Tiny output-stage fixture; actual contract/line validation has separate tests.
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);attempt=root/'runs/attempts/fixture';attempt.mkdir(parents=True)
            (root/'data/source').mkdir(parents=True);(root/'data/source/submission_template.csv').write_text(TEMPLATE)
            (root/'evaluation').mkdir();(root/'evaluation/confidence_policy.json').write_text('{}')
            for h in TARGETS:
                (attempt/f'{h}.json').write_text(json.dumps({'opinions':[self.row] if h=='H3' else [],'accounting':{}}))
            status={'identity':{'revision':1},'run_id':'fixture-1','attempt':'fixture'}
            with patch('insurance_audit.results.current_run',return_value=(attempt,status)),patch('insurance_audit.results.decision_identity',return_value=status['identity']),patch('insurance_audit.results.load_hospital'),patch('insurance_audit.results.validate_result',return_value={'fixture':True}):
                first=export_results(root);pointer=(root/'runs/current-results.json').read_bytes();csv_bytes=(root/'audit_results.csv').read_bytes()
                with self.assertRaisesRegex(RuntimeError,'before promotion'):export_results(root,inject_failure=True)
                self.assertEqual(pointer,(root/'runs/current-results.json').read_bytes());self.assertEqual(csv_bytes,(root/'audit_results.csv').read_bytes())
                self.assertEqual(verify_results(root)['release_id'],first['release_id'])
                status['attempt']='fixture-2'
                with self.assertRaisesRegex(ValueError,'Stale'):verify_results(root)
                status['attempt']='fixture';(root/'audit_results.csv').write_text(TEMPLATE)
                with self.assertRaisesRegex(ValueError,'bytes changed'):verify_results(root)
