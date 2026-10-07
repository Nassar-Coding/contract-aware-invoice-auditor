# Civil works and drilling services auditor

*Part of [Contract-Aware Invoice Auditor](../README.md). The [hospital claims auditor](../hospital-claims/) applies the
same method to five hospital contracts with labelled examples.*

Two contractors bill monthly against two long-running contracts:

- a civil engineering term subcontract, **CW-2025-0417-CIV**, billed in SAR;
- a directional drilling services contract, **DDS-2025-118**, billed in USD.

Both contracts were amended while they ran, and both exist **only as scanned PDFs with no text layer**: 43 and 42
skewed, unevenly lit pages. Invoices are evidenced by free-text paperwork, 2,169 civil site records and 8,151 daily
drilling reports, which rarely quote an item code. There are no labelled answers. This auditor checks every invoice
under both contracts against twelve audit checks. For each one it states whether the invoice is wrong, what is wrong,
the total the contract supports, and a confidence.

## Results

All 2,806 invoices receive an outcome, and **210 (7.5%) are flagged**:

| | Civil works (SAR) | Drilling services (USD) |
|---|---|---|
| Invoices / lines | 900 / 7,746 | 1,906 / 91,244 |
| Flagged | **85 (9.4%)** | **125 (6.6%)** |
| Flagged at confidence 0.95 / 0.80 / 0.60 / 0.50 | 40 / 5 / 27 / 13 | 0 / 0 / 117 / 8 |
| Not flagged at confidence 0.95 / 0.80 | 514 / 301 | 0 / 1,781 |
| Most frequent root categories of flags | rate 35, duplicate 21, arithmetic 8, evidence 7, timing 6 | rate 57, quantity 21, eligibility 14, timing 9, evidence mismatch 8 |

The dataset description gives a rough share of wrong invoices. It was used only as a sanity check, and no rule was
changed to move a count.

