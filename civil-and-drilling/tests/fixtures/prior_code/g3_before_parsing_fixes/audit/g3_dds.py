"""G3 drilling (DDS-2025-118): local entitlement and pricing of one invoice line.

Line-level checks (checks 1-8, local part): identity, term, submission window, period, unit (Cl.35; remedy is
Q11), report evidence (the quoted Daily Drilling Report for the well and day, signatures, the Schedule 5 part: Cl.37),
status and section as recorded (Cl.19, R7), quantities by service family (persons Cl.22, coordinator Q5, tool days
Cl.28, hours Cl.21/21A/P10 with Q4 alternatives, counts Cl.30, metres Cl.23-25/25A, run events Cl.26, well events
Cl.27, loss Cl.31/31A/P12), rate version by service date (instruments in issue order; 36A protection of invoices
submitted before a retrospective amendment's issue; D2), indexation (17A), build-up section -> class -> standby ->
S2/A2 discount (Cl.18, 17B) with half-even rounding at every step (Cl.17), and line arithmetic.

Not decided here (G4/G5): daily limits, duplicates, once-per-run and once-per-well counting, the A3 adjustment
posting, the DS-900 discount, VAT and totals. They are listed as g4_dependencies (or deferred for DS-900).

G3: the well class is the call-off's (Cl.4; P2, P3) and no call-off is supplied, so a class-rated
service is priced under every class (the invoice header's class is disclosed as the contractor's statement); PD-210's
allowed metres, parts and amount agree under 25A (a tolerance difference on a charge crossing a band edge is carried
as alternatives); the Q5 residual (which recorded hours DD-120/RM-530 count; HC-630 per count or per BHA run) is
computed under every reading. Every result's remaining alternatives name their condition and owning gate.
"""
from __future__ import annotations

import datetime as dt
import itertools
from collections import defaultdict
from decimal import Decimal

from . import links, records_dds, terms
from .g3_core import Inputs, LineResult, Trace, empty, headers_by_id, result_keys
from .g3_core import q as to_cents

CONTRACT_REF = "DDS-2025-118"
CONTRACTOR = "MERIDIAN DOWNHOLE SERVICES LTD"       # agreement particulars (p1)
ALWAYS_G4 = ["duplicate_scope (DDS-R16, Cl.29)"]
PERSONS = {"DD-101", "MW-301", "LW-401", "PD-201"}
COORDINATOR = "DD-102"
HOURLY = {"DD-120", "RM-530"}
COUNTS = {"DD-130": "Gyro surveys", "LW-413": "Pressure points", "HC-610": "Wiper trips", "HC-630": "Clean-out runs"}
METRE_TOOL = {"LW-410": "LW-410", "LW-411": "LW-411", "LW-412": "LW-412", "RM-510": "RM-511"}   # Cl.24; Cl.25 underreamer = App G 'hole opener'
PERFORMANCE_SECTIONS = {'12-1/4"', '8-1/2"'}                                         # App A (p29)
RUN_EVENTS = {"DD-111": ("last", "DD-110"), "LW-420": ("first", None)}              # Cl.26
WELL_EVENTS = {"DD-140", "MB-701", "MB-702", "LW-430"}                               # Cl.27
LOSS = {"LH-711", "LH-712", "LH-713", "LH-714"}
Q4_READINGS = ("A", "B", "C")
Q4_WORKING = None       # Q4 stays open: every reading is computed and carried (see spec/g3_decisions.yaml)


def _half(tr: Trace, label: str, src: str = "Cl.17 (p6): each step to the cent, half to even") -> Decimal:
    return tr.round(label, "half_even", src)


def base_rate(code, date, submitted, T, tr, readings):
    cands = T.rate_candidates(code, date, submitted)
    skipped = [i for i in T.instruments if i.retrospective and submitted is not None and submitted < i.issued
               and any(c == code and eff <= date for c, eff, _ in i.rate_rows)]
    if cands:
        order, eff, value, src = max(cands, key=lambda c: (c[0], c[1]))
        tr.start("base rate (latest-issued instrument applicable on the service date)", value, src + "; SoV p37 (issue order); D2")
    else:
        tr.start("base rate", T.sch1[code]["rate"], f"DDS.T01_SCH1 {code} (Sch 1 pp15-16)")
    for ins in skipped:
        tr.note(f"{ins.id} not applied: invoice submitted {submitted} before its issue {ins.issued}", "36A (p35); A3 (p42)")
        readings.append(f"36A protection ({ins.id})")
    if code in T.rsi_items:
        month = f"{date:%Y-%m}"
        tr.mul(f"x Rig Services Index {month} / base 100.00", T.rsi[month] / T.rsi_base, f"DDS.T05_RSI {month} (Sch 2C p18); 17A (p35)")
        _half(tr, "indexed rate, half to even", "17A (p35)")


def build_up(code, status, section, well_class, date, T, tr):
    """Cl.18 factors after the base: section (Operating only, 17B), class (not PD-210, 17B), standby %, discount."""
    if code in T.section_rated:
        if status == "Standby":
            tr.note("no hole-section factor on a Standby day", "17B (p35); OV-DDS-01")
        else:
            tr.mul(f"section {section}", T.section[section], "DDS.T08_SECTION_FACTORS (Sch 3 Part 1 p20); Cl.18 (p6)")
            _half(tr, "after section factor")
    if code in T.class_rated and code != "PD-210":
        tr.mul(f"well class {well_class} (one admissible class: the call-off is not supplied, Cl.4)", T.class_factor[well_class],
               "DDS.T10_CLASS_FACTORS (Sch 3 Part 2 p20); Cl.4 (p3); P2, P3 (p11)")
        _half(tr, "after class factor")
    if status == "Standby" and code in T.standby and T.standby[code] is not None:
        tr.mul(f"standby {T.standby[code]}%", T.standby[code] / 100, "DDS.T12_STANDBY (Sch 3 Part 3 pp20-21); Cl.20 (p6); P13 (p11)")
        _half(tr, "after standby percentage")
    disc = T.discount(code, date)
    if disc:
        tr.mul(f"discount {disc.value}%", 1 - disc.value / 100, disc.source + "; S2/A2 last factor")
        _half(tr, "after discount")


def pd210_parts(start: Decimal, end: Decimal, T):
    """Split [start, end] at the depth-band boundaries; a boundary depth belongs to the shallower band (Cl.23)."""
    parts = []
    for lo, hi, rate, band in T.depth_bands:
        a, b = max(start, lo), min(end, hi) if hi is not None else end
        if b > a:
            parts.append((band, a, b, rate))
    return parts


# Cl.34 (p8): what every charge states (the PD-210 depths are handled with the performance footage, _pd210)
CL34 = {"service_date": "date", "service_code": "service code", "description": "description", "hole_section": "hole section",
        "day_status": "status of the day", "quantity": "quantity", "unit": "unit", "unit_rate": "rate", "amount": "amount"}


