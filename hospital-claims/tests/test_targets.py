import json
from pathlib import Path
import unittest
from insurance_audit.io import Invoice,Line,Loaded
from insurance_audit.context import Context
from insurance_audit.pricing import rate_for,Uncertain
from insurance_audit.audit import audit
from insurance_audit.resolve import normalize

ROOT=Path(__file__).resolve().parents[1]


def fixture(h,name,day='2025-01-02',quantity=1,facility='F-MAIN',tier='BRONZE',history=None):
    c=json.loads((ROOT/f'contracts/hospital_{h[1:]}.raw.json').read_text());ss={s['name']:s for s in c['services']}
    invoice=Invoice('headers.csv',2,h,'I1',h,c['contract_number'],'2025-12-31','P1',facility,tier,None,None,0)
    line=Line('lines.csv',2,h,'L2','I1',1,day,name,quantity,ss[name]['unit'],0,0)
    headers=[invoice];lines=[line]
    for i,item in enumerate(history or [],3):
        n,d,q,p,f,t=item
        ident='I'+str(i);headers.append(Invoice('headers.csv',i,h,ident,h,c['contract_number'],'2025-12-31',p,f,t,None,None,0))
        lines.append(Line('lines.csv',i,h,'L'+str(i),ident,1,d,n,q,ss[n]['unit'],0,0))
    m={'records':[{'key':normalize(s['name']),'state':'accepted','service_id':s['id'],'candidates':[s['id']],'grade':'explicit'} for s in c['services']]}
    data=Loaded(h,headers,lines,[],{});ctx=Context(data,c,m)
    return c,ss[name],line,invoice,ctx,data,m


def price(*a,**kw):
    c,s,l,i,ctx,_,_=fixture(*a,**kw);return rate_for(l,i,s,c,ctx)


class TargetTests(unittest.TestCase):
    def test_h3_amendment_rate_and_added_service_before_on_after(self):
        for day,expected in [('2024-12-31',182625),('2025-01-01',208200),('2025-01-02',208200)]:
            self.assertEqual(price('H3','Ambulatory Otolaryngologic Imaging Interpretation',day)['rate'],expected)
        for day,expected in [('2024-12-31',0),('2025-01-01',86525),('2025-01-02',86525)]:
            self.assertEqual(price('H3','Advanced Dermatologic Nutritional Support',day)['rate'],expected)

    def test_h3_inherited_weekend_and_cap(self):
        self.assertEqual(price('H3','Specialist Psychiatric Discharge Planning','2025-01-04')['rate'],48455)
        c,s,l,i,ctx,data,m=fixture('H3','Ambulatory Otolaryngologic Imaging Interpretation',quantity=13)
        self.assertEqual(audit(data,c,m)['opinions'][0]['expected_total_cents'],2498400)

    def test_h3_direction_is_not_copied_from_h1(self):
        name='Advanced Gastrointestinal Telemetry Monitoring';anchor='Advanced Geriatric Nutritional Support'
        for day in ['2025-01-01','2025-01-03']:
            with self.assertRaisesRegex(Uncertain,'unresolved_exclusion'):
                price('H3',name,history=[(anchor,day,1,'P1','F-MAIN','BRONZE')])
        self.assertEqual(price('H3',name,history=[(anchor,'2025-01-02',1,'P1','F-MAIN','BRONZE')])['rate'],0)

    def test_compound_units_never_invent_second_dimension(self):
        for h,name in [('H3','Focused Urologic Telemetry Monitoring'),('H4','Intermittent Urologic Telemetry Monitoring'),('H5','Routine Psychiatric Telemetry Monitoring')]:
            with self.assertRaisesRegex(Uncertain,'composite_dimension_unobserved'):price(h,name)

    def test_h4_instance_unit_and_scope_envelope(self):
        name='Ambulatory Musculoskeletal Ventilation Support'
        self.assertEqual(price('H4',name,history=[(name,'2025-01-01',80,'P1','F-MAIN','BRONZE')])['rate'],157150)
        self.assertEqual(price('H4',name,history=[(name,'2025-01-01',81,'P1','F-MAIN','BRONZE')])['rate'],133578)
        self.assertEqual(price('H4',name,history=[(name,'2025-01-01',241,'P1','F-MAIN','BRONZE')])['rate'],110005)
        with self.assertRaisesRegex(Uncertain,'multiple_supported_rate_outcomes'):
            price('H4',name,history=[(name,'2025-01-01',81,'P2','F-MAIN','BRONZE')])

    def test_h4_premium_and_bundle(self):
        self.assertEqual(price('H4','Assisted Cardiac Ventilation Support',quantity=13)['rate'],202410)
        self.assertEqual(price('H4','Ambulatory Obstetric Case Conference',history=[('Focused Vascular Infusion Therapy','2025-01-02',1,'P1','F-MAIN','BRONZE')])['rate'],11875)

    def test_h5_facility_tier_round_at_each_step(self):
        # 8175 * 1.1 = 8992.5 -> 8993; * .9 = 8093.7 -> 8094.
        # One final rounding would give 8093 and is incorrect.
        r=price('H5','Advanced Dermatologic Pharmaceutical Dispensing',facility='F-NORTH',tier='GOLD')
        self.assertEqual(r['rate'],8094)
        self.assertEqual(r['qualifications'],['invoice_facility_projected_to_lines'])
        self.assertEqual(price('H5','Emergency Urologic Home Visit',facility='F-NORTH',tier='GOLD')['qualifications'],[])

    def test_h5_bundle_before_multipliers(self):
        partner='Supervised Palliative Consultation'
        r=price('H5','Elective Ophthalmic Recovery Room Occupancy',facility='F-NORTH',tier='GOLD',history=[(partner,'2025-01-02',1,'P1','F-NORTH','GOLD')])
        self.assertEqual(r['rate'],176934)

    def test_h5_usage_does_not_reset_by_patient_facility_or_tier(self):
        name='Ambulatory Hepatic Case Conference'
        self.assertEqual(price('H5',name,facility='F-NORTH',tier='SILVER',history=[(name,'2025-01-01',80,'P2','F-COAST','GOLD')])['rate'],21357)
        self.assertEqual(price('H5',name,facility='F-NORTH',tier='SILVER',history=[(name,'2025-01-01',81,'P2','F-COAST','GOLD')])['rate'],18794)

    def test_hospital_source_system_suffixes_and_qualifiers(self):
        for suffix in ['NG','SA','RM','CW','PH']:
            self.assertEqual(normalize('Adv Card Vent Supp /'+suffix+'-2345'),'adv card vent supp')
        self.assertNotEqual(normalize('preop card vent'),normalize('postop card vent'))
