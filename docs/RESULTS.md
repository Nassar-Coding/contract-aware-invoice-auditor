# Results and where each one comes from

The figures in the READMEs, and the others the documents rely on, appear below. Each entry names the committed file
or the command that produces it. Paths are relative to the auditor's directory named in the section heading. Figures
marked **re-run** were recomputed from a fresh run of this repository and match the committed record. The rest are
read from committed evidence.

## Hospital claims — `hospital-claims/`

### Hospital 1 (labelled)

| Figure | Value | Source | Recompute | Status |
|---|---|---|---|---|
| Labelled invoices; erroneous | 913; 58 | `data/source/labels/hospital_1_labels.csv` (`is_erroneous`) | — | re-run |
| Development / check split | 622 (42 erroneous, 580 clean) / 291 (16 erroneous, 275 clean) | `tests/evaluation/split_manifest.json` | `make hospital-eval` | **re-run** |
| Development TP / FN / FP | 42 / 0 / 0 | `EVALUATION.md` (Headline) | `make hospital-eval` → `runs/score_development.json` | **re-run** |
| Development amount exact on TPs | 39 / 42 = 0.929 | `EVALUATION.md` | same, `tp_amount.exact_match_rate` | **re-run** |
| Development rows emitted / withheld | 446 / 176 | `EVALUATION.md` | same, `emitted` / `withheld` | **re-run** |
| Check TP / FN / FP | 16 / 0 / 0 | `EVALUATION.md` | `tools/score_development.py --partition check --allow-check-partition` (see [REPRODUCING.md](REPRODUCING.md)) | **re-run** on the final code; identical to the measurement made before the last prediction change |
| Check amount exact on TPs | 14 / 16 = 0.875 | `EVALUATION.md` | same, `tp_amount` | **re-run** |
| Expected calibration error (event: correct flag *and* exact cents, emitted rows only) | 0.0332 development, 0.0293 check | `EVALUATION.md` (Headline) | same commands, `reliability.ece` | **re-run** |
| Decoy proxies flagged | 0 of 184 (122 receive an opinion, 62 are withheld) | `tests/evaluation/decoy_proxies.json`; `EVALUATION.md` | test suite (`tests/test_decoy_proxies.py`); opinion count from the development export | **re-run** |
| Recall drop under 30% description perturbation | 1.0 → 0.905, −9.5% relative, 0 new FPs, 3,424 lines perturbed | `EVALUATION.md` (Robustness) | `python tools/generalize_recall.py` | **re-run** |
| Survival under re-identification, subsampling, shuffling and perturbation | passed | `EVALUATION.md` (Robustness) | `python tools/generalize.py` | **re-run** |

### Hospitals 2–5 (unlabelled)

| Figure | Value | Source | Recompute | Status |
|---|---|---|---|---|
| Invoices | 3,942 unique (H2 1,125, H3 932, H4 835, H5 1,050) | the input CSVs | `runs/attempts/*/H*.input_quality.json` (`unique_invoice_ids`), written by `make hospital` | **re-run** |
| Opinions emitted; flags | 2,537; 281 (H2 76, H3 69, H4 61, H5 75) | `audit_results.csv` | `make hospital` (`validate_results.py` prints per-hospital rows and flags) | **re-run** |
| Withheld | 1,405: ambiguous mapping 657, composite dimension 339, rate still ambiguous 299, unresolved mapping 62, same-day allocation 39, exclusion 9 | `EVALUATION.md` | `tools/score_development.py ... --trace runs/trace/decision_trace.jsonl.gz` → `runs/trace/withheld_reasons.json` (`primary_reason`; "same-day allocation" sums its two duplicate-service-day keys) | **re-run** |
| Lines naming more than one contracted service | 1,053; the candidates disagree on 925 of them | `EVALUATION.md` (failure mode 1) | `runs/trace/decision_trace.jsonl.gz` (`mapping.state` TIE); `error_fraction` 0.5 in `runs/attempts/*/H*.json` | **re-run** |
| `audit_results.csv` SHA-256 | `09a48b06852eda729cb7171044901bf0afd480de6298b2ee0a283bfe9ea4ca0f` | `README.md` | `make hospital` | **re-run** |
| Tests | 175, 0 failures | — | `make hospital-test` | **re-run** |