def evaluate(line: dict, inv: dict | None, ddr, T=None, question_readings: dict | None = None, inputs: Inputs | None = None) -> LineResult:
    """line/inv: typed claim fields (inv None: the line's invoice header is not found); ddr: the G2 report or None.
    inputs: G2 provenance and the report's G2-unresolved fields. An input G2 left empty is recorded with its
    source, never defaulted: a check that needs it is unresolved, and a value that depends on it is unresolved (owner G5)
    unless an established consequence (not payable) already decides the charge."""
    T = T or terms.dds()
    qr = question_readings or {}
    inputs = inputs or Inputs()
    L, H, D = inputs.line_src, inputs.header_src, inputs.doc_src
    unresolved = []         # why the value cannot be established here (an input G2 left empty); never defaulted
    code = line.get("service_code")
    r = LineResult("DDS", line.get("line_ref") or "", code or "")
    tr, readings = Trace(), []
    if code == "DS-900":
        r.add("identification", "n/a", "DDS-R20", "Cl.38 (p8); P11 (p11)", detail="invoice-level discount charge; valued with the invoice total (G5)")
        r.amount_status, r.payable, r.family = "deferred", None, "DDS-DISCOUNT"
        r.reasons.append("DS-900 is recomputed from the invoice's corrected service subtotal at G5 (Cl.38, P11)")
        return r
    for f, what in CL34.items():
        if empty(line.get(f)):
            r.input_gap(f, "finding", "claim_field_missing", "Cl.34 (p8)" + ("; Cl.19 (p6)" if f in ("hole_section", "day_status") else ""),
                        f"the charge states no {what}", L)
    sch = T.sch1.get(code) if not empty(code) else None
    sd = line.get("service_date")
    payable, reasons = True, []
    if inv is None:
        r.input_gap("invoice_no", "unresolved", "input_unresolved", "Cl.32 (p8)",
                    f"the charge's invoice {line.get('invoice_no')!r} is carried by {len(inputs.header_copies)} invoice rows "
                    f"({', '.join(inputs.header_copies)}): which one the charge belongs to is not established, and no header fact "
                    "is taken from either" if inputs.header_copies else
                    f"the charge's invoice {line.get('invoice_no')!r} is not among the invoices G2 loaded: its header facts are unknown", L)
        inv = {}
    # 1 identity --------------------------------------------------------------------------------------------
    cref, contractor = inv.get("contract_ref"), inv.get("contractor")
    if empty(cref):
        r.input_gap("contract_ref", "unresolved", "input_unresolved", "p1 particulars; Cl.32 (p8)",
                    "the invoice states no contract reference: its identity is not confirmed (procedural, G3-D1)", H)
    elif cref != CONTRACT_REF:
        r.add("identity", "finding", "DDS-R01", "p1 particulars; Cl.32 (p8)", "contract_ref_variant", cref)
    elif empty(contractor):
        r.input_gap("contractor", "unresolved", "input_unresolved", "p1 particulars",
                    "the invoice names no contractor: its identity is not confirmed (procedural, G3-D1)", H)
    elif contractor.upper() != CONTRACTOR:
        r.add("identity", "finding", "DDS-R01", "p1 particulars", "contractor_mismatch", contractor)
    else:
        r.add("identity", "pass", "DDS-R01", "p1 particulars")
    well = line.get("well_name")
    if empty(well):
        r.input_gap("well_name", "unresolved", "input_unresolved", "Cl.32 (p8); Cl.15 (p5)",
                    "the charge states no well: its report cannot be tied to the well it is for", L)
        unresolved.append("well_name: the charge states no well, so its report cannot be tied to it")
    elif not empty(inv.get("well_name")) and well != inv.get("well_name"):
        r.add("identity", "finding", "DDS-R01", "Cl.32 (p8): each well invoiced separately", "line_well_differs_from_invoice",
              f"{well} vs {inv.get('well_name')}")
    elif empty(inv.get("well_name")):
        r.input_gap("well_name (invoice)", "unresolved", "input_unresolved", "Cl.32 (p8)",
                    "the invoice states no well: one well per invoice is not confirmed (procedural, G3-D1)", H)
    # 2 term ------------------------------------------------------------------------------------------------
    if empty(sd):
        r.add("term", "unresolved", "DDS-R03", "p1 particulars; A1 (p39); A2 (p41)", detail="service_date not stated (Cl.34)")
        unresolved.append("service_date: the term, the rate in force, the index/FX month and the report's day all follow "
                          "the date of the charge (Cl.34), which it does not state")
    elif not (T.commencement <= sd <= T.expiry):
        r.add("term", "finding", "DDS-R03", "p1 particulars; A2 2.1 (p41)", "out_of_term", f"{sd} outside {T.commencement}..{T.expiry}")
        payable = False
        reasons.append("service outside the term as extended is not chargeable (A1/A2)")
    else:
        r.add("term", "pass", "DDS-R03", "p1 particulars; A1 (p39); A2 (p41)")
    # 3 window and period -----------------------------------------------------------------------------------
    ps, pe, idate = inv.get("period_start"), inv.get("period_end"), inv.get("invoice_date")
    for f, v, what in (("period_start", ps, "first"), ("period_end", pe, "last")):
        if empty(v):
            r.input_gap(f, "finding", "claim_field_missing", "Cl.32 (p8)", f"the invoice states no {what} day of its period", H)
    if empty(idate):
        r.input_gap("invoice_date", "unresolved", "input_unresolved", "Cl.33 (p8); 36A (p35)",
                    "the invoice date is not stated: the window and any 36A protection cannot be established", H)
    if empty(pe) or empty(idate):
        r.add("window", "unresolved", "DDS-R04", "Cl.33 (p8)", detail="period_end or invoice_date not stated")
    elif idate < pe:
        r.add("window", "finding", "DDS-R04", "Cl.33 (p8)", "submitted_early", f"{idate} before period end {pe}")
    elif idate > pe + dt.timedelta(days=T.window_days):
        r.add("window", "finding", "DDS-R04", "Cl.33 (p8)", "submitted_late", f"{(idate - pe).days} days after period end")
    else:
        r.add("window", "pass", "DDS-R04", "Cl.33 (p8)")
    if empty(ps) or empty(pe) or empty(sd):
        r.add("period", "unresolved", "DDS-R02", "Cl.32 (p8)", detail="period_start, period_end or service_date not stated")
    elif not (ps <= sd <= pe):
        r.add("period", "finding", "DDS-R02", "Cl.32 (p8)", "outside_period", f"{sd} outside {ps}..{pe}")
    else:
        r.add("period", "pass", "DDS-R02", "Cl.32 (p8)")
    if sch is None:                    # the service cannot be valued
        r.family = "DDS-UNSCHEDULED"
        if empty(code):                # it may be the invoice-level DS-900, which no finding on the day decides (Cl.38)
            return _unresolved(r, tr, reasons, unresolved + ["service_code: the service cannot be identified without its code (Cl.34)"])
        if not payable:                # a code outside Schedule 1 (not DS-900): an established consequence above decides it
            return _finish(r, tr, False, reasons)
        r.add("identification", "unresolved", "DDS-R06", "Sch 1 (pp15-16)", "code_not_in_schedule_1")
        r.amount_status, r.payable = "unresolved", None
        return r
    # 4 unit (Cl.35; remedy Q11) ------------------------------------------------------------------------------
    unit = line.get("unit")
    wrong_unit = not empty(unit) and unit != sch["unit"]
    if empty(unit):
        r.add("unit", "unresolved", "DDS-R08", "Cl.35 (p8); Cl.34 (p8)", detail="unit not stated")
        unresolved.append("unit: whether the charge is stated in the Schedule 1 unit (Cl.35) cannot be established (Cl.34)")
    elif wrong_unit:
        r.add("unit", "finding", "DDS-R08", "Cl.35 (p8)", "wrong_unit", f"billed {unit}, Schedule 1 {sch['unit']}")
    else:
        r.add("unit", "pass", "DDS-R08", "Cl.35 (p8)")
    # 5 report evidence ---------------------------------------------------------------------------------------
    part_needed = T.sch5.get(code)
    r.family = _family(code)
    if ddr is None or inputs.report_copies:
        ref = line.get("report_ref")
        if inputs.report_copies:
            # G2 indexes reports by their Report number and keeps the first file carrying it: which delivered file is the
            # report for this charge is not established, and no fact is taken from either (identity is never inferred)
            r.input_gap("report_ref", "unresolved", "input_unresolved", "Cl.15 (p5); 19A (p35)",
                        f"report {ref!r}: the Report number is also carried by {', '.join(inputs.report_copies)} (G2 indexes "
                        f"only {ddr.file if ddr is not None else 'one'}): which file is the report for this charge is not "
                        "established", L)
            why = "report_ref: more than one delivered file carries the cited Report number"
        elif inputs.unindexed_reports and not empty(ref):
            r.input_gap("report_ref", "unresolved", "input_unresolved", "Cl.15 (p5); 19A (p35)",
                        f"report {ref!r} is not among the indexed reports, and G2 could not index every report file "
                        "(a Report number unresolved): whether it exists is not established", L)
            why = "report_ref: the report may be one G2 could not index"
        else:
            r.add("evidence", "finding", "DDS-R05", "Cl.15 (p5); 19A (p35)", "report_missing",
                  "report_ref blank: the charge cites no Daily Drilling Report" if empty(ref) else f"no report {ref}")
            return _finish(r, tr, False, reasons + ["no Daily Drilling Report evidences the day"])
        if not payable:                # an established consequence above decides the charge whatever its report
            return _finish(r, tr, False, reasons)
        return _unresolved(r, tr, reasons, unresolved + [why])
    gaps, twice = inputs.doc_gaps, inputs.doc_repeated
    # a key the report writes twice is unresolved in G2 (it keeps the last): no fact of the report is taken from it
    a = {k: v for k, v in ddr.parts.get("A", {}).items() if f"A.{k}" not in twice}
    b = {k: v for k, v in ddr.parts.get("B", {}).items() if f"B.{k}" not in twice}
    unknown = []            # report facts G2 could not establish: the evidence is unknown, not defective
    for f in sorted(twice):
        r.input_gap(f, "unresolved", "input_unresolved", "Cl.15 (p5)", "report key written twice: which value holds is not "
                    "established (G2)", D)

    def gap(field, why):
        unknown.append(field)
        r.input_gap(field, "unresolved", "input_unresolved", "Cl.15 (p5); App G (p36)", f"report {why} (G2)", D)
        unresolved.append(f"report {field}: {why}")
    if ddr.date is None:
        gap("Date", "the report's date is not established")
    elif not empty(sd) and ddr.date != sd:
        r.add("evidence", "finding", "DDS-R05", "19A (p35); Cl.15 (p5)", "report_date_mismatch", f"report {ddr.report} is for {ddr.date}")
        payable = False
        reasons.append("the quoted report is for another day: nothing recorded for the charged day (Cl.19A)")
    if ddr.well is None:
        gap("Well", "the report's well is not established")
    elif not empty(well) and ddr.well != well:
        r.add("evidence", "finding", "DDS-R05", "Cl.15 (p5)", "well_mismatch", f"report well {ddr.well}")
        payable = False
        reasons.append("the quoted report is for another well")
    unsigned = not (ddr.company_signed and ddr.driller_signed)
    if unsigned:
        r.add("evidence", "finding", "DDS-R05", "Cl.15 (p5); R8 (p14)", "report_unsigned", "; ".join(
            f"{k} missing or a placeholder" for k, ok in ((records_dds.SIG["company"], ddr.company_signed),
                                                           (records_dds.SIG["driller"], ddr.driller_signed)) if not ok))
    if part_needed:
        if part_needed not in ddr.parts and "part" in gaps:
            gap("part", f"has a part heading G2 could not read, so whether Part {part_needed} is present is not established")
        elif part_needed not in ddr.parts:
            r.add("evidence", "finding", "DDS-R05", "Cl.37 (p8); Sch 5 (p24)", "required_part_missing", f"Part {part_needed} absent")
            payable = False
            reasons.append(f"Schedule 5 Part {part_needed} not completed: record not delivered, not payable (Cl.37; Q3 reading A)")
            r.readings.append("Q3:A")
        elif unsigned:
            payable = False
            reasons.append("Schedule 5 document not delivered in signed form (Cl.15, Cl.37; Q3 reading A)")
            r.readings.append("Q3:A")
    if not any(c.check == "evidence" and c.status == "finding" for c in r.checks):
        r.add("evidence", "pass" if not unknown else "unresolved", "DDS-R05", "Cl.15 (p5); Cl.37 (p8); Sch 5 (p24)")
    if "A" not in ddr.parts:           # recorded; each fact of Part A a service needs then blocks it below
        r.input_gap("Part A", "unresolved", "input_unresolved", "Cl.15 (p5); R7 (p14)",
                    "report has no Part A (operations summary) G2 could read", D)
    # 6 status and section as recorded (Cl.19, R7) --------------------------------------------------------------
    status, section = a.get("Status"), a.get("Hole section")
    status_matters = ((code in T.standby and (T.standby[code] is None or T.standby[code] != 100)) or code == "DD-121"
                      or code in METRE_TOOL or code in T.section_rated)
    if status is None and status_matters:
        gap("A.Status", "Status of the day is not established (Cl.19)")
    if section is None and (code == "PD-210" or (code in T.section_rated and status != "Standby")):
        gap("A.Hole section", "Hole section is not established (Cl.19)")
    if status is not None and not empty(line.get("day_status")) and line.get("day_status") != status:
        r.add("status", "finding", "DDS-R05", "Cl.19 (p6); R7 (p14)", "status_mismatch", f"line {line.get('day_status')}, report {status}")
    if section is not None and not empty(line.get("hole_section")) and line.get("hole_section") != section:
        r.add("status", "finding", "DDS-R05", "Cl.19 (p6)", "section_mismatch", f"line {line.get('hole_section')}, report {section}")
    if status == "Standby" and code in T.standby and T.standby[code] is None:
        r.add("status", "finding", "DDS-R13", "Cl.20 (p6); Sch 3 Part 3 (pp20-21)", "not_chargeable_on_standby", f"{code} marked not chargeable")
        payable = False
        reasons.append("service not chargeable on a Standby day in any quantity (Cl.20)")
    if code == "DD-121" and status is not None and status != "Standby":
        r.add("status", "finding", "DDS-R13", "Cl.21 (p6); Sch 3 Part 4 (p21)", "not_chargeable_on_operating", "DD-121 only on a Standby day")
        payable = False
        reasons.append("DD-121 is charged only on a Standby day (Sch 3 Part 4)")
    # 7 quantity ----------------------------------------------------------------------------------------------
    billed = line.get("quantity")
    if empty(billed):
        r.add("quantity", "unresolved", "DDS-R07", "Cl.34 (p8)", detail="quantity not stated")
        unresolved.append("quantity: the charge states no quantity (Cl.34)")
    tools = {c for c in ddr.tools_in_hole.values() if c} if "A.In the hole" not in twice else set()
    run_tools = {c for c in ddr.tools_in_run.values() if c} if "B.Tools in run" not in twice else set()
    hole_listed, run_listed = "In the hole" in a, "Tools in run" in b
    hole_unread = any(c is None for c in ddr.tools_in_hole.values())
    run_unread = any(c is None for c in ddr.tools_in_run.values())
    q_opts = None           # label -> supported quantity where a reading the text does not settle gives more than one
    part_sets = None        # PD-210: label -> [(band, from, to, quantity, rate)]
    supported, basis = billed, ""
    if code in PERSONS or code == COORDINATOR:
        rec = Decimal(ddr.crew.get(code, 0))
        supported, basis = rec, f"{rec} persons recorded on the rig (Cl.22; App G)"
        if code == COORDINATOR:
            basis = f"{rec} coordinator(s) recorded ('night man', App G) per day (Sch 8 intro p27; Q5 reading A)"
            r.readings.append("Q5:A")
        if "Crew on tour" not in a:
            gap("A.Crew on tour", "Crew on tour is " + ("written twice" if "A.Crew on tour" in twice else "not recorded"))
        elif "A.Crew on tour" in gaps and (empty(billed) or rec < billed):
            gap("A.Crew on tour", "Crew on tour has entries G2 could not read, and those recorded do not support the charge")
    elif code in links.TOOL_DAY_SERVICES:
        bcode = links.TOOL_BASIS.get(code)
        present = bcode in tools
        supported = Decimal("1") if present else Decimal("0")
        basis = f"tool of {bcode} {'recorded' if present else 'not recorded'} in the hole (Cl.28; App G)"
        if not present and (not hole_listed or hole_unread):
            gap("A.In the hole", "In the hole " + ("is not recorded" if not hole_listed else "has terms G2 could not read")
                + f": whether the tool of {bcode} was in the hole is not established")
        elif not present:
            r.add("quantity", "finding", "DDS-R06", "Cl.28 (p7); App G (p36)", "tool_not_in_hole", basis)
            payable = False
            reasons.append("a day rental is charged only where the report records the tool in the hole (Cl.28)")
    elif code in HOURLY:
        # what the report does not establish (hours 0 to 24 on a day; whether it is the run's first day, Q4 reading C)
        # matters only if some admissible value of it changes what the charge is allowed under some reading
        missing = [k for k in ("Circulating hours", "Back-reaming hours") if a.get(k) is None]
        first_unknown = b.get("Run first day") is None
        needed = []
        if code == "DD-120" and "DD-120" not in tools and (not hole_listed or hole_unread):
            needed.append("A.In the hole")
        elif missing or first_unknown:
            variants = []
            for combo in itertools.product(*[range(25) for _ in missing]):
                aa = {**a, **dict(zip(missing, combo))}
                for fd in ([ddr.date, None] if first_unknown else [b.get("Run first day")]):
                    variants.append(_hours(code, aa, {**b, "Run first day": fd}, ddr, tools, T, qr)[0])
            if not all(v is None for v in variants) and (empty(billed) or any(
                    v is None or {min(billed, x) for x in v.values()} != {billed} for v in variants)):
                needed += [f"A.{k}" for k in missing] + (["B.Run first day"] if first_unknown else [])
            else:
                for k in [f"A.{k}" for k in missing] + (["B.Run first day"] if first_unknown else []):
                    r.input_gap(k, "n/a", None, "Cl.21, Cl.30 (pp6-7); 21A (p35)", f"report {k.split('.', 1)[1]} not "
                                f"established (G2); every reading supports the billed hours whatever it recorded", D)
        for k in needed:
            gap(k, f"{k.split('.', 1)[-1]} is not established (needed for the hours, Cl.21, Cl.30; Q4, Q5)")
        if not needed and not (missing or first_unknown):
            q_opts, basis = _hours(code, a, b, ddr, tools, T, qr)
            if q_opts is None:
                r.add("quantity", "finding", "DDS-R06", "Cl.21 (p6); Cl.28 (p7)", "tool_not_in_hole", basis)
                payable, supported = False, Decimal("0")
                reasons.append("DD-120 needs the rotary steerable in the hole")
            else:
                supported = max(q_opts.values())
        elif not needed:
            if code == "DD-120" and "DD-120" not in tools:
                q_opts, basis = _hours(code, {**a, **{k: 0 for k in missing}}, b, ddr, tools, T, qr)
                r.add("quantity", "finding", "DDS-R06", "Cl.21 (p6); Cl.28 (p7)", "tool_not_in_hole", basis)
                payable, supported, q_opts = False, Decimal("0"), None
                reasons.append("DD-120 needs the rotary steerable in the hole")
            else:
                supported, basis = billed, (f"the billed {billed} hours are supported under every reading whatever the report "
                                            f"recorded for {', '.join(missing + (['the run first day'] if first_unknown else []))}")
    elif code in COUNTS:
        if a.get(COUNTS[code]) is None:
            gap(f"A.{COUNTS[code]}", f"{COUNTS[code]} is not established (the count charged, Cl.30)")
        else:
            rec = Decimal(a.get(COUNTS[code]))
            supported, basis = rec, f"{COUNTS[code]}: {rec} recorded on the report (Cl.30)"
            if code == "HC-630":
                per_run = min(rec, Decimal("1"))
                basis += (f"; Q5 residual: counted as recorded (Cl.30) = {rec}, or one charge per BHA run (Sch 8 row 'each BHA "
                          f"run') = {per_run}")
                q_opts = {"Q5-HC630:count (Cl.30)": rec, "Q5-HC630:per BHA run (Sch 8)": per_run}
                if qr.get("Q5_HC630") == "counts":
                    q_opts = {"Q5-HC630:count (Cl.30)": rec}
                r.g4_dependencies.append("once_per_run (HC-630 under the Schedule 8 reading, Q5 residual): one charge per BHA run")
    elif code in METRE_TOOL:
        needed = [k for k in ("Depth start (m MD)", "Depth end (m MD)", "Status") if a.get(k) is None]
        if METRE_TOOL[code] not in tools and (not hole_listed or hole_unread):
            needed.append("In the hole")
        for k in needed:
            gap(f"A.{k}", f"{k} is not established (the metres drilled with the tool, Cl.24, Cl.25)")
        if not needed and not empty(billed):
            supported, basis, ok = _metres(code, line, a, tools, r, T, tr)
            if not ok:
                payable = False
                reasons.append(basis)
    elif code == "PD-210":
        needed = [k for k in ("Depth start (m MD)", "Depth end (m MD)") if a.get(k) is None]
        if section is None or needed:
            for k in needed:
                gap(f"A.{k}", f"{k} is not established (the metres drilled on the day, Cl.23)")
        elif not empty(billed) or line.get("depth_from_m") is None or line.get("depth_to_m") is None:
            supported, basis, ok, part_sets = _pd210(line, a, r, T, inputs)
            if ok is None:
                unresolved.append(basis)
            elif not ok:
                payable = False
                reasons.append(basis)
    elif code in RUN_EVENTS:
        which, tool = RUN_EVENTS[code]
        key = "Run last day" if which == "last" else "Run first day"
        day = b.get(key)
        needed = [key] if day is None else []
        if tool and tool not in run_tools and (not run_listed or run_unread):
            needed.append("Tools in run")
        if not tool and b.get("Radioactive source carried") is None:
            needed.append("Radioactive source carried")
        for k in needed:
            gap(f"B.{k}", f"{k} is not established (the run event, Cl.26)")
        if not needed and not empty(sd):
            ok_tool = (tool in run_tools) if tool else (b.get("Radioactive source carried") is True)
            supported = Decimal("1") if ok_tool and day == sd else Decimal("0")
            basis = f"run {b.get('Run')}: {'motor in the run' if tool else 'source carried'}={ok_tool}; {which} day {day} (Cl.26)"
            if not ok_tool or day != sd:
                r.add("quantity", "finding", "DDS-R15", "Cl.26 (p7)", "run_event_not_supported", basis)
                payable = False
                reasons.append("per-run charge not supported on this day by the run record (Cl.26)")
        r.g4_dependencies.append("once_per_run (DDS-R15)")
    elif code in WELL_EVENTS:
        supported, basis = Decimal("1"), "once for the well (Cl.27); first/last day of the well is G4 state"
        r.g4_dependencies.append("once_per_well (DDS-R15): first/last day on the well")
    elif code in LOSS:
        supported, basis = Decimal("1"), "one unit on the day of the loss (Cl.31)"
    else:
        r.add("quantity", "unresolved", "DDS-R07", "Sch 8 (pp27-28)", "quantity_rule_missing", code)
        return _finish(r, tr, None, reasons + ["no quantity rule"])
    blocked = bool(unresolved)
    if code not in METRE_TOOL and code != "PD-210":
        allowed = None if (empty(billed) or blocked) else min(billed, supported)
        least = min(q_opts.values()) if q_opts else supported
        if blocked or empty(billed):
            pass
        elif wrong_unit:
            r.add("quantity", "n/a", "DDS-R08", "Cl.35 (p8)", detail=f"billed in {unit}; supported {supported} {sch['unit']} (Q11)")
        elif billed > supported and not any(c.finding == "tool_not_in_hole" for c in r.checks) and code not in RUN_EVENTS:
            r.add("quantity", "finding", "DDS-R07", "Cl.21-22, 28, 30 (pp6-7)", "quantity_above_report", f"billed {billed}; {basis}")
        elif billed > least and code not in RUN_EVENTS:
            r.add("quantity", "unresolved", "DDS-R07", "Cl.21, 30 (pp6-7); 21A (p35); Sch 8 (pp27-28)", "quantity_above_report",
                  f"billed {billed} exceeds the supported quantity under some readings only; {basis}")
        elif not any(c.check == "quantity" for c in r.checks):
            r.add("quantity", "pass", "DDS-R07", "Cl.21-31 (pp6-7)", detail=basis)
    else:
        allowed = None if blocked else supported
    # 8 rate --------------------------------------------------------------------------------------------------
    well_class = inv.get("well_class")
    rates = {}              # label -> (rate, Trace); more than one only for class-rated services (F1)
    priceable = not empty(sd) and not (status is None and status_matters) and not (
        section is None and code in T.section_rated and status != "Standby")
    if code == "PD-210":
        tr.note("PD-210 priced by Schedule 2 depth band; no class factor (17B, D1); annual footage band 100% (per-well "
                "records bound 10,002 m < 40,000 m, Q11)", "Sch 2 (p17); Cl.23 (p6); 17B (p35); spec/question_scopes.json Q11_DDS")
    elif code in LOSS:
        e = {k: v for k, v in ddr.parts.get("E", {}).items() if f"E.{k}" not in twice}
        if (ddr.lost_tool_code is None or "Lost in hole tool" not in e) and ("E.Lost in hole tool" in gaps or "Lost in hole tool" not in e) \
                and "E" in ddr.parts:
            gap("E.Lost in hole tool", "Lost in hole tool is not established (Part E, Cl.31)")
        elif e.get("Circulating hours accumulated on the well") is None and "E" in ddr.parts and ddr.lost_tool_code == code:
            gap("E.Circulating hours accumulated on the well", "Circulating hours accumulated on the well is not established (Cl.31)")
        elif priceable and "E" in ddr.parts:
            try:
                rate = _loss_value(code, sd, ddr, r, tr, T, twice)
            except KeyError as ke:
                rate = None
                unresolved.append(f"no published FX for {ke} (Sch 2D p19): the loss value cannot be converted")
            if rate is None and not unresolved:
                payable = False
                reasons.append("loss not evidenced as this tool (Part E)")
            elif rate is not None:
                rates[None] = (rate, tr)
    elif priceable:
        classed = code in T.class_rated
        if classed:
            r.readings.append(f"well class not evidenced: no call-off supplied (Cl.4; P2, P3); the invoice header states "
                              f"{well_class} (the claim, not authority); every class priced")
        subs = [idate] if not empty(idate) else [None, sd]          # submitted after every issue, or on the service date
        try:
            for cls in (list(T.class_factor) if classed else [None]):
                built = []
                for s_ in subs:
                    trk = Trace()
                    trk.steps = list(tr.steps)
                    rd = []
                    base_rate(code, sd, s_, T, trk, rd)
                    build_up(code, status, section, cls, sd, T, trk)
                    built.append((trk.value, trk, rd))
                if any(x[0] != built[0][0] for x in built[1:]):
                    unresolved.append("invoice_date: a retrospective instrument changes this rate for invoices submitted before "
                                      "its issue (36A), and the invoice date is not stated")
                    rates = {}
                    break
                rates[f"class:{cls}" if cls else None] = (built[0][0], built[0][1])
                if not readings:
                    readings.extend(built[0][2])
        except KeyError as e:
            rates = {}
            tr.note(f"not priced: no published index for {e} (outside the tables)", "Sch 2C (p18)")
            unresolved.append(f"no published index for {e} (Sch 2C p18): the rate cannot be built for the service month")
        if classed and len(rates) > 1:
            r.condition("class", "G5", f"Cl.4 (p3): the call-off's well class governs; P2, P3 (p11); App A (p29); no call-off "
                        f"supplied; the invoice header states {well_class} (the claim, not authority)")
    r.readings += readings
    if code == "PD-210":
        single = [ps for ps in (part_sets or {}).values()]
        rate = single[0][0][4] if len(single) == 1 and len(single[0]) == 1 else None
        if part_sets:
            r.rates = {f"band:{b}": pr for ps in part_sets.values() for b, _f, _t, _q, pr in ps}
    else:
        rate = next(iter(rates.values()))[0] if len(rates) == 1 else None
        r.rates = {k or "": rt for k, (rt, _t) in rates.items()}
    r.unit_rate = rate
    ur = line.get("unit_rate")
    if empty(ur):
        r.add("rate", "unresolved", "DDS-R09", "Cl.17, Cl.18 (p6); Cl.34 (p8)", detail="unit_rate not stated")
    elif len(rates) > 1:
        match = [k for k, (rt, _t) in rates.items() if rt == ur]
        listing = ", ".join(f"{k} {rt}" for k, (rt, _t) in rates.items())
        if match:
            r.add("rate", "unresolved", "DDS-R09", "Cl.4 (p3); Cl.17, Cl.18 (p6)", "rate_differs",
                  f"billed {ur} is the rate under {', '.join(match)} ({listing}); the class is not established (G5)")
        else:
            r.add("rate", "finding", "DDS-R09", "Cl.17, Cl.18 (p6); instruments pp37-42", "rate_differs",
                  f"billed {ur} is the rate under no admissible class ({listing})")
    elif rate is not None and ur != rate:
        r.add("rate", "finding", "DDS-R09", "Cl.17, Cl.18 (p6); instruments pp37-42", "rate_differs", f"billed {ur}, contract {rate}")
    elif rate is not None:
        r.add("rate", "pass", "DDS-R09", "Cl.17, Cl.18 (p6)")
    # 9 arithmetic --------------------------------------------------------------------------------------------
    amt = line.get("amount")
    if empty(billed) or empty(ur) or empty(amt):
        r.add("arithmetic", "unresolved", "DDS-R20", "Cl.18 (p6); Cl.36 (p8)", detail="quantity, unit_rate or amount not stated")
    elif billed * ur != amt:
        r.add("arithmetic", "finding", "DDS-R20", "Cl.18 (p6); Cl.36 (p8)", "amount_arithmetic",
              f"{billed} x {ur} = {billed * ur}, billed {amt}")
    else:
        r.add("arithmetic", "pass", "DDS-R20", "Cl.18 (p6)")
    # G4 dependencies ----------------------------------------------------------------------------------------
    if code in T.limited:
        r.g4_dependencies.append("daily_limit (DDS-R14, D6)")
    if any("36A protection" in x for x in r.readings):
        r.g4_dependencies.append("a3_adjustment (DDS-R21): difference posted once on a later invoice")
    if payable and allowed == 0 and not q_opts:
        payable = False
        reasons.append("nothing chargeable under the quantity rules")
    if not payable:
        return _finish(r, tr, payable, reasons)
    if unresolved:          # established consequences above stand; otherwise the value waits on the missing input
        return _unresolved(r, tr, reasons, unresolved)
    # every admissible result: quantity readings x class rates, or PD-210 part sets; Q11 (wrong unit) adds reading B
    options = {}
    if code == "PD-210":
        for pl, parts in part_sets.items():
            t = Trace()
            t.steps = list(tr.steps)
            dom = next((c["domain"] for c in r.conditions if c.get("domain")), None)
            if dom and dom["mode"] == "bounds":
                t.note(f"allocation domain: {dom['count']} ways of placing {dom['allowed_m']} m in steps of {dom['step_m']} m; "
                       f"this is the {pl.split(':', 1)[1].split(',')[0]}-amount way; {dom['not_listed']}",
                       "Cl.23 (p6); Cl.34 (p8); 25A (p35); B2")
            where = {p["band"]: p["measured"] for p in dom["parts"]} if dom else {}
            for band, pa, pb, qty, prate in parts:
                m = where.get(band, f"{pa}-{pb}")
                shown = "" if m == f"{pa}-{pb}" else f" (report measures {m} m)" if m else " (report measures no metres)"
                t.part(f"band {band}: {pa}-{pb} m{shown}" + ("" if qty == pb - pa else f", {qty} m charged (25A)"), qty, prate,
                       f"DDS.T02_DEPTH_BANDS band {band} (Sch 2 p17); Cl.23 (p6): a boundary depth belongs to the shallower band; "
                       f"Cl.17 (p6): every amount in cents, half to even", mode="half_even")
            amt = t.total("amount = sum of depth-band parts", "Cl.23 (p6): each band part priced at its own rate")
            options[pl] = (sum((x[3] for x in parts), Decimal(0)), parts[0][4] if len(parts) == 1 else None, amt, t.steps)
    else:
        for (ql, sup), (rl, (rt, trk)) in itertools.product((q_opts or {None: supported}).items(), rates.items()):
            alw = min(billed, sup)
            step = _amount_step(alw, rt)
            options["|".join(x for x in (ql, rl) if x)] = (alw, rt, Decimal(step["value"]), trk.steps + [step])
    if wrong_unit:
        options = {("Q11:A|" + k if k else "Q11:A"): v for k, v in options.items()}
        options["Q11:B"] = (Decimal("0"), None, Decimal("0.00"),
                            [{"op": "note", "label": "Q11 reading B: a charge in another unit is unsupported", "source": "Cl.35 (p8)"}])
    return _finish(r, tr, True, reasons, _collapse(options))


