"""Finite contract-data validation and accepted artifact compatibility."""
from __future__ import annotations
import hashlib
import json
from datetime import date
from pathlib import Path

SCHEMA_VERSION='2'
SEMANTICS_VERSION='2'
NORMALIZER_VERSION='2'
UNITS={'per_hour','per_day','per_night','per_visit','per_test','per_procedure','per_item','per_unit_dispensed','per_hour_per_item'}
OPERATIONS=['bundle','facility','tier','daily_premium','weekend_uplift','volume_discount']


class SchemaError(ValueError):pass


def require(condition,message):
    if not condition:raise SchemaError(message)


def keys(obj,expected,label,optional=frozenset()):
    require(isinstance(obj,dict),f'{label}: object required')
    missing=set(expected)-set(optional)-set(obj);extra=set(obj)-set(expected)
    require(not missing and not extra,f'{label}: unexpected/missing fields {missing|extra}')


def integer(v,label,low=0,high=10**15):
    require(type(v) is int and low<=v<=high,f'{label}: bounded integer required')


def ratio(v,label):
    require(isinstance(v,list) and len(v)==2,f'{label}: integer ratio required')
    integer(v[0],label,0,10**9);integer(v[1],label,1,10**9)


def period(v,label):
    require(isinstance(v,str),f'{label}: ISO date required')
    try:date.fromisoformat(v)
    except ValueError as error:raise SchemaError(f'{label}: invalid ISO date') from error
    require(len(v)==10,f'{label}: YYYY-MM-DD required')


def references(values,label):
    require(isinstance(values,list) and values and all(isinstance(v,str) and v for v in values),f'{label}: source references required')


def text(value,label):
    require(isinstance(value,str) and bool(value.strip()),f'{label}: nonempty text required')


