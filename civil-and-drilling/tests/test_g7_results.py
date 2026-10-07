"""The audit results: the independent checker (tools/check_results.py) passes on the committed audit_results.csv and
fails on each controlled defect (negative controls); the root file is the pipeline's output, byte for byte."""
import csv
import io
from pathlib import Path

import check_results as CS
from audit.common import SNAPSHOT

ROOT = Path(__file__).resolve().parents[1]
SUB = ROOT / "audit_results.csv"


def _rows():
    with SUB.open(newline="") as fh:
        r = csv.DictReader(fh)
        return list(r.fieldnames), list(r)


def _write(tmp_path, cols, rows):
    p = tmp_path / "s.csv"
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    p.write_text(buf.getvalue())
    return p


def test_root_results_are_the_pipeline_output():
    assert SUB.read_bytes() == (ROOT / "verification" / "g5" / "audit_results.csv").read_bytes()


def test_checker_passes_on_the_results():
    assert CS.check(SUB, SNAPSHOT) == []


def test_checker_fails_on_each_defect(tmp_path):
    cols, rows = _rows()
    fl = next(i for i, r in enumerate(rows) if r["flagged"] == "1")
    cases = {
        "missing row": (cols, rows[1:], "rows are not the template's"),
        "rows reordered": (cols, [rows[1], rows[0]] + rows[2:], "rows are not the template's"),
        "duplicated row": (cols, rows + [rows[0]], "rows are not the template's"),
        "column order": (cols[::-1], rows, "columns"),
        "billed cents": (cols, [dict(r, billed_total_cents=str(int(r["billed_total_cents"]) + 1)) if i == 0 else r
                                for i, r in enumerate(rows)], "billed_total_cents"),
        "decimal cents": (cols, [dict(r, expected_total_cents="12.50") if i == 0 else r for i, r in enumerate(rows)],
                          "not an integer"),
        "flag without category": (cols, [dict(r, error_category="") if i == fl else r for i, r in enumerate(rows)],
                                  "error_category missing"),
        "confidence above one": (cols, [dict(r, confidence="1.2") if i == 0 else r for i, r in enumerate(rows)],
                                 "outside [0, 1]"),
        "flag not 0/1": (cols, [dict(r, flagged="yes") if i == 0 else r for i, r in enumerate(rows)], "is not 0 or 1"),
    }
    for name, (c, r, msg) in cases.items():
        errs = CS.check(_write(tmp_path, c, r), SNAPSHOT)
        assert any(msg in e for e in errs), (name, errs[:3])