def _amount_step(qty: Decimal, rate: Decimal) -> dict:
    """Cl.18: quantity x rate; Cl.17: every amount is ascertained in cents (a fraction of a cent rounded half to even)."""
    v = qty * rate
    step = {"op": "amount", "label": "amount = quantity x rate", "quantity": str(qty), "rate": str(rate), "value": str(v),
            "source": "Cl.18 (p6): quantity x rate so built up; Cl.17 (p6): every amount in cents"}
    if v != to_cents(v, "half_even"):
        step["round"] = "half_even"
    step["value"] = str(to_cents(v, "half_even"))
    return step


READING_DIMS = {"Q4", "Q5-DD120", "Q5-RM530", "Q5-HC630", "Q11"}
DIM_OWNER = {"Q4": ("G5", "21A (p35); Cl.21 (p6); P10 (p11): Q4 open, spec/g3_decisions.yaml"),
             "Q5-DD120": ("G5", "Sch 8 row 'each circulating or back-reaming hour' vs Cl.21 'per circulating hour' and Sch 1 "
                                "'circulating'; Cl.2 'A Schedule prevails over a Part' - whether it reaches Schedule 8 is D8-I1, and it "
                                "does not rank Schedule 8 against Schedule 1 (Q5 residual)"),
             "Q5-RM530": ("G5", "Sch 8 row 'each circulating or back-reaming hour' vs Cl.30 and Sch 1 'back-reaming' (Q5 residual)"),
             "Q5-HC630": ("G4", "Sch 8 row 'each BHA run, as Clause 26 describes' vs Cl.30 'clean-out runs ... in the numbers "
                                "recorded'; under the Schedule reading once per BHA run is G4 state (Q5 residual; reading G5)"),
             "Q11": ("G5", "Cl.35 (p8) states no remedy for a charge in another unit (Q11)"),
             "tolerance": ("G5", "25A (p35) pays the charged metres; Cl.23 (p6) prices metres by the band they lie in; the charge's "
                                 "depths do not say in which band the tolerance difference lies")}


