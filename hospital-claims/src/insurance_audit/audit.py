"""Complete invoice opinions; no label or model access.

Two decisions can be reached. A *finding* is evidence that is itself the error:
it is reported whatever else on the invoice is unresolved. A *complete audit*
prices every line and either reports the priced differences or asserts the
invoice is clean. Only an invoice with neither is withheld, and every withheld
invoice carries a named unresolved fact.
"""
from collections import defaultdict
from datetime import date, timedelta
from . import consistency
from .context import Context
from .findings import Findings, order
from .mapping import classify
from .resolve import resolve
from .pricing import rate_for, Uncertain
from .checks import validate_line_facts, line_result


def line_key(source, row):
    return f'{source}:{row}'


def corrected_total(facts, priced, categories):
    """Full correction where every line is priced, partial correction otherwise.

    A line the audit could not price keeps its billed amount, except where a
    contract-free correction exists: an arithmetic line total is recomputed from
    its own quantity and unit price, and a line billed twice across invoices is
    not payable at all and contributes nothing.
    """
    canonical = facts['canonical_header']
    billed = canonical['invoice_total_cents']
    if facts['reused_invoice_id'] and facts['attribution'] is None:
        # Nothing separates the lines of the records sharing the identifier, so
        # no line-level correction is supportable.
        return billed, 'reused_identifier_canonical_billed_total', []
    total = 0
    contributions = []
    full = True
    for line in facts['payable_lines']:
        key = line_key(line['source'], line['row'])
        entry = {'source': line['source'], 'source_row': line['row'], 'line_id': line['line_id']}
        if 'cross_invoice_duplicate' in line['defects']:
            contributions.append(dict(entry, contribution_cents=0, basis='duplicate_not_payable'))
            full = False
            continue
        result = priced.get(key)
        if result is not None:
            total += result['expected_total_cents']
            contributions.append(dict(entry, contribution_cents=result['expected_total_cents'], basis='corrected'))
            continue
        full = False
        if 'line_total_arithmetic' in line['defects']:
            value = line['quantity'] * line['unit_price_cents']
            total += value
            contributions.append(dict(entry, contribution_cents=value, basis='arithmetic_recomputed'))
            continue
        if line['line_total_cents'] is None:
            return billed, 'unrecoverable_line_amount_billed_total', []
        total += line['line_total_cents']
        contributions.append(dict(entry, contribution_cents=line['line_total_cents'], basis='billed'))
    basis = 'full_correction' if full else 'partial_correction'
    if facts['reused_invoice_id']:
        basis = 'attributed_' + basis
    return total, basis, contributions


def every_reading(line, invoice, candidates, contract, context):
    """Audit one ambiguous line once per candidate service.

    A line whose wording names more than one contracted service still has an
    audit result under each of them. Where every reading reaches the same
    verdict the ambiguity does not matter, and the verdict can be reported.
    """
    outcomes = []
    for service_id in candidates:
        service = context.services.get(service_id)
        if service is None:
            continue
        try:
            validate_line_facts(line, invoice, service, contract, context)
            price = rate_for(line, invoice, service, contract, context)
            result = line_result(line, service, price)
            categories = list(result['error_categories'])
            if line.unit_basis_as_billed != service['unit']:
                categories.append('wrong_unit_basis')
            outcomes.append({'service_id': service_id, 'status': 'priced',
                             'error_categories': sorted(set(categories)),
                             'expected_total_cents': result['expected_total_cents']})
        except Uncertain as error:
            outcomes.append({'service_id': service_id, 'status': 'uncertain', 'reason': error.reason})
    return outcomes


def reconcile(outcomes):
    """Turn the per-reading outcomes into one verdict for the line.

    `error` only when every reading is priced and every one of them finds a
    fault; `clean` only when every reading is priced and none does. Anything
    else is `ambiguous`, and carries the share of readings that found a fault
    so a threshold can be set on it.
    """
    if not outcomes or any(o['status'] != 'priced' for o in outcomes):
        return {'verdict': 'unresolved', 'error_fraction': None, 'error_categories': [],
                'expected_total_cents': None, 'readings': outcomes}
    faulty = [o for o in outcomes if o['error_categories']]
    fraction = len(faulty) / len(outcomes)
    amounts = {o['expected_total_cents'] for o in outcomes}
    agreed = amounts.pop() if len(amounts) == 1 else None
    if fraction == 1.0:
        shared = set(outcomes[0]['error_categories'])
        for other in outcomes[1:]:
            shared &= set(other['error_categories'])
        return {'verdict': 'error', 'error_fraction': 1.0, 'error_categories': sorted(shared),
                'expected_total_cents': agreed, 'readings': outcomes}
    if fraction == 0.0:
        return {'verdict': 'clean', 'error_fraction': 0.0, 'error_categories': [],
                'expected_total_cents': agreed, 'readings': outcomes}
    return {'verdict': 'ambiguous', 'error_fraction': fraction, 'error_categories': [],
            'expected_total_cents': agreed, 'readings': outcomes}


