"""Parse civil site records (G2) against spec/evidence_cw.yaml.

Per file: header fields; a daily date, or a week start plus the worked days with the year reconstructed
across month and year boundaries; ground where stated; foreman and Engineer's-representative signatures
with placeholder detection; the one narrative line, its matched template (the extraction rule), quantity,
physical unit, attributes and candidate items. Every value keeps the file line it came from. Anything
missing or unmatched is queued; contradictions inside a record are recorded as conflicts.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal

from .common import SNAPSHOT, Queue, Source, dec, dmy_slash, signature_state, spec_lib

SPEC = spec_lib.load_yaml(spec_lib.SPEC / "evidence_cw.yaml")
TEMPLATES = [(t, re.compile(t["regex"])) for t in SPEC["templates"]]
FAMILY_BY_TITLE = {v["title"]: k for k, v in SPEC["families"].items()}
KEYS = set(SPEC["record_layout"]["keys"])
KEY_RE = re.compile(r"^(?P<k>[A-Za-z][A-Za-z '()]*?):\s?(?P<v>.*)$")
DAY_RE = re.compile(r"^(?P<dow>Mon|Tue|Wed|Thu|Fri|Sat|Sun) (?P<d>\d{2})/(?P<m>\d{2})$")
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
AREA_RE = re.compile(r"^(?P<code>S-0[1-5]) (?P<name>.+)$")
GROUND_RE = re.compile(r"^(?P<code>G[1-5]) (?P<name>.+)$")
AREAS = {r[0]: r[1] for r in spec_lib.load_terms("CW")["tables"]["CW.T03_WORK_AREAS"]["rows"]}
GROUNDS = {r[0]: r[1] for r in spec_lib.load_terms("CW")["tables"]["CW.T08_GROUND_FACTORS"]["rows"]}


@dataclass
class CwRecord:
    ticket: str
    path: str
    family: str | None = None
    title: str | None = None
    job: str | None = None
    area: str | None = None
    area_name: str | None = None
    date: dt.date | None = None
    week_beginning: dt.date | None = None
    days_on: list[dt.date] = field(default_factory=list)
    ground: str | None = None
    narrative: str | None = None
    rule: str | None = None                  # template id = extraction rule
    quantity: Decimal | None = None
    unit: str | None = None
    attributes: dict = field(default_factory=dict)
    candidates: list[str] = field(default_factory=list)
    foreman: str | None = None
    engineer: str | None = None
    foreman_signed: bool = False
    engineer_signed: bool = False
    foreman_sig: str = "unsigned"          # signed | unsigned | unknown (common.signature_state; FD01)
    engineer_sig: str = "unsigned"
    spans: dict = field(default_factory=dict)        # field -> Source
    repeats: list = field(default_factory=list)      # identical repetitions, read once (FD02)
    conflicting: dict = field(default_factory=dict)  # key -> the differing values stated (queued 'key repeated')
    spec_mismatch: dict = field(default_factory=dict)  # attribute -> {stated, required, items} (FD03)

    ctx: str | None = None                      # run context id (audit.provenance)

def _same_value(got, want) -> bool:
    """An attribute value against Schedule 1's: numerically for numbers (1800 = 1800.0), otherwise case-insensitively."""
    if got is None:
        return False
    try:
        return Decimal(str(got)) == Decimal(str(want))
    except Exception:  # noqa: BLE001 - not a number: compare the words
        return str(got).strip().upper() == str(want).strip().upper()


def depth_candidates(depth: Decimal) -> list[str]:
    for band in SPEC["depth_rule"]["bands"]:
        if band["max_m"] is None or depth <= Decimal(band["max_m"]):
            return [band["item"]]
    return []


def reconstruct_days(week_start: dt.date, raw: str, q: Queue, ticket: str, src: Source | None) -> list[dt.date]:
    """'Mon 27/01, ..., Sun 02/02' -> dates. Year = the week start's, rolled forward when the month wraps
    past December. The weekday name must agree with the date and the day must fall in that week."""
    out = []
    for part in [p.strip() for p in raw.split(",") if p.strip()]:
        m = DAY_RE.match(part)
        if not m:
            q.add("cw_record", ticket, "Days on", f"unparseable day {part!r}", src)
            continue
        d, mth = int(m["d"]), int(m["m"])
        year = week_start.year + (1 if mth < week_start.month else 0)
        try:
            day = dt.date(year, mth, d)
        except ValueError:
            q.add("cw_record", ticket, "Days on", f"invalid date {part!r}", src)
            continue
        if DOW[day.weekday()] != m["dow"]:
            q.conflict("cw_record", ticket, "weekday_name", f"{m['dow']} written for {day.isoformat()}")
        if not (week_start <= day <= week_start + dt.timedelta(days=6)):
            q.conflict("cw_record", ticket, "day_outside_week", f"{day.isoformat()} not in week of {week_start.isoformat()}")
        out.append(day)
    if len(out) != len(set(out)):
        q.conflict("cw_record", ticket, "duplicate_day", raw)
    return out