def _collapse(options: dict) -> dict:
    """Drop every dimension whose value does not change any result, then identical labels merge."""
    def dims(label):
        return dict(x.split(":", 1) for x in label.split("|")) if label else {}
    names = sorted({d for k in options for d in dims(k)})
    for name in names:
        groups = {}
        for k, v in options.items():
            rest = "|".join(f"{d}:{x}" for d, x in dims(k).items() if d != name)
            groups.setdefault(rest, set()).add(v[:3])
        if all(len(g) == 1 for g in groups.values()):
            new = {}
            for k, v in options.items():
                rest = "|".join(f"{d}:{x}" for d, x in dims(k).items() if d != name)
                new.setdefault(rest or None, v)
            options = new
    return options


def _family(code: str) -> str:
    """The quantity route (Cl.21-31) the service is valued under; spec/g3_code_families.yaml derives the same from the
    verified tables and Appendix G, and tools/verify_g3.py X2 compares the two on every line."""
    if code in PERSONS:
        return "DDS-PERSONS"
    if code == COORDINATOR:
        return "DDS-COORDINATOR"
    if code in links.TOOL_DAY_SERVICES:
        return "DDS-TOOL-DAY"
    if code in HOURLY:
        return "DDS-HOURLY"
    if code in COUNTS:
        return "DDS-COUNTS"
    if code in METRE_TOOL:
        return "DDS-METRES"
    if code == "PD-210":
        return "DDS-PERFORMANCE"
    if code in RUN_EVENTS:
        return "DDS-RUN-EVENT"
    if code in WELL_EVENTS:
        return "DDS-WELL-EVENT"
    if code in LOSS:
        return "DDS-LOSS"
    return "DDS-NO-RULE"


