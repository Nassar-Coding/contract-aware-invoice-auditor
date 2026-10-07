"""Independent source-derived examples and uncertainty propagation checks."""
import copy
import json
from pathlib import Path
import random
import unittest
from dataclasses import replace
from insurance_audit.io import Invoice, Line, Loaded
from insurance_audit.resolve import normalize
from insurance_audit.context import Context
from insurance_audit.pricing import half_up, rate_for, Uncertain, interval_discounts
from insurance_audit.audit import audit

ROOT=Path(__file__).resolve().parents[1]
CONTRACT=json.loads((ROOT/'contracts/hospital_1.json').read_text())
EXAMPLES=json.loads((ROOT/'tests/fixtures/h1_contract_examples.json').read_text())


def service(name):
    return next(s for s in CONTRACT['services'] if s['name']==name)


def invoice(i='I1',patient='P1',total=0,**kw):
    values=dict(source='invoices.csv',source_row=int(i[1:])+1,hospital_scope='H1',invoice_id=i,
                hospital_id='H1',contract_number=CONTRACT['contract_number'],invoice_date='2025-12-31',
                patient_id=patient,facility_code='',plan_tier='',admission_date=None,discharge_date=None,
                invoice_total_cents=total)
    values.update(kw);return Invoice(**values)


def line(name,i='I1',lid='L1',day='2024-01-02',quantity=1,price=None,**kw):
    svc=service(name);price=svc['versions'][0]['cents'] if price is None else price
    values=dict(source='lines.csv',source_row=int(lid[1:])+1,hospital_scope='H1',line_id=lid,invoice_id=i,
                line_no=int(lid[1:]),service_date=day,description=name,quantity=quantity,
                unit_basis_as_billed=svc['unit'],unit_price_cents=price,line_total_cents=price*quantity)
    values.update(kw);return Line(**values)


def setup(headers,lines,dispositions=None):
    maps={'records':[{'key':normalize(s['name']),'service_id':s['id'],'state':'accepted','candidates':[s['id']],
                     'grade':'explicit'} for s in CONTRACT['services']]}
    data=Loaded('H1',headers,lines,dispositions or [],{})
    return data,maps,Context(data,CONTRACT,maps)


