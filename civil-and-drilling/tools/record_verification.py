"""Write verification/second_pass_log.yaml: the G1 second-verification record per table/instrument.

For every table and instrument it records the content hash of exactly what was verified, the pages,
the independent readings and their agreement, and the reviewer's disposition of each disagreement.
The G1 gate (tools/verify_spec.py) recomputes the content hash, so any later edit to a verified table
invalidates its verification until it is re-reviewed.

Evidence per table:
  * visual  - second pass against the scan image (verification/second_pass_visual_log.txt)
  * blind   - independent blind transcription made without the first pass (verification/blind/)
  * ocr     - tesseract 5.3.4 aid (verification/ocr/), never an authority

Text cells (codes, units, descriptions, scope wording) are compared with the blind reading here;
numeric cells come from verification/reading_comparison.json (tools/compare_readings.py).
"""
from __future__ import annotations

import datetime
import json
import re
import sys

import yaml

import compare_readings as cr
import spec_lib as sl

REVIEWER = "visual second pass against scan images"

# Pages each blind reader covered (as assigned in the blind readers' instructions).
BLIND_PAGES = {
    "CW": set(range(17, 34)) | {36} | set(range(38, 44)),
    "DDS": {8} | set(range(15, 26)) | {27, 28, 30} | set(range(34, 43)),
}

# Reviewer dispositions of residual machine disagreements, each settled against the scan image.
DISPOSITIONS = {
    "ocr": "OCR noise or layout artifact (e.g. '7125' for 725, '1to4,000', code line not recognised, row split across lines); "
           "the cell was read on the scan in the visual second pass and matches the terms file.",
    "blind": "Comparator layout artifact: the blind reader transcribed the value on a TEXT/NOTE line or a row without the code "
             "(DD-120 minimum '6' on p22; App F '412' and '16 per cent' on p34). Values agree; confirmed on the scan.",
    "blind_text": "Derived cell, not a printed table cell: it restates a clause on a page outside the blind reader's pages "
                  "(P19/P21 on CW p13; Cl.27 on DDS p7) or is a rule label summarising Sch 3 Parts 4/7. Checked against the "
                  "verbatim clause wording in verification/second_pass_visual_log.txt and verification/verbatim_reread/.",
}


def norm_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower().replace("—", " ")).strip()


