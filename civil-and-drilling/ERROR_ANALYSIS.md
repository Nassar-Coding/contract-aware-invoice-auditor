# Error analysis

## How the approach performs

No labelled answers are supplied. Performance is therefore measured in two ways. First, every rule is checked
against independent readings of the scanned contracts. Second, each outcome says how much it rests on something the
supplied documents do not settle.

| | Civil works (SAR) | Drilling services (USD) |
|---|---|---|
| Invoices | 900 | 1,906 |
| Flagged | 85 (9.4%) | 125 (6.6%) |
| Flagged at confidence 0.95 / 0.80 / 0.60 / 0.50 | 40 / 5 / 27 / 13 | 0 / 0 / 117 / 8 |
| Not flagged at confidence 0.95 / 0.80 | 514 / 301 | 0 / 1,781 |
| Most frequent root categories of flagged invoices | rate 35, duplicate 21, arithmetic 8, evidence 7, timing 6 | rate 57, quantity 21, eligibility 14, timing 9, evidence_mismatch 8 |

Overall, 210 of 2,806 invoices (7.5%) are flagged. The dataset description gives a rough share of wrong
invoices; it was used only as a sanity check, and no rule was changed to move a count.

Agreement with independent readings of the contract, each settled against the scan where it differed:
- Line cases: 903 comparisons, 858 agree; all 45 differences settled.
- Multi-invoice histories: 48 readings of 24 histories, 28 agree in full; all 59 line-level differences settled.
- Invoices: 50 readings of 25 invoices, 48 agree on flag and expected total. Of the other two, one reading misapplied
  the survey tolerance clause (33A) and one took the other side of an open reading.

## Failure types

**1. Facts held in documents that were not supplied.**
- Which facts:
  - a drilling well's class (Cl.4) and a section's performance nomination (Cl.23) are set by the call-off;
  - a civil line's ground class, where no excavation record classifies it (S4), is set by the Engineer's record.
- None of these documents is supplied, and an invoice's own statement of such a fact is not treated as evidence.
  - An invoice is flagged only if it is wrong under every admissible value.
  - It is exported under one value per well and section, shared by all its invoices.
- Scale:
  - **Drilling:** 1,906 invoices depend on the class, 638 also on a nomination.
  - **Civil:** 335 invoices depend on an unrecorded ground.
  - Flagged rows whose total depends on such a fact: CW 27, DDS 117 (confidence 0.60).
- *Example:* MDS-00753 is right as billed (49,892.39) only if the call-off states HPHT. Under Standard it would be
  49,113.13, and under Extended Reach 49,532.73.
- *Measured by:* evaluating every admissible value (`admissible_totals` in `verification/g5/outcomes.jsonl`).
- *Uncertain:* if a call-off states a class other than the one billed, an unflagged row is a missed error (confidence
  0.80), and a flagged row's total is wrong.

**2. Contract readings that stay open.**
- Where the text supports two readings and no clause ranks them, both are evaluated. An invoice wrong under one and
  right under the other is flagged at 0.50.
- **Civil (13 rows):**
  - the A3 difference recipient, "on or after" (31A) against "after" (A3): 4;
  - the order of same-date measurements at a band edge: 8;
  - the A.14.020 measurement day (Q6): 1.
- **Drilling (8 rows):**
  - the A3 recipient: 4;
  - the hour readings of 21A and Schedule 8: 4.
- *Example:* PA-00443 is the A3 recipient only under A3's "after". Its adjustment of 0.00 is an omission under that
  reading and correct under 31A.
- *Measured by:* rows with both `wrong_under` and `right_under`.
- *Uncertain:* each is a false positive under the readings where it is right.

**3. Expected total and category where a defect has several causes or values.**
- A flagged row's total and categories come from one admissible scenario; every line that differs from its bill is
  named by a finding on that line.
- Two sensitivities were measured by comparing rule versions over the whole population:
  - making the exported scenario consistent with the reported findings and with the well changed 95 flagged totals
    (CW 23, DDS 72);
  - naming each differing dimension (quantity, rate, arithmetic) moved 2 drilling rows from "rate" to "quantity".
- *Example:* MDS-00042 is flagged "rate", billed 213,194.29, expected 212,401.29 (Standard, section nominated). On the
  absent-document values alone (section not nominated) it would be 156,082.74; that value is disclosed as
  `contract_total`.
- *Uncertain:* for these rows the exported figure is an admissible value, not one the documents establish. The
  category of a multi-defect row follows a fixed order of checks.

**4. Incomplete or undated data on invoices of the same format.**
- Independent review built counterexamples the supplied data does not contain. Each was corrected and kept as a
  regression test with a control:
  - PD-210 charges without depths or with unestablished admissibility;
  - daily reports without depths;
  - undated or unvalued lines in the A3 and retention accounts;
  - a divided band line showing another band's rate.
- None occurs in the supplied population: no unformed total, no check left unestablished, no unresolved evidence item.
- *Example:* a PD-210 charge billed without depths on an earlier invoice is now a possible charge of the same metres
  as a later charge for that well-day; the later invoice is flagged at 0.50.
- *Uncertain:* combinations no test covers.

**Independent sample review.** The population review was completed:
- rule exposure, categories and outliers were reviewed;
- every residual cluster is explained, and none lies on an unflagged invoice;
- no unsupported pass or silent fallback was found.

An independent false-negative/false-positive reader sample was drawn (30 invoices, seed 6606) but never read; it is
kept with the original development repository (see [Provenance](../README.md#provenance)). The false-negative rate is therefore not measured by an
independent reading.
