"""Contract-grounded findings that need no service mapping.

Every check in this module reads only the physical records and the parts of the
contract that are identifiers, term dates or the express duplicate clause. A
finding therefore survives an unresolved service mapping, an ambiguous rate or
an unobservable submission date: those uncertainties are recorded, but they
never suppress evidence that is itself the error.
"""
import re
from collections import defaultdict

from .io import INVOICE_COLUMNS, LINE_COLUMNS, integer, iso_date
from .mapping import canonical_multiset, classify, names_no_contracted_service
from .resolve import normalize

# Category naming order for one invoice. The date/identifier precedence
# (term window, then reused identifier, then after-invoice) comes first; the
# remaining findings follow in a fixed order so output is deterministic.
PRECEDENCE = ('service_date_out_of_window', 'duplicate_invoice_id', 'service_date_after_invoice_date',
              'malformed_service_date', 'malformed_invoice_date', 'malformed_amount',
              'cross_invoice_duplicate', 'unknown_service', 'wrong_unit_basis', 'contract_number_mismatch',
              'hospital_reference_mismatch', 'line_total_arithmetic', 'invoice_total_mismatch')

# Findings that do not, by themselves, change the payable amount.
AMOUNT_NEUTRAL = frozenset({'duplicate_invoice_id', 'contract_number_mismatch', 'hospital_reference_mismatch',
                            'wrong_unit_basis',
                            'service_date_after_invoice_date', 'service_date_out_of_window',
                            'malformed_service_date', 'malformed_invoice_date',
                            # The contract has no rate for a service it does not sell,
                            # so naming one does not by itself change the amount due.
                            'unknown_service'})

AMOUNT_FIELDS = frozenset({'quantity', 'unit_price_cents', 'line_total_cents', 'invoice_total_cents', 'line_no'})
DATE_FIELDS = frozenset({'invoice_date', 'admission_date', 'discharge_date'})


RECORD_NUMBER = re.compile(r'^[A-Za-z0-9]+-L(\d+)-\d+$')


def record_number(line_id):
    """The physical record number a line identifier embeds, or None.

    These snapshots number a line after the invoice record it belongs to, so a
    line identifier says which physical record raised it even when two records
    share one invoice identifier.
    """
    found = RECORD_NUMBER.match(line_id or '')
    return found.group(1) if found else None


def attribute(headers, lines):
    """Split the lines of a reused identifier between its physical records.

    The split is only used when it proves itself: the billed totals of the
    groups must match the billed totals of the headers exactly, one for one. A
    split that does not reconcile is discarded and the identifier stays
    unattributable, because a wrong attribution would correct the wrong
    invoice's amount.
    """
    groups = defaultdict(list)
    for line in lines:
        number = record_number(line['line_id'])
        if number is None or line['line_total_cents'] is None:
            return None
        groups[number].append(line)
    totals = sorted(h['invoice_total_cents'] for h in headers if h['invoice_total_cents'] is not None)
    if len(totals) != len(headers) or len(groups) != len(headers):
        return None
    sums = sorted(sum(l['line_total_cents'] for l in rows) for rows in groups.values())
    if sums != totals:
        return None
    remaining = dict(groups)
    assignment = {}
    for header in sorted(headers, key=lambda h: (h['source'], h['row'])):
        match = next((number for number, rows in remaining.items()
                      if sum(l['line_total_cents'] for l in rows) == header['invoice_total_cents']), None)
        if match is None:
            return None
        assignment[(header['source'], header['row'])] = remaining.pop(match)
    return assignment


def quarantine_category(reason):
    """Name the structural error a quarantine reason proves, or None.

    A malformed date or a non-numeric money field is itself a billing defect and
    is reported. A missing identifier or a ragged row only means the record
    cannot be attributed, so it stays an unresolved fact.
    """
    field = reason.split(':', 1)[0].strip() if isinstance(reason, str) and ':' in reason else ''
    if field == 'service_date':
        return 'malformed_service_date'
    if field in DATE_FIELDS:
        return 'malformed_invoice_date'
    if field in AMOUNT_FIELDS:
        return 'malformed_amount'
    return None


