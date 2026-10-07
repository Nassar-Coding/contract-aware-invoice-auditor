#!/usr/bin/env bash
# One command for the G2 exit checks, after the G0/G1 checks it builds on (which also run the full test suite).
# Read-only: verify_g2 rebuilds the evidence in memory and compares it with the committed verification/g2 outputs.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
PYTHON="$PY" bash tools/check_g0_g1.sh
echo "== G2 evidence gate";                                          "$PY" tools/verify_g2.py
