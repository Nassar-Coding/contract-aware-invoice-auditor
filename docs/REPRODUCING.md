# Reproducing the results

Both auditors are deterministic: the same inputs regenerate byte-identical outputs. Neither makes a network or model
call at run time. Each fetches its input dataset once, before running.

## Environment

- **Python 3.12** for both auditors. The hospital claims auditor declares `>=3.12,<3.13` and runs on the standard
  library alone. It prints a warning, not an error, on any patch version other than its reference 3.12.13. The civil
  works and drilling auditor was developed on Python 3.11 and is verified on 3.12 as well; its pinned dependencies are
  in [`civil-and-drilling/requirements.txt`](../civil-and-drilling/requirements.txt).
- **git**. Both auditors compare their regenerated outputs with the committed ones, so run them inside a git clone of
  this repository, not an unpacked archive. The inputs are fetched as git clones too: the civil input check reads
  their object hashes.
- `make` is a convenience; every target is a few plain commands, listed below.

## With make

```bash
make hospital-inputs  # clone the hospital dataset into hospital-claims/data/source/ at its pinned commit
make hospital         # reproduce, verify-results, independent validator, byte-identity check (standard library)
make hospital-test    # 175 tests
make hospital-eval    # Hospital 1 development scoring
make setup            # python3.12 -m venv .venv; pip install -r civil-and-drilling/requirements.txt
make civil-inputs     # clone the civil dataset into .inputs/civil-and-drilling/ (18 MB download, ~90 MB on disk)
make civil            # reproduce.sh: verify inputs, rebuild every gate, check the output, git diff must be empty
make civil-verify     # tools/check_g5.sh: all gate checks G0–G5, reader comparisons, the test suite
```

Set `PYTHON=...` to choose the interpreter. Set `INVOICE_SNAPSHOT=/path/to/clone` to use a git clone of the civil
inputs that already exists elsewhere.

## Without make

**Hospital claims**, from `hospital-claims/`:

```bash
git clone https://github.com/majedzahrani3/insurance_auditing data/source
git -C data/source checkout 6fee1da60b74512156637a22be15d996a36627e1

PYTHONPATH=src python -m insurance_audit reproduce            # writes audit_results.csv and runs/
PYTHONPATH=src python -m insurance_audit verify-results       # self-check of the export
python tools/validate_results.py                              # independent re-read of the source CSVs
python tools/run_checks.py local                              # the test suite
git diff --exit-code HEAD -- audit_results.csv                # byte-identical to the committed file

# measurements
PYTHONPATH=src python -m insurance_audit reproduce --evaluate-development
python tools/score_development.py --export-current-h1 runs/h1.csv --partition development --output runs/score.json
python tools/score_development.py --export-current-h1 runs/h1_check.csv --partition check \
    --allow-check-partition --output runs/score_check.json   # the check partition, kept out of tuning
python tools/score_development.py --export-current-h1 runs/h1.csv --output runs/score.json \
    --trace runs/trace/decision_trace.jsonl.gz                # per-invoice traces and withheld-reason counts
python tools/generalize.py          # survival under re-identified, subsampled, perturbed inputs
python tools/generalize_recall.py   # recall under 30% description perturbation
```

The contract bundles record the hashes of the source documents they were read from, as metadata; the interpreter
does not check them at run time. Use the pinned commit.

**Civil works and drilling**, from `civil-and-drilling/`:

```bash
git clone https://github.com/majedzahrani3/invoice-auditing-level-2 ../.inputs/civil-and-drilling
git -C ../.inputs/civil-and-drilling checkout aef4924dc32506b4587de8b788b5a947e6beffec
pip install -r requirements.txt   # pinned: pymupdf, PyYAML, pytest, pillow

./reproduce.sh                    # snapshot verify -> build -> g3 -> g4 -> g5 -> check_results -> g6 -> git diff
PYTHON=python tools/check_g5.sh   # every gate check and the full test suite
```

Two further tools re-derive committed evidence from outside sources:

- `python tools/commit_order.py check --source /path/to/clone` re-derives `verification/commit_order.json`, the
  commit order behind the G3–G5 ordering proofs, from a clone of the original development repository (see
  [Provenance](../README.md#provenance)).
- `python tools/extract_pages.py` writes the scanned contract pages as PNG files and checks each against the pixel
  hashes in `source/pdf_pages.json`.

## Measured runs

Measured on Linux (4 vCPUs) with Python 3.12.3:

| Command | Result | Wall time |
|---|---|---|
| hospital `reproduce` | `audit_results.csv` SHA-256 `09a48b06…a4ca0f`, 2,537 rows, byte-identical | 75 s |
| hospital `verify-results`, `validate_results.py` | pass | seconds |
| hospital `run_checks.py local` | 175 tests, 0 failures | 44–60 s |
| hospital `reproduce --evaluate-development` + `score_development.py` | development 42 TP / 0 FN / 0 FP; amount exact 39 / 42; ECE 0.0332 | ~1.5 min |
| hospital `score_development.py --partition check` | 16 TP / 0 FN / 0 FP; amount exact 14 / 16; ECE 0.0293 | seconds |
| hospital `generalize.py` | status `passed` | — |
| hospital `generalize_recall.py` | 3,424 lines perturbed; recall 1.0 → 0.905 (−9.5% relative); 0 new false positives | — |
| civil `reproduce.sh` | inputs verified (10,330 files); `RESULTS OK: 2806 rows`; **"REPRODUCED: committed outputs unchanged"**; `audit_results.csv` SHA-256 `543b5896…bd58a3` | ~20 min |
| civil `tools/check_g5.sh` | `SNAPSHOT VERIFY OK`, reading comparison reproduces, `SPEC VERIFY OK`, the full test suite, `G2`/`G3`/`G4`/`G5 VERIFY OK` | ~35–40 min |

Timings were measured with other jobs sharing the four cores, so they are upper bounds. CI
([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)) runs every `make` target above on a clean `ubuntu-24.04`
runner with Python 3.12.