### Course-of-dealing test

| Hospital | Reading | Lines accounted for | Adopted |
|---|---|---|---|
| H2 | calendar Service Day | 5,004 / 5,094 = 98.23% | yes |
| H2 | 07:00 envelope | 2,175 / 5,094 = 42.70% | no |
| H4 | utilisation across all patients | 304 / 322 = 94.41% | no (below 98%) |
| H5 | facility multiplier, both readings | 12,542 / 12,900 = 97.22% | no: the comparison is vacuous (known defect) |

Source: `EVALUATION.md`; the tables are recomputed by every `reproduce` run and recorded under `adopted_readings` in
`runs/attempts/*/H*.json`.

### How the engine improved (development partition)

| Iteration | TP / FN / FP |
|---|---|
| First version | 4 / 38 / 0 |
| Structural layer | 26 / 16 / 0 |
| Matcher | 38 / 4 / 0 |
| Contract rules; pricing and confidence | 40 / 2 / 0 |
| Established differences (current) | **42 / 0 / 0** (**re-run**) |

Earlier iterations are recorded in `EVALUATION.md`. Removing the one line of `audit.py` that reports an established
rate difference on a partially resolved invoice returns the current code to 40 / 2 / 0. Development figures are
in-sample: every threshold was tuned on that partition.

## Civil works and drilling — `civil-and-drilling/`

### Outcomes

| Figure | Value | Source | Recompute | Status |
|---|---|---|---|---|
| Invoices / lines | civil 900 / 7,746; drilling 1,906 / 91,244 | `source/inventory.json`; the pinned input CSVs | `tools/snapshot.py verify` | **re-run** |
| Input files verified | 10,330, by SHA-256 and git blob | `source/manifest.tsv` | `tools/snapshot.py verify` | **re-run** |
| Flagged | 210 of 2,806 (7.5%): civil 85 (9.4%), drilling 125 (6.6%) | `verification/g5/summary.json`; `audit_results.csv` | `make civil` | **re-run** |
| Flagged at confidence 0.95 / 0.80 / 0.60 / 0.50 | civil 40 / 5 / 27 / 13; drilling 0 / 0 / 117 / 8 | `verification/g5/summary.json` (`confidence`) | same | **re-run** |
| Not flagged at 0.95 / 0.80 | civil 514 / 301; drilling 0 / 1,781 | same | same | **re-run** |
| Root categories of flags (each component of a combined label counted) | civil: rate 35, duplicate 21, arithmetic 8, evidence 7, timing 6; drilling: rate 57, quantity 21, eligibility 14, timing 9, evidence mismatch 8 | `ERROR_ANALYSIS.md`; `verification/g6/review.json` | from `audit_results.csv` | committed |
| Invoices depending on a document not supplied | drilling 1,906 (638 also on a section nomination); civil 335 | `verification/g5/summary.json` (`fact_dependent`); `decision_effects.json` (`PD210-nomination`) | — | committed |
| `audit_results.csv` SHA-256 | `543b5896eb8343ade9d3c0d8ca91603608d84a5bcf4dac1f00f266d92bbd58a3` | equals `verification/g5/audit_results.csv` | `make civil` (`git diff` must be empty) | **re-run** |

### How much the headline depends on the decisions

`verification/g5/decision_effects.json` recomputes the flag count under each alternative reading. The largest effects:

| Decision | Adopted | Alternative | Flags under the alternative |
|---|---|---|---|
| Q8, a fact set by a document not supplied (well class, ground class) | invoice wrong only if no admissible value makes it right | query every such invoice / use default values | civil 386, drilling 1,906 / civil 359, drilling 1,241 |
| Q12, civil Contract Year | restarts on 5 January 2026 | runs on through the extension (reprices 181 applications that reconcile as billed) | civil 245 |
| Q9, what counts as "wrong" | any of the twelve checks fails, including procedural or payment-only breaches | only a changed total | civil 74, drilling 110 |

