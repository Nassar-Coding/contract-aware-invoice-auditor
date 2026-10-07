"""Independent check of the audit results against the pinned input snapshot: schema, row coverage, minor-unit
conversion and the judged-field mapping, checked without the exporter.

Reads only the results file and the input snapshot's own files - submission_template.csv, civilwork/invoices/
applications.csv, drilling_services/invoices/invoices.csv - never the implementation's outputs:

  - the header is exactly the template's six columns, in the template's order;
  - the rows are exactly the template's 2,806 invoice ids, in the template's order, each once;
  - flagged is 0 or 1; error_category is non-empty exactly when flagged = 1;
  - expected_total_cents and billed_total_cents are integers (minor units: halalas / cents, no decimal point);
  - billed_total_cents = 100 x the judged header field as printed in the source (application_total for a civil
    application, invoice_total for a drilling invoice), with no rounding;
  - confidence is a number in [0, 1].

Usage::  python tools/check_results.py [path/to/audit_results.csv]      (default: audit_results.csv in this directory)
Exit status 0 when every check passes; the failures are printed otherwise.
"""
from __future__ import annotations

import csv
import os
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def snapshot() -> Path:
    env = os.environ.get("INVOICE_SNAPSHOT")
    if env:
        return Path(env)
    sys.path.insert(0, str(ROOT))
    from audit.common import SNAPSHOT
    return Path(SNAPSHOT)


def read(p: Path) -> tuple[list[str], list[dict]]:
    with p.open(newline="") as fh:
        r = csv.DictReader(fh)
        return list(r.fieldnames or []), list(r)


def check(sub_path: Path, snap: Path) -> list[str]:
    errs = []
    t_cols, t_rows = read(snap / "submission_template.csv")
    s_cols, s_rows = read(sub_path)
    if s_cols != t_cols:
        errs.append(f"columns {s_cols} are not the template's {t_cols}")
    ids = [r["invoice_id"] for r in t_rows]
    got = [r.get("invoice_id") for r in s_rows]
    if got != ids:
        missing = sorted(set(ids) - set(got))
        extra = sorted(set(got) - set(ids))
        dup = sorted({i for i in got if got.count(i) > 1}) if len(got) != len(set(got)) else []
        errs.append(f"rows are not the template's {len(ids)} ids in order: {len(got)} rows, missing {missing[:5]}, "
                    f"extra {extra[:5]}, duplicated {dup[:5]}")
    judged = {}
    for rel, key, field in (("civilwork/invoices/applications.csv", "application_no", "application_total"),
                            ("drilling_services/invoices/invoices.csv", "invoice_no", "invoice_total")):
        _c, rows = read(snap / rel)
        for r in rows:
            judged.setdefault(r[key], []).append(r[field])
    integer = re.compile(r"^-?\d+$")
    for r in s_rows:
        i = r.get("invoice_id")
        if r.get("flagged") not in ("0", "1"):
            errs.append(f"{i}: flagged {r.get('flagged')!r} is not 0 or 1")
        elif (r["flagged"] == "1") != bool((r.get("error_category") or "").strip()):
            errs.append(f"{i}: error_category {'missing on a flagged' if r['flagged'] == '1' else 'present on an unflagged'} row")
        for col in ("expected_total_cents", "billed_total_cents"):
            if not integer.match(r.get(col) or ""):
                errs.append(f"{i}: {col} {r.get(col)!r} is not an integer number of minor units")
        src = judged.get(i)
        if not src:
            errs.append(f"{i}: no header row in the source invoices")
        elif len(set(src)) != 1:
            errs.append(f"{i}: the source carries {len(src)} header rows with different judged totals {src}")
        elif integer.match(r.get("billed_total_cents") or ""):
            try:
                want = Decimal(src[0]) * 100
            except InvalidOperation:
                errs.append(f"{i}: source judged total {src[0]!r} is not a number")
                continue
            if want != want.to_integral_value() or int(r["billed_total_cents"]) != int(want):
                errs.append(f"{i}: billed_total_cents {r['billed_total_cents']} is not 100 x {src[0]}")
        try:
            c = Decimal(r.get("confidence") or "x")
            if not Decimal(0) <= c <= Decimal(1):
                errs.append(f"{i}: confidence {c} outside [0, 1]")
        except InvalidOperation:
            errs.append(f"{i}: confidence {r.get('confidence')!r} is not a number")
    return errs


def main() -> int:
    sub = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "audit_results.csv"
    errs = check(sub, snapshot())
    n = sum(1 for _ in open(sub)) - 1 if sub.exists() else 0
    for e in errs[:40]:
        print("FAIL", e)
    if len(errs) > 40:
        print(f"... {len(errs) - 40} more")
    print(f"RESULTS {'OK' if not errs else 'FAILED'}: {sub.name}, {n} rows")
    return 0 if not errs else 1


if __name__ == "__main__":
    sys.exit(main())
