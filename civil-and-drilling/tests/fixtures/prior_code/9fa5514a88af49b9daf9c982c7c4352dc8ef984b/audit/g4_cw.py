"""G4 civil (CW-2025-0417-CIV): chronology and shared state across applications.

Consumes the G2 world and the G3 civil line results (closed, never modified) and applies, in this order:
1. duplicate measurement (Cl.44 p8): the same item, work area and date appearing more than once - in one application
   or in several - is one measurement; every later one is disallowed in full. 'Later' is the later submission (Cl.40:
   applications are submitted in time); within one application the later-listed line. Applications submitted the same
   day are not ordered by the contract: which measurement is the later is kept open ('earlier'). A weekly item (47A,
   Sch 5 note) is measured by the week its record evidences: lines of the same item and work area citing the same
   week's record are the same measurement (Q7, spec/g4_state.yaml G4-D3).
2. exclusions (Cl.32 p6; Sch 4 Part 5 p26; P19, P21 p13): A.14.020 is not measurable on the day of, or the two days
   following, a measurement of A.14.010 on the same work area (P19 'within two days of'; App A 'Day': calendar days;
   G4-D4); E.51.020 is not measurable for a work area on a day any of D.41.020/030/050, D.43.010/020 is measured there.
   A measurement excludes whether or not it is payable for want of its record under reading Q6:A, only when payable
   under Q6:B.
3. daily limits (Cl.31 p6; Cl.19 p4; P6 p12; Sch 4 Part 4 p26): per item, work area and day, across applications; the
   excess is not payable and not carried forward.
4. annual quantity bands (Sch 4 Part 3 pp24-25, which substitutes Cl.30; 3A p32; Cl.28 p6): per item and Contract Year,
   counted from zero at its start in order of the date of execution; a measurement crossing an edge is divided and each
   part priced at its own band's rounded rate (the G3 rate under that band). Open: Q12 (Contract Year reset), Q6 (which
   measured quantities count), and the order of measurements of one date where it changes a division ('order').
5. the A3 retrospective difference (A3 p43; 31A p32; SoV p38) on protected work already valued, and its single
   recipient under Q1 ('on or after' 31A vs 'after' A3; no tie-breaker).
6. P23 (p14) under Q3 reading A (decided at G3): an item excluded from its own valuation is never also deducted from
   the next one.
7. retention (Cl.45 p8) per application and its 45A release (p32) on the first application after the Date for
   Completion as extended, once.
Nothing here totals an application for the judged field, flags or classifies (G5).
"""
from __future__ import annotations

import datetime as dt
import itertools
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

from . import g3_cw, terms
from .g3_core import Trace, headers_by_id, result_keys
from .g4_core import G4Line, apply_options, dims_of, label_of, not_payable

Q3_RECORD = {"record_missing", "record_wrong_series", "record_unsigned", "record_date_mismatch", "record_area_mismatch",
             "record_area_unresolved", "item_not_supported_by_record"}
EXCLUSION_DAYS = {"A.14.020": 2, "E.51.020": 0}           # G4-D4: the days following (D+1, D+2); E.51.020 the same day
DAY0 = {"A": "counts", "B": "does not count"}             # Q6 D: whether the day of the A.14.010 measurement itself is excluded
INF = Decimal("1e30")


@dataclass
class Line:
    key: str
    v: dict
    app: dict | None
    g: G4Line
    idx: int

    @property
    def item(self):
        return self.v.get("item_code") or None

    @property
    def area(self):
        return (self.v.get("site") or "").split(" ")[0] or None

    @property
    def date(self) -> dt.date | None:
        return self.v.get("work_date")

    @property
    def app_no(self):
        return self.v.get("application_no") or None

    @property
    def line_no(self):
        return self.v.get("line_no")

    @property
    def submitted(self) -> dt.date | None:
        return (self.app or {}).get("application_date")

    @property
    def ref(self):
        return self.v.get("line_ref") or self.key

    def conv(self):
        """Cl.30's former order for measurements of one date (a convention after Sch 4 Part 3 substitutes Cl.30)."""
        return (self.date or dt.date.max, self.app_no or "", self.line_no if self.line_no is not None else 10 ** 9, self.idx)


@dataclass
class CwState:
    lines: dict
    duplicates: list = field(default_factory=list)
    exclusions: list = field(default_factory=list)
    limits: list = field(default_factory=list)
    ledgers: dict = field(default_factory=dict)
    adjustments: list = field(default_factory=list)
    p23: list = field(default_factory=list)
    retention: dict = field(default_factory=dict)


# ------------------------------------------------------------------------------------------------ 1 duplicates
def _week_key(ln: Line, w) -> tuple | None:
    """The week a weekly item's line measures: the week of the weekly record it cites (47A), when that record exists."""
    rec = w.cw.get(ln.v.get("record_ref") or "") if ln.v.get("record_ref") else None
    if rec is None or not rec.week_beginning or not ln.item or not ln.area:
        return None
    return (ln.item, ln.area, "week", rec.week_beginning)


