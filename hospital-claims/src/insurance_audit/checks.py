"""Supported non-price checks and explicit correction/abstention boundaries."""
from .pricing import Uncertain, half_up


def validate_line_facts(line,invoice,service,contract,context):
    if line.quantity<=0:raise Uncertain('unsupported_nonpositive_quantity',{'line_id':line.line_id,'quantity':line.quantity})
    if not contract['term'][0]<=line.service_date<=contract['term'][1]:raise Uncertain('service_date_outside_term',{'line_id':line.line_id,'date':line.service_date})
    if contract['semantics']['service_date_not_after_invoice'] and line.service_date>invoice.invoice_date:raise Uncertain('service_date_after_invoice',{'line_id':line.line_id,'date':line.service_date,'invoice_date':invoice.invoice_date})
    if line.line_id in context.duplicate_line_ids:raise Uncertain('reused_line_identity',line.line_id)
    if service['daily_cap'] is not None and contract['semantics']['service_day']=='seven_am_unobserved':
        interval=context.daily(service['id'],invoice.patient_id,line.service_date)
        if interval['upper'] is None or interval['upper']>service['daily_cap']:
            raise Uncertain('unobserved_service_day_cap_allocation',interval)
    if contract['semantics']['duplicate_policy']=='no_explicit_service_date_prohibition':
        return {'kind':'no_explicit_service_date_prohibition','certain_records':0,'possible_records':0}
    other=context.competitors(service['id'],invoice.patient_id,line.service_date,line.line_id)
    if other['certain_records']:raise Uncertain('duplicate_service_day_allocation',other)
    if other['possible_records']:raise Uncertain('possible_duplicate_service_day',other)
    return other


def adjustment_ratios(service):
    """Every adjustment this service's own clauses could apply, with its name."""
    found=[]
    if service.get('daily_premium'):found.append(('premium',service['daily_premium']['multiplier']))
    weekend=service.get('weekend_multiplier')
    if weekend and tuple(weekend)!=(1,1):found.append(('premium',weekend))
    for table in ('facility_multipliers','tier_multipliers'):
        for ratio in (service.get(table) or {}).values():
            if tuple(ratio)!=(1,1):found.append(('unit_price',ratio))
    for discount in service.get('volume_discounts') or []:found.append(('volume_discount',discount['multiplier']))
    return found


def rate_difference_category(price,billed_rate,service):
    """Name the adjustment that accounts for a wrong unit rate.

    A rate that disagrees with the contract is attributed to the step that
    explains the difference: if applying one of this service's own adjustments
    to the billed rate reaches the contracted rate, the provider left that
    adjustment out; if applying it to the contracted rate reaches the billed
    rate, they applied one the contract does not give. Anything no adjustment
    explains stays a plain unit-price mismatch.
    """
    expected=price['rate']
    if expected==billed_rate:return None
    for stage in price.get('stages',[]):
        if stage['operation']!='bundle':continue
        base=(stage.get('base_rate') or {}).get('cents')
        if base is not None and billed_rate==base and expected!=base:return 'bundle_not_applied'
        if base is not None and expected==base and billed_rate!=base:return 'bundle_incorrectly_applied'
    for name,ratio in adjustment_ratios(service):
        if half_up(billed_rate,ratio)==expected:return name+'_omitted' if name!='unit_price' else 'unit_price_mismatch'
        if half_up(expected,ratio)==billed_rate:
            return name+'_incorrectly_applied' if name!='unit_price' else 'unit_price_mismatch'
    return 'unit_price_mismatch'


def line_result(line,service,price):
    quantity=line.quantity
    categories=[]
    # The unit basis is reported by the findings layer, which checks every
    # identified line whether or not it could be priced. Naming it again here
    # would put the same defect on a row twice.
    # The findings layer already reports this defect as line_total_arithmetic on
    # every physical line, priced or not. Naming it again here would put the same
    # defect on a row twice.
    if service['daily_cap'] is not None and quantity>service['daily_cap']:
        # The contract caps what is billable, but nothing in the record shows how
        # many units were actually delivered below that cap, so the corrected
        # amount is the capped quantity and the row carries a confidence penalty.
        # One rule, applied everywhere: never guess a quantity the data omits.
        quantity=service['daily_cap'];categories.append('daily_cap_exceeded')
    expected=price['rate']*quantity
    if line.unit_price_cents!=price['rate']:
        categories.append(rate_difference_category(price,line.unit_price_cents,service) or 'unit_price_mismatch')
    if line.line_total_cents!=expected:categories.append('line_amount_mismatch')
    if price['excluded'] and (line.unit_price_cents!=0 or line.line_total_cents!=0):categories.append('nonbillable_service')
    return {'expected_total_cents':expected,'expected_unit_rate_cents':price['rate'],'billable_quantity':quantity,
            'error_categories':sorted(set(categories))}