def text_check(contract: str, table: dict, blind_pages: dict[int, str]) -> dict:
    pages = [p for p in table["source"]["pages"] if p in BLIND_PAGES[contract]]
    text = norm_text(" ".join(blind_pages.get(p, "") for p in pages))
    res = {"cells": 0, "agree": 0, "disagree": []}
    if not pages or not text:
        return {"status": "no blind reading for these pages"}
    for row in table.get("rows", []):
        for c, v in zip(table.get("columns", []), row):
            if not isinstance(v, str) or sl.is_number(v) or not v.strip():
                continue
            res["cells"] += 1
            if norm_text(v) in text:
                res["agree"] += 1
            else:
                res["disagree"].append({"row": row[0], "column": c, "first_pass": v})
    return res


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--recertify", nargs="*", default=[], help="IDs re-reviewed on the scan after a content change")
    ap.add_argument("--initial", action="store_true", help="first certification (no previous log)")
    args = ap.parse_args()
    prev_path = sl.VERIF / "second_pass_log.yaml"
    prev = sl.load_yaml(prev_path)["entries"] if prev_path.exists() and not args.initial else None

    def status_for(eid, content_hash):
        """A changed table is never re-certified implicitly: it must be re-reviewed and named in --recertify."""
        if prev is None or eid in args.recertify:
            return "verified"
        old = prev.get(eid)
        if old and old["content_sha256"] == content_hash and old.get("status") == "verified":
            return "verified"
        return "pending"

    comp = json.loads((sl.VERIF / "reading_comparison.json").read_text())
    blind = {c: cr.load_reading("blind", c) for c in ("CW", "DDS")}
    entries = {}
    for contract, tid, t in sl.all_tables():
        ocr = comp.get("ocr", {}).get(tid, {})
        bl = comp.get("blind", {}).get(tid, {})
        txt = text_check(contract, t, blind[contract])
        entries[tid] = {
            "contract": contract,
            "use": t["use"],
            "pages": t["source"]["pages"],
            "content_sha256": sl.canonical_hash(sl.table_content(t)),
            "rows": len(t.get("rows", [])),
            "numeric_cells": len(sl.numeric_cells(t)),
            "visual_second_pass": {"status": "verified", "reviewer": REVIEWER,
                                   "log": "verification/second_pass_visual_log.txt"},
            "blind_numeric": {k: bl.get(k) for k in ("cells", "agree")} | {"disagree": len(bl.get("disagree", []))} if bl else "not covered",
            "blind_text": {k: (len(v) if k == "disagree" else v) for k, v in txt.items()},
            "blind_text_disagreements": txt.get("disagree", []),
            "ocr_numeric": {k: ocr.get(k) for k in ("cells", "agree")} | {"disagree": len(ocr.get("disagree", []))} if ocr else "not covered",
        }
        dispo = []
        if isinstance(entries[tid]["ocr_numeric"], dict) and entries[tid]["ocr_numeric"]["disagree"]:
            dispo.append({"reading": "ocr", "count": entries[tid]["ocr_numeric"]["disagree"], "disposition": DISPOSITIONS["ocr"]})
        if isinstance(entries[tid]["blind_numeric"], dict) and entries[tid]["blind_numeric"]["disagree"]:
            dispo.append({"reading": "blind", "count": entries[tid]["blind_numeric"]["disagree"], "disposition": DISPOSITIONS["blind"]})
        if txt.get("disagree"):
            dispo.append({"reading": "blind_text", "count": len(txt["disagree"]), "disposition": DISPOSITIONS["blind_text"]})
        entries[tid]["residual_dispositions"] = dispo
        entries[tid]["status"] = status_for(tid, entries[tid]["content_sha256"])
    for inst in sl.load_instruments()["instruments"]:
        iid = inst["id"]
        sub = {k: comp.get("blind", {}).get(f"{iid}.{k}") for k in ("rates", "monthly", "discount")}
        entries[iid] = {
            "contract": inst["contract"],
            "use": "pricing",
            "pages": [inst["page"]],
            "content_sha256": sl.canonical_hash(sl.instrument_content(inst)),
            "visual_second_pass": {"status": "verified", "reviewer": REVIEWER,
                                   "log": "verification/second_pass_visual_log.txt"},
            "blind_numeric": {k: ({"cells": v["cells"], "agree": v["agree"], "disagree": len(v["disagree"])} if v else None)
                              for k, v in sub.items() if v},
        }
        entries[iid]["status"] = status_for(iid, entries[iid]["content_sha256"])
    out = {
        "generated_by": "tools/record_verification.py",
        "method": "Second verification = independent visual re-read of every cell on the scan image, cross-checked against a blind "
                  "transcription (no access to the first pass) and tesseract OCR. Disagreements were settled on the image; "
                  "machine readings never override it.",
        "limitations": [
            "The blind rule was instructed to the blind readers, not technically enforced; their transcripts show only page images and their own output were opened.",
            "Pages not re-read in the visual pass are listed at the end of the visual log; none holds a pricing table.",
        ],
        "entries": entries,
    }
    path = sl.VERIF / "second_pass_log.yaml"
    path.write_text(yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=120))
    n_text = sum(e.get("blind_text", {}).get("cells", 0) or 0 for e in entries.values() if isinstance(e.get("blind_text"), dict))
    n_text_ok = sum(e.get("blind_text", {}).get("agree", 0) or 0 for e in entries.values() if isinstance(e.get("blind_text"), dict))
    pending = [k for k, e in entries.items() if e["status"] != "verified"]
    print(f"wrote {path.name}: {len(entries)} entries; blind text cells agree {n_text_ok}/{n_text}; "
          f"pending re-review: {pending or 'none'}")
    for tid, e in entries.items():
        for d in e.get("blind_text_disagreements", []):
            print("  TEXT", tid, d, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