def duplicates(lines: list[Line], w, T, st: CwState) -> dict:
    """Per line: None (no duplicate), or {'earlier': [candidates], 'disallowed_unless_first': bool}. Returns
    {key: {'ties': [line keys that may be the standing measurement] , 'disallowed': True|'tie'}}."""
    groups = {}
    for ln in lines:
        if ln.item and ln.area and ln.date:
            groups.setdefault((ln.item, ln.area, ln.date), []).append(ln)
        if ln.item in T.weekly_record:
            wk = _week_key(ln, w)
            if wk:
                groups.setdefault(wk, []).append(ln)
    # a line in two groups (its date and its week) belongs to their union
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x
    for members in groups.values():
        for a, b in zip(members, members[1:]):
            ra, rb = find(a.key), find(b.key)
            if ra != rb:
                parent[ra] = rb
    comp = {}
    for members in groups.values():
        for m in members:
            comp.setdefault(find(m.key), {})[m.key] = m
    out = {}
    for root, mem in comp.items():
        members = sorted(mem.values(), key=lambda m: (m.submitted or dt.date.min, m.app_no or "", m.line_no or 0, m.idx))
        if len(members) < 2:
            continue
        # the earliest measurement: the earliest submission; one application's lines in their listed order
        known = [m for m in members if m.submitted is not None]
        first_day = min((m.submitted for m in known), default=None)
        heads = {}
        for m in members:
            if m.submitted is None or m.submitted == first_day:
                heads.setdefault(m.app_no, m)          # an application's first line (members are in line order)
        cands = list(heads.values())
        gid = f"CW-DUP:{members[0].item}|{members[0].area}|" + ",".join(sorted({str(m.date) for m in members}))
        st.duplicates.append({"group": gid, "members": [m.ref for m in members],
                              "earlier": [c.ref for c in cands], "rule": "Cl.44 (p8)"})
        for m in members:
            if len(cands) == 1:
                out[m.key] = {"group": gid, "standing": cands[0].key, "ties": None}
            else:
                out[m.key] = {"group": gid, "standing": None, "ties": [c.key for c in cands]}
    return out


# ------------------------------------------------------------------------------------------------ 2 exclusions
def _measured(ln: Line, q6: str, dup: dict) -> bool | None:
    """Whether the line is a measurement that stands (for the exclusion trigger): not rejected in its entirety (Cl.26),
    inside the term, not itself a disallowed duplicate; unpaid for want of its record counts under Q6:A only."""
    r = ln.g.g3
    d = dup.get(ln.key)
    if d and d["standing"] is not None and d["standing"] != ln.key:
        return False
    if r.payable:
        return True
    if r.payable is None:
        return None
    reasons = set(r.findings)
    if reasons and reasons <= (Q3_RECORD | {"rate_differs", "amount_arithmetic", "submitted_late", "submitted_early",
                                            "outside_period", "contract_ref_variant", "subcontractor_mismatch",
                                            "claim_field_missing", "quantity_above_record", "ground_differs_from_record"}) \
            and reasons & Q3_RECORD:
        return q6 == "A"
    return False


def exclusions(lines: list[Line], T, dup: dict, st: CwState) -> dict:
    """{key: {q6: (excluded: bool|None, by: [refs])}} for the excluded items (A.14.020, E.51.020)."""
    by_item = {}
    for ln in lines:
        by_item.setdefault(ln.item, []).append(ln)
    rows = T.exclusion_rows
    out = {}
    for ln in lines:
        if ln.item not in EXCLUSION_DAYS:
            continue
        excluders = {b for a, b, _w, _s in rows if a == ln.item}
        res = {}
        for q6 in ("A", "B"):
            for d0 in ("A", "B"):
                hit, unknown = [], []
                for e in excluders:
                    for x in by_item.get(e, []):
                        if ln.area is None or ln.date is None or x.area is None or x.date is None:
                            if x.area in (None, ln.area) and ln.item is not None:
                                unknown.append(x.ref)
                            continue
                        if x.area != ln.area:
                            continue
                        lag = (ln.date - x.date).days
                        low = 1 if (EXCLUSION_DAYS[ln.item] > 0 and d0 == "B") else 0
                        if low <= lag <= EXCLUSION_DAYS[ln.item]:
                            m = _measured(x, q6, dup)
                            if m:
                                hit.append(x.ref)
                            elif m is None:
                                unknown.append(x.ref)
                res[(q6, d0)] = (True, sorted(hit)) if hit else ((None, sorted(unknown)) if unknown else (False, []))
        if any(v[0] is not False for v in res.values()):
            out[ln.key] = res
            st.exclusions.append({"line": ln.ref, "item": ln.item, "area": ln.area, "date": str(ln.date),
                                  "by": {f"Q6:{a}|Q6-day0:{b}": v[1] for (a, b), v in res.items()},
                                  "excluded": {f"Q6:{a}|Q6-day0:{b}": v[0] for (a, b), v in res.items()},
                                  "rule": "Cl.32 (p6); Sch 4 Part 5 (p26); P19 (p13)" if ln.item == "A.14.020" else "P21 (p13); Cl.32 (p6)"})
    return out


# ------------------------------------------------------------------------------------------------ 3 daily limits
def limit_of(item: str, T) -> Decimal | None:
    return T.daily_limits.get(item)


# ------------------------------------------------------------------------------------------------ helpers
def _cy(date: dt.date, q12: str, T) -> str | None:
    """Contract Year of a date (3A p32): A = anniversary reset on 5 January 2026; B = no new Contract Year."""
    if date is None or date < T.commencement or date > T.completion:
        return None
    if q12 == "B":
        return "CY1"
    return "CY1" if date < T.commencement.replace(year=T.commencement.year + 1) else "CY2"


def _band_parts(start: Decimal, qty: Decimal, edges) -> list[tuple[Decimal, int]]:
    """Divide the quantity placed from cumulative `start` at the band edges: band 1 is (0, e1], band 2 (e1, e2], band 3
    above e2 (Sch 4 Part 3: '1 to 4,000', '4,001 to 16,000', 'above 16,000'; a measurement crossing a band is divided)."""
    e1, e2 = edges
    out = []
    for i, (a, b) in enumerate(((Decimal(0), e1), (e1, e2), (e2, INF)), 1):
        q = min(start + qty, b) - max(start, a)
        if q > 0:
            out.append((q, i))
    return out


