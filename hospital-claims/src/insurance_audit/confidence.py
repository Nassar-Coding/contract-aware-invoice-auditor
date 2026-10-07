"""Frozen whole-row confidence policy; no labels accessed during replay."""
import math


def choose(row,policy):
    """Confidence for one emitted row, from its evidence tier.

    Hospital 1's values are fitted to its development accuracy. The other four
    have no labels at all, so each takes the fitted value reduced by a tenth: a
    judgment that they are no better understood than Hospital 1, never a claim
    that the measured accuracy carries across.
    """
    if row.get('unresolved') or row.get('expected_total_cents') is None:return None,'unresolved'
    tier=row.get('evidence_tier')
    group=policy['tiers'].get(tier)
    if group is None:raise ValueError('Unknown confidence evidence tier')
    value=group['confidence']
    if row['hospital']=='H1':
        label=('h1_fitted:' if group['n']>=policy['minimum_empirical_n'] else 'h1_sparse:')+tier
    else:
        numerator,denominator=policy['target_discount']
        value=math.floor(value*numerator/denominator*100)/100
        label='novel_reviewed:'+tier
    caps=policy['qualification_caps']
    if row.get('outcome_invariant_uncertainty'):
        value=min(value,caps['outcome_invariant']);label+=':invariant'
    for qualification in row.get('interpretation_qualifications') or []:
        value=min(value,caps.get(qualification,caps['interpretation']))
        label+=':qualified'
    if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or not 0<=value<=1:
        raise ValueError('Confidence must be finite and in [0,1]')
    return round(value,4),label


def assign_confidence(result,policy):
    if policy.get('version')!='2':raise ValueError('Unsupported confidence policy')
    for row in result['opinions']:
        value,tier=choose(row,policy)
        if value is None:raise ValueError('Unresolved row leaked into complete opinions')
        row.update(confidence=value,confidence_tier=tier,
                   confidence_state='development_supported_or_policy_judgment; not target-calibrated')
    by_id={r['invoice_id']:r for r in result['opinions']}
    for trace in result['traces']:
        if trace['status']=='supported':trace['opinion']=by_id[trace['invoice_id']]