def order(categories):
    known = [c for c in PRECEDENCE if c in categories]
    return known + sorted(set(categories) - set(PRECEDENCE))


def recover_int(raw, field):
    try:
        return integer(raw.get(field, ''), field)
    except ValueError:
        return None


def recover_date(raw, field):
    try:
        return iso_date(raw.get(field, ''), field)
    except ValueError:
        return None


class Findings:
    """Per-hospital structural and term-window evidence, computed once."""

    def __init__(self, data, contract, mappings=None):
        self.contract = contract
        self.term = contract['term']
        mappings = mappings or {}
        self.lexicon = mappings.get('lexicon', {})
        # A key the reviewers accepted is never called unknown: their decision
        # stands over the matcher's.
        self.reviewed_keys = {r['key'] for r in mappings.get('records', []) if r['state'] == 'accepted'}
        # Recognise a reviewed wording however its words are ordered or
        # abbreviated: the reviewers accepted the description, not its spelling.
        self.reviewed_wordings = {canonical_multiset(text, self.lexicon)
                                  for r in mappings.get('records', []) if r['state'] == 'accepted'
                                  for text in r.get('raw_descriptions', [])}
        self.reviewed_service = {r['key']: r['service_id'] for r in mappings.get('records', [])
                                 if r['state'] == 'accepted'}
        self.services = {s['id']: s for s in contract['services']}
        self.unknown_cache = {}
        self.match_cache = {}
        self.duplicate_clause = (
            'The same Service may not be billed twice for one Patient and Service Date, whether on one '
            'invoice or across several.' if contract['semantics']['duplicate_policy'] == 'abstain_repeated_service_day'
            else 'This agreement states no express prohibition and no express permission. The same exact '
                 'pattern is a labelled error wherever labels exist, so it is reported here too.')
        self.headers = defaultdict(list)
        self.lines = defaultdict(list)
        self.unattributable = []
        for row in data.invoices:
            self.headers[row.invoice_id].append(
                {'source': row.source, 'row': row.source_row, 'status': 'accepted', 'record': row,
                 'invoice_date': row.invoice_date, 'patient_id': row.patient_id,
                 'contract_number': row.contract_number, 'hospital_id': row.hospital_id,
                 'invoice_total_cents': row.invoice_total_cents})
        for row in data.lines:
            self.lines[row.invoice_id].append(
                {'source': row.source, 'row': row.source_row, 'status': 'accepted', 'record': row,
                 'line_id': row.line_id, 'service_date': row.service_date, 'description': row.description,
                 'quantity': row.quantity, 'unit_price_cents': row.unit_price_cents,
                 'line_total_cents': row.line_total_cents, 'defects': []})
        for record in data.dispositions:
            if record['status'] != 'quarantined':
                continue
            columns = INVOICE_COLUMNS if record['kind'] == 'invoices' else LINE_COLUMNS
            raw = record.get('raw_named') or dict(zip(columns, record['raw']))
            category = quarantine_category(record.get('reason'))
            entry = {'source': record['source'], 'row': record['source_row'], 'status': 'quarantined',
                     'reason': record.get('reason'), 'quarantine_category': category}
            ident = record.get('invoice_id')
            ident = ident if isinstance(ident, str) and ident.strip() else None
            if record['kind'] == 'invoices':
                entry.update(record=None, invoice_date=recover_date(raw, 'invoice_date'),
                             patient_id=(raw.get('patient_id') or None), contract_number=raw.get('contract_number'),
                             hospital_id=raw.get('hospital_id'),
                             invoice_total_cents=recover_int(raw, 'invoice_total_cents'))
                if ident:
                    self.headers[ident].append(entry)
                else:
                    self.unattributable.append(entry)
                continue
            entry.update(record=None, line_id=raw.get('line_id'), service_date=recover_date(raw, 'service_date'),
                         description=raw.get('description', ''), quantity=recover_int(raw, 'quantity'),
                         unit_price_cents=recover_int(raw, 'unit_price_cents'),
                         line_total_cents=recover_int(raw, 'line_total_cents'), defects=[])
            if ident:
                self.lines[ident].append(entry)
            else:
                self.unattributable.append(entry)
        for rows in self.headers.values():
            rows.sort(key=lambda r: (r['source'], r['row']))
        for rows in self.lines.values():
            rows.sort(key=lambda r: (r['source'], r['row']))
        self.cross_invoice = self._cross_invoice_duplicates()

    def unknown_service(self, description):
        """Whether this description names no service the contract sells.

        A confident no-match is the error, not a reason to abstain. The billed
        wording is compared against every contracted service name, including the
        ones added or restated by an amendment, since the contract bundle folds
        amendments into one service list with effective dates.
        """
        key = normalize(description)
        if key in self.reviewed_keys or not description:
            return False, None
        if canonical_multiset(description, self.lexicon) in self.reviewed_wordings:
            return False, None
        if key not in self.unknown_cache:
            unknown, evidence = names_no_contracted_service(description, self.contract['services'], self.lexicon)
            self.unknown_cache[key] = (unknown, evidence)
        return self.unknown_cache[key]

    def matched_service(self, description):
        """The one service this wording names, or None if it names more than one.

        A key the reviewers accepted keeps their service. Otherwise the matcher
        must return MATCH: a TIE or a WEAK reading does not identify a service,
        and no check that needs the contracted terms may run on it.
        """
        key = normalize(description)
        if key in self.reviewed_service:
            return self.services.get(self.reviewed_service[key])
        if key not in self.match_cache:
            result = classify(description, self.contract['services'], self.lexicon)
            self.match_cache[key] = result['service_id'] if result['state'] == 'MATCH' else None
        return self.services.get(self.match_cache[key])

    def canonical_header(self, invoice_id):
        """Latest-dated accepted header record; the documented choice for a reused identifier."""
        accepted = [h for h in self.headers.get(invoice_id, []) if h['status'] == 'accepted']
        if not accepted:
            return None
        return max(accepted, key=lambda h: (h['invoice_date'] or '', h['source'], h['row']))

    def _cross_invoice_duplicates(self):
        """The same patient, Service Day, normalised service and quantity on two invoices.

        H1, H3, H4 and H5 each forbid billing a Service twice for one Patient and
        Service Date, whether on one invoice or across several. H2's agreement
        states no such prohibition, but it states no permission either, and on
        the only labelled hospital every invoice this pattern matches is a
        labelled cross-invoice duplicate. Silence is therefore not evidence that
        a repeat is legitimate, and the check runs everywhere.
        """
        flagged = defaultdict(list)
        groups = defaultdict(list)
        for invoice_id, rows in self.lines.items():
            headers = self.headers.get(invoice_id, [])
            # A reused identifier makes the owning patient ambiguous; the reuse is
            # reported on its own and the lines are not used as duplicate evidence.
            if len(headers) != 1 or headers[0]['status'] != 'accepted':
                continue
            header = headers[0]
            for line in rows:
                if line['service_date'] is None or not line['quantity'] or line['quantity'] <= 0:
                    continue
                # Match on the canonical wording, so reordering the words or
                # swapping an abbreviation cannot hide a repeat.
                key = (header['patient_id'], line['service_date'],
                       canonical_multiset(line['description'], self.lexicon), line['quantity'])
                groups[key].append((header['invoice_date'] or '', invoice_id, line))
        for key, items in groups.items():
            invoices = {i[1] for i in items}
            if len(invoices) < 2:
                continue
            earliest = min((i[0], i[1]) for i in items)
            for invoice_date, invoice_id, line in items:
                if (invoice_date, invoice_id) == earliest:
                    continue
                flagged[invoice_id].append(
                    {'line_id': line['line_id'], 'line_source': f"{line['source']}:{line['row']}",
                     'patient_id': key[0], 'service_date': key[1], 'normalised_service': ' '.join(key[2]),
                     'quantity': key[3],
                     'first_billed_on_invoice_id': earliest[1], 'clause': self.duplicate_clause})
        return flagged

    def assess(self, invoice_id):
        """All mapping-free findings for one invoice identity, with evidence."""
        headers = self.headers.get(invoice_id, [])
        lines = self.lines.get(invoice_id, [])
        canonical = self.canonical_header(invoice_id)
        categories = set()
        evidence = []
        reused = len(headers) > 1
        if reused:
            categories.add('duplicate_invoice_id')
            evidence.append({'finding': 'duplicate_invoice_id', 'physical_records': len(headers),
                             'source_rows': [f"{h['source']}:{h['row']}" for h in headers],
                             'patients': sorted({h['patient_id'] for h in headers if h['patient_id']}),
                             'billed_totals': sorted({h['invoice_total_cents'] for h in headers
                                                      if h['invoice_total_cents'] is not None}),
                             'canonical_choice': 'latest-dated accepted header record',
                             'canonical_source': f"{canonical['source']}:{canonical['row']}" if canonical else None})
        for header in headers:
            if header['status'] == 'quarantined' and header['quarantine_category']:
                categories.add(header['quarantine_category'])
                evidence.append({'finding': header['quarantine_category'], 'scope': 'invoice_header',
                                 'source_row': f"{header['source']}:{header['row']}", 'reason': header['reason']})
        if canonical is not None:
            if canonical['contract_number'] != self.contract['contract_number']:
                categories.add('contract_number_mismatch')
                evidence.append({'finding': 'contract_number_mismatch', 'billed': canonical['contract_number'],
                                 'contract_number': self.contract['contract_number'],
                                 'note': 'Amendments to these agreements keep the contract number of the base agreement.'})
            if canonical['hospital_id'] != self.contract['hospital']:
                categories.add('hospital_reference_mismatch')
                evidence.append({'finding': 'hospital_reference_mismatch', 'billed': canonical['hospital_id'],
                                 'hospital': self.contract['hospital']})
        # A service cannot be invoiced before it happens. Under a reused identifier
        # the line cannot be joined to one physical header, so the latest invoice
        # date of any of them is used and only a date beyond that is reported.
        invoice_dates = [h['invoice_date'] for h in headers if h['invoice_date']]
        latest_invoice_date = max(invoice_dates) if invoice_dates else None
        duplicate_keys = {(d['line_source']) for d in self.cross_invoice.get(invoice_id, [])}
        billed_total = 0
        recoverable = True
        for line in lines:
            key = f"{line['source']}:{line['row']}"
            line['defects'] = []
            if line['status'] == 'quarantined' and line['quarantine_category']:
                categories.add(line['quarantine_category'])
                line['defects'].append(line['quarantine_category'])
                evidence.append({'finding': line['quarantine_category'], 'scope': 'line_item', 'source_row': key,
                                 'line_id': line['line_id'], 'reason': line['reason']})
            if line['service_date'] is not None:
                if not self.term[0] <= line['service_date'] <= self.term[1]:
                    categories.add('service_date_out_of_window')
                    line['defects'].append('service_date_out_of_window')
                    evidence.append({'finding': 'service_date_out_of_window', 'source_row': key,
                                     'line_id': line['line_id'], 'service_date': line['service_date'],
                                     'contract_term': list(self.term)})
                if latest_invoice_date and line['service_date'] > latest_invoice_date:
                    categories.add('service_date_after_invoice_date')
                    line['defects'].append('service_date_after_invoice_date')
                    evidence.append({'finding': 'service_date_after_invoice_date', 'source_row': key,
                                     'line_id': line['line_id'], 'service_date': line['service_date'],
                                     'invoice_date': latest_invoice_date,
                                     'basis': 'latest invoice date of every physical header for this identity'})
            if None not in (line['quantity'], line['unit_price_cents'], line['line_total_cents']):
                if line['quantity'] * line['unit_price_cents'] != line['line_total_cents']:
                    categories.add('line_total_arithmetic')
                    line['defects'].append('line_total_arithmetic')
                    evidence.append({'finding': 'line_total_arithmetic', 'source_row': key, 'line_id': line['line_id'],
                                     'quantity': line['quantity'], 'unit_price_cents': line['unit_price_cents'],
                                     'billed_line_total_cents': line['line_total_cents'],
                                     'arithmetic_line_total_cents': line['quantity'] * line['unit_price_cents']})
            service = None if line['status'] == 'quarantined' else self.matched_service(line['description'])
            billed_unit = line['record'].unit_basis_as_billed if line['record'] is not None else None
            if service is not None and billed_unit is not None and billed_unit != service['unit']:
                categories.add('wrong_unit_basis')
                line['defects'].append('wrong_unit_basis')
                evidence.append({'finding': 'wrong_unit_basis', 'source_row': key, 'line_id': line['line_id'],
                                 'service_id': service['id'], 'billed_unit_basis': billed_unit,
                                 'contracted_unit_basis': service['unit'], 'refs': service['refs'],
                                 'basis': 'The unit basis is read from the identified service only; a tie or a '
                                          'weak reading identifies no service and is not checked.'})
            unknown, unknown_evidence = self.unknown_service(line['description'])
            if unknown:
                categories.add('unknown_service')
                line['defects'].append('unknown_service')
                evidence.append(dict(unknown_evidence, finding='unknown_service', source_row=key,
                                     line_id=line['line_id'], billed_description=line['description'],
                                     basis='No contracted service for this hospital accounts for every word '
                                           'billed, across the whole service list including amendments.'))
            if key in duplicate_keys:
                line['defects'].append('cross_invoice_duplicate')
            if line['line_total_cents'] is None:
                recoverable = False
            else:
                billed_total += line['line_total_cents']
        for duplicate in self.cross_invoice.get(invoice_id, []):
            categories.add('cross_invoice_duplicate')
            evidence.append(dict(duplicate, finding='cross_invoice_duplicate'))
        # A reused identifier leaves the line-to-header split unknown, so the sum
        # of every physical line is not comparable with one header total.
        if canonical is not None and not reused and recoverable and lines:
            if billed_total != canonical['invoice_total_cents']:
                categories.add('invoice_total_mismatch')
                evidence.append({'finding': 'invoice_total_mismatch', 'billed_line_total_sum_cents': billed_total,
                                 'billed_invoice_total_cents': canonical['invoice_total_cents']})
        payable = lines
        attribution = None
        if reused and canonical is not None:
            assignment = attribute(headers, lines)
            if assignment is not None:
                payable = assignment[(canonical['source'], canonical['row'])]
                attribution = {'method': 'record number embedded in the line identifier',
                               'validated_by': 'each group of lines totals exactly one header record',
                               'canonical_source_row': f"{canonical['source']}:{canonical['row']}",
                               'lines_attributed': [l['line_id'] for l in payable],
                               'lines_on_the_other_records': [l['line_id'] for l in lines
                                                              if l not in payable]}
                evidence.append(dict(attribution, finding='duplicate_invoice_id',
                                     note='The reuse is reported; the lines it confuses are separable here.'))
        evidence.sort(key=lambda e: (e['finding'], str(e.get('source_row') or ''), str(e.get('line_id') or '')))
        return {'categories': order(categories), 'evidence': evidence, 'canonical_header': canonical,
                'lines': lines, 'payable_lines': payable, 'attribution': attribution,
                'headers': headers, 'reused_invoice_id': reused,
                'amount_affecting': sorted(set(categories) - AMOUNT_NEUTRAL)}