# ------------------------------------------------------------------------------------------------ the run
def run(w, g3res: dict, T=None, a3_rerun: bool = True) -> CwState:
    T = T or terms.cw()
    _terms_extras(T)
    rows = w.claims.rows["cw_lines"]
    keys = result_keys(rows)
    apps, _copies = headers_by_id(w.claims.rows["cw_headers"])
    lines = [Line(k, row.values, apps[row.values["application_no"]].values if row.values.get("application_no") in apps else None,
                  G4Line(k, g3res[k]), i) for i, (k, row) in enumerate(zip(keys, rows))]
    st = CwState({ln.key: ln.g for ln in lines})
    dup = duplicates(lines, w, T, st)
    exc = exclusions(lines, T, dup, st)
    # per line and scenario: the state outcome, then its value from the G3 rates
    outcome = {}
    for ln in lines:
        outcome[ln.key] = _local_outcome(ln, dup, exc, T, st)
    ledgers = _bands(lines, outcome, T, st, dup)
    for ln in lines:
        _value(ln, outcome[ln.key], ledgers.get(ln.key), T, dup, exc)
    if a3_rerun:
        _a3(w, lines, outcome, ledgers, T, st)
    _p23(lines, st)
    _retention(lines, apps, T, st)
    return st


def _terms_extras(T) -> None:
    """Daily limits and exclusion rows from the verified terms (CW.T13, CW.T14), attached once to the terms object."""
    if getattr(T, "daily_limits", None) is not None:
        return
    import yaml
    from .common import ROOT
    t = yaml.safe_load((ROOT / "spec" / "terms_cw.yaml").read_text())["tables"]
    T.daily_limits = {r[0]: Decimal(str(r[1]).replace(",", "")) for r in t["CW.T13_DAILY_LIMITS"]["rows"]}
    T.exclusion_rows = [tuple(r) for r in t["CW.T14_EXCLUSIONS"]["rows"]]
    p = {x["id"]: x["value"] for x in yaml.safe_load((ROOT / "spec" / "terms_cw.yaml").read_text())["parameters"]}
    T.retention_pct = Decimal(p["CW.P.retention_pct"])                          # Cl.45 (p8)
    num, den = p["CW.P.retention_release_fraction"].split("/")                   # 45A (p32): 'one half'
    T.release_fraction = Decimal(num) / Decimal(den)


def _local_outcome(ln: Line, dup: dict, exc: dict, T, st: CwState) -> dict:
    """The state outcome of a line apart from the bands, per scenario label: {'rejected': reason} or {'qty': allowed}.
    Scenario dimensions here: 'earlier' (a same-day tie in a duplicate group) and Q6 (an exclusion trigger unpaid for
    want of its record)."""
    out = {}
    d = dup.get(ln.key)
    e = exc.get(ln.key)
    dims = []
    if d and d["ties"]:
        dims.append(("earlier", d["ties"]))
    if e and any(e[("A", d0)][0] != e[("B", d0)][0] for d0 in ("A", "B")):
        dims.append(("Q6", ["A", "B"]))
    if e and any(e[(q6, "A")][0] != e[(q6, "B")][0] for q6 in ("A", "B")):
        dims.append(("Q6-day0", ["A", "B"]))
    for combo in itertools.product(*[v for _k, v in dims]) if dims else [()]:
        scn = dict(zip([k for k, _v in dims], combo))
        lab = label_of({k: (_ref_of(v, st) if k == "earlier" else v) for k, v in scn.items()})
        standing = (d["standing"] if d and d["standing"] else scn.get("earlier")) if d else None
        if d and standing != ln.key:
            out[lab] = {"rejected": "duplicate", "detail": f"the same item, work area and date (or week) is measured earlier "
                                                           f"({_ref_of(standing, st)}): the later measurement is disallowed in full"}
            continue
        if e:
            ex, by = e[(scn.get("Q6", "A"), scn.get("Q6-day0", "A"))]
            if ex:
                out[lab] = {"rejected": "excluded", "detail": f"{ln.item} is not measurable on {ln.date} for {ln.area}: "
                                                               f"{', '.join(by)} measured there within the stated period"}
                continue
            if ex is None:
                out[lab] = {"unknown": "exclusion", "detail": f"whether {', '.join(by)} excludes it is not established"}
                continue
        out[lab] = {"qty": None}
    return out


def _ref_of(key, st: CwState):
    g = st.lines.get(key)
    return (g.g3.line_ref if g is not None and g.g3.line_ref else key) if key else key


def _payable_qty(ln: Line, T) -> tuple[Decimal | None, str | None]:
    """The measured quantity the line is valued at before bands: G3's allowed quantity capped at the daily limit."""
    r = ln.g.g3
    if not r.payable:
        return (Decimal(0) if r.payable is False else None), None
    q = r.allowed_quantity
    lim = limit_of(ln.item, T) if ln.item else None
    if q is not None and lim is not None and q > lim:
        return lim, f"daily limitation {lim} {T.sch1[ln.item]['unit']} per work area per day (Sch 4 Part 4; Cl.31): {q - lim} not payable"
    return q, None


def _counted(ln: Line, oc: dict, q6: str, T) -> tuple[Decimal, Decimal]:
    """(lo, hi) of the quantity this line adds to its item's band count under a scenario outcome and a Q6 reading."""
    r = ln.g.g3
    if "rejected" in oc:
        return Decimal(0), Decimal(0)
    if r.payable:
        q, _why = _payable_qty(ln, T)
        return (q, q) if q is not None else (Decimal(0), ln.v.get("quantity") or INF)
    if r.payable is None:
        return Decimal(0), (ln.v.get("quantity") or INF)
    reasons = set(r.findings)
    only_record = bool(reasons & Q3_RECORD) and not (reasons & {"wrong_unit", "out_of_term", "week_not_measurable"}) \
        and "nothing chargeable" not in " ".join(r.reasons)
    if only_record and q6 == "A" and ln.v.get("quantity") is not None:
        q = ln.v["quantity"]
        lim = limit_of(ln.item, T)
        q = min(q, lim) if lim is not None else q
        return q, q
    return Decimal(0), Decimal(0)