def validate_contract(c):
    keys(c,{'schema_version','semantics_version','hospital','contract_number','term','review_state','source_hashes',
            'semantics','services','bundles','exclusions','coverage'},'contract')
    require(c['schema_version']==SCHEMA_VERSION and c['semantics_version']==SEMANTICS_VERSION,'Incompatible contract semantics/version')
    require(c['hospital'] in {'H1','H2','H3','H4','H5'},'Unknown hospital')
    require(isinstance(c['contract_number'],str) and c['contract_number'],'Missing contract identity')
    require(c['review_state'] in {'candidate','accepted'},'Invalid review state')
    require(isinstance(c['term'],list) and len(c['term'])==2,'Term required')
    for v in c['term']:period(v,'term')
    require(c['term'][0]<=c['term'][1],'Reversed term')
    require(isinstance(c['source_hashes'],dict) and c['source_hashes'],'Source hashes required')
    for path,h in c['source_hashes'].items():
        require(path.startswith('data/source/contracts/') and '..' not in Path(path).parts,'Invalid source path')
        # Historical fingerprints are metadata, never an execution precondition.
    s=c['semantics']
    keys(s,{'rounding','adjustment_order','service_day','exclusion_direction','exclusion_boundary','duplicate_policy',
            'cap_policy','facility_source','volume_basis','version_precedence','invoice_eligibility','service_date_not_after_invoice','refs'},'semantics')
    require(s['rounding']=='half_up_cent','Unsupported rounding')
    require(s['adjustment_order']==OPERATIONS,'Unknown operation/order; no executable expressions allowed')
    require(s['service_day'] in {'calendar','seven_am_unobserved'},'Unsupported service-day semantics')
    require(s['exclusion_direction'] in {'both','uncertain_before_or_both','uncertain_direction'},'Unsupported exclusion direction')
    # "Not billable within N days" covers a Service Date exactly N days away:
    # the boundary day is the last day inside the window, not the first outside it.
    require(s['exclusion_boundary']=='inclusive_at_equal','Unsupported boundary interpretation')
    require(s['duplicate_policy'] in {'abstain_repeated_service_day','no_explicit_service_date_prohibition'},'Unsupported duplicate allocation')
    require(type(s['service_date_not_after_invoice']) is bool,'Date/invoice requirement must be explicit boolean')
    require(s['invoice_eligibility'] in {'supplied_invoice_facts','unobserved_submission_deadline_and_waiver'},'Unsupported invoice eligibility')
    require(s['cap_policy']=='single_line_cap_otherwise_abstain','Unsupported cap allocation')
    require(s['facility_source'] in {'none','invoice_projection_reviewed'},'Unsupported facility source')
    require(s['volume_basis'] in {'contract_term_all_patients_strict_prior_billed_units','contract_term_patient_scope_unspecified'},'Unsupported utilisation semantics')
    require(s['version_precedence'] in {'single','amendment_over_base'},'Unsupported version precedence')
    references(s['refs'],'semantics')
    require(isinstance(c['services'],list) and c['services'],'Missing services')
    ids=set();names=set()
    for svc in c['services']:
        keys(svc,{'id','name','unit','versions','daily_premium','weekend_multiplier','volume_discounts',
                  'daily_cap','facility_multipliers','tier_multipliers','support','refs'},'service')
        require(isinstance(svc['id'],str) and svc['id'] not in ids,'Duplicate/missing service ID');ids.add(svc['id'])
        require(isinstance(svc['name'],str) and svc['name'] not in names,'Duplicate/missing service name');names.add(svc['name'])
        require(svc['unit'] in UNITS,'Unsupported unit')
        require(svc['support'] in {'supported','composite_dimension_unobserved','service_day_unobserved'},'Unsupported support state')
        require(isinstance(svc['versions'],list) and svc['versions'],'Missing rates')
        for v in svc['versions']:
            keys(v,{'start','end','cents','priority','source'},'rate version')
            period(v['start'],'rate start');period(v['end'],'rate end')
            require(c['term'][0]<=v['start']<=v['end']<=c['term'][1],'Rate outside contract term')
            integer(v['cents'],'rate');integer(v['priority'],'priority',0,100)
            require(isinstance(v['source'],str) and v['source'],'Rate source required')
        for i,a in enumerate(svc['versions']):
            for b in svc['versions'][i+1:]:
                if max(a['start'],b['start'])<=min(a['end'],b['end']):
                    require(s['version_precedence']=='amendment_over_base' and a['priority']!=b['priority'],'Unexplained/conflicting rate overlap')
        if svc['daily_premium'] is not None:
            keys(svc['daily_premium'],{'exceeds','multiplier','source'},'daily premium')
            integer(svc['daily_premium']['exceeds'],'daily threshold',0,10**9);ratio(svc['daily_premium']['multiplier'],'daily premium')
            text(svc['daily_premium']['source'],'daily premium source')
        ratio(svc['weekend_multiplier'],'weekend')
        require(not (svc['daily_premium'] and svc['weekend_multiplier']!=[1,1]),'Order within multiple premium classes not established')
        require(isinstance(svc['volume_discounts'],list),'Discount list required')
        previous=-1
        for d in svc['volume_discounts']:
            keys(d,{'exceeds','multiplier','source'},'volume discount')
            integer(d['exceeds'],'volume threshold',0,10**9);ratio(d['multiplier'],'volume discount')
            text(d['source'],'discount source')
            require(d['exceeds']>previous,'Volume thresholds must strictly increase');previous=d['exceeds']
        if svc['daily_cap'] is not None:integer(svc['daily_cap'],'daily cap',1,10**9)
        for field in ['facility_multipliers','tier_multipliers']:
            require(isinstance(svc[field],dict),f'{field}: map required')
            for k,v in svc[field].items():require(isinstance(k,str) and k,f'{field}: key');ratio(v,field)
        references(svc['refs'],'service')
    require(isinstance(c['bundles'],list) and isinstance(c['exclusions'],list),'Rule lists required')
    for b in c['bundles']:
        keys(b,{'a','b','a_cents','b_cents','source'},'bundle')
        require(b['a'] in ids and b['b'] in ids and b['a']!=b['b'],'Invalid bundle service reference')
        integer(b['a_cents'],'bundle A');integer(b['b_cents'],'bundle B')
        text(b['source'],'bundle source')
    for e in c['exclusions']:
        keys(e,{'service','anchor','days','source'},'exclusion')
        require(e['service'] in ids and e['anchor'] in ids,'Invalid exclusion reference');integer(e['days'],'window',0,10**6)
        text(e['source'],'exclusion source')
    require(isinstance(c['coverage'],list) and c['coverage'],'Clause coverage required')
    for row in c['coverage']:
        keys(row,{'source','state','representation','qualification'},'coverage')
        require(row['state'] in {'accepted','unresolved','outside_scope'},'Invalid clause state')
        text(row['source'],'coverage source');text(row['representation'],'coverage representation')
        require(isinstance(row['qualification'],str),'Coverage qualification must be text')
    return c


