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
import itertools
from dataclasses import dataclass, field
from decimal import Decimal

from . import g3_dds, terms
from .g3_core import Trace, headers_by_id, result_keys
from .g4_cw import difference_sign, open_sides, possibly_protected
from .g4_core import G4Line, apply_options, base, dims_of, half_even, label_of, local_dim, not_payable, recipient_of

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
    _undated_repeats(lines, rel, st)
    _hc630_runs(lines, st)
    _pd210(w, lines, T, st)
    _daily_limits(lines, T, st)
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
            if m.date is None:
                # G4-B03: a charge whose date is not established may be on the well's day: it stays a candidate, and
                # whether it is on its day is not established (never a finding by comparing a missing date)
                m.g.add("well_event", "unresolved", "DDS-R15", "Cl.27 (p7)", "well_event_not_on_its_day",
                        f"{code} is charged once on the well's {WELL_EVENTS[code]} day ({day}); the charge's date is not established")
            elif m.date != day:
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
                    lab = label_of({**dims_of(k3), local_dim("stands", gid): c.ref})
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


def _undated_repeats(lines, rel: dict, st: DdsState) -> None:
    """G4-B03: a charge (other than PD-210, whose metres are handled by interval) whose service date is not established
    may be for the same well-day as any dated charge of its service on the well (Cl.29): each such well-day group gains
    it as a possible standing charge. A dated charge keeps its value only in the scenarios in which it stands; which
    stands is left to Q7 C (G5), where a candidate of unknown date or submission can never be the one chosen."""
    undated = [ln for ln in lines if ln.code and ln.code not in ("DS-900", "PD-210") and ln.date is None and ln.well
               and ln.g.g3.payable is not False]
    for u in undated:
        for key, ms in rel.items():
            if key[0] != "day" or (key[1], key[3]) != (u.well, u.code):
                continue
            kept = [m for m in ms if m.g.r.payable is not False]
            if not kept:
                continue
            gid = f"DDS-ONCE:{u.code}|{u.well}|{key[2]}|undated"
            refs = [m.ref for m in kept] + [u.ref]
            st.groups.append({"group": gid, "members": refs, "candidates": refs, "possible": [u.ref], "rule": "Cl.29 (p7)",
                              "not_on_day": {}})
            for m in kept:
                opts = _opts(m.g.r)
                new = {}
                sd = local_dim("stands", gid)
                for k, a in opts.items():
                    d = dims_of(k)
                    new[label_of({**d, sd: m.ref})] = a
                    new[label_of({**d, sd: u.ref})] = {
                        "unit_rate": None, "allowed_quantity": Decimal(0), "amount": Decimal("0.00"),
                        "trace": list(a.get("trace") or []) + [{"op": "note", "label": f"G4: not chargeable if {u.ref} (date not "
                                                                "established) is for the same well-day and stands", "source": "Cl.29 (p7)"}]}
                apply_options(m.g, new, {}, "Cl.29")
                m.g.r.payable = True
                m.g.add("duplicate", "unresolved", "DDS-R16", "Cl.29 (p7)", "charged_twice",
                        f"{u.ref} charges {u.code} on {u.well} with no service date: it may be the same well-day", gid)


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
        dim = local_dim("stands-run", gid)
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


MAX_ALLOC = 256      # joint allocations enumerated per well-day; beyond it the members carry honest bounds (unresolved)


def _interval(m: Line):
    f, t = m.v.get("depth_from_m"), m.v.get("depth_to_m")
    return (f, t) if f is not None and t is not None and t > f else None


def _segments(ms: list, poss: list = ()) -> list[tuple]:
    """Elementary segments of the union of the members' intervals: [(a, b, [members covering it])]. A possible member
    (a charge whose date or depths are not established, review finding A-1) is given as (charge, [(from, to)] or None):
    it is listed among the charges covering every segment it may cover - inside its stated interval, or the well-day's
    reported interval where it states none, or anywhere where neither is established - and never makes a segment of its
    own (its metres are not established)."""
    pts = {x for m in ms for x in _interval(m)}
    lo, hi = min(pts), max(pts)
    for _p, ivs in poss:
        pts |= {x for iv in (ivs or ()) for x in iv if lo < x < hi}
    pts = sorted(pts)
    out = []
    for a, b in zip(pts, pts[1:]):
        cov = [m for m in ms if _interval(m)[0] <= a and b <= _interval(m)[1]]
        if cov:
            cov += [p for p, ivs in poss if ivs is None or any(f <= a and b <= t for f, t in ivs)]
            out.append((a, b, cov))
    return out