def _finish(r, tr, payable, reasons, options=None):
    r.payable, r.reasons = payable, reasons
    if payable is None:
        r.amount_status = "unresolved"
        r.trace = tr.steps
        return r
    if not payable:
        r.allowed_quantity, r.amount, r.amount_status = Decimal("0"), Decimal("0.00"), "not_payable"
        tr.note("not payable: " + "; ".join(reasons), "; ".join(sorted({c.clause for c in r.checks if c.status == 'finding'})))
        r.trace = tr.steps
        r.conditions = []              # 0.00 whatever the class: nothing is conditional
        return r
    if not options:                    # guard (B2): a payable line always carries an amount or its alternatives
        r.add("quantity", "unresolved", "DDS-R07", "Cl.23 (p6); 25A (p35)", "no_admissible_result",
              "no admissible result could be formed for this line")
        return _finish(r, tr, None, reasons + ["no admissible result could be formed (engine guard)"])
    if len(options) == 1:
        (alw, rt, amt, steps), = options.values()
        r.allowed_quantity, r.amount, r.trace = alw, amt, steps
        if r.code == "PD-210":
            r.unit_rate = rt
    else:
        vals = list(options.values())
        r.allowed_quantity = vals[0][0] if len({v[0] for v in vals}) == 1 else None
        r.unit_rate = vals[0][1] if len({v[1] for v in vals}) == 1 else None
        r.amount = None
        for k, (alw, rt, amt, steps) in options.items():
            r.alternatives[k] = {"unit_rate": rt, "allowed_quantity": alw, "amount": amt, "trace": steps}
        left = {x.split(":", 1)[0] for k in options for x in (k or "").split("|") if x}
        for d in sorted(left - {"class"}):
            r.condition(d, *DIM_OWNER[d])
        r.amount_status = "alternatives" if left & READING_DIMS else "conditional"
        r.readings.append("no single amount: " + ", ".join(sorted(left)) + " (every admissible result carried with its trace)")
        r.trace = [x for x in tr.steps] + [{"op": "note", "label": "alternatives: one full trace each", "source": "spec/g3_decisions.yaml"}]
    r.conditions = [c for c in r.conditions if c["dimension"] in
                    {x.split(":", 1)[0] for k in r.alternatives for x in (k or "").split("|") if x} | {"nomination"}]
    if r.code == "PD-210" and "Q8:performance-section nomination not supplied" in r.readings:
        r.condition("nomination", "G5", "Cl.23 (p6), App A (p29): PD-210 only on a section the call-off nominates; no call-off supplied")
        if r.amount_status == "determined":
            r.amount_status = "conditional"
    return r