Without labels, correctness was tested against **independent readings**. Each was produced by a separate LLM reading
pass that was given written instructions and the source materials, but not the specification or the engine's output.
The instructions are kept with the original development repository (see [Provenance](../README.md#provenance)). Every disagreement was settled against the scan and
recorded:

| Check | Agreement |
|---|---|
| Contract tables: blind transcription vs. the transcribed terms | 544 / 547 numeric cells, 1,025 / 1,047 text cells; a second visual pass of every active numeric table against the scan found 0 numeric discrepancies |
| Evidence parsing: blind annotation of a seeded record sample | 646 / 646 civil fields, 1,029 / 1,029 drilling-report fields |
| Line pricing: expected results for 192 reference cases | 858 / 903 comparisons; all 45 differences settled |
| Cross-invoice histories | 48 readings of 24 histories, 28 in full agreement; all 59 line-level differences settled |
| Whole invoices | 50 readings of 25 invoices; 48 agree on flag and expected total |

In the two whole-invoice disagreements, one reading misapplied a survey-tolerance clause and the other took the
opposite side of a reading the decision log keeps open.

The readers are instances of the same model family, reading the same scans. Their isolation was instructed, not
enforced, and the whole-invoice readers applied the audit's adopted decisions. Agreement therefore shows independent
reproduction, not correctness: a misreading shared by readers and engine would pass. The 25 whole invoices were 13
drawn at random and 12 chosen for anomalies in the raw data. The population review found that every line valued
differently from its bill is explained by a finding and lies on a flagged invoice. It found no unsupported pass and no
silent fallback.

## How it works

| Step | Code | What it does |
|---|---|---|
| Inputs (G0) | `tools/snapshot.py`, `source/` | verifies all 10,330 input files against the pinned commit by SHA-256 and git blob, and each scanned page by the hash of its decoded pixels |
| Contract terms (G1) | `spec/terms_*.yaml`, `spec/instruments.yaml`, `audit/terms.py` | every rate table, schedule and amendment transcribed from the scans, with page and provision; each cell checked by an independent second reading; `tools/verify_spec.py` fails if a table that feeds pricing is not marked verified |
| Evidence (G2) | `audit/build.py`, `claims.py`, `records_cw.py`, `records_dds.py`, `events.py`, `links.py` | invoices, civil site records and daily drilling reports parsed and linked; anything unreadable or conflicting is queued, never guessed |
| Line pricing and checks (G3) | `audit/g3_cw.py`, `g3_dds.py` | each line on its own: identity, term, window, record, quantity, unit, rate in force (build-up and rounding as the contract states), arithmetic; a traced calculation per line, replayed independently by `tools/verify_g3.py` |
| Cross-invoice state (G4) | `audit/g4_cw.py`, `g4_dds.py` | quantity bands and annual footage, daily limits, duplicates and once-only charges, the retrospective A3 difference and its recipient, retention and its release |
| Outcomes (G5) | `audit/g5_outcomes.py`, `g5_run.py` | one outcome per invoice: flag, categories, expected total (civil: sum of lines; drilling: services, the DS-900 discount, 15% VAT) and confidence |
| Review and output (G6–G7) | `tools/g6_review.py`, `tools/check_results.py` | population review of rule exposure, residuals and outliers; independent check of `audit_results.csv` against the dataset's output template and the input invoices |

Every reading of the contract that decides an outcome is recorded with its source and its alternatives in
`spec/g3_decisions.yaml`, `spec/g4_state.yaml` and `spec/g5_decisions.yaml`. Open questions are in
`spec/open_questions.yaml`. Where a question stays open, every admissible reading is evaluated: an invoice wrong under
some readings and right under others is flagged at confidence 0.50.

Some facts belong to documents that were never supplied: a well's class and a section's nomination are set by the
call-off, and a civil line's ground class, where no excavation record classifies it, is set by the Engineer's record.
For these, the invoice's own statement is never taken as the fact. The invoice is wrong only if no admissible value
makes it right. The full policy is in the [decision log](DECISION_LOG.md).

## Run it

Python 3.12 (developed on 3.11) with the pinned dependencies in `requirements.txt`. The inputs live in their own
repository and are fetched at a pinned commit. From the repository root:

```bash
make setup
make civil           # fetch the inputs, then ./reproduce.sh (about 20 minutes)
make civil-verify    # tools/check_g5.sh: every gate check, the reader comparisons and the full test suite
```

Or by hand, from this directory:

```bash
git clone https://github.com/majedzahrani3/invoice-auditing-level-2 ../.inputs/civil-and-drilling
git -C ../.inputs/civil-and-drilling checkout aef4924dc32506b4587de8b788b5a947e6beffec
pip install -r requirements.txt
./reproduce.sh
PYTHON=python tools/check_g5.sh
```

The inputs are read from `../.inputs/civil-and-drilling`, or from `$INVOICE_SNAPSHOT` when it is set.

`reproduce.sh` runs these steps in order:

1. verifies the inputs file by file against the pinned commit;
2. rebuilds the evidence, prices and checks every line, applies the cross-invoice state and forms one outcome per
   invoice;
3. writes `audit_results.csv`;
4. checks it independently against the output template and the input invoices;
5. rebuilds the population review (`verification/g6/review.json`);
6. confirms with `git diff` that every regenerated file is byte-identical to the committed one.

There is no network access and no randomness outside fixed seeds.

`check_g5.sh` runs every check in the chain:

- the input and specification checks;
- the evidence checks;
- the line-level, cross-invoice and outcome gates (`tools/verify_g3.py`, `verify_g4.py`, `verify_g5.py`);
- the comparisons with the independent readings;
- the full test suite.

Every exit check of G3–G5 (X1–X8, Y1–Y9, Z1–Z9) has a negative-control test showing that it can fail. Each defect
found by independent review is run through the corrected code and, as a control, through the code that had the defect,
kept in `tests/fixtures/prior_code/` (code as committed; a few comments reworded). The finding IDs in code and tests
(F1–F4, B1–B2, D8 and FD01–FD08 for G3; A-n, B-n, C-n, G4-B0n and G5-B0n for G4 and G5) label those review findings;
their regression tests are `tests/test_g3_review_*.py`, `tests/test_g4g5_regressions.py` and
`tests/test_g4g5_review_findings.py`.

X1 proves that each G3 review packet was committed before the readers' expected results. Y1 and Z5 also prove that,
for G4 and G5, those results were committed before the engine they were compared with. The order comes from
the original development repository (see [Provenance](../README.md#provenance)) and is recorded in `verification/commit_order.json`;
`tools/commit_order.py check --source <clone>` re-derives it from a clone of that repository.

## Repository layout

| Path | Content |
|---|---|
| `audit/` | the pipeline (evidence, line pricing, cross-invoice state, outcomes) |
| `spec/` | contract terms and instruments as printed, rule index, decisions with sources and alternatives, open questions |
| `source/` | identity of the pinned inputs: SHA-256 and git blob of all 10,330 files, scan page hashes, input inventory |
| `verification/` | committed outputs of every gate (`g2/` … `g6/`), OCR of the scans, the visual second pass and verbatim re-reads, the packets given to independent readers, their readings, and the comparisons and dispositions |
| `tools/` | verifiers, packet builders, comparison tools, the output checker, the page extractor |
| `tests/` | the test suite, including the negative controls and the prior-code fixtures |
| `audit_results.csv` | the audit output, one row per invoice |
| [`ERROR_ANALYSIS.md`](ERROR_ANALYSIS.md), [`DECISION_LOG.md`](DECISION_LOG.md) | failure types with examples; the assumptions, policy decisions and open readings |

## Development and review assistance

The code, specifications and documentation were written with the help of an AI coding assistant, under the author's
direction and review. The independent readings above, and the independent reviews whose findings became regression
tests, were separate LLM passes. Their outputs are kept under `verification/`, and every disagreement with the
implementation is settled against the contract scan and recorded.

## Known limitations

Set out in full, with an example of each, in [ERROR_ANALYSIS.md](ERROR_ANALYSIS.md):

1. **Facts held in documents that were not supplied.** All 1,906 drilling invoices depend on the well class, and 638
   also depend on a section nomination. 335 civil applications depend on an unrecorded ground class. If a call-off
   states a class other than the one billed, an unflagged row is a missed error.
2. **Contract readings that stay open.** 13 civil and 8 drilling rows are flagged at 0.50. Each is a false positive
   under the readings where it is right.
3. **Expected total and category where a defect has several causes.** The exported figure is an admissible value, not
   one the documents establish.
4. **Combinations that no test covers.** An independent false-negative/false-positive reader sample (30 invoices) was
   drawn but never read; it is kept with the original development history. **The false-negative rate is not measured
   by an independent reading.**
5. **The headline depends on stated decisions.** Under the alternative readings computed in
   `verification/g5/decision_effects.json`, the count would differ:
   - not resetting the civil Contract Year gives 245 civil flags;
   - flagging only a changed total gives civil 74 and drilling 110;
   - taking default values for documents not supplied gives civil 359 and drilling 1,241.
