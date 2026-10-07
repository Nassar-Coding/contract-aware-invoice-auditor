# Hospital claims auditor

*Part of [Contract-Aware Invoice Auditor](../README.md). The [civil works and drilling auditor](../civil-and-drilling/)
applies the same method to scanned construction contracts.*

A health insurer reimburses five hospitals under five separately negotiated contracts. Each contract is structured
differently, and one is split into a base agreement, a rate schedule and an amendment. Every invoice line carries the
hospital's own free-text description of the service: not a contract term and not a code. The same service is described
many different ways. This engine decides, for each invoice, whether it is wrong, why, and what it should have totalled.

**Approach.** Each contract was turned into finite, source-referenced JSON with LLM assistance, reviewed and then frozen
([`contracts/`](contracts/README.md)). The records cover 108, 76, 120, 98 and 84 services for Hospitals 1–5. One
standard-library interpreter replays those records with exact integer-cent arithmetic. Replay makes no model call and
needs no API key, GPU or network.

## Results

Hospital 1 is the only hospital with labels. Its 913 invoices are split by patient-connected group into 622
development and 291 check invoices.

| | Development (tuning set) | Check (kept out of tuning) |
|---|---|---|
| True positives / false negatives / false positives | **42 / 0 / 0** | **16 / 0 / 0** |
| Corrected amount exact on true positives | 0.929 | 0.875 |
| Expected calibration error | 0.0332 | 0.0293 |

- The development score is in-sample: every threshold was tuned there.
- The check partition is regression evidence: kept out of tuning, first measured before the last prediction change
  and unchanged on the final code.
- None of 184 frozen decoy proxies is flagged. These are clean invoices that carry the surface pattern of an error.
- With 30% of descriptions perturbed, recall drops 9.5% relative, with no new false positive.

On the four unlabelled hospitals, `audit_results.csv` holds **2,537 opinions, 281 of them flags** (H2 76, H3 69, H4 61,
H5 75). **1,405 invoices are withheld** rather than guessed, each with a recorded reason. Per-category results, failure
modes, the readings applied to ambiguous clauses and next steps are in **[EVALUATION.md](EVALUATION.md)**.

## How it works

The order of the layers follows what each one depends on:

1. **Structural checks** need no service identity: duplicate and reused identifiers, malformed dates, line arithmetic,
   a service date after its invoice date, cross-invoice repeats, contract number and term window.
2. **A per-line decision layer** decides separately for each line whether its evidence supports a finding, a clean
   verdict, or neither. An unresolved fact blocks only what depends on it, and evidence that is itself the error is
   always reported.
3. **Mapping** uses a four-state matcher (match / no-match / weak / tie) that scores coverage of the *billed* tokens. It
   never sees a price.
4. **Contract rules** cover caps, exclusion windows and unit basis.
5. **Pricing**, in a fixed order. It starts from the rate version in force on the service date, then applies:
   - bundles;
   - multipliers;
   - premiums;
   - uplifts;
   - the deepest qualifying volume discount, computed from retrospective usage;
   - caps.

   Each multiplicative stage rounds half-up to the cent; a bundle substitutes a contract figure, and a daily cap limits
   the billable quantity.

An ambiguous clause is settled only by a **course-of-dealing test** fixed in advance: a reading must account for at
least 98% of the hospital's own relevant lines, and every rival must account for less. Only Hospital 2's calendar
*Service Day* passes (98.23% against 42.70%). Module-by-module detail is in
[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md#hospital-claims--module-map).

## Run it

Python `>=3.12,<3.13`, standard library only. From the repository root:

```bash
make hospital-inputs   # fetch the input snapshot into data/source/ at a pinned commit
make hospital          # reproduce, verify, validate independently, confirm audit_results.csv is byte-identical
make hospital-test     # 175 tests
make hospital-eval     # score Hospital 1's development partition
```

Or from this directory, once `data/source/` exists:

```bash
PYTHONPATH=src python -m insurance_audit reproduce          # all five hospitals; writes audit_results.csv
PYTHONPATH=src python -m insurance_audit verify-results
python tools/validate_results.py                            # independent of the exporter
python tools/run_checks.py local                            # the test suite
python tools/generalize.py                                  # survival under re-identified, perturbed inputs
python tools/generalize_recall.py                           # recall under 30% description perturbation
```

`audit_results.csv` has SHA-256 `09a48b06852eda729cb7171044901bf0afd480de6298b2ee0a283bfe9ea4ca0f`.

`validate_results.py` re-reads the source invoices and checks the CSV independently of the exporter:

- column order;
- flag domain;
- confidence range;
- integer cents;
- one row per identifier;
- Hospitals 2–5 only;
- billed totals against the source.

## Layout

| Path | Content |
|---|---|
| `src/insurance_audit/` | the engine: ingestion, structural findings, matcher, pricing, consistency test, confidence, export, CLI |
| `contracts/`, `mappings/` | the reviewed contract records, rule files and service mappings the engine replays |
| `evaluation/confidence_policy.json` | the frozen confidence tiers |
| `tests/` | 175 tests; `tests/evaluation/` holds the partition manifest, frozen decoy proxies and error-family coding (evaluation inputs, never prediction inputs) |
| `tools/` | development scoring, the independent validator, the robustness harnesses, decision traces |
| `audit_results.csv` | the committed output that a rerun must reproduce byte for byte |