def contract_rule_findings(line, invoice, service, contract, context):
    """Daily caps and exclusion windows, reported without needing a priced line.

    Both are read from the contract's own reviewed rules and from the retained
    history, so they hold whatever the rest of the invoice leaves unresolved.
    """
    found = []
    if service['daily_cap'] is not None:
        total = context.service_day_total(service['id'], invoice.patient_id, line.service_date)
        if total['lower'] is not None and total['lower'] > service['daily_cap']:
            found.append({'finding': 'daily_cap_exceeded', 'line_id': line.line_id,
                          'service_id': service['id'], 'maximum_units_per_patient_per_service_day':
                              service['daily_cap'], 'billed_this_service_day': total['lower'],
                          'service_day': line.service_date, 'patient_id': invoice.patient_id,
                          'evidence': total, 'refs': service['refs'],
                          'basis': 'Quantity is summed for this patient, service and Service Day across every '
                                   'invoice in the retained history, not per line.'})
    direction = contract['semantics']['exclusion_direction']
    for rule in contract['exclusions']:
        if rule['service'] != service['id']:
            continue
        state, detail = context.exclusion(rule, invoice.patient_id, line.service_date)
        if state is not True:
            continue
        found.append({'finding': 'exclusion_window_violation', 'line_id': line.line_id,
                      'service_id': service['id'], 'anchor_service_id': rule['anchor'],
                      'window_days': rule['days'], 'service_date': line.service_date,
                      'patient_id': invoice.patient_id, 'exclusion_direction': direction,
                      'boundary': 'a Service Date exactly the window away is inside the window',
                      'evidence': detail, 'refs': [rule['source']]})
    return found


AMBIGUITY_REASONS = frozenset({'ambiguous_service_mapping', 'ambiguous_corrected_amount'})
# A finding that rests on the records and the contract's own identifiers, dates
# and arithmetic, rather than on identifying a service and pricing it.
STRUCTURAL_FINDINGS = frozenset({'duplicate_invoice_id', 'cross_invoice_duplicate', 'contract_number_mismatch',
                                 'hospital_reference_mismatch', 'service_date_after_invoice_date',
                                 'service_date_out_of_window', 'malformed_service_date',
                                 'malformed_invoice_date', 'malformed_amount', 'line_total_arithmetic',
                                 'invoice_total_mismatch'})


def evidence_tier(flagged, categories, amount_basis, reasons):
    """Which kind of evidence an emitted row rests on.

    The tiers are ordered by how much has to be true for the row to be right,
    strongest first, and each row takes the first that applies.
    """
    if not flagged:
        return 'clean'
    named = set(categories)
    if named & STRUCTURAL_FINDINGS:
        return 'structural'
    if 'unknown_service' in named:
        return 'no_match'
    if any(r['reason'] in AMBIGUITY_REASONS for r in reasons):
        return 'ambiguous'
    if amount_basis != 'full_correction':
        return 'partial_amount'
    return 'match_fully_priced'


