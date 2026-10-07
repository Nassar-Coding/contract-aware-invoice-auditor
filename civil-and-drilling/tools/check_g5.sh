#!/usr/bin/env bash
# One command for the G5 exit checks, after every G0-G4 check and the full test suite (which include the G5 negative
# controls, tests/test_g5_gate.py, and the falsification invoices, tests/test_g5_falsification.py). Read-only:
# verify_g5 re-runs the outcome engine in memory and compares with the committed verification/g5 outputs.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
PYTHON="$PY" bash tools/check_g4.sh
echo "== G5 sampled invoices vs independent readers";                "$PY" tools/g5_sample_compare.py --check
echo "== G5 full invoice outcomes gate";                              "$PY" tools/verify_g5.py