def _bands(lines: list[Line], outcome: dict, T, st: CwState, dup: dict | None = None) -> dict:
    """Band ledgers per item and Contract Year under Q12 x Q6 (x the order of one date where it changes a division).
    Returns {line key: {(q12, q6): {order label or None: {'start': (lo, hi), 'parts': [...] or None}}}}."""
    res = {}
    band_lines = [ln for ln in lines if ln.item in T.banded]
    for q12 in ("A", "B"):
        for q6 in ("A", "B"):
            ledgers = {}
            for ln in band_lines:
                cy = _cy(ln.date, q12, T)
                if cy is None:
                    continue
                ledgers.setdefault((ln.item, cy), []).append(ln)
            for (item, cy), members in ledgers.items():
                members.sort(key=Line.conv)
                edges = T.band_edges[item]
                lo = hi = Decimal(0)
                entries = []
                i = 0
                while i < len(members):
                    j = i
                    while j < len(members) and members[j].date == members[i].date:
                        j += 1
                    grp = members[i:j]
                    cnt = {m.key: _count_range(m, outcome[m.key], q6, T) for m in grp}
                    _tie_counts(grp, cnt, outcome, q6, T, dup or {})
                    tot_lo = sum(c[0] for c in cnt.values())
                    tot_hi = sum(c[1] for c in cnt.values())
                    straddle = len(grp) > 1 and any(lo < e < hi + tot_hi for e in edges)
                    has_tie = any((dup or {}).get(m.key, {}).get("ties") for m in grp)
                    # only measurements that add to the count can change a division by their order (a rejected one adds
                    # nothing); more than 6 of them are not enumerated, and a same-day duplicate tie inside a group whose
                    # order matters is not combined with the orders: in both cases the division stays unresolved
                    movers = [m for m in grp if cnt[m.key] != (Decimal(0), Decimal(0))]
                    rest = [m for m in grp if m not in movers]
                    unordered = straddle and (len(movers) > 6 or (has_tie and len(grp) > 2))
                    orders = [grp] + ([list(p) + rest for p in itertools.permutations(movers) if list(p) + rest != grp]
                                      if straddle and not unordered else [])
                    for oi, order in enumerate(orders):
                        olab = None if not straddle else ("Cl.30 wording (application number, then line)" if oi == 0 else
                                                          "then ".join(f"{m.ref} " for m in order).strip())
                        a, b = lo, hi
                        tie_start = {}
                        for m in order:
                            pay, _why = _payable_qty(m, T) if m.g.g3.payable else (Decimal(0), None)
                            c = cnt[m.key]
                            # a measurement tied for 'earlier' is valued where the tie begins: in the scenario in which it
                            # stands, the other tied measurements are disallowed and count nothing
                            d = (dup or {}).get(m.key)
                            sa, sb = (tie_start.setdefault(d["group"], (a, b)) if d and d.get("ties") and m.key in d["ties"]
                                      else (a, b))
                            parts = _band_parts(sa, pay, edges) if (pay is not None and sa == sb and not unordered) else None
                            res.setdefault(m.key, {}).setdefault((q12, q6), {})[olab] = {
                                "start": (sa, sb), "parts": parts, "count": c, "cy": cy, "ledger": f"{item}|{cy}|Q12:{q12}|Q6:{q6}"}
                            if oi == 0:
                                entries.append({"line": m.ref, "date": str(m.date), "before": [str(a), str(b)],
                                                "count": [str(c[0]), str(c[1])],
                                                "parts": [[str(q), band] for q, band in parts] if parts else None})
                            a, b = a + c[0], b + c[1]
                    lo, hi = lo + tot_lo, hi + tot_hi
                    i = j
                st.ledgers[f"{item}|{cy}|Q12:{q12}|Q6:{q6}"] = entries
    return res


def _tie_counts(grp: list, cnt: dict, outcome: dict, q6: str, T, dup: dict) -> None:
    """Measurements of one date tied for 'earlier' (same-day submissions, Cl.44): exactly one of them stands, so together
    they add the quantity of the one that stands - between the smallest and the largest candidate - once. The first of
    them in the date's order carries that range; the others add nothing (never both, never none)."""
    ties = {}
    for m in grp:
        d = dup.get(m.key)
        if d and d.get("ties") and m.key in d["ties"]:
            ties.setdefault(d["group"], []).append(m)
    for members in ties.values():
        full = []
        for m in members:
            kept = [v for k, v in outcome[m.key].items() if "rejected" not in v and dims_of(k).get("Q6") in (None, q6)]
            full.append(_counted(m, kept[0], q6, T) if kept else (Decimal(0), Decimal(0)))
        lo, hi = min(f[0] for f in full), max(f[1] for f in full)
        for i, m in enumerate(members):
            cnt[m.key] = (lo, hi) if i == 0 else (Decimal(0), Decimal(0))


def _count_range(ln: Line, oc: dict, q6: str, T) -> tuple[Decimal, Decimal]:
    """(lo, hi) this line adds to its band count under Q6 reading q6, over the other open scenarios of its outcome: a line
    rejected under some scenarios only (a same-day duplicate tie: exactly one of the tied measurements stands) adds
    nothing at the low end and its quantity at the high end, so the count after it is an interval and every later
    division in the ledger stays unresolved rather than counting the tied measurements twice."""
    vals = [v for k, v in oc.items() if dims_of(k).get("Q6") in (None, q6)] or list(oc.values())
    kept = [v for v in vals if "rejected" not in v]
    if not kept:
        return Decimal(0), Decimal(0)
    lo, hi = _counted(ln, kept[0], q6, T)
    if len(kept) < len(vals):
        return Decimal(0), hi
    return lo, hi