The headline 210 rests on these readings. The alternatives are not hidden: each is computed, and the reasons for each
adoption are in `spec/g5_decisions.yaml` and the [decision log](../civil-and-drilling/DECISION_LOG.md).

### Independent readings

| Check | Value | Source | Status |
|---|---|---|---|
| Contract tables, blind transcription vs. transcribed terms | 544 / 547 numeric cells (OCR: 522 / 547) | `verification/reading_comparison.json` | **re-run** (`compare_readings.py` reproduces the file) |
| Contract tables, text cells | 1,025 / 1,047 | `verification/second_pass_log.yaml` (sum of the `blind_text` entries) | committed |
| Second visual pass of every active numeric table | 0 numeric discrepancies | `verification/second_pass_log.yaml`, `verification/second_pass_visual_log.txt` | committed |
| Parameters and operative rules second-read | 79 / 79 supported (33 parameters, 46 rules), after 17 corrected items were re-read (`verification/param_rule_packets/packet_G.jsonl`, `packet_H.jsonl`) | `verification/param_rule_log.yaml`, `param_rule_dispositions.yaml` | checked by `tools/verify_spec.py` (**re-run**) |
| Blind annotation of records | civil 45 records, 646 / 646 fields; drilling 31 reports, 1,029 / 1,029 fields | `verification/g2/blind/comparison.json` | committed |
| Line cases | 192 cases, 903 comparisons, 858 agree, 45 settled | `verification/g3/case_comparison.json` | X1 in `tools/verify_g3.py` (**re-run**) |
| Multi-invoice histories | 24 histories (19 constructed to exercise one rule each, 5 drawn from the population), 48 readings, 28 in full agreement, 59 line-level differences settled | `verification/g4/history_comparison.json`, `history_dispositions.yaml` | Y1 in `tools/verify_g4.py` (**re-run**) |
| Whole invoices | 25 invoices (13 drawn at random with seed 5505 from band-free civil applications and drilling invoices of at most 22 lines; 12 chosen for anomalies in the raw data), 50 readings, 48 agree on flag and expected total | `verification/g5/samples/meta.yaml`, `sample_comparison.json`, `sample_dispositions.yaml` | Z5 in `tools/verify_g5.py` (**re-run**) |
| False-negative / false-positive sample | 30 invoices drawn (seed 6606), **never read** | kept with the original development repository (see [Provenance](../README.md#provenance)) | — |

The reader packets for G3 and G5 are rebuilt byte for byte by `tools/g3_cases.py` and `tools/g5_samples.py` from the
case definitions and the raw inputs.

What the readings do and do not establish:

- The readers are separate instances of the same model family, reading the same scans.
- Their isolation was instructed, not enforced.
- The whole-invoice readers were given the audit's adopted decisions to apply.
- Agreement therefore shows independent reproduction, not correctness. A misreading shared by readers and engine would
  pass.

### Caveats on the verification

| Item | Detail |
|---|---|
| Ordering proof for G3 | X1 shows the review packets were committed before the readers' expected results. The expected results and the pricing engine were first committed together, and the engine existed when two of the readers ran. The full packet → readers → engine ordering holds for G4 (Y1) and G5 (Z5). |
| Commit order | The ordering proofs read `verification/commit_order.json`, a record of the original development history. `tools/commit_order.py check --source <clone>` re-derives it from a clone of the original development repository (see [Provenance](../README.md#provenance)) and fails on any difference. |
| "Verified" tables | The verification status of each table is enforced by `tools/verify_spec.py`, a check run by `check_g0_g1.sh`, not by the pricing code at run time. |
| Decision effects | Civil site zone and night working are taken as stated on the line (G3-D3); unlike the other decisions, their alternatives are not computed per invoice in `decision_effects.json`. |
