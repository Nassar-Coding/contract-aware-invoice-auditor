#!/usr/bin/env bash
# One command for the G0/G1 exit checks. Read-only: never regenerates frozen files or re-certifies tables.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
echo "== G0 snapshot: hashes, pinned tree, scan pages, inventory";  "$PY" tools/snapshot.py verify
echo "== G1 independent readings (OCR + blind) vs terms";           "$PY" tools/compare_readings.py --write /tmp/reading_comparison.check.json
cmp -s /tmp/reading_comparison.check.json verification/reading_comparison.json && echo "reading comparison reproduces committed file"
echo "== G0/G1 specification gate";                                 "$PY" tools/verify_spec.py
echo "== tests";                                                     "$PY" -m pytest -q tests