def _nom_group(m: Line):
    """The call-off nomination a PD-210 charge's chargeability depends on (Cl.23): its well's section; None where G3
    carries no nomination condition on it."""
    if any(c["dimension"] == "nomination" for c in m.g.g3.conditions):
        return (m.well, m.v.get("hole_section"))
    return None


def _certain(m: Line, grp) -> bool:
    """Review finding A-2: the charge's metres are certainly performance-chargeable PD-210 metres (Q11 A) for the
    valuation of a charge whose nomination group is grp - admissible at G3 (payable True: an admissibility G3 left
    unresolved is only possible), chargeable under every G3 reading it carries, and its section's nomination (a call-off
    fact) either not in question or the same as the valued charge's (whose own value exists only where its section is
    nominated). Otherwise its metres are a possible earlier contributor: counted in the upper bound only."""
    g3 = m.g.g3
    if g3.payable is not True:
        return False
    if any(a.get("allowed_quantity") is None or Decimal(str(a["allowed_quantity"])) <= 0 for a in g3.alternatives.values()):
        return False
    ng = _nom_group(m)
    return ng is None or ng == grp


def _alloc_label(assign: dict) -> str:
    """The value of an allocation dimension: each contested segment and the charge that keeps it (no ':' or '|')."""
    return ";".join(f"{a}-{b}>{ref}" for (a, b), ref in sorted(assign.items())) or "none"


def alloc_dim(well: str, date) -> str:
    """The allocation dimension of one well-day, namespaced by its evidence group (no ':' or '|' in the name)."""
    return f"alloc@{well}/{date}"


