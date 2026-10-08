"""Independent semantic review of G2 derived meanings (review finding 3).

The raw-field blind annotation (tools/compare_blind_g2.py) proves transcription only. This review checks
the MEANINGS the parser and linker derive, against readers who work from the raw record text and the contract
scans alone:

  ddr    per sampled report: every tool term in the hole / in the run -> service code, every crew term ->
         service code and count, the lost tool -> LH code (Appendix G, loss context);
  lines  per sampled invoice line: whether Schedule 8 charges the service per day with the tool in the hole,
         which service's Appendix G tool evidences it, whether the cited report shows that tool, the
         Schedule 5 part the service needs and whether the report has it, the crew count for a personnel
         service, the lost tool's code for an LH line;
  civil  per sampled record: the measured quantity and unit, every number/attribute of the narrative BY NAME,
         and the Schedule 1 item(s) the work can evidence.

`sample` fixes the population (seeded), `packet` writes the readers' inputs, `compare` checks completeness
(every sampled id annotated, nothing extra, every field present) and every field by name. The result is
written to verification/g2/semantic/comparison.json. A disagreement passes only with a recorded disposition
(verification/g2/semantic/dispositions.yaml) naming both values and the raw evidence.

Usage::  python tools/semantic_review_g2.py sample|packet|compare
"""
from __future__ import annotations

import csv
import hashlib
import json
import random
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from audit.common import SNAPSHOT  # noqa: E402

DIR = ROOT / "verification" / "g2" / "semantic"
BLIND_SAMPLE = ROOT / "verification" / "g2" / "blind" / "sample.json"
SEED = 20260926
LINES_CSV = "drilling_services/invoices/invoice_lines.csv"
# Lines singled out by review and by the G2 report; always reviewed in addition to the stratified draw.
MUST_LINES = ["MDS-00022-030", "MDS-00287-082", "MDS-01043-030", "MDS-01268-014", "MDS-00954-021",
              "MDS-00876-062", "MDS-01393-036", "MDS-00001-002"]
FIELDS = {
    "ddr": ["file", "in_the_hole", "tools_in_run", "crew", "lost_tool"],
    "lines": ["line_ref", "tool_day_service", "tool_code", "tool_in_hole", "required_part", "required_part_present",
              "crew_recorded", "lost_tool_code"],
    "civil": ["ticket", "quantity", "unit", "attributes", "candidate_items"],
}
ID_KEY = {"ddr": "file", "lines": "line_ref", "civil": "ticket"}


def sample() -> dict:
    blind = json.loads(BLIND_SAMPLE.read_text())
    with (SNAPSHOT / LINES_CSV).open(newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["report_ref"]]
    by_code: dict[str, list[str]] = {}
    for r in rows:
        by_code.setdefault(r["service_code"], []).append(r["line_ref"])
    rng = random.Random(SEED)
    lines = []
    for code in sorted(by_code):
        lines += rng.sample(sorted(by_code[code]), min(2, len(by_code[code])))
    lines = sorted(set(lines) | set(MUST_LINES))
    s = {"seed": SEED, "method": "civil and drilling: the G2 blind sample (seed 20260925); lines: 2 per service code "
         "with a report reference (seeded draw) plus the lines singled out by review and by the G2 report",
         "civil": blind["civil"], "ddr": blind["drilling"], "lines": lines, "must_lines": MUST_LINES}
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / "sample.json").write_text(json.dumps(s, indent=1) + "\n")
    return s


