"""Source-derived H2 rules and evidence limitations, not fabricated timestamps."""
from dataclasses import replace
import unittest
from test_targets import fixture,price
from insurance_audit.audit import audit
from insurance_audit.checks import validate_line_facts
from insurance_audit.pricing import Uncertain


class H2Tests(unittest.TestCase):
    def test_observable_base_and_strict_prior_volume_are_diagnostic(self):
        self.assertEqual(price('H2','Comprehensive Otolaryngologic Case Conference')['rate'],16525)
        name='Emergency Haematology Transport Service'
        for prior,expected in [(60,9675),(61,8708),(180,8708),(181,7740)]:
            self.assertEqual(price('H2',name,history=[(name,'2025-01-01',prior,'P2','F-MAIN','BRONZE')])['rate'],expected)

    def test_service_date_does_not_prove_07_hour_service_day(self):
        # Business Day is defined by commencement of Service Day, not by the
        # raw invoice date. Missing duration/times cannot prove the exception
        # for a service delivered wholly within a single calendar day.
        for day in ['2025-01-03','2025-01-04']:
            with self.assertRaisesRegex(Uncertain,'multiple_supported_rate_outcomes'):
                price('H2','Emergency Renal Infusion Therapy',day)

    def test_day_uncertainty_only_blocks_outcome_relevant_premium(self):
        name='Supervised Metabolic Laboratory Panel'
        self.assertEqual(price('H2',name,quantity=16)['rate'],7925)
        with self.assertRaisesRegex(Uncertain,'multiple_supported_rate_outcomes'):price('H2',name,quantity=17)

    def test_day_cap_missing_allocation_is_not_clipped_by_assumption(self):
        c,s,l,i,ctx,_,_=fixture('H2','Focused Cardiac Dialysis Session',quantity=7)
        with self.assertRaisesRegex(Uncertain,'unobserved_service_day_cap_allocation'):validate_line_facts(l,i,s,c,ctx)
        c,s,l,i,ctx,_,_=fixture('H2','Focused Cardiac Dialysis Session',quantity=6)
        validate_line_facts(l,i,s,c,ctx)

    def test_h2_does_not_inherit_other_hospitals_prohibitions(self):
        c,s,l,i,ctx,_,_=fixture('H2','Comprehensive Otolaryngologic Case Conference')
        validate_line_facts(l,replace(i,invoice_date='2024-01-01'),s,c,ctx)
        self.assertEqual(c['semantics']['duplicate_policy'],'no_explicit_service_date_prohibition')

    def test_missing_submission_evidence_is_recorded_and_never_blocks_a_check(self):
        # Article XIII makes effectiveness turn on a submission date the data
        # does not record. The condition stays unresolved and is logged; it is
        # not an error, and it does not suppress any other check.
        c,s,l,i,ctx,data,m=fixture('H2','Comprehensive Otolaryngologic Case Conference')
        data.invoices=[replace(i,invoice_total_cents=16525,discharge_date='2025-01-02',invoice_date='2025-01-03')]
        data.lines=[replace(l,unit_price_cents=16525,line_total_cents=16525)]
        r=audit(data,c,m)
        self.assertEqual(r['abstentions'],[])
        opinion=r['opinions'][0]
        self.assertEqual((opinion['flagged'],opinion['expected_total_cents']),(0,16525))
        self.assertIn('unobserved_submission_deadline_and_waiver',opinion['interpretation_qualifications'])
        eligibility=r['traces'][0]['unobserved_eligibility']
        self.assertEqual(eligibility['provisional_60_day_deadline'],'2025-03-03')
        self.assertEqual(eligibility['recorded_discharge_date'],'2025-01-02')
        self.assertEqual(r['traces'][0]['lines'][0]['result']['expected_total_cents'],16525)

    def test_hospital_2_reports_a_service_billed_before_it_was_delivered(self):
        # H2's own contract never defines invoice_date, but a service cannot be
        # invoiced before it happens; the check is enabled for every hospital.
        c,s,l,i,ctx,data,m=fixture('H2','Comprehensive Otolaryngologic Case Conference')
        data.invoices=[replace(i,invoice_total_cents=16525,invoice_date='2025-01-01')]
        data.lines=[replace(l,service_date='2025-01-05',unit_price_cents=16525,line_total_cents=16525)]
        opinion=audit(data,c,m)['opinions'][0]
        self.assertEqual(opinion['flagged'],1)
        self.assertIn('service_date_after_invoice_date',opinion['error_category'].split(';'))

    def test_hospital_2_still_reports_a_cross_invoice_repeat_its_contract_never_permits(self):
        # H2's agreement states no prohibition on billing a Service twice for one
        # Patient and Service Date, but it states no permission either, and on the
        # only labelled hospital every invoice this pattern matches is a labelled
        # cross-invoice duplicate. Silence is not evidence a repeat is legitimate.
        c,s,l,i,ctx,data,m=fixture('H2','Comprehensive Otolaryngologic Case Conference')
        self.assertEqual(c['semantics']['duplicate_policy'],'no_explicit_service_date_prohibition')
        first=replace(i,invoice_id='I1',invoice_total_cents=16525,invoice_date='2025-01-03')
        second=replace(first,invoice_id='I2',source_row=first.source_row+1,invoice_date='2025-01-04')
        data.invoices=[first,second]
        base=replace(l,invoice_id='I1',line_id='LA',unit_price_cents=16525,line_total_cents=16525)
        data.lines=[base,replace(base,invoice_id='I2',line_id='LB',source_row=base.source_row+1)]
        opinions={r['invoice_id']:r for r in audit(data,c,m)['opinions']}
        self.assertNotIn('cross_invoice_duplicate',opinions['I1']['error_category'].split(';'))
        self.assertIn('cross_invoice_duplicate',opinions['I2']['error_category'].split(';'))
        self.assertEqual(opinions['I2']['expected_total_cents'],0)

    def test_exclusion_uses_explicit_service_date_in_both_directions(self):
        n='Extended Dermatologic Specimen Analysis';anchor='Advanced Endocrine Case Conference'
        self.assertEqual(price('H2',n,history=[(anchor,'2025-01-03',1,'P1','F-MAIN','BRONZE')])['rate'],0)

    def test_bundles_preserve_unknown_day_coincidence(self):
        name='Postoperative Orthopaedic Isolation Room Occupancy';anchor='Bedside Obstetric Physiotherapy Session'
        self.assertEqual(price('H2',name)['rate'],120475)
        with self.assertRaisesRegex(Uncertain,'multiple_supported_rate_outcomes'):
            price('H2',name,history=[(anchor,'2025-01-02',1,'P1','F-MAIN','BRONZE')])
