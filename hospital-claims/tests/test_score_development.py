import csv
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from score_development import FIELDS, read_predictions, score


def truth(flag,amount=100,categories=()):
    return {'flagged':flag,'expected_total_cents':amount,'categories':list(categories),'ambiguity_sensitive':0}


def pred(ident,flag,amount=100,confidence=.8):
    return {'invoice_id':ident,'flagged':flag,'expected_total_cents':amount,'billed_total_cents':100,
            'error_category':'pricing' if flag else '', 'confidence':confidence}


class ScoreTests(unittest.TestCase):
    def test_omissions_and_clean_fp(self):
        labels={'a':truth(1,categories=['unknown_service']),'b':truth(1,categories=['unit_price_mismatch']),
                'c':truth(0),'d':truth(0)}
        result=score(labels,{'a':pred('a',1),'c':pred('c',1)},{'a':'mapping','b':'pricing'})
        self.assertEqual([result[k] for k in ['tp','fn','fp','recall','precision']],[1,1,1,.5,.5])
        self.assertEqual(result['fn_invoice_ids'],['b'])
        self.assertEqual(result['false_positives_on_clean'][0]['invoice_id'],'c')

    def test_joint_reliability_and_tp_only_amounts(self):
        labels={'a':truth(1,120,['unknown_service']),'b':truth(0)}
        r=score(labels,{'a':pred('a',1,100,1),'b':pred('b',1,999,0)},{'a':'mapping'})
        self.assertEqual(r['tp_amount']['mae_cents'],20)
        self.assertEqual(r['tp_amount']['exact_match_rate'],0)
        self.assertEqual(r['reliability']['ece'],.5)
        self.assertEqual([b['count'] for b in r['reliability']['bins']],[1,0,0,0,1])

    def test_clean_omission_and_empty_confidence(self):
        r=score({'a':truth(0)},{},{})
        self.assertEqual((r['fn'],r['fp']),(0,0));self.assertIsNone(r['reliability']['ece'])
        self.assertIsNone(r['tp_amount']['exact_match_rate'])

    def test_csv_duplicate_nonfinite_and_fraction_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'pred.csv'
            for rows in [[pred('a',1),pred('a',1)],[pred('a',1,confidence=float('nan'))],[pred('a',1,100.5)]]:
                with p.open('w',newline='') as f:
                    w=csv.DictWriter(f,FIELDS);w.writeheader();w.writerows(rows)
                with self.assertRaises(ValueError):read_predictions(p)


if __name__=='__main__':unittest.main()