def packet() -> None:
    s = json.loads((DIR / "sample.json").read_text())
    pk = DIR / "packets"
    pk.mkdir(parents=True, exist_ok=True)
    (pk / "ddr.jsonl").write_text("".join(json.dumps({"file": f, "path": f"drilling_services/records/{f}"}) + "\n"
                                          for f in s["ddr"]))
    # Civil packet carries only the record title and the narrative line (no names or signatures): the reviewer
    # needs the site's wording, nothing personal.
    civ = []
    for t in s["civil"]:
        text = (SNAPSHOT / f"civilwork/records/{t}.txt").read_text().splitlines()
        body = [x for x in text[1:] if x.strip() and ":" not in x]
        civ.append(json.dumps({"ticket": t, "title": text[0].strip(), "narrative": body[0].strip() if len(body) == 1 else body}))
    (pk / "civil.jsonl").write_text("\n".join(civ) + "\n")
    with (SNAPSHOT / LINES_CSV).open(newline="") as fh:
        raw = fh.read().splitlines()
    header, by_ref = raw[0], {ln.split(",", 1)[0]: ln for ln in raw[1:]}
    rec = {}
    for f in (SNAPSHOT / "drilling_services/records").iterdir():
        head = f.read_text().splitlines()[1]          # "Report: DDR-..."
        rec[head.split(": ", 1)[1]] = str(f.relative_to(SNAPSHOT))   # relative to the input snapshot
    half = (len(s["lines"]) + 1) // 2
    for name, part in (("lines_1", s["lines"][:half]), ("lines_2", s["lines"][half:])):
        out = []
        for ref in part:
            row = by_ref[ref]
            report = next(csv.reader([row]))[-1]
            out.append(json.dumps({"line_ref": ref, "csv_header": header, "csv_row": row, "report_file": rec.get(report)}))
        (pk / f"{name}.jsonl").write_text("\n".join(out) + "\n")
    print({p.name: sum(1 for _ in p.open()) for p in sorted(pk.iterdir())})


