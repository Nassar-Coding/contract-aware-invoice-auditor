import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from insurance_audit.io import INVOICE_COLUMNS, InputError, integer, read_table, load_hospital
from insurance_audit.inventory import split_h1

ROOT=Path(__file__).resolve().parents[1]


class InputContractTests(unittest.TestCase):
    def make(self, header=None, rows=None):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        path=Path(temp.name)/'invoices.csv'
        valid=['I','H1','C','2024-01-02','P','F-MAIN','GOLD','','','100']
        with path.open('w',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(header or INVOICE_COLUMNS);writer.writerows(rows or [valid])
        return path,valid

    def test_headers_are_named_and_order_independent(self):
        path,row=self.make();path2,_=self.make(list(reversed(INVOICE_COLUMNS)),[list(reversed(row))])
        a,_=read_table(path,'H1','invoices');b,_=read_table(path2,'H1','invoices')
        self.assertEqual(a[0].invoice_total_cents,b[0].invoice_total_cents)

    def test_missing_duplicate_unknown_headers_fatal(self):
        for header in [INVOICE_COLUMNS[:-1],INVOICE_COLUMNS[:-1]+('invoice_id',),INVOICE_COLUMNS+('extra',)]:
            with self.subTest(header=header):
                path,_=self.make(header)
                with self.assertRaises(InputError):read_table(path,'H1','invoices')

    def test_values_quarantined_without_silent_drop(self):
        _,row=self.make();rows=[]
        for field,value in [('invoice_total_cents','NaN'),('invoice_total_cents','1.5'),('invoice_date','2024-02-30'),('patient_id','')]:
            altered=row.copy();altered[INVOICE_COLUMNS.index(field)]=value;rows.append(altered)
        rows.extend([row[:-1],row+['extra'],row])
        path,_=self.make(rows=rows);good,dispositions=read_table(path,'H1','invoices')
        self.assertEqual(len(good),1);self.assertEqual(len(dispositions),7)
        self.assertEqual(sum(x['status']=='quarantined' for x in dispositions),6)

    def test_parseable_billing_fault_is_retained(self):
        _,row=self.make();row[2]='wrong-contract';row[9]='-100'
        path,_=self.make(rows=[row]);good,ds=read_table(path,'H1','invoices')
        self.assertEqual(good[0].invoice_total_cents,-100);self.assertEqual(ds[0]['status'],'accepted')

    def test_numeric_range_and_non_finite(self):
        self.assertEqual(integer('1000000000000000','money'),10**15)
        for value in ['1000000000000001','Infinity','1e2','']:
            with self.assertRaises(ValueError):integer(value,'money')

    def test_no_label_access_and_split_identity(self):
        original=Path.open
        seen=[]
        def guarded(path,*args,**kwargs):
            seen.append(str(path))
            if 'labels' in path.parts:raise AssertionError('Label access in input/split path')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',guarded):
            data=load_hospital(ROOT/'data/source','H1');split=split_h1(data)
        self.assertEqual(len(data.invoices),918)
        quarantined_lines=[d for d in data.dispositions if d['kind']=='line_items' and d['status']=='quarantined']
        self.assertEqual(len(data.lines)+len(quarantined_lines),11415)
        self.assertEqual(len(quarantined_lines),6)
        self.assertEqual(len(split['invoice_partitions']),913)
        for row in data.invoices:
            self.assertEqual(split['invoice_partitions'][row.invoice_id],split['patient_groups'][row.patient_id]['partition'])
        self.assertTrue(seen)

    def test_hospital_scope_cannot_be_a_path(self):
        with self.assertRaises(InputError):load_hospital(ROOT/'data/source','../labels')


if __name__=='__main__':unittest.main()