def _hours(code, a, b, ddr, tools, T, qr):
    """Chargeable hours under every open reading: Q4 (first hour / minimum / period) x Q5 residual (which recorded hours
    the service counts). Labels 'Q5-...:x|Q4:y'. None if DD-120's tool is not in the hole."""
    first_run_day = b.get("Run first day") == ddr.date
    circ = Decimal(a.get("Circulating hours") or 0)
    br = Decimal(a.get("Back-reaming hours") or 0)
    one, m = T.first_hour, T.dd120_min
    if code == "DD-120":
        if "DD-120" not in tools:
            return None, "rotary steerable not recorded in the hole (Cl.21, Cl.28)"
        q5 = {"Q5-DD120:circulating (Cl.21, Sch 1)": circ}
        if br > 0:
            q5["Q5-DD120:circulating or back-reaming (Sch 8 row)"] = circ + br

        def q4(h):
            return {"A": max(h - one, m), "B": max(h, m) - one, "C": (max(h - one, m) if first_run_day else max(h, m))}
        basis = (f"{circ} circulating and {br} back-reaming hours recorded; first day of run {first_run_day}; 6-hour minimum on an "
                 f"Operating day with the tool in the hole (Cl.21, P10); rig-up hour 21A (Q4); hours counted (Q5 residual)")
    else:
        q5 = {"Q5-RM530:back-reaming (Cl.30, Sch 1)": br}
        if circ > 0:
            q5["Q5-RM530:circulating or back-reaming (Sch 8 row)"] = circ + br

        def q4(h):
            return {"A": max(h - one, Decimal("0")), "B": max(h - one, Decimal("0")),
                    "C": (max(h - one, Decimal("0")) if first_run_day else h), "counts": h}
        basis = (f"{br} back-reaming and {circ} circulating hours recorded; 21A rig-up per period in the hole (Q4); counted as "
                 f"recorded (Cl.30); hours counted (Q5 residual)")
    fixed = qr.get("Q4") or Q4_WORKING
    out = {}
    for l5, h in q5.items():
        for l4, v in q4(h).items():
            if fixed is None or l4 == fixed:
                out[f"{l5}|Q4:{l4}"] = v
    return out, basis + (f"; Q4 reading {fixed} applied" if fixed else "")


def _unresolved(r, tr, reasons, why: list[str], owner: str = "G5") -> LineResult:
    """A value that depends on an input G2 left empty: no amount, no default, the reason and who resolves it (a query to the
    contractor, reported at G5). Every check that did not need the input has run and stays on the result."""
    r.payable, r.reasons = None, reasons + why
    r.amount_status = "unresolved"
    r.allowed_quantity = r.amount = r.unit_rate = None
    r.alternatives = {}
    r.conditions = [{"dimension": "input", "owner": owner, "basis": "; ".join(why)}] + \
        [c for c in r.conditions if c["dimension"] == "nomination"]
    if r.code == "PD-210" and "Q8:performance-section nomination not supplied" in r.readings and not any(
            c["dimension"] == "nomination" for c in r.conditions):
        r.condition("nomination", "G5", "Cl.23 (p6), App A (p29): PD-210 only on a section the call-off nominates; no call-off supplied")
    tr.note("value unresolved: " + "; ".join(why), "Cl.34 (p8); drilling guidelines principle 3 and check 12")
    r.trace = tr.steps
    return r


