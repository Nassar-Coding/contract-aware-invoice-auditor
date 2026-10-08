"""G1 second verification of parameters and rules.

Tables and instruments are certified by tools/record_verification.py. This tool does the same for every
parameter in spec/terms_*.yaml and every rule in spec/rules.yaml:

  packet  - write the reading packets given to the independent second readers: each item's full statement and the scan pages it cites.
  record  - write verification/param_rule_log.yaml from the readers' output
            (verification/param_rule_readings/*.jsonl): per item the content hash of exactly what was read,
            the reader's verdict, verbatim quotes with pages, a machine check that each quote is on the cited
            page's OCR text, the printed value of a parameter, and the reviewer's disposition of any
            non-supported verdict (settled against the scan image).

The hash covers the WHOLE parameter or rule entry, so any later edit (value, page, provision, note, title,
scope, sources, validation cases, references, status) invalidates its verification until it is re-read and
named in --recertify. tools/verify_spec.py recomputes the hashes.

Usage::  python tools/param_rule_verification.py packet --out DIR
         python tools/param_rule_verification.py record [--recertify ID ...]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path

import yaml

import spec_lib as sl

READINGS = sl.VERIF / "param_rule_readings"
LOG = sl.VERIF / "param_rule_log.yaml"
DISPOSITIONS = sl.VERIF / "param_rule_dispositions.yaml"
OCR = sl.VERIF / "ocr"
INSTRUMENT_PAGES = {"CW": [38, 39, 40, 41, 42, 43], "DDS": [37, 38, 39, 40, 41, 42]}
QUOTE_MIN_RATIO = 0.85            # quote vs best-matching window of the page's OCR text (OCR is an aid, not authority)
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}


def param_content(p: dict) -> dict:
    return dict(p)


def rule_content(r: dict) -> dict:
    return dict(r)


def items() -> list[dict]:
    """Every parameter and rule, with its contract, cited pages and content hash."""
    out = []
    for c in ("CW", "DDS"):
        for p in sl.load_terms(c)["parameters"]:
            out.append({"id": p["id"], "kind": "parameter", "contract": c, "pages": [p["page"]] + _pages(p["provision"]),
                        "content": param_content(p), "sha": sl.canonical_hash(param_content(p))})
    for r in sl.load_yaml(sl.SPEC / "rules.yaml")["rules"]:
        c = "CW" if r["id"].startswith("CW") else "DDS"
        pages = []
        for s in r["sources"]:
            pages += _pages(s) or (INSTRUMENT_PAGES[c] if "instrument" in s else [])
        out.append({"id": r["id"], "kind": "rule", "contract": c, "pages": pages,
                    "documents": [s for s in r["sources"] if "guideline" in s or "README" in s],
                    "content": rule_content(r), "sha": sl.canonical_hash(rule_content(r))})
    for it in out:
        it["pages"] = sorted(set(it["pages"]))
    return out


def _pages(text: str) -> list[int]:
    """Page numbers cited in a provision string: 'p8', '(pp4, 11)', '(pp17-19)', 'p1 particulars'."""
    pages = []
    for m in re.finditer(r"\bpp?\.?\s?(\d+(?:\s*[-–,]\s*\d+)*)", text):
        for part in re.split(r"\s*,\s*", m.group(1)):
            a, _, b = part.partition("-")
            pages += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return pages


def statement(it: dict, rules: dict, params: dict) -> dict:
    """What a reader is shown for an item: the full entry minus internal cross-references (checked by verify_spec)."""
    stmt = dict(it["content"])
    if it["kind"] == "rule":
        stmt["parameter_values"] = {p: params[p]["value"] for p in rules[it["id"]].get("parameters", [])}
        for k in ("checks", "prerequisites", "consequence", "tables", "instruments", "overrides", "questions",
                  "status", "pricing", "parameters"):
            stmt.pop(k, None)
    return stmt


def _refs():
    rules = {r["id"]: r for r in sl.load_yaml(sl.SPEC / "rules.yaml")["rules"]}
    params = {p["id"]: p for c in ("CW", "DDS") for p in sl.load_terms(c)["parameters"]}
    return rules, params


def packet(out_dir: Path, groups: dict[str, list[str]]) -> None:
    """Write one reading packet per reader group (ids chosen by prefix/range)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rules, params = _refs()
    by_id = {it["id"]: it for it in items()}
    for g, ids in groups.items():
        lines = []
        for i in ids:
            it = by_id[i]
            stmt = statement(it, rules, params)
            lines.append(json.dumps({"id": i, "kind": it["kind"], "contract": it["contract"], "pages": it["pages"],
                                     "documents": it.get("documents", []), "statement": stmt}, ensure_ascii=False))
        (out_dir / f"packet_{g}.jsonl").write_text("\n".join(lines) + "\n")
        print(f"packet {g}: {len(ids)} items, pages {sorted({p for i in ids for p in by_id[i]['pages']})}")


