"""X8: every input G2 can leave empty or unresolved, through both engines and the batch.

The field inventory is G2's own, never a hand list: every column of the four claim files (audit.claims.FILES), every key
of the civil record layout and of the drilling report (spec/evidence_cw.yaml, spec/evidence_dds.yaml), whole report
parts and part headings, keys written twice, and list entries G2 cannot recognise (tool terms, crew entries, days
worked). Evidence documents are mutated as TEXT and re-parsed by the real G2 parsers, so every empty value and every
queue entry is exactly what G2 would hand over; a mutation after which G2 holds an ordinary value (a garbled free-text
'Well: ??' that G2 accepts as a well name) is out of scope and counted as such. Claim fields are emptied as the G2
loader leaves them: typed fields None, text fields blank ('') or None (a short CSV row); the join key also as a header
G2 does not have.

Sample: in every civil and drilling code family of spec/g3_code_families.yaml, the first line of every billed code
(line_ref order), plus the first line showing each input-sensitive feature (night work, each zone, rest day, a
protected submission, a record, each status and hole section, a protected invoice) and each thing the output stage's
decision scopes select on (each finding, alternative dimension, question reading and amount status); a family with no
billed line (codes outside Schedule 1) gets a line built from a real one with its code replaced. For each (line, input, empty value):
  1. an explicit result - no exception and never an engine_error;
  2. no silent default - where the input matters to this line's value (an admissible other value of it changes the
     value: the relevance probe; for an unrecognised list entry, each recognised entry), the result names the input and
     either leaves the value unresolved, or carries every value the input could take (alternatives covering the line
     as billed and each probe), or is not payable by the contract's own rule for that input (REGISTERED);
  3. nothing lost - where the input does not matter to the value, the value is unchanged;
  4. the batch - the production run() over a sub-world holding every sample line, with the input emptied on all of
     them (claim field, header field or document), completes, gives every line a result, no engine_error, and each
     result equals the direct evaluation of the same inputs; the line reference is also emptied (G2 then identifies
     the line by its source position) and repeated between lines (no line's result may replace another's);
  5. the output stage - audit.g3_run's summary, decision scopes and trace sample over each batch's results, with the
     other contract's sample lines alongside, completes; the summary counts every line once by status and compares no
     rate for a line with no value at G3; and a decision scope that reaches such a line lists it as not valued rather
     than counting it (its line count falls when those lines are removed, so it must list at least as many);
  6. coverage - every claim field (line and header) is emptied on at least one line of every code family;
  7. evidence obligations - for an input the contract makes a condition of payment of the line's service
     (obligations(): Schedule 5 Part content, the Part, its heading and signatures for drilling; the Schedule 5 record's
     required lines, narrative, title and signatures for civil - from the specs, never from the engine), emptying it,
     making it unreadable or writing it twice differently must leave a payable line unresolved and named, or apply the
     contract's own consequence; the 'nothing lost' test applies only to inputs without such an obligation.
"""
from __future__ import annotations

import copy
import csv
import datetime as dt
import inspect
import json
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from audit import claims, g3_run, records_cw, records_dds  # noqa: E402
from audit.common import SNAPSHOT, Queue, Unresolved  # noqa: E402
from audit.g3_core import Inputs, headers_by_id, result_keys  # noqa: E402

# the contract's own rule for an absent input (anything else that matters must stay unresolved or carry every value)
REGISTERED = {
    ("CW", "line", "record_ref"): {"record_missing"},
    ("CW", "doc", "Signed (foreman)"): {"record_unsigned"},
    ("CW", "doc", "Countersigned (Engineer's representative)"): {"record_unsigned"},
    ("DDS", "line", "report_ref"): {"report_missing"},
    ("DDS", "doc", "Signed (Company Representative)"): {"report_unsigned"},
    ("DDS", "doc", "Signed (lead directional driller)"): {"report_unsigned"},
    ("DDS", "part", "B"): {"required_part_missing"},
    ("DDS", "part", "C"): {"required_part_missing"},
    ("DDS", "part", "D"): {"required_part_missing"},
    ("DDS", "part", "E"): {"required_part_missing"},
    ("DDS", "doc", "D.Source handling certified"): {"source_handling_not_certified"},
}


def obligations(contract: str, code: str | None) -> set[tuple[str, str]]:
    """Source-derived evidence obligations of a line (FD04 note): the inputs the contract makes a condition
    of payment for this service, whether or not its price uses them. Taken from the specs, never from the engine:
      drilling - Cl.37 (p8), Sch 5 (p24; spec/terms_dds.yaml DDS.T17/T18): the service's Schedule 5 Part completed (every
                 content line of spec/evidence_dds.yaml for that Part, the Part and its heading) in signed form (Cl.15:
                 both signatures);
      civil    - Cl.46-47 (p8), Sch 5 (p27): the Schedule 5 record of the item's series with the lines the record layout
                 requires (spec/evidence_cw.yaml record_layout), its narrative (the item basis), its title (its type) and
                 both signatures.
    An obligated input emptied, unreadable or written twice differently may never leave a payable value standing."""
    if contract == "DDS":
        from audit import g3_dds
        part = g3_dds.terms.dds().sch5.get(code or "")
        if not part:
            return set()
        return ({("doc", f"{part}.{k}") for k in records_dds.part_keys(part)} | {("part", part), ("heading", part)}
                | {("doc", k) for k in records_dds.SIG.values()})
    from audit import g3_cw
    if not g3_cw.terms.cw().records.get(code or ""):
        return set()
    lay = records_cw.SPEC["record_layout"]
    keys = set(lay["required_daily"]) | set(lay["required_weekly"])
    return {("doc", k) for k in keys - {"Ticket", "Job"}} | {("doc", "narrative"), ("doc", "title")}
