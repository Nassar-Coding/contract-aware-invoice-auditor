import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from insurance_audit.batch import run_audit,current_run

ROOT=Path(__file__).resolve().parents[1]


class BatchTests(unittest.TestCase):
    def test_success_failure_and_repeatability(self):
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)
            first,status=run_audit(ROOT,['H1'],output)
            first_bytes=(first/'H1.json').read_bytes()
            self.assertGreater(status['results']['H1']['accounting']['opinions'],0)
            self.assertEqual(current_run(ROOT,['H1'],output)[0],first)
            pointer=(output/'current-H1.json').read_bytes()
            with self.assertRaisesRegex(RuntimeError,'Controlled failure'):run_audit(ROOT,['H1'],output,inject_failure=True)
            self.assertEqual((output/'current-H1.json').read_bytes(),pointer)
            self.assertEqual(len([p for p in (output/'attempts').glob('*/status.json') if json.loads(p.read_text())['status']=='failed']),1)
            second,second_status=run_audit(ROOT,['H1'],output)
            self.assertEqual(first_bytes,(second/'H1.json').read_bytes())
            self.assertEqual(status['run_id'],second_status['run_id'])
            with patch('insurance_audit.batch.decision_identity',return_value={'changed':'input'}):
                self.assertEqual(current_run(ROOT,['H1'],output)[0],second)
            (second/'H1.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'output modified'):current_run(ROOT,['H1'],output)