class PricingTests(unittest.TestCase):
    def test_exact_halves_and_each_step_rounding(self):
        for case in EXAMPLES['rounding']:
            if 'ratio' in case:self.assertEqual(half_up(case['amount'],case['ratio']),case['expected'])
            else:
                amount=case['amount'];observed=[]
                for r in case['ratios']:amount=half_up(amount,r);observed.append(amount)
                self.assertEqual(observed,case['expected_stages'])
        self.assertEqual(half_up(10**15,[113,100]),1130000000000000)

    def test_h1_source_thresholds_and_weekend(self):
        for case in EXAMPLES['rates']:
            with self.subTest(case=case):
                name=case['service'];current=line(name,day=case['service_date'],quantity=case['daily_quantity'])
                history=[];headers=[invoice()]
                if case['prior_quantity']:
                    history=[line(name,'I2','L2','2024-01-01',case['prior_quantity'])];headers.append(invoice('I2','P2'))
                _,_,ctx=setup(headers,[current]+history)
                self.assertEqual(rate_for(current,headers[0],service(name),CONTRACT,ctx)['rate'],case['expected_rate'])

    def test_complete_correct_and_wrong_unit_unchanged_total(self):
        name='Advanced Neurological Consultation';row=line(name,quantity=2)
        data,maps,_=setup([invoice(total=28250)],[row]);result=audit(data,CONTRACT,maps)
        self.assertEqual((result['opinions'][0]['flagged'],result['opinions'][0]['expected_total_cents']),(0,28250))
        row=line(name,quantity=2,unit_basis_as_billed='per_hour');data,maps,_=setup([invoice(total=28250)],[row])
        result=audit(data,CONTRACT,maps)['opinions'][0]
        self.assertEqual((result['flagged'],result['expected_total_cents']),(1,28250))
        self.assertEqual(result['error_category'],'wrong_unit_basis')

    def test_single_line_cap(self):
        name='Advanced Rheumatologic Laboratory Panel';row=line(name,quantity=5)
        data,maps,_=setup([invoice(total=row.line_total_cents)],[row]);r=audit(data,CONTRACT,maps)['opinions'][0]
        self.assertEqual(r['expected_total_cents'],59100);self.assertIn('daily_cap_exceeded',r['error_category'])

    def test_bundle_across_invoices_same_patient_day(self):
        names=['Advanced Cardiac Recovery Room Occupancy','Routine Cardiac Specimen Analysis']
        rows=[line(names[0]),line(names[1],'I2','L2')]
        headers=[invoice(total=16400),invoice('I2',total=19150)]
        data,maps,ctx=setup(headers,rows)
        rates=[rate_for(l,h,service(n),CONTRACT,ctx)['rate'] for l,h,n in zip(rows,headers,names)]
        self.assertEqual(rates,[16400,19150]);self.assertEqual(sum(rates),35550)
        self.assertEqual(len(audit(data,CONTRACT,maps)['opinions']),2)

    def test_exclusion_past_future_interior_and_equality(self):
        # H1 clause 10.1 measures the window in either direction, and "not
        # billable within 7 days" covers a Service Date exactly 7 days away, so
        # both boundary days are inside the window rather than uncertain.
        target='Advanced Metabolic Anaesthesia Administration';anchor='Standard Endocrine Endoscopic Procedure'
        for day,excluded in [('2024-01-04',True),('2024-01-16',True),('2024-01-03',True),('2024-01-17',True),('2024-01-18',False)]:
            with self.subTest(day=day):
                rows=[line(target,day='2024-01-10'),line(anchor,'I2','L2',day)];headers=[invoice(),invoice('I2')]
                _,_,ctx=setup(headers,rows)
                if excluded is None:
                    with self.assertRaisesRegex(Uncertain,'unresolved_exclusion'):rate_for(rows[0],headers[0],service(target),CONTRACT,ctx)
                else:self.assertEqual(rate_for(rows[0],headers[0],service(target),CONTRACT,ctx)['excluded'],excluded)

    def test_prior_all_patients_strict_date_id_and_order_independent(self):
        name='Standard Otolaryngologic Radiotherapy Fraction';sid=service(name)['id']
        rows=[line(name,'I1','L2',quantity=3),line(name,'I2','L1',quantity=61),line(name,'I3','L3',quantity=500)]
        headers=[invoice(),invoice('I2','P2'),invoice('I3','P3')]
        _,_,ctx=setup(headers,rows);before=ctx.prior(sid,'2024-01-02','L2')
        self.assertEqual((before['lower'],before['upper']),(61,61))
        random.Random(71).shuffle(rows);random.Random(27).shuffle(headers)
        _,_,ctx=setup(headers,rows);self.assertEqual(ctx.prior(sid,'2024-01-02','L2'),before)

    def test_later_revision_of_prior_claim_changes_subsequent_price(self):
        name='Standard Otolaryngologic Radiotherapy Fraction'
        current=line(name,day='2024-01-03');prior=line(name,'I2','L2','2024-01-01',60)
        headers=[invoice(total=25250),invoice('I2','P2')]
        data,maps,ctx=setup(headers,[current,prior])
        before=rate_for(current,headers[0],service(name),CONTRACT,ctx)['rate']
        revised=replace(prior,quantity=61)
        data,maps,ctx=setup(headers,[current,revised])
        after=rate_for(current,headers[0],service(name),CONTRACT,ctx)['rate']
        self.assertEqual((before,after),(25250,22220))
        opinion=next(r for r in audit(data,CONTRACT,maps)['opinions'] if r['invoice_id']=='I1')
        self.assertEqual(opinion['expected_total_cents'],22220)
        self.assertEqual(opinion['flagged'],1)

    def test_cross_invoice_daily_context_and_duplicate_allocation_fail_closed(self):
        name='Ambulatory Ophthalmic Case Conference';rows=[line(name,quantity=4),line(name,'I2','L2',quantity=3)]
        data,maps,ctx=setup([invoice(total=rows[0].line_total_cents),invoice('I2',total=rows[1].line_total_cents)],rows)
        self.assertEqual(ctx.daily(service(name)['id'],'P1','2024-01-02')['lower'],7)
        self.assertEqual(rate_for(rows[0],data.invoices[0],service(name),CONTRACT,ctx)['rate'],20310)
        r=audit(data,CONTRACT,maps);self.assertEqual(len(r['opinions']),0)
        self.assertTrue(all(a['reasons'][0]['reason']=='duplicate_service_day_allocation' for a in r['abstentions']))

    def test_conflicting_identity_does_not_multiply_history(self):
        name='Standard Otolaryngologic Radiotherapy Fraction';row=line(name,quantity=61)
        data,maps,ctx=setup([invoice(),invoice(patient='P2')],[row])
        self.assertEqual(ctx.prior(service(name)['id'],'2024-01-03','L2')['lower'],61)
        # The reuse itself is the error and is reported, but no line can be
        # attributed between the two physical records, so no amount is corrected.
        opinion=audit(data,CONTRACT,maps)['opinions'][0]
        self.assertEqual((opinion['flagged'],opinion['error_category']),(1,'duplicate_invoice_id'))
        self.assertEqual(opinion['amount_basis'],'reused_identifier_canonical_billed_total')
        self.assertEqual(opinion['expected_total_cents'],opinion['billed_total_cents'])
        self.assertEqual(opinion['amount_unchanged_reason'],'finding_does_not_affect_the_payable_amount')

    def test_conflicting_header_trace_order_is_canonical(self):
        headers=[invoice(patient='P2',source_row=100),invoice(source_row=2)]
        data,maps,_=setup(headers,[line('Advanced Neurological Consultation')])
        first=audit(data,CONTRACT,maps)
        data.invoices.reverse()
        self.assertEqual(first,audit(data,CONTRACT,maps))
        self.assertEqual([h['row'] for h in first['traces'][0]['source_headers']],[2,100])

    def test_malformed_date_preserves_possible_dependency(self):
        name='Standard Otolaryngologic Radiotherapy Fraction';row=line(name)
        q={'kind':'line_items','status':'quarantined','source':'lines.csv','source_row':3,'invoice_id':'I2','raw':[],
           'raw_named':{'line_id':'L2','invoice_id':'I2','description':name,'service_date':'not-a-date','quantity':'100'}}
        _,_,ctx=setup([invoice(),invoice('I2','P2')],[row],[q]);b=ctx.prior(service(name)['id'],row.service_date,row.line_id)
        self.assertEqual((b['lower'],b['upper']),(0,100))
        with self.assertRaisesRegex(Uncertain,'multiple_supported_rate_outcomes'):rate_for(row,invoice(),service(name),CONTRACT,ctx)

    def test_irrelevant_unknown_and_saturated_interval(self):
        name='Standard Otolaryngologic Radiotherapy Fraction';svc=service(name)
        self.assertEqual(interval_discounts(svc,{'lower':181,'upper':100000}),[(70,100)])
        self.assertEqual(interval_discounts(svc,{'lower':0,'upper':60}),[(1,1)])
        self.assertEqual(set(interval_discounts(svc,{'lower':60,'upper':61})),{(1,1),(88,100)})
        row=line('Advanced Neurological Consultation');unknown=line(name,'I2','L2',description='Consultation Adv')
        data,maps,_=setup([invoice(total=14125),invoice('I2','P2')],[row,unknown])
        self.assertIn('I1',[r['invoice_id'] for r in audit(data,CONTRACT,maps)['opinions']])

    def test_unresolved_line_without_a_finding_still_withholds(self):
        rows=[line('Advanced Neurological Consultation'),line('Advanced Neurological Consultation',lid='L2',description='Consultation Adv')]
        total=sum(r.line_total_cents for r in rows)
        data,maps,_=setup([invoice(total=total)],rows);r=audit(data,CONTRACT,maps)
        self.assertEqual(len(r['opinions']),0);self.assertEqual(len(r['abstentions']),1)
        self.assertIn('unresolved_service_mapping',{x['reason'] for x in r['abstentions'][0]['reasons']})
        self.assertEqual(len(r['traces'][0]['lines']),2)

    def test_header_arithmetic_mismatch(self):
        data,maps,_=setup([invoice(total=14126)],[line('Advanced Neurological Consultation')])
        r=audit(data,CONTRACT,maps)['opinions'][0]
        self.assertEqual(r['expected_total_cents'],14125);self.assertIn('invoice_total_mismatch',r['error_category'])

    def test_nonpositive_quantity_abstains(self):
        for kw in [dict(quantity=0),dict(quantity=-1)]:
            row=line('Advanced Neurological Consultation',**kw)
            data,maps,_=setup([invoice(total=row.line_total_cents)],[row])
            self.assertEqual(len(audit(data,CONTRACT,maps)['opinions']),0)

    def test_term_window_and_after_invoice_dates_are_reported_without_pricing(self):
        for kw,category in [(dict(day='2026-01-01'),'service_date_out_of_window'),
                            (dict(day='2025-12-30'),'service_date_after_invoice_date')]:
            with self.subTest(**kw):
                row=line('Advanced Neurological Consultation',**kw)
                data,maps,_=setup([invoice(invoice_date='2025-12-29',total=row.line_total_cents)],[row])
                opinion=audit(data,CONTRACT,maps)['opinions'][0]
                self.assertEqual(opinion['flagged'],1)
                self.assertEqual(opinion['error_category'].split(';')[0],category)
                self.assertEqual(opinion['expected_total_cents'],opinion['billed_total_cents'])
                self.assertEqual(opinion['amount_unchanged_reason'],'finding_does_not_affect_the_payable_amount')