# ----------------------------------------------------------------------------- recording
def norm(s: str) -> str:
    s = s.lower().replace("—", "-").replace("–", "-").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"[^a-z0-9%./:-]+", " ", s).strip()


def ocr_ratio(contract: str, page: int, quote: str) -> float:
    """Best similarity of the quote to any same-length window of the page's OCR text (0..1)."""
    f = OCR / contract.lower() / f"p{page:02d}.txt"
    if not f.exists():
        return 0.0
    text, q = norm(f.read_text()), norm(quote)
    if not q:
        return 0.0
    if q in text:
        return 1.0
    words, qw = text.split(), q.split()
    n, best = len(qw), 0.0
    for i in range(0, max(1, len(words) - n + 1)):
        cand = " ".join(words[i:i + n])
        r = SequenceMatcher(None, q, cand, autojunk=False).ratio()
        if r > best:
            best = r
            if best == 1.0:
                break
    return round(best, 3)


def snapshot_path(path: str):
    """A reader's document path, recorded relative to the pinned input snapshot, resolved on THIS checkout's snapshot."""
    import snapshot
    p = Path(path)
    return None if p.is_absolute() else Path(snapshot.DEFAULT_SNAPSHOT) / p


def document_match(path: str, quote: str) -> float:
    """A quote from a text document (guidelines, README) must be in it verbatim (whitespace/markup normalised)."""
    p = snapshot_path(path)
    if p is None or not p.exists():
        return 0.0
    return 1.0 if norm(quote.replace("*", "")) in norm(p.read_text().replace("*", "")) else 0.0


def printed_equivalent(value: str, printed: str | None) -> bool | None:
    """Does the value as printed on the scan mean the parameter value? None = not machine-comparable (text)."""
    if printed is None:
        return None
    p = printed.lower().replace(",", "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        m = re.search(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})", p)
        if not m or m.group(2) not in MONTHS:
            return False
        return dt.date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1))).isoformat() == value
    if re.fullmatch(r"[\d.,/]+", value):
        nums = re.findall(r"\d+(?:\.\d+)?(?:/\d+)?", p)
        words = {"one": "1", "two": "2", "five": "5", "six": "6", "half": "1/2", "one half": "1/2"}
        nums += [v for k, v in words.items() if re.search(rf"\b{k}\b", p)]
        target = value.replace(",", "")
        return any(n == target or (("/" not in n and "/" not in target) and Decimal(n) == Decimal(target)) for n in nums)
    return None