def validate_mappings(m,c):
    from .resolve import normalize
    keys(m,{'schema_version','hospital','review_state','normalizer_version','records'},'mappings')
    require(m['schema_version']==SCHEMA_VERSION and m['normalizer_version']==NORMALIZER_VERSION,'Incompatible mappings')
    require(m['hospital']==c['hospital'],'Cross-hospital mappings')
    require(m['review_state'] in {'candidate','accepted'},'Unreviewed mapping state')
    require(isinstance(m['records'],list) and m['records'],'Mapping records required')
    ids={s['id'] for s in c['services']};seen=set()
    for row in m['records']:
        keys(row,{'key','raw_descriptions','state','service_id','candidates','grade','evidence'},'mapping record')
        require(isinstance(row['key'],str) and row['key'] not in seen,'Mapping normalization collision');seen.add(row['key'])
        references(row['raw_descriptions'],'raw descriptions');references(row['evidence'],'mapping evidence')
        require(all(normalize(raw)==row['key'] for raw in row['raw_descriptions']),'Mapping key does not match retained raw descriptions')
        require(isinstance(row['candidates'],list) and all(isinstance(v,str) for v in row['candidates']),'Candidate list required')
        require(set(row['candidates'])<=ids,'Unknown candidate service')
        require(row['state'] in {'accepted','unresolved'},'Unknown mapping state')
        require(row['grade'] in {'explicit','elided','unresolved'},'Invalid mapping evidence grade')
        if row['state']=='accepted':
            require(row['service_id'] in ids and row['candidates']==[row['service_id']],'Ambiguous accepted mapping')
            require(row['grade'] in {'explicit','elided'},'Accepted mapping requires supported evidence grade')
        else:require(row['service_id'] is None,'Unresolved mapping cannot force an identity')
    return m


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()



