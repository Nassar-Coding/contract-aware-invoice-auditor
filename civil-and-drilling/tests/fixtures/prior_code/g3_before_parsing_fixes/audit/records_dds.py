"""Parse daily drilling reports (G2) against spec/evidence_dds.yaml and the verified Appendix G table.

Per file: header (Report, Contract, Well, Rig, Date), each present Part as typed values, tool terms mapped
to services (the LH-7xx code only in Part E), crew counts mapped to personnel services, and the two
signatures with placeholder detection. Reports are indexed by the internal Report number, never by the
file name. Missing or unknown elements are queued; contradictions inside a report become conflicts.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from .common import SNAPSHOT, Queue, Source, dmy_mon, is_signature, spec_lib

SPEC = spec_lib.load_yaml(spec_lib.SPEC / "evidence_dds.yaml")
GLOSSARY = spec_lib.load_terms("DDS")["tables"]["DDS.T20_GLOSSARY"]["rows"]
TERM_TO_CODES: dict[str, list[str]] = {}
for _code, _desc, _term in GLOSSARY:
    TERM_TO_CODES.setdefault(_term, []).append(_code)
CREW = SPEC["crew_terms"]
TOOL_TERMS = {t for t in TERM_TO_CODES if t not in CREW}
ENUMS = {"hole_sections": set(SPEC["hole_sections"]), "statuses": set(SPEC["statuses"])}
PART_BY_TITLE = {v["title"]: k for k, v in SPEC["parts"].items()}
KEY_RE = re.compile(r"^(?P<k>[^:]+):\s?(?P<v>.*)$")
CREW_RE = re.compile(r"^(?P<n>\d+) (?P<term>.+)$")
SIG = SPEC["signatures"]["keys"]


def service_code(term: str, part: str) -> str | None:
    """Appendix G term -> code. Loss context (Part E) takes the LH-7xx code; any other part the non-loss one."""
    codes = [c for c in TERM_TO_CODES.get(term, []) if c.startswith("LH-") == (part == "E")]
    return codes[0] if len(codes) == 1 else None


def part_keys(p: str) -> list[str]:
    s = SPEC["parts"][p]
    return (list(s.get("int_keys", [])) + list(s.get("date_keys", [])) + list(s.get("bool_keys", []))
            + list(s.get("text_keys", [])) + list(s.get("enum_keys", {})) + list(s.get("list_keys", {}))
            + list(s.get("term_keys", {})))


@dataclass
class Ddr:
    file: str
    path: str
    report: str | None = None
    contract: str | None = None
    well: str | None = None
    rig: str | None = None
    date: dt.date | None = None
    parts: dict[str, dict] = field(default_factory=dict)
    tools_in_hole: dict[str, str | None] = field(default_factory=dict)   # term -> code
    tools_in_run: dict[str, str | None] = field(default_factory=dict)
    crew: dict[str, int] = field(default_factory=dict)                   # code -> count
    crew_terms: dict[str, int] = field(default_factory=dict)             # term as written -> count
    lost_tool_term: str | None = None
    lost_tool_code: str | None = None
    company_rep: str | None = None
    lead_dd: str | None = None
    company_signed: bool = False
    driller_signed: bool = False
    signatures_after_last_part: bool = True
    spans: dict = field(default_factory=dict)

    ctx: str | None = None                      # run context id (audit.provenance)

def _typed(part: str, key: str, value: str, ident: str, src: Source, q: Queue):
    s = SPEC["parts"][part]
    try:
        if key in s.get("int_keys", []):
            if not re.fullmatch(r"\d+", value):
                raise ValueError(value)
            return int(value)
        if key in s.get("date_keys", []):
            return dmy_mon(value)
        if key in s.get("bool_keys", []):
            if value not in ("Yes", "No"):
                raise ValueError(value)
            return value == "Yes"
        if key in s.get("enum_keys", {}):
            if value not in ENUMS[s["enum_keys"][key]]:
                raise ValueError(value)
            return value
    except ValueError:
        return q.add("ddr", ident, f"{part}.{key}", f"unparseable or unknown value {value!r}", src)
    return value


def parse_file(rel: str, text: str, q: Queue) -> Ddr:
    lines = text.split("\n")
    fname = rel.rsplit("/", 1)[-1]
    d = Ddr(file=fname, path=rel)
    ident = fname
    if lines[0].strip() != SPEC["header"]["title"]:
        q.add("ddr", ident, "title", f"unexpected title {lines[0]!r}", Source(rel, 1, lines[0]))
    part = "HEAD"
    last_part_line = 0
    for n, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        src = Source(rel, n, line)
        if line.startswith("PART "):
            p = PART_BY_TITLE.get(line.strip())
            if p is None:
                q.add("ddr", ident, "part", f"unknown part heading {line!r}", src)
                part = "UNKNOWN"
            else:
                if p in d.parts:
                    q.add("ddr", ident, p, "part repeated", src)
                part, d.parts[p] = p, {}
                d.spans[p] = src
            last_part_line = n
            continue
        m = KEY_RE.match(line)
        if not m:
            q.add("ddr", ident, "line", f"unrecognised line {line!r}", src)
            continue
        k, v = m["k"], m["v"]
        if k in SIG.values():
            if k == SIG["company"]:
                d.company_rep, d.company_signed = v, is_signature(v)
            else:
                d.lead_dd, d.driller_signed = v, is_signature(v)
            d.spans[k] = src
            if n < last_part_line:
                d.signatures_after_last_part = False
            continue
        if part == "HEAD":
            if k not in SPEC["header"]["keys"]:
                q.add("ddr", ident, k, "unknown header key", src)
                continue
            d.spans[k] = src
            if k == "Date":
                try:
                    d.date = dmy_mon(v)
                except ValueError:
                    q.add("ddr", ident, "Date", f"unparseable {v!r}", src)
            else:
                setattr(d, {"Report": "report", "Contract": "contract", "Well": "well", "Rig": "rig"}[k], v)
            continue
        if part not in SPEC["parts"]:
            continue
        if k not in part_keys(part):
            q.add("ddr", ident, f"{part}.{k}", "unknown key", src)
            continue
        if k in d.parts[part]:
            q.add("ddr", ident, f"{part}.{k}", "key repeated", src)
        d.spans[f"{part}.{k}"] = src
        if k in ("In the hole", "Tools in run"):
            terms = [t.strip() for t in v.split(",") if t.strip()]
            target = d.tools_in_hole if k == "In the hole" else d.tools_in_run
            for t in terms:
                code = service_code(t, part) if t in TOOL_TERMS else None
                if code is None:
                    q.add("ddr", ident, f"{part}.{k}", f"term {t!r} is not an Appendix G tool term", src)
                target[t] = code
            d.parts[part][k] = terms
        elif k == "Crew on tour":
            for c in [c.strip() for c in v.split(",") if c.strip()]:
                cm = CREW_RE.match(c)
                code = CREW.get(cm["term"]) if cm else None
                if code is None:
                    q.add("ddr", ident, "A.Crew on tour", f"unrecognised crew entry {c!r}", src)
                    continue
                d.crew_terms[cm["term"]] = d.crew_terms.get(cm["term"], 0) + int(cm["n"])
                d.crew[code] = d.crew.get(code, 0) + int(cm["n"])
            d.parts[part][k] = v
        elif k == "Lost in hole tool":
            d.lost_tool_term = v
            d.lost_tool_code = service_code(v, "E") if v in TOOL_TERMS else None
            if d.lost_tool_code is None:
                q.add("ddr", ident, "E.Lost in hole tool", f"term {v!r} has no LH code in Appendix G", src)
            d.parts[part][k] = v
        else:
            d.parts[part][k] = _typed(part, k, v, ident, src, q)

    for k in SPEC["header"]["keys"]:
        if k not in d.spans:
            q.add("ddr", ident, k, "header line missing", None)
    if "A" not in d.parts:
        q.add("ddr", ident, "A", "Part A missing (required every day)", None)
    if "B" not in d.parts:
        q.add("ddr", ident, "B", "Part B missing", None)
    for p, vals in d.parts.items():
        for k in part_keys(p):
            if k not in vals and not q.has(ident, f"{p}.{k}"):
                q.add("ddr", ident, f"{p}.{k}", "key missing in present part", None)
    for k in SIG.values():
        if k not in d.spans:
            q.add("ddr", ident, k, "signature line missing", None)
    _consistency(d, q, ident)
    return d


def _consistency(d: Ddr, q: Queue, ident: str) -> None:
    """Contradictions inside one report (P2.5 audit §6.1: contradictory evidence stays visible)."""
    a, b, c, dd, e = (d.parts.get(p, {}) for p in "ABCDE")
    if d.report and d.well and d.date:
        if d.file != f"DDR_{d.well}_{d.date:%Y%m%d}.txt":
            q.conflict("ddr", ident, "filename_vs_header", d.file)
        exp = f"DDR-{d.well.rsplit('-', 1)[-1]}-{d.date:%Y%m%d}"
        if d.report != exp:
            q.conflict("ddr", ident, "report_number_vs_well_date", f"{d.report} vs {exp}")
    if a and b and a.get("BHA run") != b.get("Run"):
        q.conflict("ddr", ident, "A_run_vs_B_run", f"{a.get('BHA run')} vs {b.get('Run')}")
    if a and b and a.get("In the hole") != b.get("Tools in run"):
        q.conflict("ddr", ident, "A_tools_vs_B_tools", "")
    if b and d.date and isinstance(b.get("Run first day"), dt.date) and isinstance(b.get("Run last day"), dt.date):
        if not (b["Run first day"] <= d.date <= b["Run last day"]):
            q.conflict("ddr", ident, "report_date_outside_run", str(d.date))
    if a and isinstance(a.get("Depth end (m MD)"), int) and isinstance(a.get("Depth start (m MD)"), int):
        if a["Depth end (m MD)"] < a["Depth start (m MD)"]:
            q.conflict("ddr", ident, "depth_decreases", "")
        if a.get("Status") == "Standby" and a["Depth end (m MD)"] != a["Depth start (m MD)"]:
            q.conflict("ddr", ident, "standby_with_depth_change", "")
    for key in ("Circulating hours", "Back-reaming hours"):
        if isinstance(a.get(key), int) and a[key] > 24:
            q.conflict("ddr", ident, f"{key}_over_24", str(a[key]))
    if isinstance(a.get("Gyro surveys"), int):
        if c and c.get("Gyro surveys taken") != a["Gyro surveys"]:
            q.conflict("ddr", ident, "A_gyro_vs_C", f"{a['Gyro surveys']} vs {c.get('Gyro surveys taken')}")
        if not c and a["Gyro surveys"] > 0:
            q.conflict("ddr", ident, "gyro_surveys_without_part_C", str(a["Gyro surveys"]))
        if c and a["Gyro surveys"] == 0:
            q.conflict("ddr", ident, "part_C_without_gyro_surveys", "")
    if c and a and c.get("Surveyed section") != a.get("Hole section"):
        q.conflict("ddr", ident, "C_section_vs_A", "")
    if dd and b:
        if dd.get("Source run") != b.get("Run"):
            q.conflict("ddr", ident, "D_run_vs_B_run", "")
        if b.get("Radioactive source carried") is False:
            q.conflict("ddr", ident, "part_D_but_no_source_carried", "")
    if e and b:
        if e.get("Lost in hole run") != b.get("Run"):
            q.conflict("ddr", ident, "E_run_vs_B_run", "")
        if d.lost_tool_term and d.lost_tool_term not in (b.get("Tools in run") or []):
            q.conflict("ddr", ident, "lost_tool_not_in_run", d.lost_tool_term)
    if d.contract and d.contract != "DDS-2025-118":
        q.conflict("ddr", ident, "contract_reference", d.contract)


def load(snapshot=SNAPSHOT, q: Queue | None = None) -> tuple[dict[str, Ddr], dict[str, Ddr], Queue]:
    """Return (by internal report number, by file name, queue). Indexing is by the Report line."""
    q = q or Queue()
    by_report: dict[str, Ddr] = {}
    by_file: dict[str, Ddr] = {}
    for p in sorted((snapshot / "drilling_services" / "records").iterdir()):
        d = parse_file(f"drilling_services/records/{p.name}", p.read_text(encoding="utf-8"), q)
        by_file[p.name] = d
        if d.report is None:
            q.add("ddr", p.name, "Report", "no report number; not indexable", None)
        elif d.report in by_report:
            q.add("ddr", p.name, "Report", f"duplicate report number {d.report} (also {by_report[d.report].file})", None)
        else:
            by_report[d.report] = d
    return by_report, by_file, q
