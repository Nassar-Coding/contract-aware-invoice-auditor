"""Regressions for quarantined-header ownership and bundle citations, on the accepted rules and raw CSV inputs."""
import copy
import csv
import json
from pathlib import Path
import random
import tempfile
import unittest

from insurance_audit.audit import audit
from insurance_audit.confidence import assign_confidence
from insurance_audit.context import Context
from insurance_audit.io import INVOICE_COLUMNS, LINE_COLUMNS, load_hospital
from insurance_audit.pricing import rate_for, Uncertain
from insurance_audit.results import validate_result

ROOT=Path(__file__).resolve().parents[1]
POLICY=json.loads((ROOT/'evaluation/confidence_policy.json').read_text())


class Snapshot:
    def __init__(self,hospital='H1'):
        self.hospital=hospital
        self.contract=json.loads((ROOT/f'contracts/hospital_{hospital[1:]}.json').read_text())
        self.maps=json.loads((ROOT/f'mappings/hospital_{hospital[1:]}.json').read_text())
        self.services={s['id']:s for s in self.contract['services']}

    def header(self,ident,patient,**changes):
        row=dict(invoice_id=ident,hospital_id=self.hospital,contract_number=self.contract['contract_number'],
                 invoice_date='2025-12-31',patient_id=patient,facility_code='F-MAIN',plan_tier='BRONZE',
                 admission_date='2024-01-01',discharge_date='2024-01-02',
                 invoice_total_cents=9200 if ident=='I1' else 124450)
        row.update(changes);return row

    def line(self,ident,lid,sid,**changes):
        service=self.services[sid]
        alias=next(r['raw_descriptions'][0] for r in self.maps['records']
                   if r['state']=='accepted' and r['service_id']==sid and r['grade']=='explicit')
        rate=service['versions'][0]['cents']
        row=dict(line_id=lid,invoice_id=ident,line_no=1,service_date='2024-01-02',description=alias,
                 quantity=1,unit_basis_as_billed=service['unit'],unit_price_cents=rate,line_total_cents=rate)
        row.update(changes);return row

    def load(self,headers,lines,reverse_columns=False):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'invoices').mkdir()
            for kind,columns,rows in [('invoices',INVOICE_COLUMNS,headers),('line_items',LINE_COLUMNS,lines)]:
                with (root/f'invoices/hospital_{self.hospital[1:]}_{kind}.csv').open('w',newline='') as stream:
                    writer=csv.DictWriter(stream,list(reversed(columns)) if reverse_columns else columns)
                    writer.writeheader();writer.writerows(rows)
            return load_hospital(root,self.hospital)

    def run(self,headers,lines,reverse_columns=False):
        data=self.load(headers,lines,reverse_columns)
        result=audit(data,self.contract,self.maps);assign_confidence(result,POLICY)
        validation=validate_result(result,data,POLICY)
        return data,result,validation

    def exclusion_fixture(self,**changed_header):
        headers=[self.header('I1','P1',invoice_date='2024-01-03'),
                 self.header('I2','P1',invoice_date='2024-01-03'),
                 self.header('I2','P2',invoice_date='2024-01-03')]
        headers[-1].update(changed_header)
        return headers,[self.line('I1','L1','H1-S004'),self.line('I2','L2','H1-S100')]