def _band_rates(r) -> dict:
    """{non-band label: {band index: (rate, trace)}} from the G3 result's band alternatives."""
    out = {}
    for k, a in r.alternatives.items():
        d = dims_of(k)
        if "band" not in d:
            continue
        band = int(d.pop("band"))
        out.setdefault(label_of(d), {})[band] = (a["unit_rate"], a.get("trace") or [])
    return out


def _value(ln: Line, oc: dict, bands: dict | None, T, dup: dict, exc: dict) -> None:
    """Set the G4 value of the line from its state outcome and the G3 rates; record the state checks."""
    g, r3 = ln.g, ln.g.g3
    d = dup.get(ln.key)
    e = exc.get(ln.key)
    if d:
        rej = [k for k, v in oc.items() if v.get("rejected") == "duplicate"]
        if len(rej) == len(oc):
            g.add("duplicate", "finding", "CW-R17", "Cl.44 (p8)", "duplicate_measurement", next(iter(oc.values()))["detail"], d["group"])
        elif rej:
            g.add("duplicate", "unresolved", "CW-R17", "Cl.44 (p8)", "duplicate_measurement",
                  "measured more than once, and the applications were submitted the same day: which is the later measurement is "
                  "not established (no contractual tie-breaker)", d["group"])
        else:
            g.add("duplicate", "pass", "CW-R17", "Cl.44 (p8)", detail="the earliest of the repeated measurements: it stands", ledger=d["group"])
    if e:
        exr = [k for k, v in oc.items() if v.get("rejected") == "excluded"]
        if exr and len(exr) == len([k for k, v in oc.items() if "qty" in v or v.get("rejected") == "excluded"]):
            g.add("exclusion", "finding", "CW-R16", "Cl.32 (p6); Sch 4 Part 5 (p26); P19, P21 (p13)", "excluded_by_other_item",
                  next(v["detail"] for v in oc.values() if v.get("rejected") == "excluded"))
        elif exr or any("unknown" in v for v in oc.values()):
            g.add("exclusion", "unresolved", "CW-R16", "Cl.32 (p6); Sch 4 Part 5 (p26); P19, P21 (p13)", "excluded_by_other_item",
                  "excluded under some readings only (Q6: an excluding measurement unpaid for want of its record; Q6 D: "
                  "whether the day of the excluding measurement itself is within the period), or whether the excluding "
                  "measurement stands is not established")
    elif ln.item in EXCLUSION_DAYS:
        g.add("exclusion", "pass", "CW-R16", "Cl.32 (p6); Sch 4 Part 5 (p26); P19, P21 (p13)",
              detail="no excluding measurement on the same work area within the stated period")
    if r3.payable is not True:
        # not payable (or unresolved) at G3: G4 adds its consequences as checks; the value stays G3's
        if ln.item in T.banded:
            g.add("band", "n/a", "CW-R14", "Sch 4 Part 3 (pp24-25)",
                  detail="not payable at G3: nothing to divide; its quantity advances the band count under Q6 A only where "
                         "it is unpaid for want of its record (see the ledger)")
        return
    if all("rejected" in v for v in oc.values()):
        v = next(iter(oc.values()))
        not_payable(g, ("the later measurement of the same item, work area and date is disallowed in full (Cl.44)"
                        if v["rejected"] == "duplicate" else "the item is excluded by another item's measurement (Cl.32)"),
                    "Cl.44 (p8)" if v["rejected"] == "duplicate" else "Cl.32 (p6); Sch 4 Part 5 (p26); P19, P21 (p13)")
        return
    pay, why = _payable_qty(ln, T)
    lim = limit_of(ln.item, T) if ln.item else None
    if lim is not None:
        if why:
            g.add("daily_limit", "finding", "CW-R15", "Cl.31 (p6); Sch 4 Part 4 (p26); Cl.19 (p4); P6 (p12)", "above_daily_limit", why,
                  f"CW-LIM:{ln.item}|{ln.area}|{ln.date}")
        else:
            g.add("daily_limit", "pass", "CW-R15", "Cl.31 (p6); Sch 4 Part 4 (p26)",
                  detail=f"{pay} within the daily limitation {lim} for {ln.area} on {ln.date}")
    options = {}
    base = _band_rates(r3) if ln.item in T.banded and r3.alternatives else None
    for olab, v in oc.items():
        odims = dims_of(olab)
        if "rejected" in v:
            for k3 in (base or {None: None}):
                options[label_of({**odims, **dims_of(k3)})] = {"unit_rate": None, "allowed_quantity": Decimal(0),
                                                                "amount": Decimal("0.00"), "trace": list(r3.trace) + [
                    {"op": "note", "label": f"G4: {v['detail']}", "source": "Cl.44 (p8); Cl.32 (p6)"}]}
            continue
        if "unknown" in v:
            g.r.payable, g.r.amount_status, g.r.amount, g.r.alternatives = None, "unresolved", None, {}
            g.r.conditions = [{"dimension": "state", "owner": "G5", "basis": v["detail"]}]
            return
        if base is None:        # an item without bands (or a band item G3 priced at a given band): capped quantity
            for k3, a in _g3_options(r3).items():
                rate = a["unit_rate"]
                if rate is None:
                    options[label_of({**odims, **dims_of(k3)})] = a
                    continue
                t = Trace()
                t.steps = [s for s in a["trace"] if s["op"] not in ("amount", "part", "sum_parts")]
                if why:
                    t.note(f"G4 daily limit: {why}", "Cl.31 (p6); Sch 4 Part 4 (p26)")
                amt = t.amount(pay, rate, "Cl.28 (p6): quantity x rounded rate; G4 state applied")
                options[label_of({**odims, **dims_of(k3)})] = {"unit_rate": rate, "allowed_quantity": pay, "amount": amt, "trace": t.steps}
            continue
        # band item: divide the measured quantity at the band edges under each reading
        for (q12, q6), by_order in (bands or {}).items():
            if "Q6" in odims and odims["Q6"] != q6:
                continue
            for order_lab, b in by_order.items():
                sdims = {**odims, "Q12": q12, "Q6": q6}
                if order_lab:
                    sdims["order"] = order_lab
                for k3, rates in base.items():
                    lab = label_of({**sdims, **dims_of(k3)})
                    if b["parts"] is None:
                        options[lab] = {"unit_rate": None, "allowed_quantity": pay, "amount": None, "trace": [],
                                        "unknown": f"the count before this measurement is between {b['start'][0]} and "
                                                   f"{b['start'][1]} (an earlier quantity is not established)"}
                        continue
                    t = Trace()
                    first_band = b["parts"][0][1] if b["parts"] else 1
                    t.steps = [s for s in rates.get(first_band, (None, []))[1] if s["op"] not in ("amount", "part", "sum_parts")]
                    t.note(f"G4 band state {b['ledger']}: {b['start'][0]} {T.sch1[ln.item]['unit']} already measured in "
                           f"{b['cy']} before this measurement", "Sch 4 Part 3 (pp24-25); 3A (p32)")
                    for q, band in b["parts"]:
                        rate = rates[band][0]
                        t.part(f"band {band} ({T.band_pcts[ln.item][band - 1]}%): {q} {T.sch1[ln.item]['unit']} at the band-{band} rate",
                               q, rate, "CW.T12_BANDS (Sch 4 Part 3 pp24-25); Cl.28 (p6): each part at its own rounded rate")
                    amt = t.total("amount = sum of the band parts", "Cl.28 (p6): where a quantity is divided each part is "
                                                                   "multiplied by its own rounded rate and the amount is their sum")
                    single = rates[b["parts"][0][1]][0] if len(b["parts"]) == 1 else None
                    options[lab] = {"unit_rate": single, "allowed_quantity": pay, "amount": amt, "trace": t.steps,
                                    "parts": b["parts"]}
    unknown = [v for v in options.values() if v.get("unknown")]
    if unknown:
        g.r.payable, g.r.amount_status, g.r.amount, g.r.allowed_quantity, g.r.alternatives = None, "unresolved", None, pay, {}
        g.r.conditions = [{"dimension": "state", "owner": "G5", "basis": unknown[0]["unknown"]}]
        g.add("band", "unresolved", "CW-R14", "Sch 4 Part 3 (pp24-25)", "band_state_unknown", unknown[0]["unknown"])
        return
    if ln.item in T.banded and base is not None:
        divided = {lab for lab, v in options.items() if v.get("parts") and len(v["parts"]) > 1}
        bands_used = sorted({b for v in options.values() for _q, b in (v.get("parts") or [])})
        if divided and len(divided) == len(options):
            g.add("band", "finding", "CW-R14", "Sch 4 Part 3 (pp24-25); Cl.28 (p6)", "band_divided",
                  "the measurement crosses a band edge and is divided; each part at its own band's rounded rate")
        else:
            g.add("band", "unresolved" if divided else "pass", "CW-R14", "Sch 4 Part 3 (pp24-25); 3A (p32)",
                  "band_divided" if divided else None,
                  ("divided under some readings only" if divided else f"band(s) {bands_used} under every reading carried"))
        _rate_after_band(g, options, ln)
    apply_options(g, options, {}, "G4 state")
    g.r.payable = True


