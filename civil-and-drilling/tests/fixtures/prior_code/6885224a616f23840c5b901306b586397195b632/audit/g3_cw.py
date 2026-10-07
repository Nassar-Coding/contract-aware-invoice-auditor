"""G3 civil (CW-2025-0417-CIV): local entitlement and pricing of one application line.

Line-level checks (checks 1-8, local part): identity, term, submission window, period, unit (Cl.26 full
rejection), Schedule 5 evidence and signatures (Cl.46/47; Q3 reading A), identification against the record,
quantities (6A first hour, 47A five-day week, 33A 2% survey tolerance, record cap), rate version by work date
(instruments in issue order, 31A protection of applications submitted before a retrospective amendment's issue),
USD conversion (26A) and indexation (29A) half-even, build-up zone -> ground -> night -> rest-day -> band -> S2/A2
discount (Cl.27, 27A, P11), rounded once half-up (Cl.28), and line arithmetic.

Authority for pricing inputs (G3): the ground class is the one recorded at excavation on the site
record (S4; Cl.5 the Engineer's written confirmation) or G2 after 27 Sep 2025 (27A) - never the application's own
statement; where no supplied record states it the line carries every ground class (S4's G2 is the value if it was not
recorded on the day). The zone and night working are facts only the application states (Cl.4, Cl.7, Cl.42): they are
taken as stated and disclosed on the line (G3-D3). An amount that differs from quantity x billed rate on a line whose
band is not known is not an established arithmetic error where Cl.28's division at a band edge reproduces it.

Not decided here (G4): annual quantity-band state, daily limits, exclusions, duplicates, the posting of the A3
retrospective adjustment and P23 recovery. They are listed on each result as g4_dependencies. The band is an input:
a reference case states it; on the population it is unknown (band_pct=None), so a band-rated line is priced under
every band, its rate is checked against all of them, and its amount is 'conditional' with one traced amount per band
(a quantity crossing a band edge is divided at the edge, Sch 4 Part 3, so the amount lies between those bounds).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import itertools
from decimal import Decimal

from . import terms
from .g3_core import Inputs, LineResult, Trace, empty, headers_by_id, result_keys

CONTRACT_REF = "CW-2025-0417-CIV"
SUBCONTRACTOR = "RIDGEWAY CIVIL ENGINEERING LLC"      # agreement particulars (p1)
ALWAYS_G4 = ["duplicate_scope (CW-R17, Cl.44)"]


def price(code: str, work_date: dt.date, submitted: dt.date | None, zone: str | None, ground: str | None,
          night: bool, band_pct: Decimal = Decimal("100"), T=None) -> tuple[Decimal, Trace, list[str]]:
    """Built-up, rounded rate for a CW item. Returns (rate, trace, readings)."""
    T = T or terms.cw()
    tr, readings = Trace(), []
    sch = T.sch1[code]
    cands = T.rate_candidates(code, work_date, submitted)
    skipped = [i for i in T.instruments if i.retrospective and submitted is not None and submitted < i.issued
               and any(c == code and eff <= work_date for c, eff, _ in i.rate_rows)]
    if cands:
        order, eff, value, src = max(cands, key=lambda c: (c[0], c[1]))
        tr.start("base rate (latest-issued instrument applicable on the work date)", value, src + "; SoV p38 (issue order)")
    else:
        tr.start("base rate", sch["rate"], f"CW.T01_SCH1 {code} (Sch 1 pp17-19)")
    for ins in skipped:
        tr.note(f"{ins.id} not applied: application submitted {submitted} before its issue {ins.issued}", "31A (p32); A3 (p43)")
        readings.append(f"31A protection ({ins.id})")
    if code in T.usd_items:
        month = f"{work_date:%Y-%m}"
        tr.mul(f"USD x halalas per USD ({month}) / 100", T.fx[month] / 100, f"CW.T07_FX {month} (Sch 2B p22); 26A (p32); OV-CW-02")
        tr.round("converted rate, nearest halala, half to even", "half_even", "26A (p32)")
    if code in T.smi_items:
        month = f"{work_date:%Y-%m}"
        tr.mul(f"x Site Materials Index {month} / base 100.00", T.smi[month] / T.smi_base, f"CW.T05_SMI {month} (Sch 2A p21); 29A (p32)")
        tr.round("indexed rate, nearest halala, half to even", "half_even", "29A (p32)")
    zf = None
    if sch["series"] in T.zone_series and zone:
        zf = T.zones[zone]
        tr.mul(f"zone {zone}", zf, "CW.T02_ZONES (Sch 2 p20); Cl.27 (p6)")
    else:
        tr.note(f"no zone factor (series {sch['series']})", "Sch 2 (p20); Cl.29 (p6)")
    if code in T.ground_items:
        tr.mul(f"ground {ground}", T.ground[ground], "CW.T08_GROUND_FACTORS (Sch 3 p23); 27A (p32); S4 (p10)")
    night_ok = night and code in T.night and not (zf is not None and zf > T.night_suppressed_above)
    if night and code in T.night and not night_ok:
        tr.note(f"night uplift not payable: zone factor {zf} exceeds {T.night_suppressed_above}", "27A (p32)")
    rest_ok = work_date.strftime("%A") in T.rest_days and code in T.rest
    if night_ok and rest_ok:
        tr.note("night and rest-day uplifts both marked: rest-day uplift alone (no written instruction supplied)", "P11 (p13); Sch 4 note (p26); Q8 reading A")
        readings.append("P11 rest-day alone (Q8:A)")
        night_ok = False
    if night_ok:
        tr.mul(f"night uplift {T.night[code]}%", 1 + T.night[code] / 100, "CW.T10_NIGHT (Sch 4 p24); Cl.27 (p6)")
    if rest_ok:
        tr.mul(f"rest-day uplift {T.rest[code]}% ({work_date:%A})", 1 + T.rest[code] / 100, "CW.T11_REST (Sch 4 p24); Cl.8 (p3)")
    if code in T.banded:
        tr.mul(f"annual quantity band {band_pct}% (state input)", band_pct / 100, "CW.T12_BANDS (Sch 4 Part 3 pp24-25); G4 supplies the band")
        readings.append(f"band_pct={band_pct} (state input)")
    disc = T.discount(code, work_date)
    if disc:
        tr.mul(f"discount {disc.value}%", 1 - disc.value / 100, disc.source)
    rate = tr.round("rate rounded once, nearest halala, half up", "half_up", "Cl.28 (p6)")
    return rate, tr, readings


# Cl.42 (p8): what every application line states (the ground classification only "where applicable": ground items)
CL42 = {"item_code": "item code", "work_date": "date of execution", "site_zone": "Site zone", "quantity": "quantity",
        "unit": "unit", "rate_applied": "built-up rate applied", "amount": "resulting amount"}


def evaluate(line: dict, app: dict, record, record_exists: bool, band_pct: Decimal | None = None, T=None,
             inputs: Inputs | None = None) -> LineResult:
    """line/app: typed claim fields; record: G2 CwRecord or None (record_exists: a file with that ticket exists).
    band_pct: the annual quantity-band percentage when known (a case input); None = G4 state not known here.
    inputs: G2 provenance and the record's G2-unresolved fields. An input G2 left empty is recorded with its
    source, never defaulted: a check that needs it is unresolved, and a value that depends on it is unresolved (owner G5)
    unless an established consequence (not payable) already decides the line."""
    T = T or terms.cw()
    inputs = inputs or Inputs()
    L, H, D = inputs.line_src, inputs.header_src, inputs.doc_src
    code, wd = line.get("item_code"), line.get("work_date")
    r = LineResult("CW", line.get("line_ref") or "", code or "")
    blocked = []            # why the value cannot be established here: an input G2 left empty
    if record is not None and inputs.doc_repeated:
        # a record key written twice is unresolved in G2 (it keeps the last): no fact of the record is taken from it
        attrs = {"Area": "area", "Date": "date", "Week beginning": "week_beginning", "Ground": "ground"}
        record = dataclasses.replace(record, **{attrs[k]: None for k in inputs.doc_repeated if k in attrs},
                                     **({"days_on": []} if "Days on" in inputs.doc_repeated else {}))
        for f in sorted(inputs.doc_repeated):
            r.input_gap(f, "unresolved", "input_unresolved", "Cl.47 (p8)", "record key written twice: which value holds is not "
                        "established (G2)", D)
    if app is None:
        r.input_gap("application_no", "unresolved", "input_unresolved", "Cl.40 (p8)",
                    f"the line's application {line.get('application_no')!r} is carried by {len(inputs.header_copies)} application "
                    f"rows ({', '.join(inputs.header_copies)}): which one the line belongs to is not established, and no header "
                    "fact is taken from either" if inputs.header_copies else
                    f"the line's application {line.get('application_no')!r} is not among the applications G2 loaded: its header "
                    "facts are unknown", L)
        app = {}
    for f, what in CL42.items():
        if empty(line.get(f)):
            r.input_gap(f, "finding", "claim_field_missing", "Cl.42 (p8)", f"the line states no {what}", L)
    sch = T.sch1.get(code) if not empty(code) else None
    if empty(code):
        blocked.append("item_code: the item cannot be identified without its code (Cl.42)")
    elif sch is None:
        r.add("identification", "unresolved", "CW-R23", "Cl.26 (p6); Sch 6-8 (pp28-31); P24 (p14)", "code_not_in_schedule_1",
              "no Schedule 1 rate; valued only by its own route (daywork/provisional/preliminaries) whose conditions are not evidenced")
    payable, reasons = True, []

    # 1 identity ----------------------------------------------------------------------------------------------
    cref, sub = app.get("contract_ref"), app.get("subcontractor")
    if empty(cref):
        r.input_gap("contract_ref", "unresolved", "input_unresolved", "p1 particulars; Cl.40 (p8)",
                    "the application states no contract reference: its identity is not confirmed (procedural, G3-D1)", H)
    elif cref != CONTRACT_REF:
        r.add("identity", "finding", "CW-R01", "p1 particulars; Cl.40 (p8)", "contract_ref_variant", cref)
    elif empty(sub):
        r.input_gap("subcontractor", "unresolved", "input_unresolved", "p1 particulars",
                    "the application names no subcontractor: its identity is not confirmed (procedural, G3-D1)", H)
    elif sub.upper() != SUBCONTRACTOR:
        r.add("identity", "finding", "CW-R01", "p1 particulars", "subcontractor_mismatch", sub)
    else:
        r.add("identity", "pass", "CW-R01", "p1 particulars")
    # 2 term ------------------------------------------------------------------------------------------------
    if empty(wd):
        r.add("term", "unresolved", "CW-R03", "p1 particulars; A1 (p40); A2 (p42)", detail="work_date not stated (Cl.42)")
        blocked.append("work_date: the term, the rate in force, the index/FX month and a rest day all follow the date of "
                       "execution (Cl.42), which the line does not state")
    elif not (T.commencement <= wd <= T.completion):
        r.add("term", "finding", "CW-R03", "p1 particulars; A2 2.1 (p42)", "out_of_term", f"{wd} outside {T.commencement}..{T.completion}")
        payable = False
        reasons.append("work outside the term as extended is not measurable (A1/A2)")
    else:
        r.add("term", "pass", "CW-R03", "p1 particulars; A1 (p40); A2 (p42)")
    # 3 window and period (application header) -----------------------------------------------------------------
    pfrom, pto, adate = app.get("period_from"), app.get("period_to"), app.get("application_date")
    for f, v, what in (("period_from", pfrom, "first"), ("period_to", pto, "last")):
        if empty(v):
            r.input_gap(f, "finding", "claim_field_missing", "Cl.40 (p8)", f"the application states no {what} day of its period", H)
    if empty(adate):
        r.input_gap("application_date", "unresolved", "input_unresolved", "Cl.41 (p8); 31A (p32)",
                    "the application date is not stated: the window and any 31A protection cannot be established", H)
    if empty(pto) or empty(adate):
        r.add("window", "unresolved", "CW-R04", "Cl.41 (p8)", detail="period_to or application_date not stated")
    elif adate < pto:
        r.add("window", "finding", "CW-R04", "Cl.41 (p8)", "submitted_early", f"{adate} before period end {pto}")
    elif adate > pto + dt.timedelta(days=T.window_days):
        r.add("window", "finding", "CW-R04", "Cl.41 (p8)", "submitted_late", f"{(adate - pto).days} days after period end")
    else:
        r.add("window", "pass", "CW-R04", "Cl.41 (p8)")
    if empty(pfrom) or empty(pto) or empty(wd):
        r.add("period", "unresolved", "CW-R02", "Cl.41 (p8)", detail="period_from, period_to or work_date not stated")
    elif not (pfrom <= wd <= pto):
        r.add("period", "finding", "CW-R02", "Cl.41 (p8)", "outside_period", f"{wd} outside {pfrom}..{pto}")
    else:
        r.add("period", "pass", "CW-R02", "Cl.40, Cl.41 (p8)")
    if sch is None:                    # the item cannot be valued; an established consequence above still decides it
        r.family = "CW-UNSCHEDULED"
        if not payable:
            return _not_payable(r, Trace(), reasons)
        if empty(code):
            return _unresolved(r, Trace(), reasons, blocked)
        r.amount_status, r.payable = "unresolved", None
        r.reasons.append("code outside Schedule 1 (CW-R23)")
        return r
    # 4 unit ------------------------------------------------------------------------------------------------
    unit = line.get("unit")
    if empty(unit):
        r.add("unit", "unresolved", "CW-R08", "Cl.26 (p6); Cl.42 (p8)", detail="unit not stated")
        blocked.append("unit: whether the quantity is in the Schedule 1 unit (Cl.26) cannot be established (Cl.42)")
    elif unit != sch["unit"]:
        r.add("unit", "finding", "CW-R08", "Cl.26 (p6)", "wrong_unit", f"billed {unit}, Schedule 1 {sch['unit']}")
        payable = False
        reasons.append("quantity in a unit other than Schedule 1's is rejected in its entirety (Cl.26)")
    else:
        r.add("unit", "pass", "CW-R08", "Cl.26 (p6)")
    # 5 evidence (Schedule 5) ----------------------------------------------------------------------------------
    series = T.records.get(code)
    ref = line.get("record_ref")
    site_missing = empty(line.get("site"))
    area = (line.get("site") or "").split(" ")[0]
    if site_missing and (series or (code in T.ground_items and record is not None)):
        r.input_gap("site", "unresolved", "input_unresolved", "Cl.47 (p8); S4 (p10)",
                    "the line states no site (work area): a record cannot be tied to its work", L)
    evidence_ok = True
    unknown = []            # record facts G2 could not establish: the evidence is unknown, not defective
    if series:
        ev = []
        if empty(ref) or not record_exists or record is None:
            ev.append(("record_missing", "record_ref blank: the line cites no record" if empty(ref)
                       else f"record_ref '{ref}' names no delivered record"))
        else:
            if not ref.startswith(series + "-"):
                ev.append(("record_wrong_series", f"{ref} is not a {series} record"))
            sig_twice = [k for k in ("Signed (foreman)", "Countersigned (Engineer's representative)") if k in inputs.doc_repeated]
            unsigned = [k for k, ok in (("Signed (foreman)", record.foreman_signed),
                                        ("Countersigned (Engineer's representative)", record.engineer_signed)) if not ok and k not in sig_twice]
            if unsigned:                # a signature missing on its own line: unsigned whatever a twice-written other line holds
                ev.append(("record_unsigned", "; ".join(f"{k} missing or a placeholder" for k in unsigned)))
            elif sig_twice:
                unknown.append((sig_twice[0], "signature line written twice: whether the record is signed is not established"))
            weekly = record.family == "DW" or record.week_beginning
            if record.family is None:
                unknown.append(("title", "the record's type (its title) is not established"))
            elif weekly:
                wb = record.week_beginning
                if wb is None:
                    unknown.append(("Week beginning", "the week the record covers is not established"))
                elif not empty(wd) and not (wb <= wd <= wb + dt.timedelta(days=6)):
                    ev.append(("record_date_mismatch", f"work date {wd} outside the week beginning {wb}"))
                # 47A: a week is measurable on five days worked; days G2 could not read matter only while the readable
                # ones are fewer than five (they are a lower bound); a missing or twice-written line leaves them unknown
                if "days_on" not in record.spans or "Days on" in inputs.doc_repeated or (
                        "Days on" in inputs.doc_gaps and len(record.days_on) < T.weekly_min_days):
                    unknown.append(("Days on", "the days worked in the week are not all established"))
                elif "Days on" in inputs.doc_gaps:
                    r.input_gap("Days on", "n/a", None, "47A (p33)", f"record Days on has entries G2 could not read; the "
                                f"{len(record.days_on)} readable days already make the week measurable", D)
            elif record.date is None:
                unknown.append(("Date", "the record's date is not established"))
            elif not empty(wd) and record.date != wd:
                ev.append(("record_date_mismatch", f"record dated {record.date}, line {wd}"))
            if record.area is None:
                unknown.append(("Area", "the record's work area is not established"))
            elif not site_missing and record.area != area:
                ev.append(("record_area_mismatch", f"record area {record.area}, line {area}"))
            if record.rule is None or "narrative" in inputs.doc_gaps:
                unknown.append(("narrative", "the record's activity and quantity are not established"))
            elif code not in (record.candidates or []):
                ev.append(("item_not_supported_by_record", f"record evidences {record.candidates}, line bills {code}"))
            elif record.unit != sch["unit"]:
                ev.append(("item_not_supported_by_record", f"record quantity in {record.unit}, Schedule 1 unit {sch['unit']}"))
        for f, d in ev:
            r.add("evidence", "finding", "CW-R05", "Cl.46, Cl.47 (p8); Sch 5 (p27); 47A (p33)", f, d)
        for f, d in unknown:
            r.input_gap(f, "unresolved", "input_unresolved", "Cl.46, Cl.47 (p8); Sch 5 (p27)", f"record {d} (G2)", D)
        if ev:
            evidence_ok = payable = False
            reasons.append("Schedule 5 item not payable in any valuation until its record is delivered (Cl.46; Q3 reading A)")
            r.readings.append("Q3:A")
            r.g4_dependencies.append("p23_link (Q3: no second deduction if later recovered under P23)")
        elif unknown or (site_missing and record is not None):
            evidence_ok = False
            r.add("evidence", "unresolved", "CW-R05", "Cl.46, Cl.47 (p8); Sch 5 (p27)",
                  detail="whether the record evidences this line is not established")
            blocked.append("record: " + "; ".join(d for _f, d in unknown) if unknown else "site: the record cannot be tied to the line's work area")
        else:
            r.add("evidence", "pass", "CW-R05", "Cl.46, Cl.47 (p8); Sch 5 (p27)")
    else:
        r.add("evidence", "n/a", "CW-R05", "Sch 5 (p27)", detail="no Schedule 5 record prescribed")
    # 6 quantity ----------------------------------------------------------------------------------------------
    r.family = ("CW-HOUR" if series and code in T.hourly else "CW-WEEK" if series and code in T.weekly_record else
                "CW-SURV" if series and code in T.surveyed else "CW-REC" if series else "CW-MEAS")
    billed = line.get("quantity")
    allowed = billed
    if empty(billed):
        allowed = None
        r.add("quantity", "unresolved", "CW-R07", "Cl.25 (p6); Cl.42 (p8)", detail="quantity not stated")
        blocked.append("quantity: the line states no quantity (Cl.42)")
    elif series and evidence_ok:
        rq = record.quantity
        if code in T.hourly:
            supported = max(rq - T.first_hour, Decimal("0"))
            basis = f"{rq} hours attended less the first hour = {supported} (6A)"
        elif code in T.weekly_record:
            days = len(record.days_on)
            supported = rq if days >= T.weekly_min_days else Decimal("0")
            basis = f"{days} days worked in the week ({'measurable' if supported else 'not measurable'}, 47A)"
            if not supported:
                r.add("quantity", "finding", "CW-R07", "47A (p33); Sch 5 note (p27)", "week_not_measurable", basis)
        else:
            supported = rq
            basis = f"record quantity {rq}"
        if code in T.surveyed:
            limit = supported * (1 + T.survey_tolerance / 100)
            allowed = billed if billed <= limit else supported
            basis += f"; surveyed item: payable as measured up to {limit} (33A), else the surveyed quantity"
            over = billed > limit
        else:
            allowed = min(billed, supported)
            over = billed > supported
        if over and not (code in T.weekly_record and supported == 0):
            r.add("quantity", "finding", "CW-R07", "6A, 33A (p32); 47A (p33); guideline check 5", "quantity_above_record", f"billed {billed}; {basis}")
        elif not any(c.check == "quantity" for c in r.checks):
            r.add("quantity", "pass", "CW-R07", "6A, 33A (p32); 47A (p33)", detail=basis)
    elif series and not payable and not blocked:
        allowed = Decimal("0")
        r.add("quantity", "n/a", "CW-R07", "Cl.46 (p8)", detail="no deliverable record: nothing measurable")
    elif series and not evidence_ok:
        allowed = Decimal("0") if not payable else None
        r.add("quantity", "n/a" if not payable else "unresolved", "CW-R07", "Cl.46 (p8)",
              detail="no deliverable record: nothing measurable" if not payable else "the record's quantity is not established")
    else:
        r.add("quantity", "pass", "CW-R07", "Cl.25 (p6)", detail="no record prescribed; quantity as measured in the application")
    # 7-8 rate ------------------------------------------------------------------------------------------------
    zone = (line.get("site_zone") or "").split(" ")[0] or None
    zoned = sch["series"] in T.zone_series
    if zoned and zone:
        r.readings.append(f"zone {zone} as stated in the application (Cl.4, Cl.42; no supplied record states the zone; G3-D3)")
    elif zoned:
        blocked.append("site_zone: the zone factor of this item follows the Site zone the application states (Cl.4, Cl.27, "
                       "Cl.42; G3-D3), which it does not state")
    night_unknown = empty(line.get("night_work"))
    if line.get("night_work") == "Y" and code in T.night:
        r.readings.append("night work as stated in the application (Cl.7; no supplied record states the time of work; G3-D3)")
    # ground classification (F2): the authority is the classification recorded at excavation (S4; Cl.5 the Engineer's
    # written confirmation, not supplied) - never the application's own statement (Cl.42 requires it to state one)
    ground_opts = [(None, None)]
    if code in T.ground_items and not empty(wd):
        claimed = (line.get("ground_class") or "").split(" ")[0] or None
        if claimed is None:
            r.input_gap("ground_class", "finding", "claim_field_missing", "Cl.42 (p8)",
                        "the line states no ground classification for a ground item (the claim; not the authority for the class)", L)
        applies, why = record_applies(record, line, code) if record is not None else (False, "no record referenced")
        if record is not None and "narrative" in inputs.doc_gaps:
            applies, why = False, "; ".join(x for x in (why, "the record's narrative (its item basis) is not established (G2)") if x)
        # the classification is the Engineer's (Cl.5; App A 'Ground classification'; 27A 'whatever the Engineer recorded on
        # the day'; App A 'Engineer' includes the representative): a record without the representative's countersignature
        # is the Subcontractor's own statement, not that classification
        engineers = bool(record is not None and record.engineer_signed
                         and "Countersigned (Engineer's representative)" not in inputs.doc_repeated)
        recorded = bool(applies and evidence_ok and record.ground and engineers)
        if record is not None and "Ground" in inputs.doc_gaps:
            r.input_gap("Ground", "unresolved", "input_unresolved", "S4 (p10)", "record Ground line not established (G2)", D)
        if wd > T.g2_after:
            ground_opts = [(None, "G2")]
            r.readings.append("ground G2: 27A, work after 27 Sep 2025 taken as G2")
        elif recorded:
            ground_opts = [(None, record.ground)]
            r.readings.append(f"ground {record.ground}: classification recorded on the site record {line.get('record_ref')} for this "
                              f"work (same date, area and item; S4)")
        else:
            ground_opts = [(f"ground:{g}", g) for g in T.ground]
            if record is not None and record.ground and not applies:
                r.readings.append(f"ground not evidenced: the referenced record {line.get('record_ref')} states {record.ground} but "
                                  f"does not apply to this work ({why}), so it is not the classification of this excavation (S4, Cl.5)")
            elif record is not None and record.ground and not engineers:
                r.readings.append(f"ground not evidenced: the record {line.get('record_ref')} for this work states {record.ground} but "
                                  f"is not countersigned by the Engineer's representative, so it is not the Engineer's "
                                  f"classification (Cl.5; App A; 27A)")
            if record is not None and applies and engineers and record.ground is None:
                r.readings.append(f"ground not evidenced: the record {line.get('record_ref')} for this work states no Ground "
                                  f"classification" + (" G2 could read" if "Ground" in inputs.doc_gaps else ""))
            r.readings.append(f"ground not evidenced: no supplied record for this work states the classification; the application "
                              f"states {claimed or 'none'} (the claim, not authority: S4, Cl.5); G2 if not recorded on the day (S4)")
            r.condition("ground", "G5", "S4 (p10) classification recorded at excavation, G2 if not recorded on the day; "
                        "Cl.5 (p3) Engineer's written confirmation (not supplied); the application's class is the claim "
                        f"({claimed or 'none'}), not authority")
        if recorded and claimed and claimed != record.ground and wd <= T.g2_after:
            r.add("rate", "finding", "CW-R13", "S4 (p10); Cl.42 (p8)", "ground_differs_from_record",
                  f"application {claimed}, {record.ground} recorded")
        elif not recorded:
            r.add("rate", "unresolved", "CW-R13", "S4 (p10); Cl.42 (p8)", "ground_differs_from_record",
                  f"no supplied record states the classification: the application's {claimed or 'blank'} cannot be compared")
    band_unknown = code in T.banded and band_pct is None
    if band_unknown:
        band_opts = [(f"band:{i}", pct) for i, pct in enumerate(T.band_pcts[code], 1)]
        r.readings.append("band state unknown at G3: every band priced (G4, CW-R14)")
        r.condition("band", "G4", "Sch 4 Part 3 (pp24-25): the band follows the cumulative quantity in the Contract Year")
    else:
        band_opts = [(None, band_pct if band_pct is not None else Decimal("100"))]
    priced = {}
    if not empty(wd) and not (zoned and not zone):
        # a submission date or a night statement the application omits is never assumed: the rate is built only where
        # every admissible value gives the same rate (31A protection; Cl.7 night uplift), else the value waits on it
        subs = [adate] if not empty(adate) else [None, wd]          # submitted after every issue, or on the work date
        nights = [line.get("night_work") == "Y"] if not night_unknown else [False, True]
        try:
            for (gl, g), (bl, pct) in itertools.product(ground_opts, band_opts):
                built = [price(code, wd, s_, zone, g, n_, pct, T) for s_ in subs for n_ in nights]
                rate_k, tr_k, rd_k = built[0]
                if any(b[0] != rate_k for b in built[1:]):
                    priced = {}
                    if empty(adate) and any(price(code, wd, None, zone, g, n_, pct, T)[0] != price(code, wd, wd, zone, g, n_, pct, T)[0]
                                            for n_ in nights):
                        blocked.append("application_date: a retrospective instrument changes this rate for applications "
                                       "submitted before its issue (31A), and the application date is not stated")
                    if night_unknown and any(price(code, wd, s_, zone, g, False, pct, T)[0] != price(code, wd, s_, zone, g, True, pct, T)[0]
                                             for s_ in subs):
                        blocked.append("night_work: this item attracts the night uplift (Cl.7; Sch 4) and the line does not say "
                                       "whether the work was at night")
                    break
                priced["|".join(x for x in (gl, bl) if x)] = (rate_k, tr_k, [x for x in rd_k if not (band_unknown and x.startswith("band_pct="))])
        except KeyError as e:
            priced = {}
            blocked.append(f"no published index/FX for {e} (Sch 2A, 2B pp21-22): the rate cannot be built for the work month")
    if night_unknown and code in T.night:
        r.input_gap("night_work", "unresolved" if any(b.startswith("night_work") for b in blocked) else "n/a", "input_unresolved"
                    if any(b.startswith("night_work") for b in blocked) else None, "Cl.7 (p3); Sch 4 (p24)",
                    "the line does not say whether the work was at night", L)
    r.rates = {k: rt for k, (rt, _t, _r) in priced.items()}
    if priced:
        first = next(iter(priced))
        rate, tr, readings = priced[first]
        r.readings += readings
    else:
        rate, tr = None, Trace()
    multi = len(priced) > 1
    r.unit_rate = None if multi else rate
    ra = line.get("rate_applied")
    if rate is None:
        r.add("rate", "unresolved" if blocked else "n/a", "CW-R10", "Cl.27, Cl.28 (p6); Sch 2A/2B",
              detail="the rate cannot be built: " + ("; ".join(b.split(":")[0] for b in blocked) or "no published index/FX month"))
    elif empty(ra):
        r.add("rate", "unresolved", "CW-R10", "Cl.27, Cl.28 (p6); Cl.42 (p8)", detail="rate_applied not stated")
    elif multi:
        match = [k for k, (rt, _t, _r) in priced.items() if rt == ra]
        rates = ", ".join(f"{k} {rt}" for k, (rt, _t, _r) in priced.items())
        if match:
            r.add("rate", "unresolved", "CW-R10", "Cl.27, Cl.28 (p6); Sch 3, Sch 4 Part 3 (pp23-25)", "rate_differs",
                  f"billed {ra} is the rate under {', '.join(match)} ({rates}); which applies is not "
                  f"established at G3 ({', '.join(c['dimension'] + ': ' + c['owner'] for c in r.conditions)})")
        else:
            r.add("rate", "finding", "CW-R10", "Cl.27, Cl.28 (p6); Sch 3, Sch 4 Part 3 (pp23-25)", "rate_differs",
                  f"billed {ra} is the rate under no admissible alternative ({rates})")
    elif ra != rate:
        r.add("rate", "finding", "CW-R10", "Cl.27, Cl.28 (p6); instruments pp38-43", "rate_differs", f"billed {ra}, contract {rate}")
    else:
        r.add("rate", "pass", "CW-R10", "Cl.27, Cl.28 (p6)")
    # 9 arithmetic (F3): Cl.28 divides a quantity at a band edge and sums the parts, so where the band is G4 state an
    # amount that a division at contract band rates reproduces exactly is not an established arithmetic error
    amt = line.get("amount")
    if empty(billed) or empty(ra) or empty(amt):
        r.add("arithmetic", "unresolved", "CW-R20", "Cl.28 (p6); Cl.43 (p8)", detail="quantity, rate_applied or amount not stated")
    elif billed * ra == amt:
        r.add("arithmetic", "pass", "CW-R20", "Cl.28 (p6)")
    else:
        split = band_split(code, billed, amt, priced, T) if band_unknown and priced else None
        if split:
            r.add("arithmetic", "unresolved", "CW-R20", "Cl.28 (p6); Sch 4 Part 3 (pp24-25)", "amount_arithmetic",
                  f"{billed} x {ra} = {billed * ra}, billed {amt}; the amount equals "
                  f"a division at a band edge ({split}); whether that division is the right one is G4 state")
        else:
            r.add("arithmetic", "finding", "CW-R20", "Cl.28 (p6); Cl.43 (p8)", "amount_arithmetic",
                  f"{billed} x {ra} = {billed * ra}, billed {amt}"
                  + ("; no division at a band edge at the contract's band rates reproduces it" if band_unknown else ""))
    # amount --------------------------------------------------------------------------------------------------
    if payable and allowed == 0:
        payable = False
        reasons.append("nothing chargeable under the quantity rules (" + "; ".join(
            c.detail for c in r.checks if c.check == "quantity" and c.detail) + ")")
    if payable and blocked:
        _g4(r, code, band_pct, T)
        return _unresolved(r, tr, reasons, blocked)
    r.payable = payable
    r.reasons = reasons
    if payable and multi:
        r.allowed_quantity, r.amount_status = allowed, "conditional"
        reasons.append("the rate depends on facts or state G3 does not have (" + "; ".join(
            f"{c['dimension']}: {c['owner']}" for c in r.conditions) + "): the amount under each admissible alternative is "
            "carried; a quantity crossing a band edge is divided at the edge, between the band amounts")
        for k, (rt, trk, _r) in priced.items():
            amt_k = trk.amount(allowed, rt, "Cl.28 (p6): quantity x rounded rate")
            r.alternatives[k] = {"unit_rate": rt, "allowed_quantity": allowed, "amount": amt_k, "trace": trk.steps}
        tr.note("conditional: one full trace per alternative in alternatives", "Sch 3 (p23); Sch 4 Part 3 (pp24-25)")
    elif payable:
        r.allowed_quantity = allowed
        r.amount = tr.amount(allowed, rate, "Cl.28 (p6): quantity x rounded rate")
    else:
        _not_payable(r, tr, reasons)
    r.trace = tr.steps
    _g4(r, code, band_pct, T)
    return r


def _not_payable(r, tr, reasons) -> LineResult:
    r.payable, r.reasons = False, reasons
    r.allowed_quantity, r.amount = Decimal("0"), Decimal("0.00")
    tr.note("not payable: " + "; ".join(reasons), "; ".join(sorted({c.clause for c in r.checks if c.status == 'finding'})))
    r.amount_status = "not_payable"
    r.conditions = []              # 0.00 whatever the band or ground: nothing is conditional
    r.trace = tr.steps
    return r


def _g4(r, code, band_pct, T) -> None:
    """G4 dependencies (never applied here)."""
    if code in T.banded:
        r.g4_dependencies.append("band_state (CW-R14): " + ("every band priced; G4 selects or divides" if band_pct is None
                                                            else f"band {band_pct}% given as an input"))
    if code in T.limited:
        r.g4_dependencies.append("daily_limit (CW-R15)")
    if code in T.excluded:
        r.g4_dependencies.append("exclusion (CW-R16)")
    if any("31A protection" in x for x in r.readings):
        r.g4_dependencies.append("a3_adjustment (CW-R22): difference posted once on a later application")


def _unresolved(r, tr, reasons, why: list[str], owner: str = "G5") -> LineResult:
    """A value that depends on an input G2 left empty: no amount, no default, the reason and who resolves it (a query to the
    subcontractor, reported at G5). Every check that did not need the input has run and stays on the result."""
    r.payable, r.reasons = None, reasons + why
    r.amount_status = "unresolved"
    r.allowed_quantity = r.amount = r.unit_rate = None
    r.alternatives = {}
    r.conditions = [{"dimension": "input", "owner": owner, "basis": "; ".join(why)}]
    tr.note("value unresolved: " + "; ".join(why), "Cl.42 (p8); guidelines: record what is missing rather than guessing a figure")
    r.trace = tr.steps
    return r


def record_applies(record, line: dict, code: str) -> tuple[bool, str]:
    """Whether a site record is evidence about THIS work (B1): the same day (or the week that contains it), the same work
    area, and an item basis covering the billed item. Checked for every record, whether or not the item needs a
    Schedule 5 record: a record of another day, area or item says nothing about this excavation's material (CW Cl.5,
    S4 'classified at the point of excavation and recorded on the daily excavation record')."""
    wd = line["work_date"]
    area = (line.get("site") or "").split(" ")[0]
    why = []
    if record.week_beginning:
        if not (record.week_beginning <= wd <= record.week_beginning + dt.timedelta(days=6)):
            why.append(f"week beginning {record.week_beginning}, work {wd}")
    elif record.date != wd:
        why.append(f"dated {record.date}, work {wd}")
    if record.area is None or record.area != area:
        why.append(f"area {record.area}, work {area or 'area not stated (the line states no site)'}")
    if code not in (record.candidates or []):
        why.append(f"evidences {record.candidates}, line bills {code}")
    return (not why), "; ".join(why)


def band_split(code: str, quantity: Decimal, amount: Decimal, priced: dict, T) -> str | None:
    """A division of the quantity at a band edge (Cl.28; Sch 4 Part 3) that reproduces the billed amount exactly at the
    contract's own band rates (per ground alternative), with each part at the quantity's precision; None if none does.
    Two adjacent bands, or all three where the quantity exceeds band 2's width. It does not say the division is right:
    which part lies in which band follows the cumulative quantity (G4)."""
    unit = Decimal(1).scaleb(min(quantity.as_tuple().exponent, 0))
    by_ground = {}
    for k, (rt, _t, _r) in priced.items():
        dims = dict(x.split(":", 1) for x in k.split("|") if x)
        by_ground.setdefault(dims.get("ground"), {})[int(dims["band"])] = rt
    b1_to, b2_to = T.band_edges[code]
    width2 = b2_to - b1_to
    for g, rates in by_ground.items():
        if len(rates) != 3:
            continue
        for lo, hi in ((1, 2), (2, 3)):
            ra, rb = rates[lo], rates[hi]
            if ra == rb:
                continue
            q = (amount - quantity * rb) / (ra - rb)
            if 0 < q < quantity and q % unit == 0:
                return f"{q} x {ra} (band {lo}) + {quantity - q} x {rb} (band {hi})" + (f", ground {g}" if g else "")
        if quantity > width2 and rates[1] != rates[3]:
            q1 = (amount - width2 * rates[2] - (quantity - width2) * rates[3]) / (rates[1] - rates[3])
            if 0 < q1 < quantity - width2 and q1 % unit == 0:
                return (f"{q1} x {rates[1]} (band 1) + {width2} x {rates[2]} (band 2) + {quantity - width2 - q1} x {rates[3]} (band 3)"
                        + (f", ground {g}" if g else ""))
    return None


def inputs_from_world(w):
    """(line, app, record, record_exists) for every civil line of the G2 world (app None: its header is not found)."""
    apps = {k: h.values for k, h in headers_by_id(w.claims.rows["cw_headers"])[0].items()}
    for row in w.claims.rows["cw_lines"]:
        v = row.values
        ref = v.get("record_ref")
        rec = w.cw.get(ref) if ref else None
        yield v, apps.get(v.get("application_no")), rec, rec is not None


def input_context(w) -> dict[str, Inputs]:
    """Per line (keyed as the batch keys its result, g3_core.result_keys): provenance of the line, its application and
    its record, and the record's fields in G2's queue."""
    apps, copies = headers_by_id(w.claims.rows["cw_headers"])
    gaps, repeated = {}, {}
    for u in w.queue.items:
        if u.kind == "cw_record":
            gaps.setdefault(u.ident, set()).add(u.field)
            if u.reason == "key repeated":
                repeated.setdefault(u.ident, set()).add(u.field)
    out = {}
    for key, row in zip(result_keys(w.claims.rows["cw_lines"]), w.claims.rows["cw_lines"]):
        v = row.values
        h = apps.get(v.get("application_no"))
        rec = w.cw.get(v["record_ref"]) if v.get("record_ref") else None
        out[key] = Inputs(f"{row.source.path}:{row.source.line}", f"{h.source.path}:{h.source.line}" if h else None,
                          rec.path if rec is not None else None,
                          frozenset(gaps.get(rec.ticket, ())) if rec is not None else frozenset(), False,
                          frozenset(repeated.get(rec.ticket, ())) if rec is not None else frozenset(),
                          header_copies=copies.get(v.get("application_no"), ()))
    return out


def engine_error(line: dict, ident: str, e: Exception) -> LineResult:
    """The batch never stops on one line: an exception becomes an explicit result naming it (X3 fails on any)."""
    r = LineResult("CW", line.get("line_ref") or ident, line.get("item_code") or "")
    r.add("engine", "unresolved", "G3-ENGINE", "G3 engine", "engine_error", f"{type(e).__name__}: {e}")
    r.amount_status, r.payable, r.family = "unresolved", None, "CW-ENGINE-ERROR"
    r.reasons.append(f"engine error ({type(e).__name__}): the line is not valued; a defect in G3, not a finding on the application")
    r.conditions = [{"dimension": "engine", "owner": "G3", "basis": "engine error"}]
    return r


def run(w, T=None) -> dict[str, LineResult]:
    out = {}
    ctx = input_context(w)
    rows = w.claims.rows["cw_lines"]
    for key, (line, app, rec, exists) in zip(result_keys(rows), inputs_from_world(w)):
        try:
            res = evaluate(line, app, rec, exists, T=T, inputs=ctx[key])
        except Exception as e:  # noqa: BLE001 - recorded on the line, never swallowed: X3 fails on engine_error
            res = engine_error(line, key, e)
        res.ctx = w.run_context["id"] if w.run_context else None
        out[key] = res
    return out