def audit(data, contract, mappings):
    # Where the agreement admits more than one reading of a pricing stage, test
    # each against this hospital's own billing before auditing anything: a
    # reading the parties plainly worked to settles the stage, and the lines it
    # cannot account for become findings instead of withheld invoices.
    readings = consistency.assess(data, contract, mappings, Context)
    contract = consistency.apply(contract, readings)
    context = Context(data, contract, mappings)
    structure = Findings(data, contract, mappings)
    by_invoice = defaultdict(list)
    for line in data.lines:
        by_invoice[line.invoice_id].append(line)
    opinions = []
    abstentions = []
    traces = []
    for invoice_id in sorted(context.invoice_ids):
        facts = structure.assess(invoice_id)
        canonical = facts['canonical_header']
        source_headers = context.header_evidence[invoice_id]
        trace = {'invoice_id': invoice_id, 'hospital': data.hospital,
                 'source_headers': [{'source': h['source'], 'row': h['row']} for h in source_headers],
                 'findings': facts['categories'], 'finding_evidence': facts['evidence'], 'lines': []}
        if any(h['status'] == 'quarantined' for h in source_headers):
            trace['header_ownership_evidence'] = source_headers
        reasons = []
        qualifications = set()
        if canonical is None:
            reasons.append({'reason': 'no_accepted_invoice_header'})
        if facts['reused_invoice_id']:
            reasons.append({'reason': 'conflicting_reused_invoice_id', 'detail': {
                'patients': sorted({h['patient_id'] for h in facts['headers'] if h['patient_id']}),
                'billed_totals': sorted({h['invoice_total_cents'] for h in facts['headers']
                                         if h['invoice_total_cents'] is not None})}})
        if structure.unattributable:
            reasons.append({'reason': 'unlinked_quarantined_invoice_header',
                            'detail': [{'source': r['source'], 'row': r['row'], 'reason': r['reason']}
                                       for r in structure.unattributable]})
        for line in facts['lines']:
            if line['status'] == 'quarantined' and not line['quarantine_category']:
                reasons.append({'reason': 'quarantined_required_source_record',
                                'detail': {'source_row': line_key(line['source'], line['row']),
                                           'quarantine_reason': line['reason']}})
        if not facts['lines']:
            reasons.append({'reason': 'invoice_without_accepted_lines'})
        # Hospital 2's Article XIII makes an invoice effective only if submitted
        # within sixty days of discharge, but no submission date is recorded, so
        # the condition is unobservable. It is logged and never suppresses a
        # finding or a priced difference: the invoice date does not prove the
        # submission date either way.
        if contract['semantics']['invoice_eligibility'] == 'unobserved_submission_deadline_and_waiver' and canonical:
            discharge = canonical['record'].discharge_date
            deadline = (date.fromisoformat(discharge) + timedelta(days=60)).isoformat() if discharge else None
            trace['unobserved_eligibility'] = {
                'source': 'H2 Articles II.5 and XIII.1-XIII.5', 'recorded_discharge_date': discharge,
                'provisional_60_day_deadline': deadline, 'invoice_date': canonical['invoice_date'],
                'missing': ['actual submission date', 'applicable episode/leave evidence',
                            'written agreement or exception if late'],
                'effect': 'Effectiveness is unresolved. It is recorded, not treated as an error, '
                          'and every other check proceeds normally.'}
            qualifications.add('unobserved_submission_deadline_and_waiver')
        priced = {}
        categories = []
        reading_findings = []
        rule_evidence = []
        grades = set()
        invariant = False
        separable = not facts['reused_invoice_id'] or facts['attribution'] is not None
        complete = canonical is not None and separable and bool(facts['payable_lines'])
        line_reasons = []
        if canonical is not None and separable:
            invoice = canonical['record']
            payable_ids = {l['line_id'] for l in facts['payable_lines']}
            for line in sorted((l for l in by_invoice[invoice_id] if l.line_id in payable_ids),
                               key=lambda l: (l.line_no, l.line_id, l.source_row)):
                key = line_key(line.source, line.source_row)
                sid, possible, grade = resolve(line.description, context.index, context.services)
                entry = {'line_id': line.line_id, 'source': line.source, 'source_row': line.source_row,
                         'description': line.description, 'service_id': sid,
                         'possible_service_ids': sorted(possible), 'mapping_grade': grade,
                         'quantity': line.quantity, 'billed_unit': line.unit_basis_as_billed,
                         'billed_unit_price_cents': line.unit_price_cents,
                         'billed_line_total_cents': line.line_total_cents}
                identified = structure.matched_service(line.description)
                if identified is not None:
                    for found in contract_rule_findings(line, invoice, identified, contract, context):
                        reading_findings.append(found['finding'])
                        rule_evidence.append(dict(found, source_row=key))
                if sid is None:
                    # A description that names no contracted service has no
                    # contracted rate either. Pricing it against whichever service
                    # it resembles would put a fabricated figure on the row, so the
                    # billed amount stands and only the naming is reported.
                    if structure.unknown_service(line.description)[0]:
                        reason = {'reason': 'unknown_service_has_no_contracted_rate', 'line_id': line.line_id,
                                  'detail': {'description': line.description}}
                        line_reasons.append(reason)
                        entry.update(status='uncertain', reason=reason)
                        complete = False
                        trace['lines'].append(entry)
                        continue
                    reading = classify(line.description, contract['services'], context.lexicon)
                    verdict = reconcile(every_reading(line, invoice, reading['candidates'], contract, context))
                    entry.update(mapping_state=reading['state'], every_reading=verdict['readings'],
                                 error_fraction=verdict['error_fraction'])
                    if verdict['verdict'] in {'error', 'clean'} and verdict['expected_total_cents'] is not None:
                        result = {'expected_total_cents': verdict['expected_total_cents'],
                                  'error_categories': verdict['error_categories']}
                        entry.update(status='supported_under_every_reading', result=result)
                        priced[key] = result
                        categories.extend(verdict['error_categories'])
                        grades.add('elided')
                        trace['lines'].append(entry)
                        continue
                    if verdict['verdict'] == 'error':
                        # Every reading finds a fault and they disagree only on the
                        # corrected amount. The fault is established; the amount is
                        # not, so the line keeps what was billed.
                        named = verdict['error_categories'] or ['ambiguous_service_pricing']
                        reading_findings.extend(named)
                        trace['finding_evidence'].append(
                            {'finding': named[0], 'also_named': named[1:], 'source_row': key,
                             'line_id': line.line_id, 'billed_description': line.description,
                             'candidates': reading['candidates'], 'matcher_state': reading['state'],
                             'expected_totals': sorted({o['expected_total_cents'] for o in verdict['readings']}),
                             'basis': 'Every candidate reading of this wording finds the same fault; they '
                                      'disagree only on the corrected amount.'})
                        entry.update(status='faulty_under_every_reading',
                                     result={'error_categories': named, 'expected_total_cents': None})
                        line_reasons.append({'reason': 'ambiguous_corrected_amount', 'line_id': line.line_id,
                                             'detail': {'candidates': reading['candidates'],
                                                        'expected_totals': sorted(
                                                            {o['expected_total_cents'] for o in verdict['readings']})}})
                        complete = False
                        trace['lines'].append(entry)
                        continue
                    reason = {'reason': 'ambiguous_service_mapping' if verdict['verdict'] == 'ambiguous'
                                        else 'unresolved_service_mapping',
                              'line_id': line.line_id,
                              'detail': {'description': line.description, 'candidates': reading['candidates'],
                                         'matcher_state': reading['state'],
                                         'error_fraction': verdict['error_fraction']}}
                    line_reasons.append(reason)
                    entry.update(status='uncertain', reason=reason)
                    complete = False
                    trace['lines'].append(entry)
                    continue
                try:
                    service = context.services[sid]
                    entry['source_refs'] = service['refs']
                    entry['duplicate_check'] = validate_line_facts(line, invoice, service, contract, context)
                    price = rate_for(line, invoice, service, contract, context)
                    result = line_result(line, service, price)
                    entry.update(status='supported', pricing=price, result=result)
                    priced[key] = result
                    if 'daily_cap_exceeded' in result['error_categories']:
                        qualifications.add('capped_quantity_substituted_for_an_unobserved_one')
                    # A line that resolved to one service and priced to one rate
                    # under the adopted readings carries its own evidence. An
                    # unrelated unresolved line elsewhere on the invoice does not
                    # make that difference any less established, so it is reported
                    # rather than withheld with the rest of the invoice.
                    reading_findings.extend(result['error_categories'])
                    for named in result['error_categories']:
                        rule_evidence.append({
                            'finding': named, 'source_row': key, 'line_id': line.line_id,
                            'service_id': service['id'], 'billed_unit_price_cents': line.unit_price_cents,
                            'contracted_unit_rate_cents': price['rate'],
                            'billed_line_total_cents': line.line_total_cents,
                            'expected_line_total_cents': result['expected_total_cents'],
                            'adjustments': [s['operation'] for s in price['stages']],
                            'refs': service['refs'],
                            'basis': 'The contracted rate is rebuilt from the clauses in the order the '
                                     'agreement fixes, and compared with what was billed.'})
                    categories.extend(result['error_categories'])
                    grades.add(grade)
                    invariant |= price['outcome_invariant_uncertainty']
                    qualifications.update(price.get('qualifications', []))
                except Uncertain as error:
                    reason = {'reason': error.reason, 'line_id': line.line_id, 'detail': error.detail}
                    line_reasons.append(reason)
                    entry.update(status='uncertain', reason=reason)
                    complete = False
                trace['lines'].append(entry)
        if any(l['status'] == 'quarantined' for l in facts['payable_lines']):
            complete = False
        reasons.extend(line_reasons)
        # A fault every candidate reading agrees on is established evidence, so
        # it joins the findings and is reported whatever else is unresolved.
        findings = order(list(dict.fromkeys(facts['categories'] + sorted(set(reading_findings)))))
        trace['finding_evidence'].extend(rule_evidence)
        trace['findings'] = findings
        trace['finding_evidence'].sort(key=lambda e: (e['finding'], str(e.get('source_row') or '')))
        # No accepted header record means no billed total to report against, so
        # even a named finding cannot be emitted as a row.
        if canonical is not None and (findings or (complete and categories)):
            named = order(list(dict.fromkeys(findings + sorted(set(categories)))))
            expected, basis, contributions = corrected_total(facts, priced, named)
            billed = canonical['invoice_total_cents']
            if complete and expected != billed and 'invoice_total_mismatch' not in named:
                named = order(named + ['invoice_amount_mismatch'])
            unchanged = None
            if expected == billed:
                unchanged = ('finding_does_not_affect_the_payable_amount'
                             if not facts['amount_affecting'] and not categories
                             else 'corrections_offset_to_the_billed_total')
            opinion = {'invoice_id': invoice_id, 'hospital': data.hospital, 'flagged': 1,
                       'error_category': ';'.join(named), 'expected_total_cents': expected,
                       'billed_total_cents': billed, 'confidence': None,
                       'mapping_evidence': 'elided' if 'elided' in grades else 'explicit',
                       'outcome_invariant_uncertainty': invariant,
                       'confidence_state': 'not_assigned_before_policy',
                       'interpretation_qualifications': sorted(qualifications),
                       'decision_basis': 'complete_audit' if complete and not findings else 'finding',
                       'amount_basis': basis, 'amount_unchanged_reason': unchanged,
                       'evidence_tier': evidence_tier(1, named, basis, reasons),
                       'trace_key': invoice_id}
            trace.update(status='supported', opinion=opinion, amount_contributions=contributions,
                         unresolved_facts=reasons)
            opinions.append(opinion)
        elif reasons or not complete:
            if not reasons:
                reasons.append({'reason': 'invoice_not_completely_audited'})
            definite = order(list(dict.fromkeys(findings + sorted(set(categories)))))
            trace.update(status='abstained', reasons=reasons, definite_error_categories=definite)
            abstentions.append({'invoice_id': invoice_id, 'hospital': data.hospital, 'reasons': reasons,
                                'definite_error_categories': definite})
        else:
            opinion = {'invoice_id': invoice_id, 'hospital': data.hospital, 'flagged': 0,
                       'error_category': '', 'expected_total_cents': sum(r['expected_total_cents'] for r in priced.values()),
                       'billed_total_cents': canonical['invoice_total_cents'], 'confidence': None,
                       'mapping_evidence': 'elided' if 'elided' in grades else 'explicit',
                       'outcome_invariant_uncertainty': invariant,
                       'confidence_state': 'not_assigned_before_policy',
                       'interpretation_qualifications': sorted(qualifications),
                       'decision_basis': 'complete_audit', 'amount_basis': 'full_correction',
                       'amount_unchanged_reason': None, 'evidence_tier': 'clean',
                       'trace_key': invoice_id}
            if opinion['expected_total_cents'] != opinion['billed_total_cents']:
                opinion.update(flagged=1, error_category='invoice_amount_mismatch',
                               amount_unchanged_reason=None,
                               evidence_tier=evidence_tier(1, ['invoice_amount_mismatch'],
                                                           'full_correction', reasons))
            opinions.append(opinion)
            trace.update(status='supported', opinion=opinion,
                         amount_contributions=[{'source': l['source'], 'source_row': l['row'],
                                                'line_id': l['line_id'],
                                                'contribution_cents': priced[line_key(l['source'], l['row'])]['expected_total_cents'],
                                                'basis': 'corrected'} for l in facts['payable_lines']],
                         unresolved_facts=[])
        traces.append(trace)
    return {'hospital': data.hospital, 'opinions': opinions, 'abstentions': abstentions, 'traces': traces,
            'adopted_readings': readings,
            'accounting': {'raw_records': len(data.dispositions), 'accepted_invoice_records': len(data.invoices),
                           'accepted_line_records': len(data.lines),
                           'quarantined_records': sum(d['status'] == 'quarantined' for d in data.dispositions),
                           'unique_invoice_ids': len(context.invoice_ids), 'opinions': len(opinions),
                           'abstentions': len(abstentions), 'context_records': len(context.items),
                           'orphan_line_count': sum(l.invoice_id not in context.invoice_ids for l in data.lines)}}
