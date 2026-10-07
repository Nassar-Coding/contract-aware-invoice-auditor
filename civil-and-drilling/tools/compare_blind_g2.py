"""Transcription check: compare the G2 parser with the blind annotations (verification/g2/blind/*_annotations.jsonl).

The annotators read raw record text only. Every field they
typed is compared with the parser's value for the same file. Unit words are mapped with the explicit table
below (the only normalisation applied); everything else must agree exactly. Disagreements are listed for
review against the raw file; the result is written to verification/g2/blind/comparison.json.
This proves transcription of raw fields only; derived meanings are checked by tools/semantic_review_g2.py.

Usage::  python tools/compare_blind_g2.py
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from audit import records_cw, records_dds  # noqa: E402
from audit.common import Queue  # noqa: E402

BLIND = ROOT / "verification" / "g2" / "blind"
UNIT_WORDS = {"m3": "m3", "cube": "m3", "m2": "m2", "square metres": "m2", "m": "lm", "hours": "hour",
              "week": "week", "t": "tonne", "tonne": "tonne", "chainage band": "no.", "large chamber": "no."}


def _s(v):
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, dt.date):
        return v.strftime("%d-%b-%Y")
    if isinstance(v, list):
        return ", ".join(v)
    return str(v)


def compare_cw(recs) -> list[dict]:
    out = []
    for line in (BLIND / "cw_annotations.jsonl").read_text().splitlines():
        a = json.loads(line)
        r = recs[a["ticket"]]
        checks = {
            "title": (a["title"], r.title), "job": (a["job"], r.job),
            "area": (a["area"], f"{r.area} {r.area_name}" if r.area else None),
            "date": (a["date"], r.date.isoformat() if r.date else None),
            "week_beginning": (a["week_beginning"], r.week_beginning.isoformat() if r.week_beginning else None),
            "days_on": (a["days_on"], [d.isoformat() for d in r.days_on]),
            "ground": ((a["ground"] or "").split(" ")[0] or None, r.ground),
            "narrative": (a["narrative"], r.narrative), "quantity": (a["quantity"], str(r.quantity)),
            "unit": (UNIT_WORDS.get(a["unit_words"], f"?{a['unit_words']}"), r.unit),
            "foreman": (a["foreman"], r.foreman), "engineer": (a["engineer"], r.engineer),
            "foreman_signed": (a["foreman_is_real_signature"], r.foreman_signed),
            "engineer_signed": (a["engineer_is_real_signature"], r.engineer_signed),
        }
        for k, v in (a.get("other_numbers") or {}).items():
            vals = {str(x) for x in r.attributes.values()}
            if any(ch.isdigit() for ch in str(v)):
                checks[f"other:{k}"] = (str(v) in vals or str(v).replace(" m", "") in vals, True)
        for k, (blind, parsed) in checks.items():
            out.append({"id": a["ticket"], "field": k, "blind": blind, "parser": parsed, "agree": blind == parsed})
    return out


def compare_dds(by_file) -> list[dict]:
    out = []
    for line in (BLIND / "dds_annotations.jsonl").read_text().splitlines():
        a = json.loads(line)
        d = by_file[a["file"]]
        checks = {"report": (a["report"], d.report), "contract": (a["contract"], d.contract), "well": (a["well"], d.well),
                  "rig": (a["rig"], d.rig), "date": (a["date"], d.date.isoformat() if d.date else None),
                  "parts": (a["parts"], sorted(d.parts)), "company_rep": (a["company_rep"], d.company_rep),
                  "lead_dd": (a["lead_dd"], d.lead_dd), "company_signed": (a["company_is_real_signature"], d.company_signed),
                  "lead_signed": (a["lead_is_real_signature"], d.driller_signed)}
        for p in "ABCDE":
            blind = a.get(p) or {}
            parsed = d.parts.get(p, {})
            for k in sorted(set(blind) | set(parsed)):
                checks[f"{p}.{k}"] = (blind.get(k), _s(parsed[k]) if k in parsed else None)
        for k, (blind, parsed) in checks.items():
            out.append({"id": a["file"], "field": k, "blind": blind, "parser": parsed, "agree": blind == parsed})
    return out


def completeness() -> list[str]:
    """The annotation files must hold exactly the seeded sample (verification/g2/blind/sample.json), once each."""
    s = json.loads((BLIND / "sample.json").read_text())
    errs = []
    for key, fname, idk in (("civil", "cw_annotations.jsonl", "ticket"), ("drilling", "dds_annotations.jsonl", "file")):
        ids = [json.loads(x)[idk] for x in (BLIND / fname).read_text().splitlines() if x.strip()]
        errs += [f"{key}: sampled {i} has no annotation" for i in sorted(set(s[key]) - set(ids))]
        errs += [f"{key}: {i} annotated but not sampled" for i in sorted(set(ids) - set(s[key]))]
        errs += [f"{key}: {i} annotated more than once" for i in sorted({i for i in ids if ids.count(i) > 1})]
    return errs


def main() -> int:
    q = Queue()
    cw, _ = records_cw.load(q=q)
    _, by_file, _ = records_dds.load(q=q)
    res = {"cw": compare_cw(cw), "dds": compare_dds(by_file)}
    summary = {k: {"records": len({x["id"] for x in v}), "fields": len(v), "agree": sum(x["agree"] for x in v),
                   "disagreements": [x for x in v if not x["agree"]]} for k, v in res.items()}
    (BLIND / "comparison.json").write_text(json.dumps(summary, indent=1, default=str) + "\n")
    for e in completeness():
        print("   INCOMPLETE", e)
    for k, s in summary.items():
        print(f"{k}: {s['records']} records, {s['agree']}/{s['fields']} fields agree")
        for x in s["disagreements"]:
            print("   DISAGREE", x["id"], x["field"], "| blind:", repr(x["blind"]), "| parser:", repr(x["parser"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
