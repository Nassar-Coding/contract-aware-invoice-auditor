from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from decision_trace import cross_tab,histogram,mapping_state,observe


class TraceTests(unittest.TestCase):
    def test_states_do_not_treat_runtime_all_service_fallback_as_tie(self):
        index={'match':{'state':'accepted','service_id':'s','candidates':['s']},
               'tie':{'state':'unresolved','candidates':['a','b']},'weak':{'state':'unresolved','candidates':['a']}}
        self.assertEqual([mapping_state(x,index)['state'] for x in ['match','tie','weak','missing']],['MATCH','TIE','WEAK','NO_MATCH'])

    def test_early_withholding_retains_quarantined_line_and_posthoc_provenance(self):
        data=SimpleNamespace(invoice_identities=lambda:{'a'},dispositions=[{'kind':'line_items','invoice_id':'a',
            'line_id':'l','source':'lines.csv','source_row':2,'raw_named':{'description':'x'},'status':'quarantined','reason':'invalid date'}])
        result={'hospital':'H1','opinions':[],'traces':[{'invoice_id':'a','lines':[],
                'reasons':[{'reason':'quarantined_required_source_record'}]}]}
        row=list(observe(result,data,{}))[0]
        self.assertEqual(row['decision'],'withhold');self.assertEqual(row['amount_status'],'none')
        self.assertEqual(row['lines'][0]['mapping_observation'],'posthoc_lookup_only')
        self.assertEqual(row['unresolved_facts'][-1]['line_ids'],['l'])
        result['traces'][0]['reasons']=[]
        with self.assertRaises(ValueError):list(observe(result,data,{}))

    def test_partial_is_diagnostic_and_does_not_emit(self):
        raw={'kind':'line_items','invoice_id':'a','line_id':'l','source':'lines.csv','source_row':2,
             'raw_named':{'description':'x'},'status':'accepted','reason':None}
        data=SimpleNamespace(invoice_identities=lambda:{'a'},dispositions=[raw])
        result={'hospital':'H2','opinions':[],'traces':[{'invoice_id':'a','reasons':[{'reason':'eligibility'}],
            'lines':[{'source':'lines.csv','source_row':2,'status':'supported','result':{'error_categories':['price']}}]}]}
        row=list(observe(result,data,{}))[0]
        self.assertEqual((row['decision'],row['amount_status'],row['confidence']),('withhold','partial',None))
        self.assertEqual(row['checks_fired'],['price'])

    def test_histograms_and_crosstab_use_exclusive_first_reason(self):
        rows=[{'invoice_id':'a','decision':'withhold','primary_withheld_reason':'mapping','withheld_reasons':['mapping','mapping','history']},
              {'invoice_id':'b','decision':'withhold','primary_withheld_reason':'history','withheld_reasons':['history']}]
        h=histogram(rows);self.assertEqual(h['primary_reason'],{'mapping':1,'history':1})
        self.assertEqual(h['any_reason_invoice_incidence'],{'mapping':1,'history':2})
        c=cross_tab(['a','b'],{'a':'structural','b':'pricing'},rows)
        self.assertEqual(c['exclusive_total'],2);self.assertEqual(len(c['any_reason_crosstab']),3)


if __name__=='__main__':unittest.main()
