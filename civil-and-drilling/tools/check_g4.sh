#!/usr/bin/env bash
# One command for the G4 exit checks, after every G0-G3 check and the full test suite (which include the G4 negative
# controls, tests/test_g4_gate.py, and the falsification histories, tests/test_g4_falsification.py). Read-only:
# verify_g4 re-runs the state engines in memory and compares with the committed verification/g4 outputs.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
PYTHON="$PY" bash tools/check_g3.sh
echo "== G4 multi-invoice histories vs independent readers";         "$PY" tools/g4_history_compare.py --check
echo "== G4 chronology and shared state gate";                       "$PY" tools/verify_g4.py