def _g3_options(r3) -> dict:
    if r3.alternatives:
        return {k: {"unit_rate": a.get("unit_rate"), "allowed_quantity": a.get("allowed_quantity"), "amount": a.get("amount"),
                    "trace": a.get("trace") or []} for k, a in r3.alternatives.items()}
    return {None: {"unit_rate": r3.unit_rate, "allowed_quantity": r3.allowed_quantity, "amount": r3.amount, "trace": r3.trace}}


def _rate_after_band(g: G4Line, options: dict, ln: Line) -> None:
    """G3 left the billed rate and amount of a band line unresolved (the band was G4 state). With the band known under
    each reading: a billed rate that is no single-band rate of any option, or a billed amount no option's value, is a
    finding; one some options give is still open (their readings are not decided)."""
    ra, amt = ln.v.get("rate_applied"), ln.v.get("amount")
    if ra is None or amt is None:
        return
    rates = {v["unit_rate"] for v in options.values()}
    vals = {v["amount"] for v in options.values()}
    if ra in rates and len(rates) == 1:
        g.add("band_rate", "pass", "CW-R10", "Cl.27, Cl.28 (p6)", detail=f"billed {ra} is the rate of the band the measurement lies in")
    elif ra in rates or amt in vals:
        g.add("band_rate", "unresolved", "CW-R10", "Cl.27, Cl.28 (p6); Sch 4 Part 3", "rate_differs",
              f"billed {ra} (amount {amt}) matches the value under some of the readings carried only")
    else:
        g.add("band_rate", "finding", "CW-R10", "Cl.27, Cl.28 (p6); Sch 4 Part 3", "rate_differs",
              f"billed {ra} x {ln.v.get('quantity')} = {amt}; the band state gives {sorted(str(x) for x in vals)}")


