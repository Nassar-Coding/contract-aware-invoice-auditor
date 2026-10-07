"""Evidence-derived working reports; no fitting or alteration of decisions."""
from collections import Counter
import json
import warnings
from pathlib import Path
from .batch import current_run
from .io import write_json
from .schema import digest
from .results import TARGETS, verify_results


def pct(value):return 'undefined' if value is None else f'{value*100:.2f}%'
def score(value):return 'undefined' if value is None else f'{value:.4f}'


def render_reports(root,evaluations):
    root=Path(root);h1,_=current_run(root,['H1']);targets,_=current_run(root,TARGETS)
    metrics={group:{k:v for k,v in report.items() if k not in {'details','provenance'}} for group,report in evaluations.items()}
    write_json(root/'reports/metrics.json',metrics)
    workload={}
    for hospital,directory in [('H1',h1)]+[(h,targets) for h in TARGETS]:
        result=json.loads((directory/f'{hospital}.json').read_text())
        reason_counts=Counter(reason for row in result['abstentions'] for reason in {v['reason'] for v in row['reasons']})
        maps=json.loads((root/f'mappings/hospital_{hospital[1:]}.json').read_text())['records']
        workload[hospital]={**result['accounting'],
            'flagged_opinions':sum(r['flagged'] for r in result['opinions']),
            'correct_opinions':sum(r['flagged']==0 for r in result['opinions']),
            'coverage':len(result['opinions'])/result['accounting']['unique_invoice_ids'] if result['accounting']['unique_invoice_ids'] else 0,
            'abstention_invoice_counts_by_reason':dict(sorted(reason_counts.items())),
            'confidence_tiers':dict(sorted(Counter(r['confidence_tier'] for r in result['opinions']).items())),
            'qualified_opinions':sum(bool(r['interpretation_qualifications']) for r in result['opinions']),
            'distinct_mapping_keys':len(maps),'unresolved_mapping_keys':sum(r['state']=='unresolved' for r in maps),
            'review_scope':'all source services and all observed distinct keys examined; unresolved keys retained',
            'reason_count_note':'An invoice may have several reasons; cause counts do not sum to omissions.'}
    write_json(root/'reports/workload.json',workload)
    if 'development' in evaluations:
        try:
            render_evaluation(root,evaluations,metrics,workload,h1)
        except Exception as exc:
            warnings.warn(f'Optional evaluation report failed: {exc}',RuntimeWarning)
            (root/'reports/evaluation_report.md').write_text('# Evaluation report unavailable\n\nOptional evaluation rendering failed; prediction outputs remain validated.\n')
    else:
        (root/'reports/evaluation_report.md').write_text('# Evaluation not requested\n\nPrediction replay completed without opening labels or the partition manifest.\nSee workload.json for current hospital coverage.\n')
    manifest=verify_results(root)
    source_files=list((root/'src').rglob('*.py'))+list((root/'tests').rglob('*.py'))+list((root/'tests/fixtures').glob('*.json'))
    source_files+=[root/'evaluation/confidence_policy.json',root/'.python-version',root/'requirements.txt']
    evidence_files=[root/'audit_results.csv',root/'reports/metrics.json',root/'reports/workload.json',root/'reports/evaluation_report.md']
    release={'target_release_id':manifest['release_id'],
        'run_ids':{'H1':current_run(root,['H1'])[1]['run_id'],'targets':current_run(root,TARGETS)[1]['run_id']},
        'prediction_inputs':{'H1':current_run(root,['H1'])[1]['identity'],'targets':current_run(root,TARGETS)[1]['identity']},
        'evaluation_testing_and_documentation_inputs':{str(p.relative_to(root)):digest(p) for p in sorted(set(source_files)) if p.is_file()},
        'stable_outputs':{str(p.relative_to(root)):digest(p) for p in evidence_files},
        'scope':'Deterministic invoice audit, evaluation and reproducibility evidence for the supplied snapshot'}
    write_json(root/'reports/release_manifest.json',release)
    return workload


