"""Portability and label isolation regressions; synthetic fixtures only."""
import csv
import io
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch
import warnings
import sys

from insurance_audit.pipeline import reproduce
from insurance_audit.results import validate_csv,FIELDS
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from generalize import perturb,CODE,sample_patients


class ReplayIndependenceTests(unittest.TestCase):
    def pipeline_fixture(self,root,evaluate=False,version='3.12.14',evaluation_error=None):
        status={'attempt':'synthetic'}
        with patch('insurance_audit.pipeline.platform.python_version',return_value=version), \
             patch('insurance_audit.pipeline.run_audit',return_value=(root,status)), \
             patch('insurance_audit.pipeline.export_results',return_value={'rows':0,'results_sha256':'recorded-only'}), \
             patch('insurance_audit.pipeline.render_reports',return_value={'H1':{'opinions':0}}), \
             patch('insurance_audit.pipeline.evaluate_run',side_effect=evaluation_error) as evaluator:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always');result=reproduce(root,evaluate_development=evaluate)
            return result,evaluator.call_args_list,caught

    def test_other_patch_warns_without_python_version_file(self):
        with tempfile.TemporaryDirectory() as d:
            result,calls,caught=self.pipeline_fixture(Path(d))
            self.assertEqual(result['status'],'success');self.assertFalse(calls);self.assertTrue(caught)

    def test_reference_patch_does_not_warn(self):
        with tempfile.TemporaryDirectory() as d:
            result,calls,caught=self.pipeline_fixture(Path(d),version='3.12.13')
            self.assertEqual(result['status'],'success');self.assertFalse(caught)

    def test_optional_evaluation_skips_when_either_input_absent(self):
        for missing in ['labels','manifest']:
            with self.subTest(missing=missing),tempfile.TemporaryDirectory() as d:
                root=Path(d);label=root/'data/source/labels/hospital_1_labels.csv';manifest=root/'tests/evaluation/split_manifest.json'
                present=manifest if missing=='labels' else label;present.parent.mkdir(parents=True);present.write_text('{}')
                result,calls,_=self.pipeline_fixture(root,evaluate=True)
                self.assertFalse(calls);self.assertEqual(result['evaluation']['status'],'skipped_missing_labels_or_manifest')

    def test_optional_evaluation_failure_cannot_fail_predictions(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for name in ['data/source/labels/hospital_1_labels.csv','tests/evaluation/split_manifest.json']:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
            result,calls,_=self.pipeline_fixture(root,evaluate=True,evaluation_error=ValueError('bad label fixture'))
            self.assertEqual(result['status'],'success');self.assertEqual(result['evaluation']['status'],'failed_nonfatal')
            self.assertEqual(calls[0].args[1],'development');self.assertEqual(len(calls),1)

    def test_prediction_replay_never_opens_labels_or_split(self):
        with tempfile.TemporaryDirectory() as d:
            original=Path.open
            def guarded(path,*args,**kwargs):
                if 'labels' in path.parts or path.name=='split_manifest.json':raise AssertionError('Evaluation input opened')
                return original(path,*args,**kwargs)
            with patch.object(Path,'open',guarded):
                result,calls,_=self.pipeline_fixture(Path(d))
            self.assertFalse(calls);self.assertFalse(result['evaluation']['check_partition_read'])

    def test_all_withheld_is_header_only_valid_results(self):
        header=','.join(FIELDS)+'\n'
        self.assertEqual(validate_csv(header,[],header),0)

    def test_code_is_preserved_exactly_or_removed_never_mutated(self):
        original='Routine Procedure /NG-3022'
        kept,actions,_=perturb(original,True,1);removed,_,_=perturb(original,False,0)
        self.assertEqual(CODE.findall(kept),['/NG-3022']);self.assertFalse(CODE.findall(removed))
        self.assertEqual(set(actions),{'case','abbreviation','separator_token_order','one_character_typo','whitespace'})
        self.assertNotEqual(kept,original);self.assertNotEqual(removed,original)

    def test_connected_patient_sampling_preserves_reused_id_owners(self):
        headers=[{'patient_id':f'P{i}','invoice_id':f'I{i}'} for i in range(10)]
        headers.append({'patient_id':'P1','invoice_id':'I0'})
        patients,total,target=sample_patients(headers,random.Random(10))
        self.assertEqual(len(patients),target);self.assertEqual(total,10)
        self.assertEqual('P0' in patients,'P1' in patients)

    def test_development_loader_skips_invalid_unselected_truth(self):
        from insurance_audit.evaluation import load_labels
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'hospital_1_labels.csv'
            path.write_text('invoice_id,is_erroneous,error_categories,expected_total_cents,ambiguity_sensitive\nD,1,unit_price_mismatch,100,0\nC,POISON,UNREAD,POISON,POISON\n')
            result=load_labels(path,{'D':'development','C':'check'},'development')
            self.assertEqual(set(result),{'D'})
