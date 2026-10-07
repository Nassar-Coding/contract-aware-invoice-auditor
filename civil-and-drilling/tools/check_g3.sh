#!/usr/bin/env bash
# One command for the G3 exit checks, after every G0/G1/G2 check and the full test suite (which include the G3
# negative controls, tests/test_g3_gate.py). Read-only: verify_g3 re-evaluates every line in memory and compares
# with the committed verification/g3 outputs; nothing is regenerated.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
PYTHON="$PY" bash tools/check_g2.sh
echo "== G3 reference cases vs independent readers";                 "$PY" tools/g3_case_compare.py --check
echo "== G3 local entitlement and pricing gate";                     "$PY" tools/verify_g3.py
