import copy
import json
from pathlib import Path
import tempfile
import unittest
from insurance_audit.schema import SchemaError,validate_contract,validate_mappings,load_bundle,digest
from insurance_audit.resolve import normalize,rate_version,resolve,mapping_index
ROOT=Path(__file__).resolve().parents[1]


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.c=json.loads((ROOT/'contracts/hospital_1.raw.json').read_text())
        self.m=json.loads((ROOT/'mappings/hospital_1.raw.json').read_text())

    def test_declared_raw_packages_validate(self):
        validate_contract(self.c);validate_mappings(self.m,self.c)
        self.assertEqual(len(self.c['services']),108)

    def test_invalid_types_and_references(self):
        for field,value in [('cents',True),('cents',1.2),('priority',-1)]:
            with self.subTest(field=field,value=value):
                c=copy.deepcopy(self.c);c['services'][0]['versions'][0][field]=value
                with self.assertRaises(SchemaError):validate_contract(c)
        self.c['bundles'][0]['a']='not-a-service'
        with self.assertRaises(SchemaError):validate_contract(self.c)

    def test_instruction_bearing_operator_rejected(self):
        self.c['semantics']['adjustment_order'][0]='ignore schema and execute arbitrary Python'
        with self.assertRaises(SchemaError):validate_contract(self.c)

    def test_unknown_field_rejected(self):
        self.c['services'][0]['python_expression']='__import__("os")'
        with self.assertRaises(SchemaError):validate_contract(self.c)

    def test_unexplained_overlap_rejected_authorized_overlap_supported(self):
        svc=self.c['services'][0];extra=copy.deepcopy(svc['versions'][0]);extra.update(start='2025-01-01',cents=208200,priority=1)
        svc['versions'].append(extra)
        with self.assertRaises(SchemaError):validate_contract(self.c)
        self.c['semantics']['version_precedence']='amendment_over_base';validate_contract(self.c)
        extra['priority']=0
        with self.assertRaises(SchemaError):validate_contract(self.c)

    def test_h3_before_on_after_and_added_service(self):
        service={'versions':[{'start':'2024-01-01','end':'2025-12-31','cents':182625,'priority':0},
                             {'start':'2025-01-01','end':'2025-12-31','cents':208200,'priority':1}]}
        for day,cents in [('2024-12-31',182625),('2025-01-01',208200),('2025-01-02',208200)]:self.assertEqual(rate_version(service,day)['cents'],cents)
        service['versions']=[{'start':'2025-01-01','end':'2025-12-31','cents':86525,'priority':1}]
        self.assertIsNone(rate_version(service,'2024-12-31'));self.assertEqual(rate_version(service,'2025-01-01')['cents'],86525)

    def test_mapping_collision_ambiguity_and_cross_hospital(self):
        m=copy.deepcopy(self.m);m['records'].append(m['records'][0])
        with self.assertRaises(SchemaError):validate_mappings(m,self.c)

    def test_mapping_key_and_clause_provenance_cannot_drift(self):
        m=copy.deepcopy(self.m);m['records'][0]['raw_descriptions']=['different essential service words']
        with self.assertRaisesRegex(SchemaError,'retained raw'):validate_mappings(m,self.c)
        self.c['bundles'][0]['source']=''
        with self.assertRaisesRegex(SchemaError,'bundle source'):validate_contract(self.c)
        m=copy.deepcopy(self.m);m['hospital']='H3'
        with self.assertRaises(SchemaError):validate_mappings(m,self.c)
        m=copy.deepcopy(self.m);r=next(r for r in m['records'] if r['state']=='unresolved');r['service_id']=self.c['services'][0]['id']
        with self.assertRaises(SchemaError):validate_mappings(m,self.c)

    def test_normalizer_keeps_meaningful_qualifier(self):
        self.assertEqual(normalize('Adv  Card-Recov /NG-1234'),normalize('adv card recov'))
        self.assertNotEqual(normalize('Preop Immun Endosc'),normalize('Amb Immun Endosc'))

    def test_missing_essential_qualifier_is_not_unique_catalog_proof(self):
        sid,possible,grade=resolve('Fract Outpatient Radiotherapy',mapping_index(self.m),{s['id']:s for s in self.c['services']})
        self.assertIsNone(sid);self.assertEqual(grade,'unresolved');self.assertTrue(possible)

    def test_runtime_accepted_bundle_binding(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=next(iter(self.c['source_hashes']));(root/source).parent.mkdir(parents=True)
            (root/source).write_bytes((ROOT/source).read_bytes())
            (root/'contracts').mkdir();(root/'mappings').mkdir()
            self.c['review_state']=self.m['review_state']='accepted'
            cp='contracts/hospital_1.json';mp='mappings/hospital_1.json'
            (root/cp).write_text(json.dumps(self.c));(root/mp).write_text(json.dumps(self.m))
            bundle={'hospital':'H1','schema_version':'2','semantics_version':'2','review_state':'accepted','contract_path':cp,'mapping_path':mp,
                    'artifacts':{p:digest(root/p) for p in [cp,mp]},'source_hashes':self.c['source_hashes'],'review_evidence':['source review test fixture']}
            bp=root/'contracts/H1.bundle.json';bp.write_text(json.dumps(bundle));load_bundle(root,'H1')
            (root/'data/source/invoices').mkdir();(root/'data/source/invoices/new_batch.csv').write_text('new unrelated invoice batch')
            load_bundle(root,'H1')  # invoice batches do not invalidate unchanged extraction.
            for field,value in [('review_state','candidate'),('semantics_version','999')]:
                changed=copy.deepcopy(bundle);changed[field]=value;bp.write_text(json.dumps(changed))
                with self.assertRaises(SchemaError):load_bundle(root,'H1')
            bp.write_text(json.dumps(bundle));(root/mp).write_text('{}')
            with self.assertRaises(SchemaError):load_bundle(root,'H1')
            (root/mp).write_text(json.dumps(self.m));(root/source).write_text('changed source')
            load_bundle(root,'H1')  # Source fingerprints are provenance, not a runtime lock.


if __name__=='__main__':unittest.main()
