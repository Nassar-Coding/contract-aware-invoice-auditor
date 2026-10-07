"""G4 drilling (DDS-2025-118): chronology and shared state across invoices.

Consumes the G2 world and the G3 drilling line results (closed, never modified) and applies:
1. once only (Cl.29 p7 'No service is charged twice for the same well and the same day, save that item PD-210 may be
   charged more than once on a day for different depth intervals'; Cl.26 once per BHA run, DD-111 on the run's last day
   and LW-420 on its first; Cl.27 once per well, MB-701/DD-140 on the first day on the well and MB-702/LW-430 on the
   last, LW-430 only where logging while drilling was run; Cl.31 a loss charged as one unit on the day of the loss):
   of the charges of one service for one well-day, run, well or loss, one stands. The contract does not say which of
   two admissible charges is the one that stands (CW Cl.44's later-copy rule is not imported: Q7 C), so where two or
   more could, each is carried as an alternative ('stands'); a charge the contract does not allow on its day never
   stands. The first and last days on a well are the first and last days its Daily Drilling Reports cover (one report
   per well and day, Sch 5 p24; rig-move days are not days on the well, P4 p11).
2. daily limits (Cl.22 p6; Sch 3 Part 5 pp21-22), per well-day (D6), on the charge that stands.
3. annual footage (Sch 2 Part 2 p17; 3A p35): PD-210's rate is taken at 100/96/92% by the metres already drilled on the
   well in the Contract Year; an interval crossing 40,000 or 120,000 m is divided. Open: which metres count (Q11: the
   PD-210 metres charged, or every metre drilled on the well) and whether the Contract Year restarts on 1 January 2026
   (Q14); every reading is computed.
4. the A3 retrospective difference (A3 p42; 36A p35; SoV p37) on protected services already invoiced, and its single
   recipient under Q1 (36A 'on or after' / A3 'after', contract-wide; C: each well's own first invoice).
DS-900, VAT and invoice totals are G5's.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from . import g3_dds, terms
from .g3_core import Trace, headers_by_id, result_keys
from .g4_core import G4Line, apply_options, dims_of, half_even, label_of, not_payable

RUN_EVENTS = {"DD-111": "last", "LW-420": "first"}                     # Cl.26
WELL_EVENTS = {"MB-701": "first", "DD-140": "first", "MB-702": "last", "LW-430": "last"}   # Cl.27
LWD_CODES = {"LW-410", "LW-411", "LW-412"}                             # App G: gamma tool, resistivity tool, density-neutron
LOSS = {"LH-711", "LH-712", "LH-713", "LH-714"}


@dataclass
class Line:
    key: str
    v: dict
    inv: dict | None
    g: G4Line
    idx: int
    ddr: object = None

    @property
    def code(self):
        return self.v.get("service_code") or None

    @property
    def well(self):
        return self.v.get("well_name") or None

    @property
    def date(self):
        return self.v.get("service_date")

    @property
    def ref(self):
        return self.v.get("line_ref") or self.key

    @property
    def submitted(self):
        return (self.inv or {}).get("invoice_date")


@dataclass
class DdsState:
    lines: dict
    groups: list = field(default_factory=list)
    limits: list = field(default_factory=list)
    footage: dict = field(default_factory=dict)
    adjustments: list = field(default_factory=list)


def _union(pairs_by_key: dict) -> list[list]:
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x
    members = {}
    for ms in pairs_by_key.values():
        for m in ms:
            members[m.key] = m
        for a, b in zip(ms, ms[1:]):
            ra, rb = find(a.key), find(b.key)
            if ra != rb:
                parent[ra] = rb
    comp = {}
    for k, m in members.items():
        comp.setdefault(find(k), []).append(m)
    return [sorted(v, key=lambda m: (m.submitted or dt.date.min, m.v.get("invoice_no") or "", m.v.get("line_no") or 0, m.idx))
            for v in comp.values() if len(v) > 1 or v[0].code in WELL_EVENTS or v[0].code in RUN_EVENTS or v[0].code in LOSS]


def run(w, g3res: dict, T=None, a3_rerun: bool = True) -> DdsState:
    T = T or terms.dds()
    rows = w.claims.rows["dds_lines"]
    keys = result_keys(rows)
    heads, _c = headers_by_id(w.claims.rows["dds_headers"])
    lines = [Line(k, row.values, heads[row.values["invoice_no"]].values if row.values.get("invoice_no") in heads else None,
                  G4Line(k, g3res[k]), i, w.ddr.get(row.values.get("report_ref") or "")) for i, (k, row) in enumerate(zip(keys, rows))]
    st = DdsState({ln.key: ln.g for ln in lines})
    _limits_table(T)
    rel = {}
    for ln in lines:
        c = ln.code
        if not c or c == "DS-900":
            continue
        if c != "PD-210" and ln.well and ln.date:
            rel.setdefault(("day", ln.well, ln.date, c), []).append(ln)
        if c in RUN_EVENTS:
            run_no = _run_no(ln)
            if ln.well and run_no is not None:
                rel.setdefault(("run", ln.well, run_no, c), []).append(ln)
        if c in WELL_EVENTS and ln.well:
            rel.setdefault(("well", ln.well, c), []).append(ln)
        if c in LOSS and ln.well:
            e = (ln.ddr.parts.get("E", {}) if ln.ddr is not None else {})
            rel.setdefault(("loss", ln.well, c, e.get("Lost in hole run")), []).append(ln)
    for comp in _union(rel):
        _once(comp, w, T, st)
    _hc630_runs(lines, st)
    _pd210_intervals(lines, T, st)
    _daily_limits(lines, T, st)
    _footage(w, lines, T, st)
    if a3_rerun:
        _a3(w, lines, T, st)
    return st


def _limits_table(T):
    if getattr(T, "daily_limits", None) is not None:
        return
    import yaml
    from .common import ROOT
    t = yaml.safe_load((ROOT / "spec" / "terms_dds.yaml").read_text())["tables"]
    T.daily_limits = {r[0]: Decimal(str(r[1])) for r in t["DDS.T13_DAILY_LIMITS"]["rows"]}
    T.annual_bands = [(Decimal("40000"), Decimal("100")), (Decimal("120000"), Decimal("96")), (None, Decimal("92"))]
    rows = t["DDS.T03_ANNUAL_BANDS"]["rows"]            # 1 to 40,000 100%; 40,001 to 120,000 96%; above 120,000 92%
    T.annual_bands = [(Decimal(str(rows[0][1]).replace(",", "")), Decimal(rows[0][2])),
                      (Decimal(str(rows[1][1]).replace(",", "")), Decimal(rows[1][2])), (None, Decimal(rows[2][2]))]


def _run_no(ln: Line):
    if ln.ddr is None:
        return None
    b = ln.ddr.parts.get("B", {})
    return b.get("Run") if b.get("Run") is not None else ln.ddr.parts.get("A", {}).get("BHA run")


def _well_days(w, well):
    span = w.wells.get(well)
    return (span.first_report, span.last_report) if span else (None, None)


def _lwd_run(w, well) -> bool | None:
    seen = False
    for d in w.ddr.values():
        if d.well != well:
            continue
        seen = True
        codes = {c for c in d.tools_in_hole.values() if c} | {c for c in d.tools_in_run.values() if c}
        if codes & LWD_CODES:
            return True
    return False if seen else None


def _once(comp: list[Line], w, T, st: DdsState) -> None:
    """One charge stands for the service on the well-day / run / well / loss."""
    code = comp[0].code
    cands, notes = [], {}
    for m in comp:
        r = m.g.g3
        if r.payable is False:
            continue
        if code in WELL_EVENTS:
            first, last = _well_days(w, m.well)
            day = first if WELL_EVENTS[code] == "first" else last
            if day is None:
                m.g.add("well_event", "unresolved", "DDS-R15", "Cl.27 (p7)", "well_event_not_on_its_day",
                        "no Daily Drilling Report of the well is supplied: its first and last days are not established")
                continue
            if m.date != day:
                notes[m.key] = ("well_event_not_on_its_day", f"{code} is charged once for the well on its {WELL_EVENTS[code]} day "
                                f"({day}, the {WELL_EVENTS[code]} Daily Drilling Report of {m.well}); charged on {m.date}")
                continue
            if code == "LW-430":
                lwd = _lwd_run(w, m.well)
                if lwd is False:
                    notes[m.key] = ("lwd_not_run", f"LW-430 only where logging while drilling was run (Cl.27): no report of "
                                                   f"{m.well} records an LWD tool (LW-410/411/412) in the hole")
                    continue
        cands.append(m)
    gid = f"DDS-ONCE:{code}|{comp[0].well}|" + ",".join(sorted({str(m.date) for m in comp}))
    rule = ("Cl.27 (p7); Sch 3 Part 6 (p22)" if code in WELL_EVENTS else "Cl.26 (p7)" if code in RUN_EVENTS else
            "Cl.31 (p7)" if code in LOSS else "Cl.29 (p7)")
    fam = "well_event" if code in WELL_EVENTS else "run_event" if code in RUN_EVENTS else "loss_event" if code in LOSS else "duplicate"
    rep = {"well_event": "well_event_repeated", "run_event": "run_event_repeated", "loss_event": "loss_repeated",
           "duplicate": "charged_twice"}[fam]
    st.groups.append({"group": gid, "members": [m.ref for m in comp], "candidates": [m.ref for m in cands], "rule": rule,
                      "not_on_day": {st_ref(st, k): v[1] for k, v in notes.items()}})
    for m in comp:
        if m.key in notes:
            f, d = notes[m.key]
            m.g.add(fam, "finding", "DDS-R15", rule, f, d, gid)
            if fam == "well_event" and any(c is not m and c.date and m.date and c.date < m.date for c in cands):
                m.g.add(fam, "finding", "DDS-R15", rule, "well_event_repeated",
                        f"{code} was already charged for the well on its day ({', '.join(c.ref for c in cands)}): charged "
                        "again", gid)
            if m.g.r.payable is not False:
                not_payable(m.g, d, rule)
    if len(comp) == 1 and not notes:
        comp[0].g.add(fam, "pass" if cands else "n/a", "DDS-R15" if fam != "duplicate" else "DDS-R16", rule,
                      detail="the only charge for it" if cands else "the only charge for it, not payable at G3", ledger=gid)
        return
    same_day = {m.key for m in comp for o in comp if o is not m and o.date == m.date and m.date is not None}
    if fam != "duplicate":
        for m in comp:
            if m.key in same_day and m.g.g3.payable is not False and m.key not in notes:
                m.g.add("duplicate", "finding" if len(cands) == 1 and m is not cands[0] else "unresolved" if len(cands) > 1
                        else "pass", "DDS-R16", "Cl.29 (p7)", "charged_twice" if len(cands) > 1 or m is not cands[0] else None,
                        "the same service is also charged for the well on the same day (Cl.29)", gid)
    if len(cands) == 1:
        c = cands[0]
        c.g.add(fam, "pass", "DDS-R16" if fam == "duplicate" else "DDS-R15", rule, detail="the one charge that stands", ledger=gid)
        for m in comp:
            if m is not c and m.key not in notes:
                m.g.add(fam, "finding", "DDS-R16", rule, rep, f"charged again ({c.ref} stands): not chargeable", gid)
                if m.g.r.payable is not False:
                    not_payable(m.g, f"charged again: the service is charged once ({c.ref})", rule)
        return
    if not cands:
        return
    for m in comp:
        if m in cands:
            m.g.add(fam, "unresolved", "DDS-R16", rule, rep,
                    f"{len(cands)} admissible charges of one {code} ({', '.join(x.ref for x in cands)}): one stands, and the "
                    "contract does not say which (Q7 C) - each carried", gid)
            opts = {}
            for c in cands:
                for k3, a in _opts(m.g.g3).items():
                    lab = label_of({**dims_of(k3), "stands": c.ref})
                    if c is m:
                        opts[lab] = a
                    else:
                        opts[lab] = {"unit_rate": None, "allowed_quantity": Decimal(0), "amount": Decimal("0.00"),
                                     "trace": list(a["trace"]) + [{"op": "note", "label": f"G4: not chargeable if {c.ref} stands "
                                                                   "(charged once)", "source": rule}]}
            apply_options(m.g, opts, {}, rule)
            m.g.r.payable = True
        elif m.key not in notes and m.g.g3.payable is False:
            m.g.add(fam, "n/a", "DDS-R16", rule, detail="not payable at G3: not a charge that could stand")


HC_COUNT, HC_RUN = "count (Cl.30)", "per BHA run (Sch 8)"


def _hc630_runs(lines, st: DdsState) -> None:
    """Q5 residual, HC-630 under the Schedule 8 reading ('each BHA run, as Clause 26 describes', p28): one charge per BHA
    run. Cl.26 gives HC-630 no day, so where a run has more than one admissible HC-630 charge the one that stands is not
    stated (Q7 C): each is carried under that reading. Under the Cl.30 reading (clean-out runs counted per Operating day)
    every charge keeps its G3 value. G3 computed both readings per line (spec/g3_decisions.yaml Q5)."""
    runs = {}
    for ln in lines:
        if ln.code == "HC-630" and ln.well and ln.g.r.payable is not False:
            rn = _run_no(ln)
            if rn is not None:
                runs.setdefault((ln.well, rn), []).append(ln)
    for (well, rn), ms in runs.items():
        gid = f"DDS-HC630-RUN:{well}|run {rn}"
        if len(ms) == 1:
            ms[0].g.add("run_event", "pass", "DDS-R15", "Sch 8 (p28); Cl.30 (p7)", detail="the only HC-630 charge of its BHA run",
                        ledger=gid)
            continue
        st.groups.append({"group": gid, "members": [m.ref for m in ms], "candidates": [m.ref for m in ms],
                          "rule": "Sch 8 (p28) under Q5-HC630 'per BHA run'; Cl.30 (p7) under 'count'"})
        dim = "stands" if not any("stands" in dims_of(k) for m in ms for k in m.g.r.alternatives) else "stands-run"
        for m in ms:
            opts = {}
            for k3, a in _opts(m.g.r).items():
                d3 = dims_of(k3)
                hc = d3.pop("Q5-HC630", None)
                if hc in (None, HC_COUNT):
                    opts[label_of({**d3, "Q5-HC630": HC_COUNT})] = a
                if hc in (None, HC_RUN):
                    for c in ms:
                        lab = label_of({**d3, "Q5-HC630": HC_RUN, dim: c.ref})
                        opts[lab] = a if c is m else {
                            "unit_rate": None, "allowed_quantity": Decimal(0), "amount": Decimal("0.00"),
                            "trace": list(a["trace"]) + [{"op": "note", "label": f"G4: under the Schedule 8 reading HC-630 is charged "
                                                          f"once for BHA run {rn}; not chargeable if {c.ref} stands", "source": "Sch 8 (p28)"}]}
            apply_options(m.g, opts, {dim: ("G5", "Sch 8 (p28) 'each BHA run, as Clause 26 describes': Cl.26 (p7) gives HC-630 no "
                                               "day, so which charge of the run stands is not stated (Q7 C)")}, "Sch 8")
            m.g.r.payable = True
            m.g.add("run_event", "unresolved", "DDS-R15", "Sch 8 (p28); Cl.30 (p7)", "run_event_repeated",
                    f"{len(ms)} HC-630 charges in BHA run {rn}: under the Schedule 8 reading one stands (which is not stated); "
                    "under the Cl.30 reading each is payable (Q5 residual)", gid)


def st_ref(st, key):
    g = st.lines.get(key)
    return g.g3.line_ref if g is not None and g.g3.line_ref else key


def _opts(r) -> dict:
    if r.alternatives:
        return {k: {"unit_rate": a.get("unit_rate"), "allowed_quantity": a.get("allowed_quantity"), "amount": a.get("amount"),
                    "trace": a.get("trace") or []} for k, a in r.alternatives.items()}
    return {None: {"unit_rate": r.unit_rate, "allowed_quantity": r.allowed_quantity, "amount": r.amount, "trace": r.trace}}


def _pd210_intervals(lines, T, st: DdsState) -> None:
    """Cl.29 allows PD-210 more than once on a day for different depth intervals; the same metres charged twice are not
    (Cl.23: each part states its depths). Overlapping intervals of one well-day: one charge keeps the metres."""
    days = {}
    for ln in lines:
        if ln.code == "PD-210" and ln.well and ln.date:
            days.setdefault((ln.well, ln.date), []).append(ln)
    for (well, date), ms in days.items():
        ms = [m for m in ms if m.g.g3.payable is not False]
        known = [m for m in ms if m.v.get("depth_from_m") is not None and m.v.get("depth_to_m") is not None]
        over = [(a, b) for i, a in enumerate(known) for b in known[i + 1:]
                if max(a.v["depth_from_m"], b.v["depth_from_m"]) < min(a.v["depth_to_m"], b.v["depth_to_m"])]
        gid = f"DDS-PD210:{well}|{date}"
        if len(ms) > 1:
            st.groups.append({"group": gid, "members": [m.ref for m in ms], "overlaps": [[a.ref, b.ref] for a, b in over],
                              "rule": "Cl.23 (p6); Cl.29 (p7)"})
        if not over:
            for m in ms:
                if len(ms) > 1:
                    m.g.add("duplicate", "pass", "DDS-R16", "Cl.23 (p6); Cl.29 (p7)",
                            detail="PD-210 charged more than once on the day for different depth intervals (allowed)", ledger=gid)
            continue
        inv = {m.key for pair in over for m in pair}
        for m in ms:
            if m.key not in inv:
                continue
            others = [o for pair in over for o in pair if m in pair and o is not m]
            opts = {}
            for keeper in [m] + others:
                for k3, a in _opts(m.g.g3).items():
                    lab = label_of({**dims_of(k3), "stands": keeper.ref})
                    if keeper is m:
                        opts[lab] = a
                        continue
                    lo, hi = m.v["depth_from_m"], m.v["depth_to_m"]
                    ko, kh = keeper.v["depth_from_m"], keeper.v["depth_to_m"]
                    rest = [(lo, min(hi, ko)), (max(lo, kh), hi)]
                    rest = [(p, q) for p, q in rest if q > p]
                    if not rest:
                        opts[lab] = {"unit_rate": None, "allowed_quantity": Decimal(0), "amount": Decimal("0.00"),
                                     "trace": list(a["trace"]) + [{"op": "note", "label": f"G4: the metres {lo}-{hi} are charged by "
                                                                   f"{keeper.ref} (Cl.29: not twice)", "source": "Cl.23 (p6); Cl.29 (p7)"}]}
                    elif m.g.g3.allowed_quantity == hi - lo and not m.g.g3.alternatives:
                        t = Trace()
                        t.steps = [s for s in a["trace"] if s["op"] not in ("part", "sum_parts", "amount")]
                        t.note(f"G4: {keeper.ref} charges {max(lo, ko)}-{min(hi, kh)} m; the rest of this interval is charged here",
                               "Cl.23 (p6); Cl.29 (p7)")
                        q = Decimal(0)
                        for p0, p1 in rest:
                            for band, pa, pb, rate in g3_dds.pd210_parts(p0, p1, T):
                                t.part(f"band {band}: {pa}-{pb} m", pb - pa, rate, "Sch 2 (p17); Cl.23 (p6); Cl.17 (p6)", mode="half_even")
                                q += pb - pa
                        amt = t.total("amount = sum of depth-band parts", "Cl.23 (p6)")
                        opts[lab] = {"unit_rate": None, "allowed_quantity": q, "amount": amt, "trace": t.steps}
                    else:
                        opts[lab] = {"unit_rate": None, "allowed_quantity": None, "amount": None, "trace": [], "unknown": True}
            if any(v.get("unknown") for v in opts.values()):
                m.g.r.payable, m.g.r.amount_status, m.g.r.amount, m.g.r.alternatives = None, "unresolved", None, {}
                m.g.r.conditions = [{"dimension": "state", "owner": "G5", "basis": "overlapping PD-210 intervals: the value of the "
                                     "metres not charged twice is not established"}]
            else:
                apply_options(m.g, opts, {}, "Cl.29")
                m.g.r.payable = True
            m.g.add("duplicate", "unresolved", "DDS-R16", "Cl.23 (p6); Cl.29 (p7)", "charged_twice",
                    f"PD-210 intervals overlap on {date}: the same metres are charged twice; which charge keeps them is not "
                    "stated (Q7 C) - each carried", gid)


def _daily_limits(lines, T, st: DdsState) -> None:
    """Cl.22: no service charged on any day in a quantity above its daily limit; per well-day (D6)."""
    groups = {}
    for ln in lines:
        if ln.code in T.daily_limits and ln.well and ln.date:
            groups.setdefault((ln.well, ln.date, ln.code), []).append(ln)
    for (well, date, code), ms in groups.items():
        lim = T.daily_limits[code]
        for m in ms:
            r = m.g.r
            if r.payable is False:
                m.g.add("daily_limit", "n/a", "DDS-R14", "Cl.22 (p6)", detail="not chargeable: nothing to limit")
                continue
            opts = _opts(r)
            over = {k: a for k, a in opts.items() if a.get("allowed_quantity") is not None and a["allowed_quantity"] > lim}
            if not over:
                m.g.add("daily_limit", "pass", "DDS-R14", "Cl.22 (p6); Sch 3 Part 5 (pp21-22); D6",
                        detail=f"within the limit {lim} {T.sch1[code]['unit']} per well-day")
                continue
            new = {}
            for k, a in opts.items():
                if k not in over or a.get("unit_rate") is None:
                    new[k] = a
                    continue
                t = Trace()
                t.steps = [s for s in a["trace"] if s["op"] != "amount"]
                t.note(f"G4 daily limit {lim} per well-day (Cl.22; D6): {a['allowed_quantity'] - lim} above it not chargeable",
                       "Cl.22 (p6); Sch 3 Part 5 (pp21-22)")
                v = t.amount(lim, a["unit_rate"], "Cl.18 (p6): quantity x rate")
                t.steps[-1]["value"] = str(half_even(v))
                if v != half_even(v):
                    t.steps[-1]["round"] = "half_even"
                new[k] = {"unit_rate": a["unit_rate"], "allowed_quantity": lim, "amount": half_even(v), "trace": t.steps}
            apply_options(m.g, new, {}, "Cl.22")
            m.g.r.payable = True
            m.g.add("daily_limit", "finding", "DDS-R14", "Cl.22 (p6); Sch 3 Part 5 (pp21-22); D6", "above_daily_limit",
                    f"the well-day's quantity is limited to {lim}", f"DDS-LIM:{well}|{date}|{code}")
            st.limits.append({"well": well, "date": str(date), "code": code, "line": m.ref, "limit": str(lim)})


def _cy(date, q14, T):
    if date is None or date < T.commencement or date > T.expiry:
        return None
    if q14 == "B":
        return "CY1"
    return "CY1" if date < T.commencement.replace(year=T.commencement.year + 1) else "CY2"


def _footage(w, lines, T, st: DdsState) -> None:
    """Schedule 2 Part 2: metres already drilled on the well in the Contract Year before each PD-210 interval, under Q11
    (A: the PD-210 metres charged; B: every metre drilled on the well, Part A depths) and Q14 (A: new Contract Year on
    1 January 2026; B: none). A reading under which any metre lies above 40,000 re-prices the line."""
    wells = {}
    for ln in lines:
        if ln.code == "PD-210" and ln.well:
            wells.setdefault(ln.well, []).append(ln)
    days = {}
    for d in w.ddr.values():
        a = d.parts.get("A", {})
        if d.well in wells and d.date and a.get("Depth start (m MD)") is not None and a.get("Depth end (m MD)") is not None:
            days.setdefault(d.well, []).append((d.date, Decimal(a["Depth start (m MD)"]), Decimal(a["Depth end (m MD)"])))
    for well, ms in wells.items():
        ms.sort(key=lambda m: (m.date or dt.date.max, m.v.get("depth_from_m") or Decimal(0), m.idx))
        before = {}
        for q14 in ("A", "B"):
            for q11 in ("A", "B"):
                for m in ms:
                    cy = _cy(m.date, q14, T)
                    f0 = m.v.get("depth_from_m")
                    if cy is None or f0 is None:
                        continue
                    if q11 == "A":
                        prior = sum(((o.g.g3.allowed_quantity or Decimal(0)) for o in ms if o is not m and o.g.g3.payable
                                     and _cy(o.date, q14, T) == cy and (o.date, o.v.get("depth_from_m") or 0) < (m.date, f0)),
                                    Decimal(0))
                    else:
                        prior = sum((max(Decimal(0), e - s) for dd, s, e in days.get(well, []) if _cy(dd, q14, T) == cy and dd < m.date),
                                    Decimal(0))
                        prior += sum((max(Decimal(0), min(e, f0) - s) for dd, s, e in days.get(well, []) if dd == m.date), Decimal(0))
                    before[(m.key, q14, q11)] = prior
        edge = T.annual_bands[0][0]
        st.footage[well] = {f"{st_ref(st, k)}|Q14:{a}|Q11:{b}": str(v) for (k, a, b), v in before.items()}
        for m in ms:
            vals = {k: v for k, v in before.items() if k[0] == m.key}
            if not vals or m.g.r.payable is False:
                continue
            qty = m.g.g3.allowed_quantity or Decimal(0)
            if all(v + qty <= edge for v in vals.values()):
                m.g.add("footage", "pass", "DDS-R12", "Sch 2 Part 2 (p17); 3A (p35)",
                        detail=f"at most {max(vals.values()) + qty} m in the Contract Year under every reading (Q11, Q14): 100%")
                continue
            _footage_reprice(m, vals, T)


def _annual_segments(start: Decimal, qty: Decimal, T):
    out, a = [], Decimal(0)
    for top, pct in T.annual_bands:
        hi = top if top is not None else Decimal("1e30")
        q = min(start + qty, hi) - max(start, a)
        if q > 0:
            out.append((q, pct))
        a = hi
    return out


def _footage_reprice(m: Line, vals: dict, T) -> None:
    """Price the interval's metres by depth band (Sch 2) and by the metres already drilled in the Contract Year (Part 2):
    each piece at the depth rate taken at its percentage, rounded half to even, and the amount in cents (Cl.17)."""
    g = m.g
    f0, t0 = m.v.get("depth_from_m"), m.v.get("depth_to_m")
    if g.g3.alternatives or f0 is None or t0 is None or g.g3.allowed_quantity != t0 - f0:
        g.r.payable, g.r.amount_status, g.r.amount, g.r.alternatives = None, "unresolved", None, {}
        g.r.conditions = [{"dimension": "state", "owner": "G5", "basis": "annual footage above 40,000 m on a charge whose metres are "
                           "not placed by its depths: the percentage of each metre is not established"}]
        g.add("footage", "unresolved", "DDS-R12", "Sch 2 Part 2 (p17)", "footage_band_divided", "not established")
        return
    opts, divided = {}, set()
    for (key, q14, q11), prior in vals.items():
        t = Trace()
        t.steps = [s for s in g.g3.trace if s["op"] not in ("part", "sum_parts", "amount")]
        t.note(f"G4 annual footage: {prior} m already drilled on {m.well} in the Contract Year (Q14:{q14}, Q11:{q11})",
               "Sch 2 Part 2 (p17); 3A (p35)")
        pos = prior
        for band, pa, pb, rate in g3_dds.pd210_parts(f0, t0, T):
            for q, pct in _annual_segments(pos, pb - pa, T):
                r = half_even(rate * pct / 100)
                t.part(f"band {band}: {q} m of {pa}-{pb} m at {pct}% of {rate} = {r}", q, r,
                       "Sch 2 (p17); Sch 2 Part 2 (p17); Cl.17 (p6): half to even", mode="half_even")
                if pct != 100:
                    divided.add((q14, q11))
            pos += pb - pa
        amt = t.total("amount = sum of depth and footage parts", "Cl.23 (p6); Sch 2 Part 2 (p17)")
        opts[label_of({"Q14": q14, "Q11": q11})] = {"unit_rate": None, "allowed_quantity": g.g3.allowed_quantity, "amount": amt,
                                                    "trace": t.steps}
    apply_options(g, opts, {"Q11": ("G5", "Sch 2 Part 2 (p17) 'metres already drilled on the well': the PD-210 metres "
                                          "charged (A) or every metre drilled (B); spec/open_questions.yaml Q11")}, "footage")
    g.r.payable = True
    status = "pass" if not divided else "finding" if len(divided) == len(vals) else "unresolved"
    g.add("footage", status, "DDS-R12", "Sch 2 Part 2 (p17); 3A (p35)", "footage_band_divided" if divided else None,
          "metres above 40,000 in the Contract Year priced at the Part 2 percentage" +
          ("" if status != "unresolved" else " under some readings only (Q11, Q14)"))


def _a3(w, lines, T, st: DdsState) -> None:
    retro = [i for i in T.instruments if i.retrospective]
    if not retro:
        return
    ctx = g3_dds.input_context(w)
    heads = {h.values["invoice_no"]: h.values for h in w.claims.rows["dds_headers"]}
    for ins in retro:
        eligible = [ln for ln in lines if f"36A protection ({ins.id})" in ln.g.g3.readings]
        by_line = {}
        for ln in eligible:
            g = ln.g
            if g.r.payable is False:
                by_line[ln.ref] = {"difference": "0.00", "basis": "not chargeable: nothing invoiced to re-price"}
                continue
            if g.r.payable is None or (g.changed and not any("stands" in k for k in g.r.alternatives)):
                by_line[ln.ref] = {"difference": None, "basis": "the line's value after state is not established here"}
                continue
            new3 = g3_dds.evaluate(ln.v, {**(ln.inv or {}), "invoice_date": ins.issued}, ln.ddr, T=T, inputs=ctx.get(ln.key))
            new = _opts(new3)
            old = _opts(g.r)
            diff = {}
            for k, a in old.items():
                kd = dims_of(k)
                stands = kd.pop("stands", None)
                base = label_of(kd)
                n = new.get(base, new.get(None))
                if stands is not None and stands != ln.ref:
                    diff[k] = Decimal("0.00")
                else:
                    diff[k] = None if n is None or a["amount"] is None or n["amount"] is None else n["amount"] - a["amount"]
            by_line[ln.ref] = {"difference": {(k or ""): (str(v) if v is not None else None) for k, v in diff.items()},
                               "invoice": ln.v.get("invoice_no"), "well": ln.well, "service_date": str(ln.date)}
        subs = sorted({(h["invoice_date"], k) for k, h in heads.items() if h.get("invoice_date")})
        on_or_after = [(d, k) for d, k in subs if d >= ins.issued]
        after = [(d, k) for d, k in subs if d > ins.issued]
        first = lambda xs: ([k for d, k in xs if d == xs[0][0]] if xs else [])  # noqa: E731
        fa, fb = first(on_or_after), first(after)
        per_well = {}
        for ln in eligible:
            wl = ln.well
            if wl in per_well:
                continue
            xs = sorted((h["invoice_date"], k) for k, h in heads.items() if h.get("well_name") == wl and h.get("invoice_date")
                        and h["invoice_date"] >= ins.issued)
            f = first(xs)
            per_well[wl] = f[0] if len(f) == 1 else ({"tie": f} if f else None)
        from .g4_cw import _sum_options
        st.adjustments.append({
            "instrument": ins.id, "issued": str(ins.issued), "effective": str(min(e for _c, e, _v in ins.rate_rows)),
            "eligible_lines": len(eligible), "by_line": by_line, "total": _sum_options(by_line),
            "recipient": {"Q1:A (36A: first invoice submitted on or after the date of issue)": fa[0] if len(fa) == 1 else ({"tie": fa} if fa else None),
                          "Q1:B (A3: first invoice submitted after the date of issue)": fb[0] if len(fb) == 1 else ({"tie": fb} if fb else None),
                          "Q1:C (each well's own first invoice on or after the date of issue)": per_well},
            "wells_without_recipient_under_C": sorted(k for k, v in per_well.items() if v is None),
            "posted": "once per reading; under C once per well (the difference on that well's services); a tie stays open (Q1)",
            "basis": "36A (p35); A3 (p42); SoV (p37); Cl.32 (p8) each well invoiced separately"})
