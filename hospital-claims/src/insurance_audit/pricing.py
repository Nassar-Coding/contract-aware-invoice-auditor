"""Exact finite arithmetic with no binary floating-point monetary operations."""
from datetime import date
from fractions import Fraction
from .resolve import rate_version


class Uncertain(ValueError):
    def __init__(self,reason,detail=None):
        self.reason=reason;self.detail=detail
        super().__init__(reason)


def half_up(amount,ratio):
    numerator,denominator=ratio
    sign=-1 if amount<0 else 1
    return sign*((2*abs(amount)*numerator+denominator)//(2*denominator))


def qualifying_discount(service,quantity):
    values=[[1,1]]+[x['multiplier'] for x in service['volume_discounts'] if quantity>x['exceeds']]
    return min(values,key=lambda r:Fraction(*r))


def interval_discounts(service,interval):
    low,high=interval['lower'],interval['upper']
    if low is None:raise Uncertain('unknown_utilisation_quantity',interval)
    quantities={low,high if high is not None else max([x['exceeds'] for x in service['volume_discounts']]+[0])+1}
    quantities.update(x['exceeds']+1 for x in service['volume_discounts'] if low<=x['exceeds'] and (high is None or x['exceeds']<high))
    return sorted({tuple(qualifying_discount(service,q)) for q in quantities})


def rate_for(line,invoice,service,contract,context):
    if service['support']!='supported':raise Uncertain(service['support'],{'service_id':service['id'],'unit':service['unit']})
    version=rate_version(service,line.service_date)
    if version is None:
        # An explicit added-service date is different from an arbitrary missing rule.
        earliest=min(v['start'] for v in service['versions'])
        if line.service_date<earliest and earliest>contract['term'][0]:
            return {'rate':0,'stages':[{'operation':'not_contracted_before_addition','after':[0],'source':service['versions'][0]['source']}],
                    'context':[],'excluded':True,'version':None,'outcome_invariant_uncertainty':False}
        raise Uncertain('missing_applicable_rate',{'service_id':service['id'],'day':line.service_date})
    details=[];stages=[];amounts={version['cents']};ambiguous=False;qualifications=[]
    for rule in contract['exclusions']:
        if rule['service']!=service['id']:continue
        excluded,detail=context.exclusion(rule,invoice.patient_id,line.service_date);details.append(detail)
        if excluded is True:
            return {'rate':0,'stages':[{'operation':'exclusion','after':[0],'source':rule['source']}],
                    'context':details,'excluded':True,'version':version,'outcome_invariant_uncertainty':False}
        if excluded is None:raise Uncertain('unresolved_exclusion',detail)
    bundle_options=None;bundle_rules=[]
    for bundle_index,bundle in enumerate(contract['bundles']):
        if service['id'] not in [bundle['a'],bundle['b']]:continue
        partner=bundle['b'] if service['id']==bundle['a'] else bundle['a']
        bundled=bundle['a_cents'] if service['id']==bundle['a'] else bundle['b_cents']
        present,detail=context.presence(partner,invoice.patient_id,line.service_date);details.append(detail)
        bundle_rules.append({'rule_index':bundle_index,'source':bundle['source'],
            'service_id':service['id'],'partner_service_id':partner,'substituted_cents':bundled,
            'presence':present,'context_index':len(details)-1})
        options={bundled} if present is True else {version['cents']} if present is False else {version['cents'],bundled}
        if bundle_options is not None and bundle_options!=options:raise Uncertain('multiple_competing_bundles',detail)
        bundle_options=options
    if bundle_options is not None:amounts=bundle_options;ambiguous|=len(amounts)>1
    # Record all applicable/possible clauses, including the evidence selecting
    # them. An absent partner uses the dated standalone version; equal outcomes
    # do not erase uncertainty about which clause was applicable.
    bundle_sources={r['source'] for r in bundle_rules if r['presence'] is not False}
    if not bundle_rules or any(r['presence'] is not True for r in bundle_rules):bundle_sources.add(version['source'])
    stages.append({'operation':'bundle','before':[version['cents']],'after':sorted(amounts),
                   'source':sorted(bundle_sources),'base_rate':version,'rules':bundle_rules})
    def apply(operation,ratios,source):
        nonlocal amounts,ambiguous
        old=sorted(amounts);ratios=sorted(set(tuple(r) for r in ratios));ambiguous|=len(ratios)>1
        amounts={half_up(a,r) for a in amounts for r in ratios}
        stages.append({'operation':operation,'before':old,'ratios':[list(r) for r in ratios],'after':sorted(amounts),'source':source})
    for operation,field,value in [('facility','facility_multipliers',invoice.facility_code),('tier','tier_multipliers',invoice.plan_tier)]:
        table=service[field]
        if not table:ratios=[[1,1]]
        elif value in table:ratios=[table[value]]
        else:raise Uncertain(f'unknown_{operation}_code',{'value':value,'supported':sorted(table)})
        if operation=='facility' and table and len({tuple(r) for r in table.values()})>1 and contract['semantics']['facility_source']=='invoice_projection_reviewed':
            qualifications.append('invoice_facility_projected_to_lines')
        apply(operation,ratios,service['refs'])
    premium=service['daily_premium']
    if premium:
        interval=context.daily(service['id'],invoice.patient_id,line.service_date);details.append(interval)
        low,high=interval['lower'],interval['upper']
        if low is None:raise Uncertain('unknown_daily_quantity',interval)
        ratios=[premium['multiplier']] if low>premium['exceeds'] else [[1,1]] if high is not None and high<=premium['exceeds'] else [[1,1],premium['multiplier']]
        apply('daily_premium',ratios,premium['source'])
    if contract['semantics']['service_day']=='seven_am_unobserved':
        apply('weekend_uplift',[[1,1],service['weekend_multiplier']],service['refs'])
    elif date.fromisoformat(line.service_date).weekday()>=5:apply('weekend_uplift',[service['weekend_multiplier']],service['refs'])
    if service['volume_discounts']:
        interval=context.prior(service['id'],line.service_date,line.line_id,invoice.patient_id);details.append(interval)
        apply('volume_discount',interval_discounts(service,interval),[d['source'] for d in service['volume_discounts']])
    if len(amounts)!=1:raise Uncertain('multiple_supported_rate_outcomes',{'rates':sorted(amounts),'context':details,'stages':stages})
    return {'rate':next(iter(amounts)),'stages':stages,'context':details,'excluded':False,'version':version,'outcome_invariant_uncertainty':ambiguous,'qualifications':qualifications}