def _pd210_depths_missing(line, a, r, T, f, t, inputs):
    """Cl.34 (p8): a PD-210 charge states 'the depths at which the charged interval starts and ends'. Without them, which
    metres the charge is for - so the metres the report supports for it (25A) and the band they lie in (Cl.23) - cannot be
    established from the charge. The value is unresolved (owner G5: a query to the contractor); the interval is never
    guessed from the report and never set to zero. What holds for EVERY interval inside the report's measured one is kept:
    the metres drilled on the day, the bands and their rates, a charge above the day's metres (25A), and the rate where
    the day's drilling lies in one band."""
    gone = [n for n, v in (("depth_from_m", f), ("depth_to_m", t)) if v is None]
    other = [f"{n} {v}" for n, v in (("depth_from_m", f), ("depth_to_m", t)) if v is not None]
    for n in gone:
        r.input_gap(n, "finding", "depths_missing", "Cl.34 (p8)",
                    "the charge states no " + ("start" if n == "depth_from_m" else "end") + " depth"
                    + (f" (it states {', '.join(other)})" if other else ""), inputs.line_src)
    why = (f"PD-210 charge without its {' and '.join(gone)} (Cl.34): the metres the report supports for the charge and the "
           f"band they lie in cannot be established from it; not guessed from the report, not zero")
    billed = line["quantity"]
    s_raw, e_raw = a.get("Depth start (m MD)"), a.get("Depth end (m MD)")
    if s_raw is None or e_raw is None:
        return None, why + "; the report's measured depths are not established either", None, None
    start, end = Decimal(s_raw), Decimal(e_raw)
    day = max(end - start, Decimal("0"))
    bands = pd210_parts(start, end, T)
    r.rates = {f"band:{b}": rt for b, _pa, _pb, rt in bands}        # the rates of any interval inside the day's drilling
    r.readings.append(f"known without the charge's depths: the report measures {start}-{end} m, {day} m drilled on the day, in "
                      + ", ".join(f"band {b} ({pa}-{pb} m) at {rt}" for b, pa, pb, rt in bands))
    if billed is not None and billed > day * (1 + T.metre_tolerance / 100):
        r.add("quantity", "finding", "DDS-R07", "Cl.23 (p6); 25A (p35)", "quantity_above_report",
              f"billed {billed} m; the report measures {day} m drilled on the day, so no interval of that day supports more "
              f"than {day} m (whatever depths the charge would state)")
    if len(bands) == 1 and line.get("unit_rate") is not None:
        rate = bands[0][3]
        if line["unit_rate"] != rate:
            r.add("rate", "finding", "DDS-R09", "Sch 2 (p17); Cl.23 (p6)", "rate_differs",
                  f"billed {line['unit_rate']}; every metre drilled on the day lies in band {bands[0][0]} ({rate})")
        else:
            r.add("rate", "pass", "DDS-R09", "Sch 2 (p17); Cl.23 (p6)", detail=f"every metre drilled on the day lies in band {bands[0][0]}")
    return None, why, None, None


def _pd210(line, a, r, T, inputs=None):
    """PD-210 (Cl.23, 25A; F4): the allowed metres (25A: as charged within 1% of the metres the report supports for the
    charged interval, else the supported metres), priced by the band the metres lie in. The parts always carry the allowed
    quantity: in one band the charged metres take that band's rate; across bands a difference between the charged
    metres and the interval cannot be placed from the charge (-> every admissible allocation, or the two extremes and the
    whole domain beyond ENUMERATE_MAX: 'tolerance', owner G5; B2)."""
    billed = line["quantity"]
    f, t = line.get("depth_from_m"), line.get("depth_to_m")
    section = a.get("Hole section")
    if section not in PERFORMANCE_SECTIONS:
        r.add("quantity", "finding", "DDS-R19", "Cl.23 (p6); App A (p29)", "not_performance_section", f"section {section}")
        return Decimal("0"), f"PD-210 only on a performance-drilled (12-1/4 or 8-1/2 inch) section; report section {section}", False, None
    r.readings.append("Q8:performance-section nomination not supplied")
    if f is None or t is None:
        return _pd210_depths_missing(line, a, r, T, f, t, inputs or Inputs())
    start, end = Decimal(a.get("Depth start (m MD)")), Decimal(a.get("Depth end (m MD)"))
    lo, hi = max(f, start), min(t, end)
    sup = max(hi - lo, Decimal("0"))
    within = billed <= sup * (1 + T.metre_tolerance / 100)
    if t - f != billed:                    # Cl.34: a charge states the depths of its metres, whatever 25A then allows
        r.add("quantity", "finding", "DDS-R07", "Cl.34 (p8)", "depths_differ_from_quantity", f"{f}-{t} vs {billed}")
    if not within:
        r.add("quantity", "finding", "DDS-R07", "Cl.23 (p6); 25A (p35)", "quantity_above_report", f"billed {billed}; report supports {sup} m in {f}-{t}")
        f, t, allowed = lo, hi, sup
    else:
        allowed = billed
        r.add("quantity", "pass", "DDS-R07", "Cl.23 (p6); 25A (p35)", detail=f"{billed} m charged; report supports {sup} m (1% tolerance)")
    if allowed == 0:
        return Decimal("0"), "no metres the report supports in the charged interval (Cl.23)", False, None
    parts = [(band, pa, pb, pb - pa, rate) for band, pa, pb, rate in pd210_parts(f, t, T)]
    if len(parts) > 1:
        r.add("quantity", "finding", "DDS-R12", "Cl.23 (p6)", "band_crossing_not_split", f"{f}-{t} spans {len(parts)} bands")
    # where the metres can lie: in each band of the charged interval, the metres the report measures there (Cl.23: 'taken
    # from the measured depths on the Daily Drilling Report'); the charge's own depths are the claim and never widen it
    measured = {band: (pa, pb) for band, pa, pb, _rt in pd210_parts(lo, hi, T)}
    cap = [measured[p[0]][1] - measured[p[0]][0] if p[0] in measured else Decimal(0) for p in parts]
    if len(parts) == 1:
        band, pa, pb, _q, rate = parts[0]
        sets = {None: [(band, pa, pb, allowed, rate)]}
    elif allowed == sum(cap, Decimal(0)) and cap == [p[3] for p in parts]:
        sets = {None: parts}
    else:
        sets = _allocation_sets(parts, allowed, pd210_step(allowed, f, t, lo, hi), r, cap, measured)
    return allowed, f"report depths {start}-{end}; charged {f}-{t}; allowed {allowed} m", True, sets


ENUMERATE_MAX = 25        # allocations listed one by one up to this many; beyond it the two extremes and the domain


def pd210_step(*xs: Decimal) -> Decimal:
    """The finest decimal place used by the charged quantity and the depths (whole metres when all are whole)."""
    return Decimal(1).scaleb(min(min(x.as_tuple().exponent for x in xs), 0))


def pd210_domain(parts, allowed: Decimal, cap: list | None = None) -> tuple[list, list]:
    """Per-band bounds of the admissible allocations of the allowed metres to the bands a crossing interval spans (B2).
    `cap`: the metres the report measures in each band of the charged interval (default: the interval's own lengths).
    Fewer metres than that: each band carries between 0 and its measured metres, the others taking the rest. More
    (within 25A): each band carries at least its measured metres, the excess anywhere in the bands the charged interval
    spans."""
    ln = list(cap) if cap is not None else [p[3] for p in parts]
    total = sum(ln, Decimal(0))
    if allowed <= total:
        return [max(Decimal(0), allowed - (total - x)) for x in ln], [min(x, allowed) for x in ln]
    return list(ln), [x + allowed - total for x in ln]


def pd210_count(lo, hi, allowed: Decimal, step: Decimal) -> int:
    """Number of allocations q (lo <= q <= hi, sum q = allowed) in steps of `step` (dynamic programme over the bands)."""
    width = [int((h - l) / step) for l, h in zip(lo, hi)]
    rest = int((allowed - sum(lo, Decimal(0))) / step)
    ways = [1] + [0] * rest
    for w in width:
        new, run = [0] * (rest + 1), 0
        for s in range(rest + 1):
            run += ways[s] - (ways[s - w - 1] if s - w - 1 >= 0 else 0)
            new[s] = run
        ways = new
    return ways[rest]