class HeaderOwnershipRegressions(unittest.TestCase):
    def test_reviewer_counterexample_through_confidence_and_validation(self):
        f=Snapshot()
        for changes in [{},{'invoice_date':'2024-02-30'}]:
            with self.subTest(changes=changes):
                data,result,validation=f.run(*f.exclusion_fixture(**changes))
                self.assertEqual([r['invoice_id'] for r in result['opinions']],['I2'])
                self.assertEqual(validation['traces_checked'],2)
                self.assertEqual((validation['complete_audits'],validation['finding_rows']),(0,1))
                traces={t['invoice_id']:t for t in result['traces']}
                self.assertEqual(traces['I1']['reasons'][0]['reason'],'unresolved_exclusion')
                # I2's identifier is on two physical records: that reuse is itself
                # the error and is reported although its exclusion state is unknown.
                self.assertEqual(traces['I2']['status'],'supported')
                self.assertIn('duplicate_invoice_id',traces['I2']['findings'])
                if changes:
                    detail=traces['I1']['reasons'][0]['detail']
                    self.assertEqual((detail['certain_records'],detail['possible_records']),(0,1))
                    ownership=detail['header_ownership_evidence'][0]
                    self.assertEqual(ownership['candidate_patients'],['P1','P2'])
                    self.assertFalse(ownership['unbounded_patient_identity'])
                    self.assertEqual([h['row'] for h in ownership['headers']],[3,4])
                    self.assertEqual(data.quarantined_headers()[0]['raw_named']['invoice_date'],'2024-02-30')
                    self.assertEqual([h['row'] for h in traces['I2']['source_headers']],[3,4])

    def test_other_malformed_fields_and_reordered_columns_preserve_conflict(self):
        f=Snapshot()
        for changes in [{'invoice_total_cents':'NaN'},{'admission_date':'not-a-date'},
                        {'discharge_date':'2024-02-30'}]:
            with self.subTest(changes=changes):
                _,result,_=f.run(*f.exclusion_fixture(**changes),reverse_columns=True)
                self.assertEqual([r['invoice_id'] for r in result['abstentions']],['I1'])
                self.assertEqual(result['abstentions'][0]['reasons'][0]['reason'],'unresolved_exclusion')

    def test_missing_patient_is_unbounded_not_a_false_singleton(self):
        f=Snapshot();headers,lines=f.exclusion_fixture(patient_id='')
        headers[1]['patient_id']='P2'
        data,result,_=f.run(headers,lines);ctx=Context(data,f.contract,f.maps)
        for patient in ['P1','P2','P3']:
            present,detail=ctx.presence('H1-S100',patient,'2024-01-02')
            self.assertIsNone(present)
            self.assertEqual(detail['header_ownership_evidence'][0]['candidate_patients'],['P2'])
            self.assertTrue(detail['header_ownership_evidence'][0]['unbounded_patient_identity'])
        self.assertEqual([r['invoice_id'] for r in result['abstentions']],['I1'])

    def test_only_quarantined_header_retains_identity_and_own_omission(self):
        f=Snapshot();headers,lines=f.exclusion_fixture(invoice_date='2024-02-30')
        headers.pop(1)  # I2 now has only a recoverable P2 header with a bad date.
        data,result,validation=f.run(headers,lines)
        self.assertEqual(data.invoice_identities(),{'I1','I2'})
        self.assertEqual(result['accounting']['unique_invoice_ids'],2)
        self.assertEqual(data.quality()['unique_invoice_ids'],2)
        self.assertEqual(data.quality()['orphan_lines'],[])
        self.assertEqual(result['accounting']['orphan_line_count'],0)
        self.assertEqual(validation['traces_checked'],2)
        self.assertEqual(result['opinions'][0]['invoice_id'],'I1')
        self.assertEqual(result['opinions'][0]['expected_total_cents'],9200)
        self.assertEqual(result['abstentions'][0]['invoice_id'],'I2')
        self.assertIn('no_accepted_invoice_header',{r['reason'] for r in result['abstentions'][0]['reasons']})

    def test_same_patient_bad_date_does_not_invent_ownership_uncertainty(self):
        f=Snapshot();headers,lines=f.exclusion_fixture(invoice_date='2024-02-30',patient_id='P1')
        data,result,_=f.run(headers,lines);ctx=Context(data,f.contract,f.maps)
        state,detail=ctx.presence('H1-S100','P1','2024-01-02')
        self.assertIs(state,True)
        self.assertEqual(detail['header_ownership_evidence'][0]['candidate_patients'],['P1'])
        opinions={r['invoice_id']:r for r in result['opinions']}
        self.assertEqual(opinions['I1']['expected_total_cents'],0)
        self.assertIn('duplicate_invoice_id',opinions['I2']['error_category'].split(';'))
        self.assertEqual(result['abstentions'],[])

    def test_unrelated_recoverable_patients_do_not_block_other_invoice(self):
        f=Snapshot();headers,lines=f.exclusion_fixture(invoice_date='2024-02-30',patient_id='P3')
        headers[1]['patient_id']='P2'
        _,result,_=f.run(headers,lines)
        self.assertEqual([(r['invoice_id'],r['flagged']) for r in result['opinions']],[('I1',0),('I2',1)])
        self.assertEqual(result['abstentions'],[])

    def test_daily_premium_and_duplicate_context_remain_possible(self):
        f=Snapshot();sid=next(s['id'] for s in f.services.values() if s['name']=='Ambulatory Ophthalmic Case Conference')
        headers,unused=f.exclusion_fixture(invoice_date='2024-02-30')
        lines=[f.line('I1','L1',sid,quantity=4),f.line('I2','L2',sid,quantity=3)]
        data,result,_=f.run(headers,lines);ctx=Context(data,f.contract,f.maps)
        daily=ctx.daily(sid,'P1','2024-01-02')
        self.assertEqual((daily['lower'],daily['upper']),(4,7))
        other=ctx.competitors(sid,'P1','2024-01-02','L1')
        self.assertEqual((other['certain_records'],other['possible_records']),(0,1))
        trace={t['invoice_id']:t for t in result['traces']}['I1']
        self.assertEqual([r['reason'] for r in trace['unresolved_facts']],['possible_duplicate_service_day'])
        with self.assertRaisesRegex(Uncertain,'multiple_supported_rate_outcomes'):
            rate_for(data.lines[0],data.invoices[0],f.services[sid],f.contract,ctx)

    def test_all_patient_prior_stays_known_but_h4_patient_lower_bound_does_not(self):
        for hospital,name,quantity in [('H1','Standard Otolaryngologic Radiotherapy Fraction',61),
                                       ('H4','Ambulatory Musculoskeletal Ventilation Support',81)]:
            with self.subTest(hospital=hospital):
                f=Snapshot(hospital);sid=next(s['id'] for s in f.services.values() if s['name']==name)
                headers=[f.header('I1','P1'),f.header('I2','P1'),f.header('I2','P2',invoice_date='2024-02-30')]
                lines=[f.line('I1','L1',sid,service_date='2025-01-02'),
                       f.line('I2','L2',sid,service_date='2025-01-01',quantity=quantity)]
                data=f.load(headers,lines);ctx=Context(data,f.contract,f.maps)
                interval=ctx.prior(sid,'2025-01-02','L1','P1')
                self.assertEqual((interval['lower'],interval['upper']),(quantity if hospital=='H1' else 0,quantity))
                if hospital=='H4':
                    with self.assertRaisesRegex(Uncertain,'multiple_supported_rate_outcomes'):
                        rate_for(data.lines[0],data.invoices[0],f.services[sid],f.contract,ctx)

    def test_unrecoverable_invoice_identity_is_explicitly_fail_closed(self):
        f=Snapshot();headers,lines=f.exclusion_fixture(invoice_id='',invoice_date='2024-02-30')
        data,result,_=f.run(headers,lines)
        self.assertEqual([r['invoice_id'] for r in result['opinions']],[])
        self.assertEqual(len(data.quarantined_headers()),1)
        self.assertTrue(all('unlinked_quarantined_invoice_header' in {r['reason'] for r in a['reasons']} for a in result['abstentions']))

    def test_quarantined_ownership_and_output_are_permutation_invariant(self):
        f=Snapshot();data,result,_=f.run(*f.exclusion_fixture(invoice_date='2024-02-30'))
        rng=random.Random(193)
        rng.shuffle(data.invoices);rng.shuffle(data.lines);rng.shuffle(data.dispositions)
        repeated=audit(data,f.contract,f.maps);assign_confidence(repeated,POLICY)
        validate_result(repeated,data,POLICY)
        self.assertEqual(result,repeated)