# the keys by which G2 joins a line to its header or a report to the lines citing it (G2 joins by nothing else;
# records_dds.load indexes by the Report line). Emptied or repeated, the joined document is no longer attributed to the
# line, and G3 never attributes it by its content: the result must still be explicit (named; unresolved, or the
# contract's rule for an absent document), but 'nothing lost' does not apply - every probe is a state in which G2 does
# attribute a document to the line, a state the empty key is not.
JOIN_KEYS = {("CW", "line", "application_no"), ("DDS", "line", "invoice_no"), ("DDS", "doc", "Report"),
             ("CW", "header", "application_no"), ("DDS", "header", "invoice_no")}
KINDS = {"CW": ("cw_lines", "cw_headers"), "DDS": ("dds_lines", "dds_headers")}
HKEY = {"CW": "application_no", "DDS": "invoice_no"}
REF = {"CW": "record_ref", "DDS": "report_ref"}
UNSCHEDULED = {"CW": ("item_code", "Z.99.999"), "DDS": ("service_code", "ZZ-999")}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def claim_fields(kind: str) -> list[str]:
    with (SNAPSHOT / claims.FILES[kind]).open(newline="", encoding="utf-8") as fh:
        return next(csv.reader(fh))


def empties(kind: str, field: str) -> list:
    return [None] if field in claims.TYPES[kind] else ["", None]


def value_of(r) -> tuple:
    """The value of a result: status, payability, rate, quantity, amount and each alternative's - not its wording or trace.
    A line not payable is worth 0.00 whatever rate its rate check found or quantity route it would take: neither is part
    of its value."""
    if r.amount_status == "not_payable":
        return (r.amount_status, r.payable, str(r.amount))
    alts = tuple(sorted((str(k), str(a.get("unit_rate")), str(a.get("allowed_quantity")), str(a.get("amount")))
                        for k, a in r.alternatives.items()))
    return (r.amount_status, r.payable, str(r.unit_rate), str(r.allowed_quantity), str(r.amount), alts, r.family)


def doc_dates(contract: str, doc) -> list:
    """The days the line's own record or report is for: an admissible value of the line's work or service date."""
    if doc is None:
        return []
    if contract == "CW" and doc.week_beginning:
        return [doc.week_beginning + dt.timedelta(days=k) for k in range(7)]
    return [doc.date] if doc.date else []


def names(r) -> str:
    """Everything a result says in words: where an empty input must be named."""
    parts = [f"{c.check} {c.finding} {c.detail}" for c in r.checks] + list(r.reasons) + list(r.readings)
    parts += [f"{c.get('dimension')} {c.get('basis')}" for c in r.conditions]
    return " | ".join(str(x) for x in parts)


def _pairs(r) -> set:
    if r.alternatives:
        return {(str(a.get("allowed_quantity")), str(a.get("amount"))) for a in r.alternatives.values()}
    return {(str(r.allowed_quantity), str(r.amount))}


def covers(m, others) -> bool:
    """A conditional result carries the missing input's admissible values rather than defaulting it when every value it
    could take (the line as billed and each probe) is among its alternatives - and it has more than one."""
    got = _pairs(m)
    return len(got) > 1 and all(_pairs(o) <= got for o in others if o.amount_status not in ("unresolved", "deferred"))


# ----------------------------------------------------------------------------------------------- engine adapters
class Engines:
    """The engines and output stage under test (the current modules, or an earlier version for the negative control)."""

    def __init__(self, cw, dds, outputs=None):
        self.mod = {"CW": cw, "DDS": dds}
        self.outputs = outputs or g3_run
        self.takes_inputs = {c: "inputs" in inspect.signature(m.evaluate).parameters for c, m in self.mod.items()}

    def evaluate(self, contract, line, header, doc, exists, inputs):
        kw = {"inputs": inputs} if self.takes_inputs[contract] else {}
        if contract == "CW":
            return self.mod["CW"].evaluate(line, header, doc, exists, **kw)
        return self.mod["DDS"].evaluate(line, header, doc, **kw)

    def context(self, contract, w) -> dict:
        m = self.mod[contract]
        return m.input_context(w) if hasattr(m, "input_context") else {}