def render_evaluation(root,evaluations,metrics,workload,h1):
    lines=['# Hospital 1 evaluation and implementation limitations','',
        'These are executed results from the current deterministic pipeline. Hospital 1 is development data. '
        'The patient-group check was first opened after the mapping and confidence freeze; the current check '
        'is a regression after that exposure, not a new holdout. Full H1 includes development. '
        'No target labels or target accuracy estimates exist.','',
        '| Partition | All IDs | Opinions | Coverage | Flag + exact cents on opinions | Error precision | Error recall, all IDs | F1 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for group,m in metrics.items():
        b=m['flag_metrics_all']
        lines.append(f"| {group} | {m['population']} | {m['covered']} | {pct(m['coverage'])} | {m['whole_row_success_count']}/{m['covered']} | {pct(b['precision'])} | {pct(b['recall'])} | {score(b['f1'])} |")
    lines+=['','Abstentions are excluded from conditional accuracy and counted as missed positives in population recall. '
        'They are never correct negatives. The joint event is a correct binary flag and exact corrected cents; '
        'billed cents, identity, and complete line provenance are separately validated. Free-text category '
        'correctness is measured by a disclosed many-to-one family crosswalk, not folded into that joint event. '
        'Undefined denominators remain undefined. High conditional accuracy with low recall is a substantial limitation.','',
        '## Per-category development performance','',
        'Family precision/recall use the explicit crosswalk in `src/insurance_audit/evaluation.py`. '
        'Broad pricing diagnostics do not establish which particular premium or discount caused a rate mismatch.','',
        '| Family | Positive labels | TP | FP | Misses incl. abstentions | Precision | Recall | F1 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for family,b in metrics['development']['per_category_family'].items():
        lines.append(f"| {family} | {b['support']} | {b['tp']} | {b['fp']} | {b['fn_including_abstentions']} | {pct(b['precision'])} | {pct(b['recall'])} | {score(b['f1'])} |")
    lines+=['','Original-label detection is also reported below. This table measures detection on invoices carrying '
        'each label, not category-specific precision. An invoice can carry multiple labels.','',
        '| Original label | Support | Covered | Flagged | Exact amount | Joint successes | Detection recall |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for label,b in metrics['development']['per_original_label_detection'].items():
        lines.append(f"| {label} | {b['support']} | {b['covered']} | {b['flagged']} | {b['amount_exact']} | {b['whole_row_success']} | {pct(b['detection_recall_all'])} |")
    lines+=['','## Confidence support','',
        'Confidence tiers are frozen in `evaluation/confidence_policy.json` (see EVALUATION.md); the unlabelled '
        'hospitals receive the tier value discounted by the policy. These are judgments, not demonstrated '
        'probability calibration outside Hospital 1. Missing necessary facts cause whole-invoice omission.','',
        '| Development tier | n | Joint successes | Mean confidence | Brier score |',
        '|---|---:|---:|---:|---:|']
    for tier,b in metrics['development']['confidence_groups'].items():
        lines.append(f"| {tier} | {b['n']} | {b['successes']} | {score(b['mean_confidence'])} | {score(b['brier_score'])} |")
    result=json.loads((h1/'H1.json').read_text());development=evaluations['development']
    details={d['invoice_id']:d for d in development['details']}
    def example(reasons):
        candidates=[a for a in result['abstentions'] if a['invoice_id'] in details and any(r['reason'] in reasons for r in a['reasons'])]
        candidates.sort(key=lambda a:(not details[a['invoice_id']]['truth']['flagged'],a['invoice_id']))
        if not candidates:return 'No matching development example was observed; no example is fabricated.'
        a=candidates[0];why=next(r['reason'] for r in a['reasons'] if r['reason'] in reasons)
        return f"Example `{a['invoice_id']}` was withheld for `{why}`; its development truth is flagged={details[a['invoice_id']]['truth']['flagged']}. See that invoice's current trace."
    lines+=['','## Withheld examples from this run','',
        '- Insufficient description evidence: '+example({'unresolved_service_mapping','ambiguous_service_mapping'}),'',
        '- Damaged source identity or dates: '+example({'conflicting_reused_invoice_id','quarantined_required_source_record'}),'',
        '- Unobservable rule context: '+example({'multiple_supported_rate_outcomes','unresolved_exclusion','duplicate_service_day_allocation'}),'',
        'Systematic failure modes, with worked examples, are described in EVALUATION.md.','',
        '## Target coverage and unresolved workload','',
        '| Hospital | Unique IDs | Opinions | Flagged | Withheld | Coverage | Unresolved mapping keys |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for h in TARGETS:
        w=workload[h];lines.append(f"| {h} | {w['unique_invoice_ids']} | {w['opinions']} | {w['flagged_opinions']} | {w['abstentions']} | {pct(w['coverage'])} | {w['unresolved_mapping_keys']} |")
    lines+=['','Every raw CSV occurrence is accounted for. All five source contracts and all observed mapping keys '
        'were examined; complete invoice coverage remains limited by evidence. `workload.json` records overlapping '
        'omission causes and review items. The system audits the supplied snapshot, not an unseen complete '
        'claims feed. Late or corrected records require a new run and context recomputation.','',
        'Replay uses retained reviewed schemas/mappings and standard-library Python. Repeating fresh LLM extraction '
        'is a different activity and is not claimed bit-reproducible.']
    (root/'reports/evaluation_report.md').write_text('\n'.join(lines)+'\n')
