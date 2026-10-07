"""Strict snapshot ingestion; billing errors are retained, malformed values quarantined."""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

INVOICE_COLUMNS = ('invoice_id','hospital_id','contract_number','invoice_date','patient_id',
                   'facility_code','plan_tier','admission_date','discharge_date','invoice_total_cents')
LINE_COLUMNS = ('line_id','invoice_id','line_no','service_date','description','quantity',
                'unit_basis_as_billed','unit_price_cents','line_total_cents')
HOSPITALS = {f'H{i}': f'hospital_{i}' for i in range(1, 6)}


class InputError(ValueError):
    """A file cannot be interpreted under the declared input contract."""


def integer(value: str, field: str, bound: int = 10**15) -> int:
    if not re.fullmatch(r'[+-]?\d+', value):
        raise ValueError(f'{field}: not an exact integer')
    result = int(value)
    if abs(result) > bound:
        raise ValueError(f'{field}: exceeds supported absolute bound {bound}')
    return result


def iso_date(value: str, field: str, optional: bool = False) -> str | None:
    if optional and not value:
        return None
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError(f'{field}: expected YYYY-MM-DD')
    try:
        date.fromisoformat(value)
    except ValueError as error:
        # Name the field so a quarantine reason stays attributable to a column.
        raise ValueError(f'{field}: {error}') from error
    return value


@dataclass(frozen=True)
class Invoice:
    source: str
    source_row: int
    hospital_scope: str
    invoice_id: str
    hospital_id: str
    contract_number: str
    invoice_date: str
    patient_id: str
    facility_code: str
    plan_tier: str
    admission_date: str | None
    discharge_date: str | None
    invoice_total_cents: int


@dataclass(frozen=True)
class Line:
    source: str
    source_row: int
    hospital_scope: str
    line_id: str
    invoice_id: str
    line_no: int
    service_date: str
    description: str
    quantity: int
    unit_basis_as_billed: str
    unit_price_cents: int
    line_total_cents: int


@dataclass
class Loaded:
    hospital: str
    invoices: list[Invoice]
    lines: list[Line]
    dispositions: list[dict[str, Any]]
    source_hashes: dict[str, str]

    def invoice_identities(self) -> set[str]:
        """Recoverable header identities need a disposition even if no header parsed."""
        return {r.invoice_id for r in self.invoices} | {
            d['invoice_id'] for d in self.dispositions
            if d['kind']=='invoices' and isinstance(d.get('invoice_id'),str) and d['invoice_id'].strip()}

    def quarantined_headers(self) -> list[dict[str, Any]]:
        return [d for d in self.dispositions if d['kind']=='invoices' and d['status']=='quarantined']

    def quality(self) -> dict[str, Any]:
        invoice_ids=self.invoice_identities()
        by_id: dict[str,list[Invoice]] = defaultdict(list)
        by_line: dict[str,list[Line]] = defaultdict(list)
        for row in self.invoices:
            by_id[row.invoice_id].append(row)
        for row in self.lines:
            by_line[row.line_id].append(row)
        duplicate_invoices = []
        for key, rows in sorted(by_id.items()):
            if len(rows) > 1:
                substantive = [{k:v for k,v in asdict(row).items() if k not in {'source','source_row'}} for row in rows]
                duplicate_invoices.append({'invoice_id':key,'source_rows':[r.source_row for r in rows],
                                           'identical_headers':all(v==substantive[0] for v in substantive),
                                           'patients':sorted({r.patient_id for r in rows}),
                                           'billed_totals':sorted({r.invoice_total_cents for r in rows})})
        return {'hospital':self.hospital,'accepted_invoice_records':len(self.invoices),
                'unique_invoice_ids':len(invoice_ids),'accepted_line_records':len(self.lines),
                'unique_line_ids':len(by_line),'disposition_counts':dict(Counter(d['status'] for d in self.dispositions)),
                'raw_record_count':len(self.dispositions),'duplicate_invoice_ids':duplicate_invoices,
                'duplicate_line_ids':{k:[r.source_row for r in v] for k,v in sorted(by_line.items()) if len(v)>1},
                'orphan_lines':[r.line_id for r in self.lines if r.invoice_id not in invoice_ids],
                'invoices_without_lines':sorted(invoice_ids-{r.invoice_id for r in self.lines}),
                'hospital_field_mismatches':[r.invoice_id for r in self.invoices if r.hospital_id!=self.hospital],
                'quarantined':[d for d in self.dispositions if d['status']=='quarantined'],
                'unobserved_facts':['service timestamps','leave intervals','settlement status','independent unit dimensions'],
                'note':'Raw occurrences are not deduplicated or multiplied through joins. Unknowns remain context dependencies.'}