def _allocation_sets(parts, allowed, step, r, cap=None, measured=None) -> dict:
    """Every admissible allocation as its own alternative ('tolerance:<metres per band>'), or, beyond ENUMERATE_MAX,
    the lowest- and highest-amount allocations with the whole domain stated on the owned condition: the metres the
    charge's depths do not place are never put in one band by assumption (Cl.23 prices metres by the band they lie in;
    Cl.34 the charge states depths; 25A pays the charged metres)."""
    lo, hi = pd210_domain(parts, allowed, cap)
    count = pd210_count(lo, hi, allowed, step)
    measured = measured or {p[0]: (p[1], p[2]) for p in parts}

    def label(qs, prefix=""):
        return "tolerance:" + prefix + " + ".join(f"{q} m in band {p[0]}" for q, p in zip(qs, parts))

    def as_set(qs):
        return [(band, pa, pb, q, rate) for q, (band, pa, pb, _l, rate) in zip(qs, parts)]

    if count <= ENUMERATE_MAX:
        found = []

        def walk(i, acc, left):
            if i == len(parts) - 1:
                if lo[i] <= left <= hi[i]:
                    found.append(acc + [left])
                return
            q = lo[i]
            while q <= hi[i] and q <= left:
                walk(i + 1, acc + [q], left - q)
                q += step
        walk(0, [], allowed)
        sets = {label(qs): as_set(qs) for qs in found}
        mode = "enumerated"
    else:
        def extreme(order):
            qs, left = list(lo), allowed - sum(lo, Decimal(0))
            for i in order:
                add = min(hi[i] - lo[i], left)
                qs[i] += add
                left -= add
            return qs
        by_rate = sorted(range(len(parts)), key=lambda i: parts[i][4])
        low, high = extreme(by_rate), extreme(by_rate[::-1])
        sets = {label(low, "lowest, "): as_set(low), label(high, "highest, "): as_set(high)}
        mode = "bounds"
    r.conditions.append({
        "dimension": "tolerance", "owner": DIM_OWNER["tolerance"][0], "basis": DIM_OWNER["tolerance"][1],
        "domain": {"mode": mode, "allowed_m": str(allowed), "step_m": str(step), "count": count,
                   "parts": [{"band": p[0], "from_m": str(p[1]), "to_m": str(p[2]), "interval_m": str(p[3]),
                              "measured": (f"{measured[p[0]][0]}-{measured[p[0]][1]}" if p[0] in measured else None),
                              "min_m": str(a), "max_m": str(b)} for p, a, b in zip(parts, lo, hi)],
                   "listed": len(sets),
                   "not_listed": ("none" if mode == "enumerated" else
                                  f"{count - 2} allocations between the two listed extremes: each admissible, unresolved, owner G5")}})
    return sets


def _metres(code, line, a, tools, r, T, tr):
    start, end = Decimal(a.get("Depth start (m MD)")), Decimal(a.get("Depth end (m MD)"))
    billed = line["quantity"]
    status = a.get("Status")
    tool = METRE_TOOL[code]
    present = tool in tools and status == "Operating"
    sup = (end - start) if present else Decimal("0")
    if not present:
        r.add("quantity", "finding", "DDS-R06", "Cl.24, Cl.25 (p6)", "tool_not_in_hole", f"tool of {tool} not in the hole on an Operating day")
    allowed = billed if billed <= sup * (1 + T.metre_tolerance / 100) else sup
    if present and billed > sup * (1 + T.metre_tolerance / 100):
        r.add("quantity", "finding", "DDS-R07", "Cl.24-25 (p6); 25A (p35)", "quantity_above_report", f"billed {billed}; drilled {sup}")
    elif present:
        r.add("quantity", "pass", "DDS-R07", "Cl.24-25 (p6); 25A (p35)", detail=f"drilled {sup} m; charged {billed} within 1%")
    if not present:
        return Decimal("0"), "metre charge needs its tool in the hole on an Operating day (Cl.24, Cl.25)", False
    return allowed, f"metres drilled {sup}", True


def _loss_value(code, sd, ddr, r, tr, T, twice=frozenset()):
    e = {k: v for k, v in ddr.parts.get("E", {}).items() if f"E.{k}" not in twice}
    if ddr.lost_tool_code != code:
        r.add("identification", "finding", "DDS-R17", "Cl.31 (p7); App G (p36)", "lost_tool_mismatch", f"Part E tool {ddr.lost_tool_term} -> {ddr.lost_tool_code}")
        return None
    hours = Decimal(e.get("Circulating hours accumulated on the well"))
    loss_value(code, sd, hours, T, tr, "as stated on the Lost in Hole Report", "Q13 reading A")
    r.readings.append("Q13:A (Part E hours; corroborated by the tool's own daily history)")
    return tr.value


def loss_value(code, sd, hours: Decimal, T, tr: Trace, basis: str, reading: str) -> Decimal:
    """Cl.31/31A: Sch 2D SAR value / FX of the month of loss, half-even; less 1% per complete 25 h, max 50%."""
    month = f"{sd:%Y-%m}"
    tr.start(f"replacement value SAR ({code})", T.sar[code], f"DDS.T06_SAR_VALUES (Sch 2D p19); 31A (p35); OV-DDS-04")
    tr.div(f"/ SAR per USD for {month} (halalas per USD / 100)", T.fx[month] / 100, f"DDS.T07_FX {month} (Sch 2D p19); 31A")
    _half(tr, "converted value, half to even", "31A (p35)")
    steps = min((hours // T.lih_step_hours) * T.lih_pct_per_step, T.lih_cap_pct)
    tr.mul(f"less depreciation {steps}% ({hours} h {basis}; 1% per complete 25 h, max 50%)",
           1 - steps / 100, f"Cl.31 (p7); P12 (p11); Sch 5 Part E (p24); {reading}")
    return _half(tr, "depreciated value, half to even", "Cl.31 (p7); Cl.17")


def inputs_from_world(w):
    """(line, invoice, report) for every drilling line of the G2 world (invoice None: its header is not found)."""
    invs = {k: h.values for k, h in headers_by_id(w.claims.rows["dds_headers"])[0].items()}
    for row in w.claims.rows["dds_lines"]:
        v = row.values
        yield v, invs.get(v.get("invoice_no")), w.ddr.get(v["report_ref"]) if v.get("report_ref") else None


def input_context(w) -> dict[str, Inputs]:
    """Per line (keyed as the batch keys its result, g3_core.result_keys): provenance of the line, its invoice and its
    report, and the report's fields in G2's queue."""
    invs, hcopies = headers_by_id(w.claims.rows["dds_headers"])
    gaps, repeated = {}, {}
    for u in w.queue.items:
        if u.kind == "ddr":
            gaps.setdefault(u.ident, set()).add(u.field)
            if u.reason == "key repeated":
                repeated.setdefault(u.ident, set()).add(u.field)
    files = defaultdict(list)          # Report number -> the delivered files carrying it (G2's by-file index)
    for f, d in sorted(w.ddr_by_file.items()):
        if d.report is not None:
            files[d.report].append(f)
    unindexed = any(d.report is None for d in w.ddr_by_file.values())
    out = {}
    for key, row in zip(result_keys(w.claims.rows["dds_lines"]), w.claims.rows["dds_lines"]):
        v = row.values
        h = invs.get(v.get("invoice_no"))
        ddr = w.ddr.get(v["report_ref"]) if v.get("report_ref") else None
        copies = tuple(f for f in files.get(v.get("report_ref") or "", ()) if ddr is None or f != ddr.file)
        out[key] = Inputs(f"{row.source.path}:{row.source.line}", f"{h.source.path}:{h.source.line}" if h else None,
                          ddr.path if ddr is not None else None,
                          frozenset(gaps.get(ddr.file, ())) if ddr is not None else frozenset(), unindexed,
                          frozenset(repeated.get(ddr.file, ())) if ddr is not None else frozenset(), copies,
                          hcopies.get(v.get("invoice_no"), ()))
    return out


def engine_error(line: dict, ident: str, e: Exception) -> LineResult:
    """The batch never stops on one line: an exception becomes an explicit result naming it (X3 fails on any)."""
    r = LineResult("DDS", line.get("line_ref") or ident, line.get("service_code") or "")
    r.add("engine", "unresolved", "G3-ENGINE", "G3 engine", "engine_error", f"{type(e).__name__}: {e}")
    r.amount_status, r.payable, r.family = "unresolved", None, "DDS-ENGINE-ERROR"
    r.reasons.append(f"engine error ({type(e).__name__}): the line is not valued; a defect in G3, not a finding on the invoice")
    r.conditions = [{"dimension": "engine", "owner": "G3", "basis": "engine error"}]
    return r


def run(w, T=None) -> dict[str, LineResult]:
    out = {}
    ctx = input_context(w)
    rows = w.claims.rows["dds_lines"]
    for key, (line, inv, ddr) in zip(result_keys(rows), inputs_from_world(w)):
        try:
            res = evaluate(line, inv, ddr, T=T, inputs=ctx[key])
        except Exception as e:  # noqa: BLE001 - recorded on the line, never swallowed: X3 fails on engine_error
            res = engine_error(line, key, e)
        res.ctx = w.run_context["id"] if w.run_context else None
        out[key] = res
    return out