class BundleTraceRegressions(unittest.TestCase):
    def test_applied_bundle_points_to_clause_and_partner_context(self):
        f=Snapshot();headers=[f.header('I1','P1'),f.header('I2','P1')]
        lines=[f.line('I1','L1','H1-S001'),f.line('I2','L2','H1-S083')]
        _,result,_=f.run(headers,lines)
        expected={'H1-S001':('H1-S083',16400),'H1-S083':('H1-S001',19150)}
        for trace in result['traces']:
            item=trace['lines'][0];price=item['pricing'];stage=price['stages'][0]
            partner,cents=expected[item['service_id']]
            self.assertEqual(stage['source'],['data/source/contracts/hospital_1/provider_services_agreement.md#L219'])
            self.assertEqual(stage['after'],[cents]);self.assertEqual(price['rate'],cents)
            rule=stage['rules'][0]
            self.assertEqual((rule['rule_index'],rule['partner_service_id'],rule['substituted_cents']),(0,partner,cents))
            self.assertIs(rule['presence'],True)
            detail=price['context'][rule['context_index']]
            self.assertEqual(detail['query'],{'service_id':partner,'patient':'P1','day':'2024-01-02'})
            self.assertEqual(detail['certain_records'],1)

    def test_absent_partner_uses_dated_standalone_source(self):
        f=Snapshot();data=f.load([f.header('I1','P1')],[f.line('I1','L1','H1-S001')])
        price=rate_for(data.lines[0],data.invoices[0],f.services['H1-S001'],f.contract,Context(data,f.contract,f.maps))
        stage=price['stages'][0]
        self.assertEqual(stage['source'],[price['version']['source']])
        self.assertIs(stage['rules'][0]['presence'],False)
        self.assertEqual(stage['after'],[price['version']['cents']])

    def test_uncertain_bundle_retains_both_sources_and_withholds_unequal_prices(self):
        f=Snapshot();headers=[f.header('I1','P1'),f.header('I2','P1'),f.header('I2','P2',invoice_date='2024-02-30')]
        lines=[f.line('I1','L1','H1-S001'),f.line('I2','L2','H1-S083')]
        data,result,_=f.run(headers,lines)
        # I1's header total does not match its own line, so it is reported; the
        # bundle's unresolved rate stays recorded on the trace either way.
        reason={t['invoice_id']:t for t in result['traces']}['I1']['unresolved_facts'][0]
        self.assertEqual(reason['reason'],'multiple_supported_rate_outcomes')
        stage=reason['detail']['stages'][0];rule=stage['rules'][0]
        self.assertIsNone(rule['presence'])
        self.assertEqual(set(stage['source']),{f.contract['bundles'][0]['source'],f.services['H1-S001']['versions'][0]['source']})
        detail=reason['detail']['context'][rule['context_index']]
        self.assertEqual((detail['certain_records'],detail['possible_records']),(0,1))

    def test_amended_standalone_trace_keeps_effective_version(self):
        f=Snapshot('H3');sid=next(s['id'] for s in f.services.values() if s['name']=='Ambulatory Otolaryngologic Imaging Interpretation')
        for day,cents in [('2024-12-31',182625),('2025-01-01',208200)]:
            with self.subTest(day=day):
                data=f.load([f.header('I1','P1')],[f.line('I1','L1',sid,service_date=day)])
                price=rate_for(data.lines[0],data.invoices[0],f.services[sid],f.contract,Context(data,f.contract,f.maps))
                stage=price['stages'][0]
                self.assertEqual(stage['base_rate']['cents'],cents)
                self.assertEqual(stage['source'],[price['version']['source']])
                self.assertEqual(stage['rules'],[])