# ------------------------------------------------------------------------------------------------ 5 A3
def _a3(w, lines: list[Line], outcome: dict, ledgers: dict, T, st: CwState) -> None:
    """The retrospective difference on work already valued (31A 'The difference between the rates on work already
    valued'; A3 'already certified' - no certification is supplied, the submitted valuation is the proxy): each protected
    line re-valued at the substituted rates with the same state (quantity, bands, factors, discount by work date), and
    the single recipient under Q1."""
    retro = [i for i in T.instruments if i.retrospective]
    if not retro:
        return
    ctx = g3_cw.input_context(w)
    apps = {h.values["application_no"]: h.values for h in w.claims.rows["cw_headers"]}
    recs = w.cw
    for ins in retro:
        eligible = [ln for ln in lines if any(x == f"31A protection ({ins.id})" for x in ln.g.g3.readings)]
        by_line, totals = {}, {}
        for ln in eligible:
            g = ln.g
            if g.r.payable is False:
                by_line[ln.ref] = {"difference": "0.00", "basis": "not payable: nothing valued"}
                continue
            if g.r.payable is None:
                by_line[ln.ref] = {"difference": None, "basis": "the line's value is not established"}
                continue
            rec = recs.get(ln.v.get("record_ref")) if ln.v.get("record_ref") else None
            new3 = g3_cw.evaluate(ln.v, {**(ln.app or {}), "application_date": ins.issued}, rec, rec is not None, T=T, inputs=ctx.get(ln.key))
            twin = Line(ln.key, ln.v, ln.app, G4Line(ln.key, new3), ln.idx)
            _value(twin, outcome[ln.key], ledgers.get(ln.key), T, {}, {})
            old = _opts(g.r)
            new = _opts(twin.g.r)
            diff = {}
            for k in sorted(set(old) | set(new), key=lambda x: x or ""):
                o = old.get(k, old.get(None))
                n = new.get(k, new.get(None))
                diff[k] = None if o is None or n is None else n - o
            by_line[ln.ref] = {"difference": {(k or ""): (str(v) if v is not None else None) for k, v in diff.items()},
                               "old": {(k or ""): str(v) for k, v in old.items()}, "new": {(k or ""): str(v) for k, v in new.items()},
                               "application": ln.app_no, "work_date": str(ln.date)}
            for k, v in diff.items():
                totals.setdefault(k, []).append(v)
        subs = sorted({(a["application_date"], k) for k, a in apps.items() if a.get("application_date")})
        on_or_after = [k for d, k in subs if d >= ins.issued]
        after = [k for d, k in subs if d > ins.issued]
        first_a = [k for d, k in subs if on_or_after and d == apps[on_or_after[0]]["application_date"]]
        first_b = [k for d, k in subs if after and d == apps[after[0]]["application_date"]]
        total = _sum_options(by_line)
        st.adjustments.append({
            "instrument": ins.id, "issued": str(ins.issued), "effective": str(min(e for _c, e, _v in ins.rate_rows)),
            "eligible_lines": len(eligible), "by_line": by_line, "total": total,
            "recipient": {"Q1:A (31A: first application submitted on or after the date of issue)":
                          first_a[0] if len(first_a) == 1 else ({"tie": first_a} if first_a else None),
                          "Q1:B (A3: first application submitted after the date of issue)":
                          first_b[0] if len(first_b) == 1 else ({"tie": first_b} if first_b else None)},
            "posted": "once, on the recipient under each reading; a tie has no contractual tie-breaker and stays open (Q1)",
            "basis": "31A (p32); A3 (p43); SoV (p38); submitted applications as the proxy for 'already certified' (no "
                     "certificates supplied)"})


def _opts(r) -> dict:
    if r.alternatives:
        return {k: v["amount"] for k, v in r.alternatives.items()}
    return {None: r.amount}


GLOBAL_READINGS = ("Q12", "Q6", "Q6-day0", "Q14", "Q11", "Q4", "Q5-DD120", "Q5-RM530", "Q5-HC630")
GROUP_DIMS = ("stands", "order", "earlier")


def _well_fact(dd: dict, well) -> dict:
    """A well's class is one fact for all its services: lines of one well range over it jointly (class@<well>)."""
    if well and "class" in dd:
        dd = {**dd}
        dd["class@" + well] = dd.pop("class")
    return dd


def _sum_options(by_line: dict) -> dict:
    """Total of the line differences under each shared reading (a reading of the contract applies to every line at once),
    and within a reading the range over what stays per line or per group: a line's facts (class, ground) are its own, so
    they range independently, except a well's class, which is joint for the well's lines; lines whose alternatives share an allocation dimension (stands, order, earlier) with the
    same values are one ledger group and are summed jointly under each value of that dimension."""
    known, unknown = {}, 0
    for ref, v in by_line.items():
        d = v["difference"]
        if d is None:
            unknown += 1
            continue
        opts = {"": Decimal(d)} if isinstance(d, str) else {k: (None if x is None else Decimal(x)) for k, x in d.items()}
        if any(x is None for x in opts.values()):
            unknown += 1
            continue
        known[ref] = {k: (_well_fact(dims_of(k or None), v.get("well")), x) for k, x in opts.items()}
    present = {}
    for opts in known.values():
        for dd, _x in opts.values():
            for g in GLOBAL_READINGS:
                if g in dd:
                    present.setdefault(g, set()).add(dd[g])
    names = sorted(present)
    combos = [dict(zip(names, c)) for c in itertools.product(*[sorted(present[n]) for n in names])] or [{}]
    out = {}
    for combo in combos:
        groups = {}
        for ref, opts in known.items():
            sel = [(dd, x) for dd, x in opts.values() if all(dd.get(n, combo[n]) == combo[n] for n in names)]
            gd = sorted({g for dd, _x in sel for g in dd if g in GROUP_DIMS or g.startswith("class@")})
            gkey = tuple((g, frozenset(dd[g] for dd, _x in sel if g in dd)) for g in gd)
            groups.setdefault(gkey, []).append(sel)
        lo = hi = Decimal(0)
        for gkey, members in groups.items():
            if not gkey:
                for sel in members:
                    lo += min(x for _d, x in sel)
                    hi += max(x for _d, x in sel)
                continue
            joint = []
            for vals in itertools.product(*[sorted(vs) for _g, vs in gkey]):
                want = dict(zip([g for g, _v in gkey], vals))
                tl = th = Decimal(0)
                for sel in members:
                    xs = [x for dd, x in sel if all(dd.get(g, want[g]) == want[g] for g in want)]
                    tl += min(xs)
                    th += max(xs)
                joint.append((tl, th))
            lo += min(j[0] for j in joint)
            hi += max(j[1] for j in joint)
        out[label_of(combo) or ""] = {"min": str(lo), "max": str(hi)}
    return {"by_reading": out, "min": str(min(Decimal(v["min"]) for v in out.values())),
            "max": str(max(Decimal(v["max"]) for v in out.values())), "lines_not_established": unknown}


