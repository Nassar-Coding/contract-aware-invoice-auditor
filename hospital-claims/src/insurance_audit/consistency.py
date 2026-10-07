"""Settle a contract ambiguity by how the parties actually performed it.

Where an agreement admits more than one reading of a pricing stage, the audit
otherwise records every supported outcome and withholds the invoice. That
is right when the readings genuinely disagree about this hospital's billing,
and wasteful when they do not: if one reading accounts for essentially every
rate the hospital itself billed and the others account for far fewer, the
parties have shown which reading they were working to, and the handful of
lines that reading cannot account for are the errors.

This is **not** the billed price being used as evidence of which service a line
names — the matcher never sees a price, and that separation is asserted by its
own tests. It is the billed population being used to choose between readings of
a clause, which is how a course of dealing settles an ambiguous term.

A reading is adopted only when all of these hold:

- the contract text admits it (each candidate below cites the clause);
- the stage covers at least `MINIMUM_LINES` of this hospital's supported lines,
  so a handful of lines cannot settle a clause;
- it accounts for at least `MINIMUM_SHARE` of *every* relevant line, counting a
  line it leaves ambiguous as one it has not accounted for, so a reading cannot
  win the test by committing to less;
- every other admissible reading accounts for strictly less.

Otherwise the stage stays ambiguous and those invoices stay withheld. Nothing
here is fitted to labels, stored between runs, or keyed to an invoice, a record
count or an input fingerprint: the test is recomputed from whatever data is
present, so it re-decides itself on unseen invoices.
"""
import copy

from .pricing import Uncertain, rate_for
from .resolve import resolve

MINIMUM_SHARE = 0.98
MINIMUM_LINES = 50


def _service_day_relevant(service, contract):
    """Services whose rate or cap depends on which Service Day a line falls in."""
    bundled = {b['a'] for b in contract['bundles']} | {b['b'] for b in contract['bundles']}
    return (tuple(service['weekend_multiplier']) != (1, 1) or bool(service['daily_premium'])
            or service['id'] in bundled or service['daily_cap'] is not None)


def _volume_relevant(service, contract):
    return bool(service['volume_discounts'])


def _facility_relevant(service, contract):
    return bool(service['facility_multipliers'])


# Each entry names a semantics field, the readings the contract text admits, and
# the clause that admits them. A field is only tested when the contract declares
# one of the readings listed as ambiguous.
CANDIDATES = {
    'service_day': {
        'ambiguous_values': ('seven_am_unobserved',),
        'readings': {
            'calendar': 'A Service delivered wholly within a single calendar day is delivered on the '
                        'Service Day bearing that calendar date (H2 clause 2.2).',
            'seven_am_unobserved': 'A Service Day is the 24 hours from 07:00, so a service may fall in '
                                   'the Service Day beginning on the previous calendar date (H2 clause 2.2).'},
        'relevant': _service_day_relevant,
        'stages': 'weekend uplift, daily premium, bundle partner presence and daily cap allocation'},
    'volume_basis': {
        'ambiguous_values': ('contract_term_patient_scope_unspecified',),
        'readings': {
            'contract_term_all_patients_strict_prior_billed_units':
                'Cumulative utilisation is aggregated across all patients over the term.',
            'contract_term_patient_scope_unspecified':
                'The clause does not say whether utilisation aggregates across patients, so only the '
                "same patient's prior utilisation is certain."},
        'relevant': _volume_relevant,
        'stages': 'cumulative volume discount'},
    'facility_source': {
        'ambiguous_values': ('invoice_projection_reviewed',),
        'readings': {
            'none': 'No facility multiplier is applied.',
            'invoice_projection_reviewed': "The invoice's facility code is projected onto its lines."},
        'relevant': _facility_relevant,
        'stages': 'facility multiplier'},
}


def _explained(data, contract, mappings, relevant, context_factory):
    """How many of the relevant lines this reading prices to exactly what was billed."""
    context = context_factory(data, contract, mappings)
    services = {s['id']: s for s in contract['services']}
    headers = {row.invoice_id: row for row in data.invoices}
    matched = differed = indefinite = 0
    for line in data.lines:
        header = headers.get(line.invoice_id)
        if header is None:
            continue
        service_id, _, _ = resolve(line.description, context.index, context.services)
        if service_id is None:
            continue
        service = services[service_id]
        if not relevant(service, contract):
            continue
        try:
            rate = rate_for(line, header, service, contract, context)['rate']
        except Uncertain:
            indefinite += 1
            continue
        if rate == line.unit_price_cents:
            matched += 1
        else:
            differed += 1
    priced = matched + differed
    # The denominator is every relevant line, not just the ones this reading
    # chose to price. A reading that leaves a line ambiguous has not accounted
    # for it either, and judging each reading only on the lines it happens to
    # settle would hand the test to whichever reading commits to least.
    relevant_lines = priced + indefinite
    return {'relevant_lines': relevant_lines, 'lines_priced_to_one_rate': priced,
            'accounted_for': matched, 'differing': differed, 'left_ambiguous': indefinite,
            'share': round(matched / relevant_lines, 6) if relevant_lines else None}


def assess(data, contract, mappings, context_factory):
    """Test every admissible reading of each ambiguous stage against this hospital's billing."""
    findings = {}
    for field, candidate in sorted(CANDIDATES.items()):
        declared = contract['semantics'].get(field)
        if declared not in candidate['ambiguous_values']:
            continue
        measured = {}
        for reading in sorted(candidate['readings']):
            trial = copy.deepcopy(contract)
            trial['semantics'][field] = reading
            measured[reading] = _explained(data, trial, mappings, candidate['relevant'], context_factory)
        ranked = sorted(measured.items(), key=lambda pair: (-(pair[1]['share'] or 0), pair[0]))
        best, runner_up = ranked[0], (ranked[1] if len(ranked) > 1 else None)
        adopted = None
        why = 'no reading met the bar'
        if best[1]['share'] is None or best[1]['relevant_lines'] < MINIMUM_LINES:
            why = f'fewer than {MINIMUM_LINES} lines could be priced to one rate under any reading'
        elif best[1]['share'] < MINIMUM_SHARE:
            why = f'the best reading accounts for {best[1]["share"]:.3f}, below {MINIMUM_SHARE}'
        elif runner_up is not None and (runner_up[1]['share'] or 0) >= best[1]['share']:
            why = 'another reading accounts for at least as much, so the billing does not choose between them'
        else:
            adopted, why = best[0], 'adopted'
        findings[field] = {'declared': declared, 'adopted': adopted, 'outcome': why,
                           'stages': candidate['stages'], 'minimum_share': MINIMUM_SHARE,
                           'minimum_lines': MINIMUM_LINES,
                           'readings': {name: dict(measured[name], clause=candidate['readings'][name])
                                        for name in measured}}
    return findings


def apply(contract, findings):
    """A copy of the contract with every adopted reading written in."""
    adopted = {field: row['adopted'] for field, row in findings.items() if row['adopted']}
    if not adopted:
        return contract
    resolved = copy.deepcopy(contract)
    for field, reading in adopted.items():
        resolved['semantics'][field] = reading
    resolved['adopted_readings'] = adopted
    return resolved
