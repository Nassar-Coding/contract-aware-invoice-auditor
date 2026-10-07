# Evaluation

Contract records and service mappings, reviewed and frozen, drive a deterministic replay with no model calls, API keys
or GPU. Every figure below is measured against Hospital 1's labels, the only labels the dataset provides, with
`tools/score_development.py` (`make hospital-eval` from the repository root).

## Headline

Hospital 1's 913 invoices are split by patient-connected group into a development partition, used for all tuning, and a
check partition kept out of tuning.

| | Development | Check |
|---|---|---|
| Erroneous invoices | 42 | 16 |
| Clean invoices | 580 | 275 |
| True positives | **42** | **16** |
| False negatives | **0** | **0** |
| False positives | **0** | **0** |
| Corrected amount exact on true positives | 0.929 | 0.875 |
| Expected calibration error | 0.0332 | 0.0293 |

- **The development column is in-sample.** It is measured on the current code, and every threshold and rule was tuned
  on that partition.
- **The check column is regression evidence.** It was first measured on the code as it stood before the last change
  described under [Settling an ambiguous clause](#settling-an-ambiguous-clause-by-course-of-dealing), and re-scored on
  the final code with identical results. An earlier version of the engine had already been evaluated on it, so it is
  not an untouched holdout. To re-score it: `python tools/score_development.py --export-current-h1 runs/h1_check.csv
  --partition check --allow-check-partition --output runs/score_check.json`.
- **Rows emitted:** 446 of 622 development invoices and 217 of 291 check invoices; the rest are withheld with a recorded
  reason.
- **Decoy proxies:** none of the 184 frozen decoy proxies is flagged, and no single pattern among them produces a flag.
  These are clean development invoices that carry the surface pattern of an error; 122 of them receive an opinion.

Calibration is measured on the event "correct flag *and* exact expected amount", over emitted rows.

## Per-category detection

The share of invoices carrying each labelled category that are flagged. This measures detection per invoice, not
whether the predicted category label matches.

| Category | Development | Check |
|---|---|---|
| bundle_not_applied | 3/3 | 2/2 |
| contract_number_mismatch | 4/4 | 1/1 |
| cross_invoice_duplicate | 2/2 | 2/2 |
| daily_cap_exceeded | 2/2 | 2/2 |
| duplicate_invoice_id | 4/4 | 1/1 |
| exclusion_window_violation | 3/3 | 1/1 |
| invoice_total_mismatch | 5/5 | 1/1 |
| line_total_arithmetic | 3/3 | 3/3 |
| malformed_service_date | 4/4 | 2/2 |
| premium_incorrectly_applied | 5/5 | 1/1 |
| premium_omitted | 3/3 | — |
| service_date_after_invoice_date | 5/5 | — |
| service_date_out_of_window | 4/4 | 1/1 |
| unit_price_mismatch | 7/7 | 3/3 |
| unknown_service | 9/9 | 3/3 |
| volume_discount_incorrectly_applied | 3/3 | 1/1 |
| volume_discount_omitted | 3/3 | 1/1 |
| wrong_unit_basis | 6/6 | 5/5 |

## How the engine improved

Development detection over successive iterations, each measured on the same partition before the next began:

| Iteration | What changed | TP / FN / FP |
|---|---|---|
| First version | contract records, mappings, pricing; an invoice was withheld whenever any fact was unresolved | 4 / 38 / 0 |
| Structural layer | checks that need no service identity were moved out of the mapping path, and evidence that is itself the error is always reported; one unobservable Hospital 2 clause had been suppressing every other check on 1,110 of its 1,125 invoices | 26 / 16 / 0 |
| Matcher | four-state matcher; a confident no-match is reported as `unknown_service`; unit basis checked | 38 / 4 / 0 |
| Contract rules | reviewed daily caps and exclusion windows | 40 / 2 / 0 |
| Pricing and confidence | pricing order and rounding, amount policy, ambiguity threshold, confidence tiers | 40 / 2 / 0 |
| Established differences | a line that resolved to one service and one rate is reported even when another line on the invoice is unresolved | **42 / 0 / 0** |

The last two development misses were closed by the established-difference change. Removing that one line of
`audit.py` returns the result to 40 / 2 / 0.

## Settling an ambiguous clause by course of dealing

Several agreements admit more than one reading of a pricing stage. Recording every supported outcome and withholding
the invoice is right when the readings genuinely disagree about a hospital's billing, and wasteful when they do not.

Each admissible reading is therefore tested against the hospital's **own supported lines**. A reading is adopted only
when all four conditions hold:

- the contract text admits it;
- the stage covers at least 50 lines;
- it accounts for at least **98%** of every relevant line;
- every other reading accounts for strictly less.

The denominator is every relevant line, not the subset a reading chooses to price. A reading that leaves a line
ambiguous has not accounted for it either. That distinction decides the test: scored only on the lines it settles,
Hospital 2's unobserved 07:00 envelope reaches 99.7% by declining to price 2,912 of 5,094 lines.

This uses the billed **population** to choose between readings of a clause, the way a course of dealing settles an
ambiguous term. A line's billed price is never used as evidence of which service it names: the matcher never sees a
price, and its own tests assert that.

| Hospital | Stage | Reading | Accounted for | Adopted |
|---|---|---|---|---|
| H1 | — | no ambiguous stage declared | — | — |
| **H2** | weekend uplift, daily premium, bundle presence, cap allocation | **calendar Service Day** (clause 2.2) | **5,004 / 5,094 = 98.23%** | **yes** |
| H2 | " | 07:00 envelope (clause 2.2) | 2,175 / 5,094 = 42.70% | no |
| H3 | — | no ambiguous stage declared | — | — |
| H4 | cumulative volume discount | utilisation across all patients | 304 / 322 = 94.41% | no — below 98% |
| H4 | " | same patient only (clause silent) | 82 / 322 = 25.47% | no |
| H5 | facility multiplier | no facility multiplier | 12,542 / 12,900 = 97.22% | no — see the caveat |
| H5 | " | invoice facility projected onto lines | 12,542 / 12,900 = 97.22% | no — see the caveat |

Only Hospital 2's Service Day clears the bar. It settles four Hospital 2 stages at once and, with the
established-difference change above, cut Hospital 2's withheld invoices from 992 to 451.

**Hospital 5's row does not mean what it appears to mean.** Its two readings score identically in every cell because
`facility_source` only sets a qualification label in the pricing engine: the multiplier keyed by the invoice's facility
code is applied under both readings alike. The "no facility multiplier" reading was therefore never exercised, so the
comparison is vacuous. The outcome is unaffected, because the stage is not adopted either way. This is the one known
defect, and fixing it is the first item under [Next steps](#next-steps).

## Systematic failure modes

**1. A description that names more than one contracted service.**
- This is the largest source of withheld invoices. Across Hospitals 2–5, 1,053 lines name more than one contracted
  service, and on 925 of them the candidates disagree on whether the line is billed correctly.
- *Example:* a Hospital 2 line reading `admin ophth anaes`. The agreement contracts both *Intermittent* and
  *Postoperative* Ophthalmic Anaesthesia Administration at different rates, and the abbreviation drops the one word that
  would separate them.
- Each candidate is priced. Where the readings agree the verdict is reported; where they disagree, the invoice is
  withheld.
- When the threshold was fitted, 143 development invoices sat at an error fraction of exactly 0.5 and 142 of them were
  clean, which is why flagging a split reading was measured and rejected. On the final engine, 159 development
  invoices have such a line: 149 are clean and withheld, and the 10 erroneous ones are already flagged by other
  findings.

*A tempting shortcut, deliberately not taken.* Every one of those 925 lines has **exactly one** candidate whose
contracted rate equals the billed rate. Choosing it would be using price as mapping evidence, and on a line whose rate
is *wrong* it would select the service that makes the error disappear. The one signal that would close this gap is the
one that would silently hide the errors being looked for.

**2. A rate still ambiguous after the consistency test.** Hospital 4's volume discount stays unresolved because its
better reading accounts for 94.4%, below the 98% bar. Hospital 5's facility multiplier stays unresolved for the weaker
reason set out above. These invoices are withheld rather than decided on a guess.

**3. An amount the record cannot determine.** Where a billed quantity exceeds a daily cap, the contract fixes what is
billable, but nothing shows how many units were actually delivered below the cap. The corrected amount is the capped
quantity, the row is qualified and its confidence capped, and the quantity is never guessed.

**4. A composite dimension the record does not observe.** 339 invoices are withheld because a rate depends on a dimension
the data does not carry. Telemetry Monitoring is the case in every hospital: it is contracted **per hour per item**, and
each line carries a single quantity, so hours and items cannot be separated. No reading of the contract resolves this;
it is an evidence limit, not a missing check.

Across Hospitals 2–5, 1,405 invoices are withheld:

| Reason | Invoices |
|---|---|
| ambiguous service mapping | 657 |
| composite dimension unobserved | 339 |
| rate still ambiguous | 299 |
| unresolved mapping | 62 |
| same-day allocation | 39 |
| exclusion | 9 |

## Readings applied to ambiguous or silent clauses

Each reading is applied uniformly and recorded in the traces.

| Reading | Where | Basis |
|---|---|---|
| Volume discounts are **line-level** everywhere | all five | H1 2.4, H2 2.7 and 3.5, H3 6.1, H4 8.3–8.5, H5 8: the discount applies to *a line item* whose utilisation *prior to that line* exceeds the threshold, and not to the line that crosses it. |
| A reused invoice identifier's lines are **attributable** | all five | A line identifier embeds the record number that raised it. Used only where each group's billed total matches one header exactly: 29 of 31 reused identifiers. This is accounting, not identification, and two tests assert no mapping moves under price perturbation. |
| The canonical record of a reused identifier is the **latest-dated** one | all five | Every labelled development case agrees, for both the billed and the expected total. |
| An exclusion window **includes** its boundary day | all five | "Not billable within N days" covers a Service Date exactly N days away. |
| Exclusion direction, where the clause is silent, uses **every reading** | H2, H3, H5 | Only an anchor the readings agree on is reported. |
| **Service Day = the calendar date** | H2 | Adopted by the consistency test above, at 98.23% against 42.70%. |
| The **submission deadline is unobservable**, and blocks nothing | H2 | Article XIII conditions effectiveness on a submission date the data never records. Recorded as unresolved; it suppresses no other check. |
| A **cross-invoice repeat is reported even where no clause forbids it** | H2 | H1 11.4, H3 10.3, H4 11.3 and H5 10.3 forbid billing a service twice for one patient and date. H2's agreement is silent, but silence is not permission. |
| An **unknown service has no contracted rate** | all five | Its billed amount stands; only the naming is reported. |
| Ambiguity is flagged only on a **strict majority** of readings | all five | When the threshold was fitted, every lower threshold, swept on development, added 142 false positives to gain one detection; on the final engine it would add 149 and gain none. |
| Caps and exclusion windows are **unamended** | H3 | Amendment No. 1 clause A1.4.1 states they are unchanged. |
| H4's volume utilisation is **patient-scoped where unstated** | H4 | A different patient's history leaves the rate undetermined rather than assumed. |

## Robustness

- **Survival** (`tools/generalize.py`). Every invoice is re-identified, about 70% of patients are kept with their full
  histories, rows are shuffled and 30% of descriptions are perturbed. The unchanged pipeline must run end to end and
  produce schema-valid output for all five hospitals.
- **Recall under perturbation** (`tools/generalize_recall.py`). 30% of Hospital 1's line descriptions (3,424 lines) are
  perturbed and recall is scored on the development partition; every identifier, quantity, date and amount is left untouched. Recall drops from 1.0 to 0.905 (−9.5%
  relative), with **no new false positive**.

An early version of the matcher failed the second test badly. A typo made a token unexplained, and an unexplained
token was indistinguishable from an absent service. The rebuilt matcher scores coverage of the *billed* tokens and
guards drift three ways:

- a contract-vocabulary check;
- a canonical token multiset;
- a single-character repair rule.

## Next steps

1. **Make Hospital 5's facility comparison real.** The `none` reading must actually suppress the multiplier before the
   consistency test can say anything about Hospital 5.
2. **Resolve the 925 disagreeing tie lines without price evidence.** Move the consistency idea from pricing to mapping: where an
   abbreviation is ambiguous, ask whether the hospital's *unabbreviated* lines name only one of the candidates, and
   adopt it on the same decisive-margin test.
3. **Hospital 4's patient scope: enumerate more readings.** The bar is a share, so additional lines cannot lift 94.4%.
   The clause also admits aggregation reset per contract year, and aggregation within an admission.

Each is measurable on development before it ships, under the standing rule that anything adding a false positive on
clean development invoices or on the frozen decoy proxies is reverted.