def _pd210(w, lines, T, st: DdsState) -> None:
    """PD-210 state per well (G4-B01, G4-B02).

    1. Allocation (Cl.23 p6; Cl.29 p7). PD-210 may be charged more than once on a day for DIFFERENT depth intervals; the
       same metres are charged once. The intervals of one well-day are cut at every boundary into elementary segments;
       a segment covered by more than one admissible charge is contested and belongs to exactly one of them. Which one
       is not stated (Q7 C; G5 applies the earlier-invoice policy): every assignment of every contested segment
       is a scenario - the complete joint domain, including several keepers at once, zero for a charge whose metres
       are all kept by others, and exact duplicates. Beyond MAX_ALLOC assignments the members carry honest bounds.
    2. Annual footage (Sch 2 Part 2 p17; 3A p35). The metres already drilled in the Contract Year are counted over
       UNIQUE metres: under Q11 A the union of the admissible PD-210 intervals (a contested segment once, whoever keeps
       it), under Q11 B the Part A depths of the reports; Q14 A/B the Contract Year. A charge whose date or metres are
       not established stays a possible earlier contributor (a range, never omitted); a price that depends on it is
       unresolved.
    3. Valuation. A charge's value in a scenario is the sum, over the segments it keeps, of each depth-band piece at the
       band rate taken at its annual percentage (half to even, Cl.17). A charge that keeps its whole interval at 100%
       under every reading keeps its G3 value."""
    wells = {}
    for ln in lines:
        if ln.code == "PD-210" and ln.well:
            wells.setdefault(ln.well, []).append(ln)
    reports, gaps = {}, {}
    for d in w.ddr.values():
        if d.well is not None and d.well not in wells:
            continue
        a = d.parts.get("A", {})
        if d.well and d.date and a.get("Depth start (m MD)") is not None and a.get("Depth end (m MD)") is not None:
            reports.setdefault(d.well, []).append((d.date, Decimal(a["Depth start (m MD)"]), Decimal(a["Depth end (m MD)"])))
        else:
            # review finding A-3: a report whose well, date or Part A depths are not established drilled an unknown
            # number of metres (at least 0) on a day that may be earlier: under Q11 B it bounds nothing above
            for wl in ([d.well] if d.well else list(wells)):
                gaps.setdefault(wl, []).append(d.date)
    edge = T.annual_bands[0][0]
    for well, ms in wells.items():
        adm = [m for m in ms if m.g.g3.payable is not False]
        placed = [m for m in adm if m.date and _interval(m)]
        loose = [m for m in adm if m not in placed]            # date or depths not established: possible contributors
        days = {}
        for m in placed:
            days.setdefault(m.date, []).append(m)
        day_iv = {}
        for dd, s0, e0 in reports.get(well, []):
            day_iv.setdefault(dd, []).append((s0, e0))
        # ---------------------------------------------------------------- 1. joint allocation per well-day
        alloc = {}          # date -> {"segs": [...], "combos": [assign dict] or None (too many), "gid": ...}
        for date, dms in days.items():
            # review finding A-1: a charge of the well whose date or depths are not established may charge the same
            # metres as a charge of this day (Cl.29): a possible contestant of every segment it may cover
            poss = []
            for p in loose:
                if p.date is not None and p.date != date:
                    continue
                iv = _interval(p)
                known_day = p.date == date and day_iv.get(date) and date not in gaps.get(well, []) and \
                    None not in gaps.get(well, [])
                poss.append((p, [iv] if iv else (day_iv[date] if known_day else None)))
            segs = _segments(dms, poss)
            contested = [(a, b, cov) for a, b, cov in segs if len(cov) > 1]
            gid = f"DDS-PD210:{well}|{date}"
            n = 1
            for _a, _b, cov in contested:
                n *= len(cov)
            combos = None
            if contested and n <= MAX_ALLOC:
                combos = []
                for choice in itertools.product(*[cov for _a, _b, cov in contested]):
                    combos.append({(a, b): c.ref for (a, b, _cov), c in zip(contested, choice)})
            alloc[date] = {"segs": segs, "contested": contested, "combos": combos if contested else [{}], "gid": gid, "n": n}
            possible = sorted({c.ref for _a, _b, cov in contested for c in cov if c not in dms})
            if len(dms) > 1 or contested:
                st.groups.append({"group": gid, "members": [m.ref for m in dms],
                                  "overlaps": [[f"{a}-{b}", [c.ref for c in cov]] for a, b, cov in contested],
                                  "union_m": str(sum((b - a for a, b, _c in segs), Decimal(0))),
                                  "allocations": n if contested else 1, "rule": "Cl.23 (p6); Cl.29 (p7)",
                                  **({"possible": possible} if possible else {})})
        # ---------------------------------------------------------------- 2. annual positions over unique metres
        # position of each unique segment: (lo, hi) metres already drilled in its Contract Year before it
        loose_m = []
        for m in loose:
            q = m.g.g3.allowed_quantity
            if q is None and m.date:
                q = sum((max(Decimal(0), e - s) for dd, s, e in reports.get(well, []) if dd == m.date), Decimal(0)) or None
            loose_m.append((m, q))
        useg = sorted(((date, a, b, [c for c in cov if c in days[date]]) for date, al in alloc.items()
                       for a, b, cov in al["segs"]), key=lambda x: x[:3])
        memo = {}

        def pos_of(date, a, b, q14, q11, grp, useg=useg, loose_m=loose_m, well=well, memo=memo):
            """(lo, hi) metres already drilled on the well in the Contract Year before metre a of this day, for the
            valuation of a charge of nomination group grp; None outside the term (G3 decides payability there)."""
            k = (date, a, b, q14, q11, grp)
            if k in memo:
                return memo[k]
            cy = _cy(date, q14, T)
            if cy is None:
                memo[k] = None
                return None
            if q11 == "A":
                before = [(b2 - a2, cov2) for d2, a2, b2, cov2 in useg if _cy(d2, q14, T) == cy and (d2, a2) < (date, a)]
                lo = sum((x for x, cov2 in before if any(_certain(c, grp) for c in cov2)), Decimal(0))
                hi = sum((x for x, _c in before), Decimal(0))
                for m, q in loose_m:                    # could be earlier in the same Contract Year
                    if m.date is None or (_cy(m.date, q14, T) == cy and m.date <= date):
                        hi = None if q is None or hi is None else hi + q
            else:
                lo = sum((max(Decimal(0), e - s) for dd, s, e in reports.get(well, []) if _cy(dd, q14, T) == cy and dd < date),
                         Decimal(0))
                lo += sum((max(Decimal(0), min(e, a) - s) for dd, s, e in reports.get(well, []) if dd == date), Decimal(0))
                hi = None if any(dd is None or (_cy(dd, q14, T) == cy and dd <= date) for dd in gaps.get(well, [])) else lo
            memo[k] = (lo, hi)
            return memo[k]

        def seg_group(cov):
            gs = {_nom_group(c) for c in cov}
            return next(iter(gs)) if len(gs) == 1 else None

        st.footage[well] = {}
        for date, a, b, cov in useg:
            for q14 in ("A", "B"):
                for q11 in ("A", "B"):
                    r = pos_of(date, a, b, q14, q11, seg_group(cov))
                    if r is not None:
                        st.footage[well][f"{date}|{a}-{b}|Q14:{q14}|Q11:{q11}"] = [str(r[0]), None if r[1] is None else str(r[1])]
        # ---------------------------------------------------------------- 3. valuation per charge
        for m in placed:
            al = alloc[m.date]
            f0, t0 = _interval(m)
            mine = [(a, b) for a, b, cov in al["segs"] if m in cov]
            contested_mine = any(m in cov for _a, _b, cov in al["contested"])
            grp = _nom_group(m)
            all_100 = all(r is not None and r[1] is not None and r[1] + (b - a) <= edge
                          for (a, b) in mine for q14 in ("A", "B") for q11 in ("A", "B")
                          for r in [pos_of(m.date, a, b, q14, q11, grp)]) or _cy(m.date, "A", T) is None
            if not contested_mine and all_100:
                if len(al["segs"]) > 1 or len(days[m.date]) > 1:
                    m.g.add("duplicate", "pass", "DDS-R16", "Cl.23 (p6); Cl.29 (p7)",
                            detail="PD-210 charged more than once on the day for different depth intervals (allowed)", ledger=al["gid"])
                m.g.add("footage", "pass", "DDS-R12", "Sch 2 Part 2 (p17); 3A (p35)",
                        detail="100% under every reading (Q11, Q14), unique metres counted once")
                continue
            g = m.g
            exact = not g.g3.alternatives and g.g3.allowed_quantity == t0 - f0
            if not exact or al["combos"] is None:
                _pd210_unresolved(m, "the charge's metres are not its full stated interval (25A tolerance or alternatives)" if not exact
                                  else f"{al['n']} joint allocations of the contested metres exceed {MAX_ALLOC}: bounds only", al)
                continue
            opts, divided, unknown = {}, set(), False
            for combo in al["combos"]:
                kept = [(a, b) for a, b in mine if combo.get((a, b), m.ref) == m.ref]
                for q14 in ("A", "B"):
                    for q11 in ("A", "B"):
                        t = Trace()
                        t.steps = [x for x in g.g3.trace if x["op"] not in ("part", "sum_parts", "amount")]
                        if al["contested"]:
                            t.note(f"G4 allocation {_alloc_label(combo)}: this charge keeps "
                                   f"{', '.join(f'{a}-{b}' for a, b in kept) or 'no metres'} (Cl.23, Cl.29: each metre charged once)",
                                   "Cl.23 (p6); Cl.29 (p7)")
                        qty = Decimal(0)
                        for a, b in kept:
                            r = pos_of(m.date, a, b, q14, q11, grp)
                            if r is None:                   # outside the term: G3 decides payability
                                r = (Decimal(0), Decimal(0))
                            lo, hi = r
                            if hi is None or _pct_set(lo, b - a, T) != _pct_set(hi, b - a, T):
                                unknown = True
                                continue
                            t.note(f"G4 annual footage: {lo} m already drilled on {m.well} in the Contract Year before {a} m "
                                   f"(Q14:{q14}, Q11:{q11}; unique metres)", "Sch 2 Part 2 (p17); 3A (p35)")
                            p = lo
                            for band, pa, pb, rate in g3_dds.pd210_parts(a, b, T):
                                for q, pct in _annual_segments(p, pb - pa, T):
                                    rr = half_even(rate * pct / 100)
                                    t.part(f"band {band}: {q} m of {pa}-{pb} m at {pct}% of {rate} = {rr}", q, rr,
                                           "Sch 2 (p17); Sch 2 Part 2 (p17); Cl.17 (p6): half to even", mode="half_even")
                                    if pct != 100:
                                        divided.add((id(combo), q14, q11))
                                p += pb - pa
                            qty += b - a
                        amt = t.total("amount = sum of depth and footage parts", "Cl.23 (p6); Sch 2 Part 2 (p17)") if qty else Decimal("0.00")
                        lab = {"Q14": q14, "Q11": q11}
                        if al["contested"]:
                            lab[alloc_dim(well, m.date)] = _alloc_label(combo)
                        opts[label_of(lab)] = {"unit_rate": None, "allowed_quantity": qty, "amount": amt, "trace": t.steps}
            if unknown:
                _pd210_unresolved(m, "an earlier charge's date or metres are not established and the annual percentage depends "
                                     "on them", al)
                continue
            apply_options(g, opts, {"Q11": ("G5", "Sch 2 Part 2 (p17) 'metres already drilled on the well': the PD-210 metres "
                                                  "charged (A) or every metre drilled (B); spec/open_questions.yaml Q11"),
                                    alloc_dim(well, m.date): ("G5", "Cl.23 (p6), Cl.29 (p7): the same metres are charged once; which "
                                                                 "charge keeps each contested segment is not stated (Q7 C)")},
                          "PD-210 state")
            g.r.payable = True
            if al["contested"]:
                g.add("duplicate", "unresolved", "DDS-R16", "Cl.23 (p6); Cl.29 (p7)", "charged_twice",
                      f"PD-210 intervals overlap on {m.date}: contested metres are charged once; {al['n']} allocations carried",
                      al["gid"])
            n_readings = 4 * max(1, len(al["combos"]))
            status = "pass" if not divided else "finding" if len(divided) == n_readings else "unresolved"
            g.add("footage", status, "DDS-R12", "Sch 2 Part 2 (p17); 3A (p35)", "footage_band_divided" if divided else None,
                  f"annual footage over unique metres ({n_readings} scenarios)")
        for m in loose:
            m.g.add("footage", "n/a", "DDS-R12", "Sch 2 Part 2 (p17)",
                    detail="the charge's date or depths are not established: carried as a possible earlier contributor (range)")