# ------------------------------------------------------------------------------------------------ 6 P23
def _p23(lines: list[Line], st: CwState) -> None:
    subs = sorted({(ln.submitted, ln.app_no) for ln in lines if ln.submitted and ln.app_no})
    for ln in lines:
        r = ln.g.g3
        if "Q3:A" not in r.readings or r.payable is not False:
            continue
        nxt = [a for d, a in subs if ln.submitted and d > ln.submitted]
        st.p23.append({"line": ln.ref, "application": ln.app_no, "next_valuation": nxt[0] if nxt else None,
                       "status": "not posted",
                       "basis": "Q3 reading A (decided at G3): the item is not payable in its own valuation until its record is "
                                "delivered (Cl.46), so it was never included in a valuation; P23 deducts only an amount that was "
                                "(p14). Posting a deduction as well would count the same amount twice."})
        ln.g.add("p23", "pass", "CW-R05", "Cl.46 (p8); P23 (p14)", detail="excluded from its own valuation (Q3:A); no P23 deduction is posted")


# ------------------------------------------------------------------------------------------------ 7 retention
def _retention(lines: list[Line], apps: dict, T, st: CwState) -> None:
    """Cl.45 (p8): 5% of the value of each valuation, on the application total, rounded down to the halala; 45A (p32): on
    the first application submitted after the Date for Completion as extended, one half of the retention held on all
    earlier applications is released, rounded down. The application total here is the sum of its lines' values after
    state (a range where lines carry alternatives); the outcome of each application is G5's."""
    by_app = {}
    for ln in lines:
        if ln.app_no is None:
            continue
        r = ln.g.r
        if r.payable is None:
            v = None
        elif not r.payable:
            v = "0.00"
        elif r.amount is not None:
            v = str(r.amount)
        else:
            v = {k: (None if a.get("amount") is None else str(a["amount"])) for k, a in r.alternatives.items()}
        by_app.setdefault(ln.app_no, {})[ln.key] = {"difference": v}
    pct = T.retention_pct / 100

    def ret(x):
        return (x * pct).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    def single_or_range(d: dict, f) -> object:
        """{reading: {min, max}} -> the single value where every reading and fact gives one, else per reading."""
        vals = {(f(Decimal(v["min"])), f(Decimal(v["max"]))) for v in d.values()}
        if len(vals) == 1 and len(set(next(iter(vals)))) == 1:
            return str(next(iter(vals))[0])
        return {"by_reading": {k: {"min": str(f(Decimal(v["min"]))), "max": str(f(Decimal(v["max"])))} for k, v in d.items()}}
    out, tot = {}, {}
    for no, h in apps.items():
        s_ = _sum_options(by_app.get(no, {}))
        known = not s_["lines_not_established"]
        tot[no] = s_["by_reading"] if known else None
        out[no] = {"submitted": str(h.values.get("application_date")),
                   "total": single_or_range(s_["by_reading"], lambda x: x) if known else None,
                   "retention": single_or_range(s_["by_reading"], ret) if known else None}
    subs = sorted((h.values["application_date"], no) for no, h in apps.items() if h.values.get("application_date"))
    after = [(d, no) for d, no in subs if d > T.completion]
    release = None
    if after:
        day = after[0][0]
        tied = [no for d, no in after if d == day]
        earlier = [no for d, no in subs if d < day]
        unknown = [no for no in earlier if tot[no] is None]
        rel = lambda x: (x * T.release_fraction).quantize(Decimal("0.01"), rounding=ROUND_DOWN)  # noqa: E731
        present = {}
        for no in earlier:
            for k in (tot[no] or {}):
                for d, x in dims_of(k or None).items():
                    present.setdefault(d, set()).add(x)
        names = sorted(present)
        combos = {}
        for vals_ in itertools.product(*[sorted(present[n]) for n in names]):
            want = dict(zip(names, vals_))
            lab = label_of(want) or ""
            lo = hi = Decimal(0)
            for no in earlier:
                if tot[no] is None:
                    continue
                pick = next(v for k, v in tot[no].items() if all(want.get(d) == x for d, x in dims_of(k or None).items()))
                lo += ret(Decimal(pick["min"]))
                hi += ret(Decimal(pick["max"]))
            combos[lab] = {"min": str(rel(lo)), "max": str(rel(hi))}
        release = {"recipient": tied[0] if len(tied) == 1 else {"tie": tied}, "submitted": str(day),
                   "earlier_applications": len(earlier),
                   "released": None if unknown else single_or_range(combos, lambda x: x),
                   "not_established": sorted(unknown)[:20], "not_established_count": len(unknown),
                   "later_applications_without_release": [no for d, no in after if d > day],
                   "basis": "45A (p32): 'On the first Application for Payment submitted after the Date for Completion as extended "
                            f"({T.completion}), one half of the retention held on all earlier Applications is released, rounded "
                            "down to the halala' - once; Cl.45 (p8) retention rounded down per application"}
    st.retention = {"per_application": out, "release": release}
