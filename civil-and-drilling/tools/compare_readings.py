"""Compare the first-pass terms with independent readings of the scans (G1 aid, not an authority).

1. OCR (tesseract 5.3.4, --psm 6, outputs committed under verification/ocr/): for a row keyed by a code,
   every numeric cell must appear on the OCR line(s) holding that code; for other tables, the table's
   numeric cells must appear in order in the OCR token stream of its pages.
2. Blind transcriptions (verification/blind/*_blind.txt), when present: same row/sequence test.

A disagreement is not a correction. It is a queue item that the visual second pass must settle against the
scan image; the result is recorded in verification/second_pass_log.yaml.

Usage::

    python tools/compare_readings.py [--write verification/reading_comparison.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import spec_lib as sl

CODE_RE = re.compile(r"^[A-Z]{1,2}[.\-][0-9]")
OCR_FIX = str.maketrans({"S": "5", "O": "0", "o": "0", "l": "1", "I": "1"})


def norm_num(tok: str) -> str:
    t = tok.strip().strip(".,:;|'\"`()[]{}~_-—%").replace(",", "")
    return t


def tokens(text: str) -> list[str]:
    return [norm_num(t) for t in re.split(r"[\s|]+", text) if norm_num(t)]


def code_key(code: str) -> str:
    return code.replace(" ", "").upper().translate(OCR_FIX)


def load_reading(kind: str, contract: str) -> dict[int, str]:
    """Page number -> text for a reading source."""
    pages: dict[int, str] = {}
    if kind == "ocr":
        d = sl.VERIF / "ocr" / contract.lower()
        for f in sorted(d.glob("p*.txt")):
            pages[int(f.stem[1:])] = f.read_text(errors="replace")
    else:
        f = sl.VERIF / "blind" / f"{contract.lower()}_blind.txt"
        if not f.exists():
            return {}
        cur = None
        for line in f.read_text(errors="replace").splitlines():
            m = re.match(r"=== PAGE (\d+) ===", line.strip())
            if m:
                cur = int(m.group(1))
                pages.setdefault(cur, "")
                continue
            if cur is not None:
                pages[cur] += line + "\n"
    return pages


def row_lines(text: str, code: str) -> list[str]:
    key = code_key(code)
    out = []
    for line in text.splitlines():
        compact = code_key(line.replace("—", " "))
        if key in compact:
            out.append(line)
    return out


def check_table(tid: str, table: dict, pages: dict[int, str]) -> dict:
    src_pages = table["source"]["pages"]
    text = "\n".join(pages.get(p, "") for p in src_pages)
    cells = sl.numeric_cells(table)
    res = {"cells": len(cells), "agree": 0, "disagree": []}
    if not text.strip():
        res["status"] = "no reading for pages"
        return res
    cols = table.get("columns", [])
    rows = table.get("rows", [])
    keyed = bool(rows) and all(isinstance(r[0], str) and CODE_RE.match(r[0]) for r in rows)
    if keyed:
        for i, row in enumerate(rows):
            lines = row_lines(text, row[0])
            toks = set(t for l in lines for t in tokens(l))
            for c, v in zip(cols, row):
                if sl.is_number(v):
                    if v.replace(",", "") in toks:
                        res["agree"] += 1
                    else:
                        res["disagree"].append({"row": row[0], "column": c, "first_pass": v,
                                                "reading_lines": lines[:2] or ["<code not found>"]})
    else:
        stream = tokens(text)
        pos = 0
        for i, c, v in cells:
            target = v.replace(",", "")
            try:
                pos = stream.index(target, pos) + 1
                res["agree"] += 1
            except ValueError:
                # accept out-of-order presence as weaker agreement, but report it
                if target in stream:
                    res["agree"] += 1
                    res.setdefault("out_of_order", []).append({"row": i, "column": c, "first_pass": v})
                else:
                    res["disagree"].append({"row": i, "column": c, "first_pass": v, "reading_lines": ["<not in page tokens>"]})
    res["status"] = "all agree" if not res["disagree"] else f"{len(res['disagree'])} to review"
    return res


def compare(kind: str) -> dict:
    out = {}
    for contract in ("CW", "DDS"):
        pages = load_reading(kind, contract)
        if not pages:
            continue
        for tid, t in sl.load_terms(contract)["tables"].items():
            out[tid] = check_table(tid, t, pages)
        for inst in sl.load_instruments()["instruments"]:
            if inst["contract"] != contract:
                continue
            pseudo_rows = [[r["code"], r["from"], r["to"]] for r in inst.get("rate_rows", [])]
            mon = inst.get("monthly_rows")
            tables = []
            if pseudo_rows:
                tables.append((inst["id"] + ".rates", {"source": {"pages": [inst["page"]]}, "columns": ["code", "from", "to"], "rows": pseudo_rows}))
            if mon:
                tables.append((inst["id"] + ".monthly", {"source": {"pages": [inst["page"]]}, "columns": ["month", "rate"], "rows": mon["rows"]}))
            if inst.get("discount"):
                tables.append((inst["id"] + ".discount", {"source": {"pages": [inst["page"]]}, "columns": ["pct"], "rows": [[inst["discount"]["pct"]]]}))
            for tid, t in tables:
                out[tid] = check_table(tid, t, pages)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", type=Path, default=sl.VERIF / "reading_comparison.json")
    args = ap.parse_args()
    report = {}
    for kind in ("ocr", "blind"):
        r = compare(kind)
        if r:
            report[kind] = r
            cells = sum(v["cells"] for v in r.values())
            agree = sum(v["agree"] for v in r.values())
            print(f"{kind}: {agree}/{cells} numeric cells agree; tables to review: "
                  + (", ".join(f"{k}({len(v['disagree'])})" for k, v in r.items() if v["disagree"]) or "none"))
    args.write.write_text(json.dumps(report, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