def read_table(path: Path, hospital: str, kind: str) -> tuple[list[Any],list[dict[str,Any]]]:
    columns = INVOICE_COLUMNS if kind == 'invoices' else LINE_COLUMNS
    records, dispositions = [], []
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.reader(stream, strict=True)
        try:
            header = next(reader)
        except (StopIteration,csv.Error) as error:
            raise InputError(f'{path.name}: missing/unreadable header') from error
        if len(header)!=len(set(header)) or set(header)!=set(columns):
            raise InputError(f'{path.name}: incompatible headers: {header!r}; expected {list(columns)!r}')
        try:
            for rowno, values in enumerate(reader, 2):
                raw = dict(zip(header, values))
                entry = {'source':path.name,'source_row':rowno,'kind':kind,'raw':values,'raw_named':raw,
                         'invoice_id':raw.get('invoice_id'),'line_id':raw.get('line_id')}
                try:
                    if len(values)!=len(header):
                        raise ValueError('ragged row')
                    required = ['invoice_id','patient_id'] if kind=='invoices' else ['line_id','invoice_id']
                    for field in required:
                        if not raw[field].strip():
                            raise ValueError(f'{field}: missing identifier')
                    common = {'source':path.name,'source_row':rowno,'hospital_scope':hospital}
                    if kind=='invoices':
                        row = Invoice(**common,**{k:raw[k] for k in ('invoice_id','hospital_id','contract_number','patient_id','facility_code','plan_tier')},
                                      invoice_date=iso_date(raw['invoice_date'],'invoice_date'),
                                      admission_date=iso_date(raw['admission_date'],'admission_date',True),
                                      discharge_date=iso_date(raw['discharge_date'],'discharge_date',True),
                                      invoice_total_cents=integer(raw['invoice_total_cents'],'invoice_total_cents'))
                    else:
                        row = Line(**common,**{k:raw[k] for k in ('line_id','invoice_id','description','unit_basis_as_billed')},
                                   line_no=integer(raw['line_no'],'line_no',10**9),
                                   service_date=iso_date(raw['service_date'],'service_date'),
                                   quantity=integer(raw['quantity'],'quantity',10**9),
                                   unit_price_cents=integer(raw['unit_price_cents'],'unit_price_cents'),
                                   line_total_cents=integer(raw['line_total_cents'],'line_total_cents'))
                    records.append(row)
                    entry.update(status='accepted',reason=None)
                except ValueError as error:
                    entry.update(status='quarantined',reason=str(error))
                dispositions.append(entry)
        except csv.Error as error:
            raise InputError(f'{path.name}: invalid CSV encoding/quoting: {error}') from error
    return records, dispositions


def load_hospital(source_root: Path, hospital: str) -> Loaded:
    if hospital not in HOSPITALS:
        raise InputError(f'Unsupported hospital scope {hospital!r}')
    records, dispositions, hashes = {}, [], {}
    for kind in ('invoices','line_items'):
        path = source_root/'invoices'/f'{HOSPITALS[hospital]}_{kind}.csv'
        if not path.is_file():
            raise InputError(f'Missing {path.name}')
        hashes[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        records[kind], ds = read_table(path,hospital,kind)
        dispositions.extend(ds)
    return Loaded(hospital,records['invoices'],records['line_items'],dispositions,hashes)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,sort_keys=True,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