# ----------------------------------------------------------------------------------------------- sample
def sample(w, res) -> tuple[dict[str, list[str]], dict[str, list]]:
    """Per contract: the first line of every billed code in every family and of every input-sensitive feature; and
    synthetic rows for the families with no billed line (a real row with a code outside Schedule 1)."""
    out, extra = {"CW": [], "DDS": []}, {"CW": [], "DDS": []}
    for c, (lk, _hk) in KINDS.items():
        lines = {r.ident: r.values for r in w.claims.rows[lk]}
        first = {}
        for ref in sorted(res[c]):
            first.setdefault((res[c][ref].family, res[c][ref].code), ref)
        picks = set(first.values())
        feats = {}
        for ref in sorted(res[c]):
            v, r = lines[ref], res[c][ref]
            if c == "CW":
                keys = [("night", v.get("night_work")), ("zone", v.get("site_zone")),
                        ("rest", v["work_date"].strftime("%A") if v.get("work_date") else None),
                        ("31A", any("31A protection" in x for x in r.readings)), ("record", bool(v.get("record_ref")))]
            else:
                keys = [("status", (v.get("day_status"), r.family)), ("section", v.get("hole_section")),
                        ("36A", any("36A protection" in x for x in r.readings))]
            # what the output stage's decision scopes select on: each finding, alternative dimension, reading, status
            keys += [("finding", f) for f in r.findings] + [("unresolved", f) for f in r.unresolved]
            keys += [("dim", x.split(":", 1)[0]) for k in r.alternatives for x in (k or "").split("|") if x]
            keys += [("reading", x.split(" ")[0]) for x in r.readings if x.startswith(("Q3:", "P11", "night work as", "zone "))]
            keys += [("amount_status", r.amount_status)]
            for k in keys:
                feats.setdefault(k, ref)
        picks |= set(feats.values())
        out[c] = sorted(picks)
        field, code = UNSCHEDULED[c]
        base = next(r for r in w.claims.rows[lk] if r.ident == out[c][0])
        syn = copy.copy(base)
        syn.ident = f"{base.ident}#unscheduled"
        syn.values = {**base.values, field: code, "line_ref": syn.ident}
        extra[c].append(syn)
    return out, extra


# ----------------------------------------------------------------------------------------------- documents
def doc_text(doc) -> str:
    return (SNAPSHOT / doc.path).read_text(encoding="utf-8")


def _other(pools, contract, key, value) -> str:
    vals = pools.doc_values(contract, key, value)
    return vals[0] if vals else "??"


def cw_doc_mutations(text: str, doc, pools) -> list[tuple]:
    """(kind, field, variant, new text, probe texts or None): each record line dropped, garbled and written twice with
    another value; the narrative; the title; an unreadable day worked (probed by each real day of that week)."""
    lines = text.split("\n")
    out = [("doc", "title", "garble", "\n".join(["??"] + lines[1:]), [])]
    body_idx = [i for i, ln in enumerate(lines) if i and ln.strip() and ":" not in ln]
    for i, ln in enumerate(lines):
        if i == 0 or not ln.strip():
            continue
        key = ln.split(":", 1)[0] if i not in body_idx else "narrative"
        out.append(("doc", key, "drop", "\n".join(lines[:i] + lines[i + 1:]), None))
        out.append(("doc", key, "garble", "\n".join(lines[:i] + [("??" if key == "narrative" else f"{key}: ??")] + lines[i + 1:]), None))
        if key == "narrative":
            out.append(("doc", key, "twice", "\n".join(lines[:i + 1] + [ln] + lines[i + 1:]), None))
        else:
            out.append(("doc", key, "twice", "\n".join(lines[:i + 1] + [f"{key}: {_other(pools, 'CW', key, ln.split(':', 1)[1].strip())}"]
                                                      + lines[i + 1:]), None))
        if key == "Days on" and doc.week_beginning:
            days = [doc.week_beginning + dt.timedelta(days=k) for k in range(7)]
            probes = ["\n".join(lines[:i] + [ln + f", {DAYS[d.weekday()]} {d:%d/%m}"] + lines[i + 1:]) for d in days
                      if d not in doc.days_on]
            out.append(("doc", key, "append", "\n".join(lines[:i] + [ln + ", Xyz 99/99"] + lines[i + 1:]), probes))
    return out


def dds_doc_mutations(text: str, pools) -> list[tuple]:
    """Each header/part key dropped, garbled and (part keys) written twice with another value; each part dropped and its
    heading garbled; an unrecognisable tool term and crew entry (probed by each Appendix G term); each signature dropped
    and made a placeholder."""
    lines = text.split("\n")
    out = []
    part = None
    for i, ln in enumerate(lines):
        if i == 0 or not ln.strip():
            continue
        if ln.startswith("PART "):
            part = records_dds.PART_BY_TITLE.get(ln.strip())
            out.append(("heading", part, "garble", "\n".join(lines[:i] + ["PART ?? — ??"] + lines[i + 1:]), []))
            continue
        key = ln.split(":", 1)[0]
        sig = key in records_dds.SIG.values()
        name = key if (part is None or sig) else f"{part}.{key}"
        out.append(("doc", name, "drop", "\n".join(lines[:i] + lines[i + 1:]), None))
        out.append(("doc", name, "garble", "\n".join(lines[:i] + [f"{key}: ____" if sig else f"{key}: ??"] + lines[i + 1:]), None))
        if part is not None and not sig:
            other = _other(pools, "DDS", name, ln.split(":", 1)[1].strip())
            out.append(("doc", name, "twice", "\n".join(lines[:i + 1] + [f"{key}: {other}"] + lines[i + 1:]), None))
        if key in ("In the hole", "Tools in run"):
            probes = ["\n".join(lines[:i] + [ln + f", {t}"] + lines[i + 1:]) for t in sorted(records_dds.TOOL_TERMS)]
            out.append(("doc", name, "append", "\n".join(lines[:i] + [ln + ", unknown tool"] + lines[i + 1:]), probes))
        if key == "Crew on tour":
            probes = ["\n".join(lines[:i] + [ln + f", 3 {t}"] + lines[i + 1:]) for t in sorted(records_dds.CREW)]
            out.append(("doc", name, "append", "\n".join(lines[:i] + [ln + ", 3 unknown hands"] + lines[i + 1:]), probes))
    for p, spec in records_dds.SPEC["parts"].items():
        if spec["title"] in text:
            keep, skip = [], False
            for ln in lines:
                if ln.startswith("PART "):
                    skip = ln.strip() == spec["title"]
                elif ln.startswith("Signed"):
                    skip = False
                if not skip:
                    keep.append(ln)
            out.append(("part", p, "drop", "\n".join(keep), []))
    return out