def parse_file(rel: str, text: str, q: Queue) -> CwRecord:
    ticket = rel.rsplit("/", 1)[-1].removesuffix(".txt")
    r = CwRecord(ticket=ticket, path=rel)
    lines = text.split("\n")
    r.title = lines[0].strip()
    r.family = FAMILY_BY_TITLE.get(r.title)
    r.spans["title"] = Source(rel, 1, lines[0])
    if r.family is None:
        q.add("cw_record", ticket, "title", f"unknown record title {r.title!r}", r.spans["title"])
    occ: dict[str, list[tuple[str, Source]]] = {}
    body = []
    for n, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        m = KEY_RE.match(line)
        src = Source(rel, n, line)
        if m and m["k"] in KEYS:
            occ.setdefault(m["k"], []).append((m["v"], src))
        else:
            body.append((line.strip(), src))
    # FD02: a key stated twice with the same value states one fact; with different values it is queued and
    # no value is taken from it (never the first or last by position)
    kv: dict[str, tuple[str, Source]] = {}
    for k, o in occ.items():
        if len({v.strip() for v, _ in o}) == 1:
            kv[k] = o[0]
            if len(o) > 1:
                r.repeats.append(f"{k} x{len(o)} (identical)")
        else:
            q.add("cw_record", ticket, k, "key repeated", o[-1][1])
            r.conflicting[k] = [v for v, _ in o]
    if len(body) > 1 and len({b for b, _ in body}) == 1:
        r.repeats.append(f"narrative x{len(body)} (identical)")
        body = body[:1]
    weekly = SPEC["families"].get(r.family, {}).get("weekly", False)
    needed = SPEC["record_layout"]["required_weekly" if weekly else "required_daily"]
    for k in needed:
        if k not in kv and k not in r.conflicting:
            q.add("cw_record", ticket, k, "required line missing", None)
    for k in kv:
        if k not in needed and k not in SPEC["record_layout"]["optional"]:
            q.add("cw_record", ticket, k, "line not expected for this family", kv[k][1])

    if "Ticket" in kv and kv["Ticket"][0] != ticket:
        q.conflict("cw_record", ticket, "ticket_vs_filename", kv["Ticket"][0])
    if "Job" in kv:
        r.job, r.spans["job"] = kv["Job"]
    if "Area" in kv:
        v, src = kv["Area"]
        m = AREA_RE.match(v)
        r.spans["area"] = src
        if m and AREAS.get(m["code"]) == m["name"]:
            r.area, r.area_name = m["code"], m["name"]
        else:
            q.add("cw_record", ticket, "Area", f"not a Schedule 2 work area: {v!r}", src)
    if weekly:
        if "Week beginning" in kv:
            v, src = kv["Week beginning"]
            r.spans["week_beginning"] = src
            try:
                r.week_beginning = dmy_slash(v)
            except ValueError:
                q.add("cw_record", ticket, "Week beginning", f"unparseable {v!r}", src)
        if "Days on" in kv and r.week_beginning:
            v, src = kv["Days on"]
            r.spans["days_on"] = src
            r.days_on = reconstruct_days(r.week_beginning, v, q, ticket, src)
    elif "Date" in kv:
        v, src = kv["Date"]
        r.spans["date"] = src
        try:
            r.date = dmy_slash(v)
        except ValueError:
            q.add("cw_record", ticket, "Date", f"unparseable {v!r}", src)
    if "Ground" in kv:
        v, src = kv["Ground"]
        r.spans["ground"] = src
        m = GROUND_RE.match(v)
        if m and GROUNDS.get(m["code"], "").lower() == m["name"].lower():
            r.ground = m["code"]
        else:
            q.add("cw_record", ticket, "Ground", f"not a Schedule 3 class: {v!r}", src)
    for key, attr in (("Signed (foreman)", "foreman"), ("Countersigned (Engineer's representative)", "engineer")):
        if key in kv:
            v, src = kv[key]
            st = signature_state(v)
            if st == "unknown":
                q.add("cw_record", ticket, key, f"signature text {v!r} does not establish a signature or its absence", src)
            setattr(r, attr, v)
            setattr(r, attr + "_sig", st)
            setattr(r, attr + "_signed", st == "signed")
            r.spans[attr] = src
        elif key in r.conflicting:            # stated twice differently: neither signed nor unsigned is established
            setattr(r, attr + "_sig", "unknown")

    if len(body) != 1:
        q.add("cw_record", ticket, "narrative", f"expected one narrative line, found {len(body)}", body[0][1] if body else None)
    if body:
        r.narrative, src = body[0]
        r.spans["narrative"] = src
        for tpl, rx in TEMPLATES:
            m = rx.match(r.narrative)
            if m and tpl["family"] == r.family:
                r.rule, r.unit, r.quantity = tpl["id"], tpl["unit"], dec(m["q"])
                r.attributes = {k: v for k, v in m.groupdict().items() if k != "q"}
                if tpl.get("reason"):
                    r.attributes["reason"] = tpl["reason"]
                r.candidates = depth_candidates(Decimal(r.attributes["depth_m"])) if tpl["candidates"] == "depth_rule" else list(tpl["candidates"])
                bad = {a: (r.attributes.get(a), want) for a, want in (tpl.get("requires") or {}).items()
                       if not _same_value(r.attributes.get(a), want)}
                if bad:                    # FD03: the record is for another specification - it evidences no candidate
                    r.spec_mismatch = {a: {"stated": got, "required": want, "items": list(r.candidates)} for a, (got, want) in bad.items()}
                    r.candidates = []
                break
        else:
            q.add("cw_record", ticket, "narrative", f"no evidence template matches {r.narrative!r}", src)
    return r


def load(snapshot=SNAPSHOT, q: Queue | None = None) -> tuple[dict[str, CwRecord], Queue]:
    q = q or Queue()
    recs = {}
    for p in sorted((snapshot / "civilwork" / "records").iterdir()):
        if p.suffix != ".txt":
            q.add("cw_record", p.name, "file", "not a .txt record", None)
            continue
        recs[p.stem] = parse_file(f"civilwork/records/{p.name}", p.read_text(encoding="utf-8"), q)
    return recs, q