def _pct_set(start: Decimal, qty: Decimal, T) -> tuple:
    return tuple(pct for _q, pct in _annual_segments(start, qty, T))


def _pd210_unresolved(m: Line, why: str, al: dict) -> None:
    g = m.g
    g.r.payable, g.r.amount_status, g.r.amount, g.r.alternatives = None, "unresolved", None, {}
    g.r.conditions = [c for c in g.r.conditions if c["dimension"] == "nomination"] + [
        {"dimension": "state", "owner": "G5", "basis": f"PD-210 state not established: {why}"}]
    g.add("footage", "unresolved", "DDS-R12", "Cl.23 (p6); Cl.29 (p7); Sch 2 Part 2 (p17)", "charged_twice"
          if al["contested"] else "footage_band_divided", why, al["gid"])


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


def _annual_segments(start: Decimal, qty: Decimal, T):
    out, a = [], Decimal(0)
    for top, pct in T.annual_bands:
        hi = top if top is not None else Decimal("1e30")
        q = min(start + qty, hi) - max(start, a)
        if q > 0:
            out.append((q, pct))
        a = hi
    return out


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
            if g.r.payable is None or (g.changed and not any(base(d) in ("stands", "stands-run") for k in g.r.alternatives
                                                             for d in dims_of(k))):
                by_line[ln.ref] = {"difference": None, "basis": "the line's value after state is not established here",
                                   "sign": difference_sign(T, ins, ln.code, ln.date)}
                continue
            new3 = g3_dds.evaluate(ln.v, {**(ln.inv or {}), "invoice_date": ins.issued}, ln.ddr, T=T, inputs=ctx.get(ln.key))
            new = _opts(new3)
            old = _opts(g.r)
            diff = {}
            for k, a in old.items():
                kd = dims_of(k)
                sk = [d for d in kd if base(d) in ("stands", "stands-run")]
                if any(base(d) == "alloc" for d in kd):
                    diff[k] = None           # a PD-210 allocation is not re-priced at the new rates here: not established
                    continue
                rest = label_of({d: x for d, x in kd.items() if d not in sk})
                n = new.get(rest, new.get(None))
                if any(kd[d] != ln.ref for d in sk):          # another charge of one of its groups stands
                    diff[k] = Decimal("0.00")
                else:
                    diff[k] = None if n is None or a["amount"] is None or n["amount"] is None else n["amount"] - a["amount"]
            by_line[ln.ref] = {"difference": {(k or ""): (str(v) if v is not None else None) for k, v in diff.items()},
                               "invoice": ln.v.get("invoice_no"), "well": ln.well, "service_date": str(ln.date),
                               "sign": difference_sign(T, ins, ln.code, ln.date)}
        for ln in lines:
            # review finding A-4: a charge whose 36A protection G3 could not establish (its invoice's submission date,
            # its service date or its rate not established) is a possible contributor - the account is not established
            if ln in eligible or ln.g.r.payable is False or not possibly_protected(ln.code, ln.date, ln.submitted, ins):
                continue
            by_line[ln.ref] = {"difference": None, "possible": True, "invoice": ln.v.get("invoice_no"), "well": ln.well,
                               "service_date": None if ln.date is None else str(ln.date),
                               "sign": difference_sign(T, ins, ln.code, ln.date),
                               "basis": "possibly protected (36A): its submission date, service date or rate is not established"}
        subs = sorted({(h["invoice_date"], k) for k, h in heads.items() if h.get("invoice_date")})
        on_or_after = [(d, k) for d, k in subs if d >= ins.issued]
        after = [(d, k) for d, k in subs if d > ins.issued]
        first = lambda xs: ([k for d, k in xs if d == xs[0][0]] if xs else [])  # noqa: E731
        fa, fb = first(on_or_after), first(after)
        undated = sorted(k for k, h in heads.items() if not h.get("invoice_date"))       # G4-B03: possible recipients
        per_well = {}
        for ln in eligible:
            wl = ln.well
            if wl in per_well:
                continue
            xs = sorted((h["invoice_date"], k) for k, h in heads.items() if h.get("well_name") == wl and h.get("invoice_date")
                        and h["invoice_date"] >= ins.issued)
            f = first(xs)
            per_well[wl] = recipient_of(f, sorted(k for k, h in heads.items() if h.get("well_name") == wl and not h.get("invoice_date")))
        from .g4_cw import _sum_options
        st.adjustments.append({
            "instrument": ins.id, "issued": str(ins.issued), "effective": str(min(e for _c, e, _v in ins.rate_rows)),
            "eligible_lines": len(eligible), "possible_lines": sum(1 for v in by_line.values() if v.get("possible")),
            "by_line": by_line, "total": {**_sum_options(by_line), **open_sides(by_line)},
            "recipient": {"Q1:A (36A: first invoice submitted on or after the date of issue)": recipient_of(fa, undated),
                          "Q1:B (A3: first invoice submitted after the date of issue)": recipient_of(fb, undated),
                          "Q1:C (each well's own first invoice on or after the date of issue)": per_well},
            "wells_without_recipient_under_C": sorted(k for k, v in per_well.items() if v is None),
            "posted": "once per reading; under C once per well (the difference on that well's services); a tie stays open (Q1)",
            "basis": "36A (p35); A3 (p42); SoV (p37); Cl.32 (p8) each well invoiced separately"})