def reparse(contract: str, doc, text: str):
    """The real G2 parser on the mutated text: (document, queued fields, fields written twice, unindexable, queue)."""
    q = Queue()
    if contract == "CW":
        d = records_cw.parse_file(doc.path, text, q)
        mine = [u for u in q.items if u.ident == d.ticket]
        return d, frozenset(u.field for u in mine), frozenset(u.field for u in mine if u.reason == "key repeated"), False, q
    d = records_dds.parse_file(doc.path, text, q)
    mine = [u for u in q.items if u.ident == d.file]
    return d, frozenset(u.field for u in mine), frozenset(u.field for u in mine if u.reason == "key repeated"), d.report is None, q


def left_empty(contract: str, d, gaps: frozenset, where: str, field: str) -> bool:
    """Whether G2 left the mutated field empty or unresolved (in scope); a garbled free-text value G2 accepts as a value
    (a report 'Well: ??', a record 'Job: ??') is neither."""
    if where in ("part", "heading") or field in gaps:
        return True
    if contract == "DDS":
        if field in records_dds.SIG.values():
            return not (d.company_signed if field == records_dds.SIG["company"] else d.driller_signed)
        if "." in field:
            part, key = field.split(".", 1)
            return d.parts.get(part, {}).get(key) is None
        return getattr(d, {"Report": "report", "Contract": "contract", "Well": "well", "Rig": "rig", "Date": "date"}[field]) is None
    if field in ("Signed (foreman)", "Countersigned (Engineer's representative)"):
        return not (d.foreman_signed if field == "Signed (foreman)" else d.engineer_signed)
    if field == "Days on":
        return not d.days_on
    attr = {"Area": "area", "Date": "date", "Week beginning": "week_beginning", "Ground": "ground", "narrative": "rule",
            "title": "family", "Job": "job"}.get(field)
    return attr is not None and getattr(d, attr) in (None, [], "")