def record(recertify: list[str]) -> int:
    readings = {}
    for f in sorted(READINGS.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                r["_file"] = f"verification/param_rule_readings/{f.name}"
                readings[r["id"]] = r
    dispo = sl.load_yaml(DISPOSITIONS)["dispositions"] if DISPOSITIONS.exists() else {}
    shown = {}
    for f in sorted((sl.VERIF / "param_rule_packets").glob("packet_*.jsonl")):
        for line in f.read_text().splitlines():
            x = json.loads(line)
            shown[(f.stem.split("_", 1)[1], x["id"])] = x["statement"]
    rules, params = _refs()
    prev = sl.load_yaml(LOG)["entries"] if LOG.exists() else None
    entries = {}
    for it in items():
        i = it["id"]
        r = readings.get(i)
        e = {"kind": it["kind"], "contract": it["contract"], "pages": it["pages"], "content_sha256": it["sha"]}
        if r is None:
            e["status"] = "pending"
            e["reason"] = "no second reading"
            entries[i] = e
            continue
        quotes = []
        for q in r.get("quotes", []):
            ratio = ocr_ratio(it["contract"], q["page"], q["text"]) if isinstance(q.get("page"), int) \
                else document_match(q.get("document") or "", q["text"])
            quotes.append({"page": q.get("page"), "document": q.get("document"), "text": q["text"], "ocr_match": ratio})
        e["reading"] = {"reader": r.get("reader"), "file": r["_file"], "verdict": r["verdict"],
                        "discrepancies": r.get("discrepancies") or None, "pages_read": r.get("pages_read")}
        if it["kind"] == "parameter":
            e["reading"]["printed_value"] = r.get("printed_value")
            e["value_equivalent"] = printed_equivalent(it["content"]["value"], r.get("printed_value"))
        e["quotes"] = quotes
        e["read_current_statement"] = shown.get((r.get("reader"), i)) == statement(it, rules, params)
        e["reading"]["source_labels_ok"] = r.get("source_labels_ok", True)
        cited = set(it["pages"])
        quoted = {q["page"] for q in quotes if isinstance(q.get("page"), int)}
        e["cited_pages_without_quote"] = sorted(cited - quoted)
        e["weak_ocr_quotes"] = [q["page"] or q["document"] for q in quotes if q["ocr_match"] is not None and q["ocr_match"] < QUOTE_MIN_RATIO]
        d = dispo.get(i)
        if d:
            e["disposition"] = d
        needs = r["verdict"] != "supported" or e["cited_pages_without_quote"] or e["weak_ocr_quotes"] \
            or e.get("value_equivalent") is False or not e["read_current_statement"] or r.get("source_labels_ok") is False
        ok = not needs or (d and d.get("content_sha256") == it["sha"])
        if not ok:
            e["status"] = "pending"
        elif prev is None or i in recertify:
            e["status"] = "verified"
        else:
            old = prev.get(i)
            e["status"] = "verified" if old and old.get("content_sha256") == it["sha"] and old.get("status") == "verified" \
                else "pending"
        entries[i] = e
    out = {
        "generated_by": "tools/param_rule_verification.py record",
        "method": "Second verification of every parameter and rule against the scan images by independent readers "
                  "who did not see the specification files, reports or "
                  "earlier readings: each item's statement was checked against the cited pages and supported with verbatim "
                  "quotes. Every quote is machine-matched to the OCR text of its page; a parameter's value as printed is "
                  "machine-compared with the specification value. Non-supported verdicts, unquoted cited pages and weak OCR "
                  "matches are settled on the scan image and recorded in verification/param_rule_dispositions.yaml, bound "
                  "to the item's content hash.",
        "entries": entries,
    }
    LOG.write_text(yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=120))
    pend = [i for i, e in entries.items() if e["status"] != "verified"]
    kinds = {k: sum(1 for e in entries.values() if e["kind"] == k) for k in ("parameter", "rule")}
    print(f"wrote {LOG.name}: {kinds}; verified {len(entries) - len(pend)}; pending {pend or 'none'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("packet")
    p.add_argument("--out", required=True)
    p.add_argument("--group", help="write one packet with --ids (a re-reading after corrections)")
    p.add_argument("--ids", nargs="*", default=[])
    r = sub.add_parser("record")
    r.add_argument("--recertify", nargs="*", default=[])
    a = ap.parse_args()
    if a.cmd == "packet" and a.group:
        packet(Path(a.out), {a.group: a.ids})
        return 0
    if a.cmd == "packet":
        its = items()
        ids = lambda pred: [x["id"] for x in its if pred(x)]  # noqa: E731
        rn = lambda x: int(x["id"].split("-R")[1]) if x["kind"] == "rule" else 0  # noqa: E731
        groups = {
            "A": ids(lambda x: x["contract"] == "CW" and x["kind"] == "parameter"),
            "B": ids(lambda x: x["contract"] == "CW" and x["kind"] == "rule" and rn(x) <= 12),
            "C": ids(lambda x: x["contract"] == "CW" and x["kind"] == "rule" and rn(x) > 12),
            "D": ids(lambda x: x["contract"] == "DDS" and x["kind"] == "parameter"),
            "E": ids(lambda x: x["contract"] == "DDS" and x["kind"] == "rule" and rn(x) <= 11),
            "F": ids(lambda x: x["contract"] == "DDS" and x["kind"] == "rule" and rn(x) > 11),
        }
        packet(Path(a.out), groups)
        return 0
    return record(a.recertify)


if __name__ == "__main__":
    sys.exit(main())