def validate_rules(rules,contract):
    """Check the reviewed cap and exclusion file against the contract it cites."""
    keys(rules,{'hospital','schema_version','review_state','contract_number','contract_term','service_day',
                'exclusion_direction','exclusion_boundary','daily_caps','exclusion_windows','scope',
                'amendment_note','boundary_reading','direction_reading'},'rules',optional={'amendment_note'})
    require(rules['schema_version']=='1','Unsupported rules schema')
    require(rules['review_state']=='accepted','Rules file is unaccepted')
    require(rules['hospital']==contract['hospital'],'Cross-hospital rules file')
    require(rules['contract_number']==contract['contract_number'],'Rules cite a different contract')
    require(rules['contract_term']==list(contract['term']),'Rules cite a different term')
    for field in ('service_day','exclusion_direction','exclusion_boundary'):
        require(rules[field]==contract['semantics'][field],'Rules state a different '+field)
    require(rules['boundary_reading'] and rules['direction_reading'],'Rules must record both readings')
    services={s['id']:s for s in contract['services']}
    term=contract['term']
    for row in rules['daily_caps']:
        keys(row,{'service_id','service_name','maximum_units_per_patient_per_service_day','effective_from',
                  'effective_to','refs'},'daily cap')
        service=services.get(row['service_id'])
        require(service is not None,'Cap names an unknown service')
        require(service['daily_cap']==row['maximum_units_per_patient_per_service_day'],'Cap differs from the contract')
        require(isinstance(row['maximum_units_per_patient_per_service_day'],int)
                and row['maximum_units_per_patient_per_service_day']>0,'Cap must be a positive whole number')
        require(term[0]<=row['effective_from']<=row['effective_to']<=term[1],'Cap effective range leaves the term')
        require(row['refs'],'Cap without a clause citation')
    capped={r['service_id'] for r in rules['daily_caps']}
    require(capped=={s['id'] for s in contract['services'] if s['daily_cap'] is not None},'Cap coverage differs')
    for row in rules['exclusion_windows']:
        keys(row,{'service_id','service_name','anchor_service_id','anchor_service_name','window_days',
                  'effective_from','effective_to','refs'},'exclusion window')
        require(row['service_id'] in services and row['anchor_service_id'] in services,'Exclusion names an unknown service')
        require(isinstance(row['window_days'],int) and row['window_days']>0,'Exclusion window must be positive whole days')
        require(term[0]<=row['effective_from']<=row['effective_to']<=term[1],'Exclusion range leaves the term')
        require(row['refs'],'Exclusion without a clause citation')
    declared={(r['service_id'],r['anchor_service_id'],r['window_days']) for r in rules['exclusion_windows']}
    require(declared=={(r['service'],r['anchor'],r['days']) for r in contract['exclusions']},'Exclusion coverage differs')
    return rules

def load_bundle(project: Path,hospital: str):
    require(hospital in {'H1','H2','H3','H4','H5'},'Unknown hospital')
    path=project/f'contracts/{hospital}.bundle.json'
    require(path.is_file(),f'No accepted {hospital} bundle')
    manifest=json.loads(path.read_text())
    keys(manifest,{'hospital','schema_version','semantics_version','review_state','contract_path','mapping_path','artifacts','source_hashes','review_evidence'},'bundle')
    require(manifest['hospital']==hospital,'Bundle hospital mismatch')
    require(manifest['schema_version']==SCHEMA_VERSION and manifest['semantics_version']==SEMANTICS_VERSION,'Incompatible bundle/interpreter')
    require(manifest['review_state']=='accepted','Bundle is unaccepted')
    references(manifest['review_evidence'],'bundle review evidence')
    for rel,sha in (manifest['artifacts']|manifest['source_hashes']).items():
        require(not Path(rel).is_absolute() and '..' not in Path(rel).parts,'Unsafe artifact path')
        # Review records and their historical hashes are optional replay evidence.
    require(manifest['contract_path'] in manifest['artifacts'] and manifest['mapping_path'] in manifest['artifacts'],'Unbound contract/mapping path')
    contract=validate_contract(json.loads((project/manifest['contract_path']).read_text()))
    mappings=validate_mappings(json.loads((project/manifest['mapping_path']).read_text()),contract)
    require(contract['hospital']==hospital,'Cross-hospital contract')
    require(contract['review_state']=='accepted' and mappings['review_state']=='accepted','Contract or mappings not reviewed')
    # The reviewed abbreviation lexicon travels with the bundle; the matcher
    # needs it, and it carries no price, rate or label.
    rules_path=project/f"contracts/rules_{Path(manifest['contract_path']).stem}.json"
    rules=validate_rules(json.loads(rules_path.read_text()),contract) if rules_path.is_file() else None
    contract['rules']=rules
    lexicon_path=next((rel for rel in manifest['artifacts'] if rel.endswith('lexicon_v1.json')),None)
    lexicon=json.loads((project/lexicon_path).read_text()) if lexicon_path else {}
    require(all(isinstance(k,str) and isinstance(v,list) for k,v in lexicon.items()),'Malformed lexicon')
    return contract,dict(mappings,lexicon=lexicon),manifest