# ----------------------------------------------------------------------------------------------- relevance probe
class Pools:
    """Other admissible values of each input, as the population itself shows them (no invented values)."""

    def __init__(self, w, res=None):
        # a code: one per code family and billed unit (a code needing a record and one that does not route the line
        # differently; a code in another unit only meets the unit rule)
        lines = {c: {r.ident: r.values for r in w.claims.rows[kind]} for c, (kind, _hk) in KINDS.items()}
        self.codes = {kind: sorted({(r.family, lines[c].get(k, {}).get("unit")): r.code for k, r in (res or {}).get(c, {}).items()
                                    if r.code and r.family}.values())
                      for c, (kind, _hk) in KINDS.items()}
        self.claim = defaultdict(list)
        for kind, rows in w.claims.rows.items():
            for row in rows[:4000]:
                for k, v in row.values.items():
                    if v not in (None, "") and v not in self.claim[(kind, k)] and len(self.claim[(kind, k)]) < 6:
                        self.claim[(kind, k)].append(v)
        self.doc = defaultdict(list)
        for d in list(w.ddr.values())[:600]:
            part = None
            for ln in doc_text(d).split("\n")[1:]:
                if ln.startswith("PART "):
                    part = records_dds.PART_BY_TITLE.get(ln.strip())
                elif ":" in ln:
                    k, v = ln.split(":", 1)
                    k = f"{part}.{k}" if part and k not in records_dds.SIG.values() else k
                    if v.strip() not in self.doc["DDS " + k] and len(self.doc["DDS " + k]) < 6:
                        self.doc["DDS " + k].append(v.strip())
        for r in list(w.cw.values())[:600]:
            for ln in doc_text(r).split("\n")[1:]:
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    if v.strip() not in self.doc["CW " + k] and len(self.doc["CW " + k]) < 6:
                        self.doc["CW " + k].append(v.strip())

    def claim_values(self, kind, field, current):
        if field in ("item_code", "service_code") and self.codes.get(kind):
            return [v for v in self.codes[kind] if v != current]
        vals = [v for v in self.claim[(kind, field)] if v != current][:3]
        if isinstance(current, dt.date):
            vals += [current + dt.timedelta(days=d) for d in (1, 35, -200, -400, 400)]
        if isinstance(current, Decimal):
            vals += [current * 2, current + 1, current / 2, Decimal(0)]
        return vals

    def doc_values(self, contract, key, current):
        vals = [v for v in self.doc[f"{contract} {key}"] if v != current][:3]
        bare = key.split(".", 1)[-1]
        if current is not None and str(current).isdigit():
            vals += [str(int(current) // 2), str(int(current) * 2 + 1), "0"]
        if bare == "Crew on tour":
            vals += [f"1 {t}" for t in sorted(records_dds.CREW)]
        if "hours" in bare:
            vals += ["24"]
        if bare.startswith(("Signed", "Countersigned")):
            vals += ["____"]
        if bare in ("In the hole", "Tools in run"):
            vals += sorted(records_dds.TOOL_TERMS)
        return vals


def _swap_value(text: str, key: str, value: str) -> str:
    return "\n".join(f"{key}: {value}" if ":" in ln and ln.split(":", 1)[0] == key else ln for ln in text.split("\n"))


# ----------------------------------------------------------------------------------------------- the sweep
def sweep(w, res, eng: Engines) -> tuple[list[str], dict]:
    """Direct evaluation of every (sample line, input, empty value), with G2's provenance for the line."""
    pools = Pools(w, res)
    errs, stats = [], defaultdict(int)
    picks, extra = sample(w, res)
    for c, (lk, hk) in KINDS.items():
        ctx = eng.context(c, w)
        rows = {r.ident: r for r in w.claims.rows[lk]} | {r.ident: r for r in extra[c]}
        hdrs = {r.ident: r for r in w.claims.rows[hk]}
        hkey = HKEY[c]
        for ref in picks[c] + [r.ident for r in extra[c]]:
            row = rows[ref]
            line = row.values
            header = hdrs[line[hkey]].values
            doc = (w.cw if c == "CW" else w.ddr).get(line.get(REF[c]) or "")
            inputs = ctx.get(ref.split("#")[0]) if eng.takes_inputs[c] else None
            base = eng.evaluate(c, line, header, doc, doc is not None, inputs)
            obs = obligations(c, line.get(UNSCHEDULED[c][0]))
            family = base.family
            stats[f"{c} family {family}"] += 1

            def judge(where, field, empty_val, mutated_fn, probes):
                stats["mutations"] += 1
                stats[f"{c} cell {family}|{where}|{field}"] += 1
                tag = f"{c} {ref} ({family}) {where} {field}={empty_val!r}"
                try:
                    m = mutated_fn()
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{tag}: exception {type(e).__name__}: {e}")
                    stats["exceptions"] += 1
                    return
                if any(ch.finding == "engine_error" for ch in m.checks):
                    errs.append(f"{tag}: engine_error")
                    return
                try:
                    others = [p() for p in probes]
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{tag}: exception on an admissible other value: {type(e).__name__}: {e}")
                    stats["exceptions"] += 1
                    return
                # an unrecognisable entry appended to a list leaves the required line and its readable entries in place:
                # whether they meet the condition is the relevance probe's question, not an unestablished obligation
                obligated = base.payable is True and (where, field) in obs and empty_val != "append"
                relevant = obligated or any(value_of(o) != value_of(base) for o in others)
                changed = value_of(m) != value_of(base)
                token = field.split(".", 1)[-1] if where == "doc" else field
                said = token in names(m) or (where in ("part", "heading") and (f"Part {field}" in names(m) or "part heading" in names(m)))
                registered = m.amount_status == "not_payable" and bool(set(m.findings) & REGISTERED.get((c, where, field), set()))
                informative = [o for o in others if o is not None] and (c, where, field) not in JOIN_KEYS
                if changed and not relevant and informative and not registered:
                    # no admissible value of the input changes the value, yet emptying it did: a known value lost
                    errs.append(f"{tag}: no admissible value of it changes the value, but the empty input changed it "
                                f"({m.amount_status}): a known value lost")
                    stats["lost"] += 1
                elif obligated and (m.payable is True or not said or (m.amount_status == "not_payable" and not registered)):
                    # a condition of payment the source states is not established, yet the line keeps a value
                    errs.append(f"{tag}: a source evidence obligation of this service is not established, but the result "
                                f"({m.amount_status}{', not named' if not said else ''}) does not leave it unresolved or apply "
                                f"the contract's consequence")
                    stats["obligation_ignored"] += 1
                elif relevant or changed:
                    if not said:
                        errs.append(f"{tag}: the value depends on it but the result does not name it (silent)")
                        stats["silent"] += 1
                    elif m.amount_status == "determined" or (m.amount_status in ("conditional", "alternatives")
                                                             and not covers(m, [base] + others)):
                        errs.append(f"{tag}: the value depends on it but the result fixes a value ({m.amount_status}): a default")
                        stats["silent"] += 1
                    elif m.amount_status == "not_payable" and not (set(m.findings) & REGISTERED.get((c, where, field), set())):
                        errs.append(f"{tag}: not payable without the contract's own rule for this input ({m.findings})")
                        stats["silent"] += 1
                    else:
                        stats[{"unresolved": "explicit_unresolved", "not_payable": "registered"}.get(m.amount_status, "all_values_carried")] += 1
                else:
                    stats["irrelevant_unchanged"] += 1

            for ev in empties(lk, hkey):          # the join key: the line's header is not found
                judge("line", hkey, ev, lambda ev=ev: eng.evaluate(c, {**line, hkey: ev}, None, doc, doc is not None, inputs),
                      [lambda: eng.evaluate(c, line, header, doc, doc is not None, inputs)])
            # the header number carried by a second, later-dated row: G2 hands both over; which applies is not established
            later = _later_copy(hdrs[line[hkey]])
            two = (f"{hdrs[line[hkey]].source.path}:{hdrs[line[hkey]].source.line}", f"{later.source.path}:{later.source.line}")
            both = replace(inputs or Inputs(), header_src=None, header_copies=two) if eng.takes_inputs[c] else None
            judge("header", hkey, "repeated", lambda _b=both: eng.evaluate(c, line, None, doc, doc is not None, _b),
                  [lambda _h=later.values: eng.evaluate(c, line, _h, doc, doc is not None, inputs)])
            for where, kind, cur in (("line", lk, line), ("header", hk, header)):
                for field in claim_fields(kind):
                    if field == hkey and where == "line":
                        continue
                    for ev in empties(kind, field):
                        if where == "line":
                            fn = lambda ev=ev, field=field: eng.evaluate(c, {**line, field: ev}, header, doc, doc is not None, inputs)  # noqa: E731
                            vals = pools.claim_values(kind, field, cur.get(field))
                            if field in ("work_date", "service_date"):
                                vals += [d for d in doc_dates(c, doc) if d != cur.get(field) and d not in vals]
                            probes = [lambda v=v, field=field: eng.evaluate(c, {**line, field: v}, header, doc, doc is not None, inputs)
                                      for v in vals]
                        else:
                            fn = lambda ev=ev, field=field: eng.evaluate(c, line, {**header, field: ev}, doc, doc is not None, inputs)  # noqa: E731
                            probes = [lambda v=v, field=field: eng.evaluate(c, line, {**header, field: v}, doc, doc is not None, inputs)
                                      for v in pools.claim_values(kind, field, cur.get(field))]
                        judge(where, field, ev, fn, probes)
            if doc is None:
                continue
            text = doc_text(doc)

            def run_text(t, _doc=doc, _line=line, _header=header, _inputs=inputs):
                dd, gg, rr, uu, _q = reparse(c, _doc, t)
                inp = replace(_inputs or Inputs(), doc_gaps=gg, doc_repeated=rr, unindexed_reports=uu) if eng.takes_inputs[c] else None
                if c == "DDS" and (uu or dd.report != _line.get("report_ref")):
                    return eng.evaluate(c, _line, _header, None, False, inp)      # G2's index does not give it to this line
                return eng.evaluate(c, _line, _header, dd, True, inp)
            muts = cw_doc_mutations(text, doc, pools) if c == "CW" else dds_doc_mutations(text, pools)
            for where, field, variant, new, probe_texts in muts:
                d2, gaps, _rr, _uu, _q = reparse(c, doc, new)
                if not left_empty(c, d2, gaps, where, field):
                    stats["out_of_scope_value_accepted_by_G2"] += 1
                    continue
                if probe_texts is None:
                    probe_texts = []
                    if where == "doc" and field not in ("title", "narrative"):
                        key = field.split(".", 1)[-1] if c == "DDS" else field
                        current = next((ln.split(":", 1)[1].strip() for ln in text.split("\n")
                                        if ":" in ln and ln.split(":", 1)[0] == key), None)
                        probe_texts = [_swap_value(text, key, v) for v in pools.doc_values(c, field, current)]
                        if c == "DDS" and key in ("Run first day", "Run last day") and doc.date:
                            probe_texts.append(_swap_value(text, key, f"{doc.date:%d-%b-%Y}"))
                        if c == "CW" and field == "Days on" and doc.days_on:
                            probe_texts.append(_swap_value(text, key, ", ".join(f"{DAYS[d.weekday()]} {d:%d/%m}" for d in doc.days_on[:2])))
                        ld = line.get("service_date" if c == "DDS" else "work_date")
                        if key in ("Date", "Week beginning") and ld is not None:     # the document for the line's own day
                            probe_texts.append(_swap_value(text, key, f"{ld:%d-%b-%Y}" if c == "DDS" else f"{ld:%d/%m/%Y}"))
                judge(where, field, variant, lambda new=new: run_text(new), [lambda t=t: run_text(t) for t in probe_texts])
            if c == "DDS" and doc.date:
                # another delivered file carries the same Report number (G2 indexes only the first and queues the other);
                # probe: that file is a report of another day under the same number
                other = reparse(c, doc, _swap_value(text, "Date", f"{doc.date + dt.timedelta(days=1):%d-%b-%Y}"))[0]
                copies = replace(inputs or Inputs(), report_copies=(f"COPY-{doc.file}",)) if eng.takes_inputs[c] else None
                judge("doc", "Report", "repeated", lambda _c=copies: eng.evaluate(c, line, header, doc, True, _c),
                      [lambda _o=other: eng.evaluate(c, line, header, _o, True, inputs)])
    return errs, dict(stats)


# ----------------------------------------------------------------------------------------------- the batch
def _later_copy(h):
    """A second header row with the same number, every date 40 days later (another submission under the same number)."""
    h2 = copy.copy(h)
    h2.values = {k: (v + dt.timedelta(days=40) if isinstance(v, dt.date) else v) for k, v in h.values.items()}
    h2.source = replace(h.source, line=h.source.line + 100000)
    return h2


def _sub_world(w, c, rows, docs=None, queue_items=(), unindex=(), others=None, files=None):
    """A copy of the world whose claim lines are `rows` (every other line of that contract left out; `others`: the other
    contract's lines, likewise), with documents replaced by `docs` (and G2's by-file report index by `files`) and G2
    queue entries added, as G2 would hand them over."""
    lk = KINDS[c][0]
    sub = copy.copy(w)
    sub.claims = copy.copy(w.claims)
    sub.claims.rows = {**w.claims.rows, **{KINDS[o][0]: r for o, r in (others or {}).items()}, lk: rows}
    sub.queue = copy.copy(w.queue)
    sub.queue.items = list(w.queue.items) + list(queue_items)
    if c == "CW":
        sub.cw = {**w.cw, **(docs or {})}
    else:
        sub.ddr = {k: v for k, v in {**w.ddr, **(docs or {})}.items() if k not in unindex}
        sub.ddr_by_file = {**w.ddr_by_file, **(files or {})}
    return sub


def _batch_once(eng: Engines, c, sub, label: str, errs: list) -> None:
    mod = eng.mod[c]
    lk, hk = KINDS[c]
    rows = sub.claims.rows[lk]
    try:
        out = mod.run(sub)
    except Exception as e:  # noqa: BLE001
        errs.append(f"{c} batch {label}: exception {type(e).__name__}: {e}")
        return
    if len(out) != len(rows):
        errs.append(f"{c} batch {label}: {len(out)} results for {len(rows)} lines")
    bad = [k for k, r in out.items() if any(ch.finding == "engine_error" for ch in r.checks)]
    if bad:
        errs.append(f"{c} batch {label}: engine_error on {bad[:3]}")
        return
    ctx = eng.context(c, sub)
    hdrs = {k: h.values for k, h in headers_by_id(sub.claims.rows[hk])[0].items()}   # a number two rows carry: no header
    for key, row in zip(result_keys(rows), rows):
        v = row.values
        ref = v.get(REF[c])
        doc = (sub.cw if c == "CW" else sub.ddr).get(ref) if ref else None
        try:
            direct = eng.evaluate(c, v, hdrs.get(v.get(HKEY[c])), doc, doc is not None, ctx.get(key) if eng.takes_inputs[c] else None)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{c} batch {label}: the direct evaluation of {key} raised {type(e).__name__}: {e}")
            return
        got = out.get(key)
        a = {k: x for k, x in direct.to_json().items() if k != "ctx"}
        b = {k: x for k, x in got.to_json().items() if k != "ctx"} if got is not None else None
        if a != b:
            errs.append(f"{c} batch {label}: {key} " + ("has no result" if got is None else "differs from its direct evaluation"))
            return
    _outputs_once(eng, c, sub, out, label, errs)


def _flat(x) -> set:
    if isinstance(x, dict):
        return set().union(*(_flat(v) for v in x.values()))
    return set(x) if isinstance(x, list) else set()


def _without(sub, res, gone):
    """The sub-world and results with the lines in `gone` (per contract, by result key) removed; the remaining lines
    re-keyed as the batch would key them."""
    s2 = copy.copy(sub)
    s2.claims = copy.copy(sub.claims)
    s2.claims.rows = dict(sub.claims.rows)
    res2 = {}
    for cc, (lk, _hk) in KINDS.items():
        rows = sub.claims.rows[lk]
        keep = [(k, row) for k, row in zip(result_keys(rows), rows) if k not in gone[cc]]
        s2.claims.rows[lk] = [row for _k, row in keep]
        res2[cc] = dict(zip(result_keys(s2.claims.rows[lk]), (res[cc][k] for k, _row in keep)))
    return s2, res2


def _outputs_once(eng: Engines, c, sub, out, label: str, errs: list) -> None:
    """The output stage (audit.g3_run) over the batch's results and the other contract's sample lines."""
    o = "DDS" if c == "CW" else "CW"
    try:
        both = {c: out, o: eng.mod[o].run(sub)}
        res = {k: both[k] for k in KINDS}
        s = eng.outputs.summary(sub, res)
        d = eng.outputs.decision_scopes(sub, res)
        json.dumps([s, d, eng.outputs.trace_sample(res)], default=str)
    except Exception as e:  # noqa: BLE001
        errs.append(f"{c} outputs {label}: exception {type(e).__name__}: {e}")
        return
    gone = {cc: {k for k, r in rs.items() if r.payable is None} for cc, rs in res.items()}
    for cc, rs in res.items():
        got = s["contracts"].get(cc, {})
        if got.get("lines") != len(sub.claims.rows[KINDS[cc][0]]) or \
                got.get("amount_status") != dict(sorted(Counter(r.amount_status for r in rs.values()).items())):
            errs.append(f"{c} outputs {label}: the summary does not count every {cc} line once by status")
        if sum(e.get("value_not_formed", 0) for e in got.get("codes", {}).values()) != len(gone[cc]):
            errs.append(f"{c} outputs {label}: the summary compares or values {cc} lines that have no value at G3")
    if not any(gone.values()):
        return
    try:
        d2 = eng.outputs.decision_scopes(*_without(sub, res, gone))
    except Exception as e:  # noqa: BLE001
        errs.append(f"{c} outputs {label}: exception without the unvalued lines {type(e).__name__}: {e}")
        return
    for k, sc in d.items():
        fell = (sc.get("lines_decided") or 0) - ((d2.get(k) or {}).get("lines_decided") or 0)
        listed = _flat(sc.get("lines_not_valued"))
        if fell > len(listed):
            errs.append(f"{c} outputs {label}: scope {k} reaches {fell} line(s) with no value at G3 and lists "
                        f"{len(listed)} as not valued")


def batch(w, res, eng: Engines, fields=None) -> tuple[list[str], int]:
    """The production run() over a sub-world of the sample lines, once per input: each claim field and header field
    emptied on every sample line, the header missing, the line reference emptied and repeated, and each document
    mutation applied to every sample document it fits; each run's results also through the output stage. `fields`
    limits the runs to those claim line fields (a test's shortcut). Returns (errors, number of batch runs)."""
    errs, runs = [], 0
    picks, extra = sample(w, res)
    pools = Pools(w, res)
    bases = {c: [r for r in w.claims.rows[lk] if r.ident in set(picks[c])] + extra[c] for c, (lk, _hk) in KINDS.items()}
    for c, (lk, hk) in KINDS.items():
        base_rows = bases[c]
        others = {o: r for o, r in bases.items() if o != c}
        hkey = HKEY[c]
        # claim line fields, the join key, a header G2 does not have, and a line reference two lines share
        for field in claim_fields(lk) + ["<header missing>", "<line_ref repeated>"]:
            if fields is not None and field not in fields:
                continue
            rows = []
            for i, row in enumerate(base_rows):
                r2 = copy.copy(row)
                r2.values = dict(row.values)
                if field == "<header missing>":
                    r2.values[hkey] = f"NO-SUCH-{r2.values[hkey]}"
                elif field == "<line_ref repeated>":
                    if i % 2:                  # G2 keeps both rows, each identified by the shared reference
                        r2.values["line_ref"] = r2.ident = rows[-1].values["line_ref"]
                elif field == "line_ref":
                    r2.values["line_ref"] = empties(lk, field)[-1]
                    r2.ident = f"{row.source.path}:{row.source.line}"   # as G2 identifies a row with no reference
                else:
                    r2.values[field] = empties(lk, field)[-1]
                rows.append(r2)
            label = f"with line {field}" if field.startswith("<") else f"with line {field} empty"
            _batch_once(eng, c, _sub_world(w, c, rows, others=others), label, errs)
            runs += 1
        if fields is not None:
            continue
        # header fields: emptied on the headers of the sample lines
        hids = {r.values[hkey] for r in base_rows}
        # a header number two rows carry (G2's input assertion 'ids unique' fails): the second row a later-dated copy
        hrows = list(w.claims.rows[hk]) + [_later_copy(h) for h in w.claims.rows[hk] if h.ident in hids]
        sub = _sub_world(w, c, base_rows, others=others)
        sub.claims.rows = {**sub.claims.rows, hk: hrows}
        _batch_once(eng, c, sub, "with header number repeated", errs)
        runs += 1
        for field in claim_fields(hk):
            if field == hkey:
                continue
            sub = _sub_world(w, c, base_rows, others=others)
            hrows = []
            for h in w.claims.rows[hk]:
                if h.ident in hids:
                    h2 = copy.copy(h)
                    h2.values = {**h.values, field: empties(hk, field)[-1]}
                    hrows.append(h2)
                else:
                    hrows.append(h)
            sub.claims.rows = {**sub.claims.rows, hk: hrows}
            _batch_once(eng, c, sub, f"with header {field} empty", errs)
            runs += 1
        # documents: each mutation (field, variant) applied to every sample document it fits
        store = w.cw if c == "CW" else w.ddr
        docs = {}
        for row in base_rows:
            ref = row.values.get(REF[c])
            if ref and ref in store:
                docs[ref] = store[ref]
        by_key = defaultdict(dict)
        for ref, doc in docs.items():
            text = doc_text(doc)
            muts = cw_doc_mutations(text, doc, pools) if c == "CW" else dds_doc_mutations(text, pools)
            for where, field, variant, new, _p in muts:
                d2, gaps, _rr, unidx, q = reparse(c, doc, new)
                if left_empty(c, d2, gaps, where, field):
                    by_key[(where, field, variant)].setdefault(ref, (d2, q.items, unidx))
        if c == "DDS":
            copies = {f"COPY-{d.file}": d for d in docs.values()}
            items = [Unresolved("ddr", f, "Report", f"duplicate report number {d.report} (also {d.file})") for f, d in copies.items()]
            _batch_once(eng, c, _sub_world(w, c, base_rows, None, items, (), others, copies), "with doc Report repeated", errs)
            runs += 1
        for (where, field, variant), changed in sorted(by_key.items(), key=lambda kv: str(kv[0])):
            new_docs = {ref: d2 for ref, (d2, _q, unidx) in changed.items() if not unidx}
            unindex = {ref for ref, (_d, _q, unidx) in changed.items() if unidx}
            items = [u for _r, (_d, q, _u) in changed.items() for u in q]
            items += [Unresolved("ddr", store[ref].file, "Report", "no report number; not indexable") for ref in unindex]
            files = {store[ref].file: d2 for ref, (d2, _q, _u) in changed.items()} if c == "DDS" else None
            _batch_once(eng, c, _sub_world(w, c, base_rows, new_docs, items, unindex, others, files), f"with {where} {field} {variant}", errs)
            runs += 1
    return errs, runs


def coverage(stats: dict) -> list[str]:
    """Every claim field (line and header) emptied on at least one line of every code family the sample reaches."""
    errs = []
    for c, (lk, hk) in KINDS.items():
        fams = sorted(k.split(" family ", 1)[1] for k in stats if k.startswith(f"{c} family "))
        for where, kind in (("line", lk), ("header", hk)):
            for field in claim_fields(kind):
                gone = [f for f in fams if not stats.get(f"{c} cell {f}|{where}|{field}")]
                if gone:
                    errs.append(f"X8 coverage: {c} {where} {field} never emptied on families {gone}")
    return errs


def x8(w, res, eng: Engines) -> tuple[list[str], dict]:
    errs, stats = sweep(w, res, eng)
    b, runs = batch(w, res, eng)
    stats["batch_runs"] = runs
    return errs + coverage(stats) + b, stats
