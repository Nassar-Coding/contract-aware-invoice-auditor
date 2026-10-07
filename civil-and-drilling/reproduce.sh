#!/usr/bin/env bash
# Reproduce audit_results.csv from the pinned input snapshot (see README.md, "Run it").
# Snapshot location: $INVOICE_SNAPSHOT, else ../.inputs/civil-and-drilling (source/SNAPSHOT.md).
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python}
$PY tools/snapshot.py verify                 # the inputs are the pinned commit, file by file
$PY -m audit.build --quiet                   # evidence: claims, site records, daily drilling reports
$PY -m audit.g3_run                          # each line priced and checked on its own
$PY -m audit.g4_run                          # cross-invoice state: bands, limits, duplicates, footage, A3, retention
$PY -m audit.g5_run                          # one outcome per invoice; verification/g5/audit_results.csv
cp verification/g5/audit_results.csv audit_results.csv
$PY tools/check_results.py audit_results.csv # independent format and coverage check against the template and inputs
$PY tools/g6_review.py > /dev/null          # population review: rule exposure, residuals, outliers; verification/g6
git diff --stat --exit-code HEAD -- audit_results.csv verification/ && echo "REPRODUCED: committed outputs unchanged"
