import unittest
from insurance_audit.evaluation import binary_metrics,measure


class EvaluationTests(unittest.TestCase):
    def test_known_answer_and_abstention_denominators(self):
        m=binary_metrics([1,1,0,0,1],[1,0,1,0,None])
        self.assertEqual((m['tp'],m['fp'],m['fn_including_abstentions'],m['tn_emitted']),(1,1,2,1))
        self.assertEqual(m['precision'],.5);self.assertEqual(m['recall'],1/3);self.assertEqual(m['f1'],.4)
        self.assertIsNone(binary_metrics([0],[0])['precision'])
        self.assertIsNone(binary_metrics([0],[0])['recall'])

    def test_whole_row_requires_flag_and_amount(self):
        labels={str(i):{'invoice_id':str(i),'flagged':i%2,'expected_total_cents':100,'categories':[],'ambiguity_sensitive':0} for i in range(4)}
        predictions=[{'invoice_id':'0','flagged':0,'expected_total_cents':99,'error_category':'','confidence':.8},
                     {'invoice_id':'1','flagged':0,'expected_total_cents':100,'error_category':'','confidence':.8},
                     {'invoice_id':'2','flagged':0,'expected_total_cents':100,'error_category':'','confidence':.8}]
        m=measure(labels,predictions)
        self.assertEqual((m['covered'],m['abstained'],m['whole_row_success_count']),(3,1,1))
        self.assertEqual(m['flag_accuracy_covered'],2/3);self.assertEqual(m['amount_accuracy_covered'],2/3)
        self.assertEqual(m['whole_row_accuracy_covered'],1/3)

    def test_free_text_category_crosswalk_is_explicit(self):
        labels={'1':{'invoice_id':'1','flagged':1,'expected_total_cents':100,'categories':['volume_discount_omitted'],'ambiguity_sensitive':0}}
        p=[{'invoice_id':'1','flagged':1,'expected_total_cents':100,'error_category':'effective_rate_mismatch;line_amount_mismatch','confidence':.9}]
        r=measure(labels,p)
        self.assertEqual(r['per_category_family']['pricing']['f1'],1)
        self.assertEqual(r['per_original_label_detection']['volume_discount_omitted']['whole_row_success'],1)
