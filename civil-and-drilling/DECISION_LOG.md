# Decision log

Full register with sources and computed alternatives: `spec/g5_decisions.yaml`, `spec/g4_state.yaml`,
`spec/g3_decisions.yaml`, `spec/open_questions.yaml`. Effects of each alternative: `verification/g5/decision_effects.json`.

## Assumptions

- **The contract governs.** Where the contract and the guidelines differ, the contract governs.
- **What is judged.** For a civil application: `application_total` = the sum of its line amounts; the A3
  adjustment, retention and release are payment fields outside it (45A). For a drilling invoice: `invoice_total` =
  net + 15% VAT, where net includes the DS-900 discount (Cl.36–40).
- **Flag rule.** An invoice is flagged when any of the twelve checks fails, including a procedural breach or an
  omitted payment duty. Flagging only a changed total would give CW 74 / DDS 110 instead of 85 / 125.
- **Rates.** Rates in force on the work or service date, instruments applied in order of issue. A retrospective
  amendment (A3) does not reprice work already valued (31A, 36A); its difference is posted once, to one recipient.
- **Records.** A line whose required record is missing is not payable in that valuation (Cl.46, Cl.37). The civil
  site zone and night working are taken as stated on the line. No unbilled charge is added.
- **Contract Year.** The civil Contract Year restarts on 5 January 2026 (3A). The other reading would reprice 181
  applications that reconcile as billed, and flag 245 civil applications. For drilling, the same reading changes no
  line.

## Policy decisions

The contract text does not settle these two questions. Each was decided as a recorded policy choice, not derived
from the text, and is marked `decided_by: policy` in the register.

- **Q7 C.** Of two admissible charges of one drilling service for one well-day, run, well or loss, the charge on
  the earlier-submitted invoice stands. On the supplied data this concerns 3 pairs, each within one invoice; no flag
  changes.
- **Q6 D.** Whether the day of the A.14.010 measurement is itself excluded for A.14.020 (P19 against Cl.32) is left
  open, and both readings are reported. PA-00801 is flagged at confidence 0.50.

## Ambiguities kept open (every reading evaluated; wrong under one, right under another: flagged at 0.50)

| Question | Readings | Rows at 0.50 |
|---|---|---|
| A3 adjustment recipient | 31A/36A "on or after" the date of issue vs A3 "after"; same-day ties have no tie-breaker | CW 4, DDS 4 |
| Order of same-date civil measurements at a band edge | Cl.30 wording vs execution order | CW 8 |
| Drilling hours (21A; Schedule 8 rows for DD-120, RM-530) | first-hour and minimum readings; Schedule 8 vs Cl.21/Cl.30 | DDS 4 |

## Documents not supplied (unresolved-evidence policy)

- **Which documents.** The call-off, which sets a drilling well's class (Cl.4) and a section's PD-210 nomination
  (Cl.23), is not supplied. Nor is the excavation record that sets the ground class of 649 civil lines on 335
  applications (S4).
- **Rule.** An invoice's own statement of such a fact is never taken as evidence. The invoice is right when some
  admissible value makes it right, and wrong only when none does.
- **Shared facts.** One class governs a whole well (Cl.4). Where a well's invoices can be right only under different
  classes, the class becomes one open reading shared by all of them.

## Export policies (`expected_total_cents`)

- **Missing documents (EXPORT-E).** A row that depends on a missing document is exported under one admissible value
  of it, shared by every invoice of the well or section. An unflagged row is thus exported at its own total, and on a
  flagged row every difference from the bill is named by a finding. The flag never depends on this choice.
- **Absent-document values (EXPORT-D).** The total on the values the evidence establishes without the missing
  document (Standard class, section not nominated, ground G2) is reported as `contract_total`. It is also the export
  for every row that depends on no missing document.
- **Unformed totals (EXPORT-U).** Where a total cannot be formed, the lower bound is exported and the bounds are
  disclosed; no billed or zero value is substituted. No supplied invoice needs this.
- **Confidence scale.**

  | Confidence | When |
  |---|---|
  | 0.95 | every check made and the outcome certain |
  | 0.80 | the outcome rests on a missing document or the Q7 C policy allocation |
  | 0.60 | wrong under every reading, but the total depends on one |
  | 0.50 | the readings disagree on whether the invoice is wrong |
  | 0.30 | the total cannot be formed |