# ----------------------------------------------------------------------------- comparison
def _load(kind: str) -> list[dict]:
    out = []
    for f in sorted(DIR.glob(f"{kind}*_review.jsonl")):
        out += [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
    return out


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def completeness(kind: str, annotations: list[dict], expected: list[str]) -> list[str]:
    """Every sampled id annotated exactly once with every field; nothing outside the sample."""
    errs = []
    ids = [a.get(ID_KEY[kind]) for a in annotations]
    for i in sorted(set(expected) - set(ids)):
        errs.append(f"{kind}: sampled {i} has no annotation")
    for i in sorted(set(ids) - set(expected)):
        errs.append(f"{kind}: {i} annotated but not in the sample")
    for i in sorted({i for i in ids if ids.count(i) > 1}):
        errs.append(f"{kind}: {i} annotated more than once")
    for a in annotations:
        miss = [k for k in FIELDS[kind] if k not in a]
        if miss:
            errs.append(f"{kind}: {a.get(ID_KEY[kind])} lacks fields {miss}")
    return errs


def compare_ddr(annotations, by_file) -> list[dict]:
    out = []
    for a in annotations:
        d = by_file.get(a["file"])
        if d is None:
            continue
        checks = {}
        for fld, parsed in (("in_the_hole", d.tools_in_hole), ("tools_in_run", d.tools_in_run)):
            for term in sorted(set(a.get(fld) or {}) | set(parsed)):
                checks[f"{fld}:{term}"] = ((a.get(fld) or {}).get(term), parsed.get(term))
        for code in sorted(set(a.get("crew") or {}) | set(d.crew)):
            checks[f"crew:{code}"] = ((a.get("crew") or {}).get(code), d.crew.get(code))
        lost = a.get("lost_tool") or {}
        checks["lost_tool:term"] = (lost.get("term"), d.lost_tool_term)
        checks["lost_tool:code"] = (lost.get("code"), d.lost_tool_code)
        out += [{"id": a["file"], "field": k, "review": r, "parser": p, "agree": r == p} for k, (r, p) in checks.items()]
    return out


def compare_lines(annotations, dds_links) -> list[dict]:
    out = []
    for a in annotations:
        l = dds_links.get(a["line_ref"])
        if l is None:
            continue
        s = l.semantic
        checks = {"tool_day_service": (a["tool_day_service"], "tool_in_hole" in s)}
        if a["tool_day_service"] or "tool_in_hole" in s:
            checks["tool_code"] = (a["tool_code"], s.get("tool_basis_code"))
            checks["tool_in_hole"] = (a["tool_in_hole"], s.get("tool_in_hole"))
        checks["required_part"] = (a["required_part"], s.get("required_part"))
        checks["required_part_present"] = (a["required_part_present"], s.get("required_part_present"))
        checks["crew_recorded"] = (a["crew_recorded"], s.get("crew_recorded"))
        checks["lost_tool_code"] = (a["lost_tool_code"], s.get("lost_tool_code"))
        out += [{"id": a["line_ref"], "field": k, "review": r, "parser": p, "agree": r == p} for k, (r, p) in checks.items()]
    return out


def compare_civil(annotations, recs) -> list[dict]:
    out = []
    for a in annotations:
        r = recs.get(a["ticket"])
        if r is None:
            continue
        checks = {"quantity": (a["quantity"], str(r.quantity) if r.quantity is not None else None), "unit": (a["unit"], r.unit),
                  "candidate_items": (sorted(a["candidate_items"] or []), sorted(r.candidates or []))}
        attrs = a.get("attributes") or {}
        for name in sorted(set(attrs) | set(r.attributes)):
            checks[f"attribute:{name}"] = (None if attrs.get(name) is None else str(attrs[name]),
                                           None if r.attributes.get(name) is None else str(r.attributes[name]))
        out += [{"id": a["ticket"], "field": k, "review": rv, "parser": p, "agree": rv == p} for k, (rv, p) in checks.items()]
    return out


def run(world, annotations: dict[str, list[dict]] | None = None, sample_ids: dict | None = None) -> dict:
    """Compare everything; returns {kind: {...}, 'failures': [...]}. Arguments allow negative controls."""
    s = sample_ids or json.loads((DIR / "sample.json").read_text())
    ann = annotations or {k: _load(k) for k in FIELDS}
    dispo_path = DIR / "dispositions.yaml"
    dispo = (yaml.safe_load(dispo_path.read_text()) or {}).get("dispositions", {}) if dispo_path.exists() else {}
    res, failures = {}, []
    for kind, fn, arg in (("ddr", compare_ddr, world.ddr_by_file), ("lines", compare_lines, world.dds_links),
                          ("civil", compare_civil, world.cw)):
        comp = fn(ann[kind], arg)
        errs = completeness(kind, ann[kind], s[kind])
        for x in comp:
            if not x["agree"]:
                d = dispo.get(f"{x['id']}|{x['field']}")
                if d and d.get("review") == x["review"] and d.get("parser") == x["parser"]:
                    x["disposition"] = d["disposition"]
                else:
                    errs.append(f"{kind} {x['id']} {x['field']}: review {x['review']!r} parser {x['parser']!r}")
        res[kind] = {"annotated": len({a.get(ID_KEY[kind]) for a in ann[kind]}), "sampled": len(s[kind]),
                     "fields": len(comp), "agree": sum(x["agree"] for x in comp),
                     "disposed": sum(1 for x in comp if "disposition" in x),
                     "annotations_sha": _sha(ann[kind]), "disagreements": [x for x in comp if not x["agree"]]}
        failures += errs
    res["failures"] = failures
    return res


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "compare"
    if cmd == "sample":
        s = sample()
        print({k: len(v) for k, v in s.items() if isinstance(v, list)})
        return 0
    if cmd == "packet":
        packet()
        return 0
    from audit import build
    res = run(build.build())
    (DIR / "comparison.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
    for k in FIELDS:
        r = res[k]
        print(f"{k}: {r['annotated']}/{r['sampled']} sampled annotated; {r['agree']}/{r['fields']} fields agree; disposed {r['disposed']}")
    for f in res["failures"]:
        print("   FAIL", f)
    return 1 if res["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
