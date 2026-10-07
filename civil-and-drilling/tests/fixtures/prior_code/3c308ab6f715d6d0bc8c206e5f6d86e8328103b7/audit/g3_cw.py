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

import datetime as dt
import itertools
from decimal import Decimal

from . import terms
from .g3_core import LineResult, Trace

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


def evaluate(line: dict, app: dict, record, record_exists: bool, band_pct: Decimal | None = None, T=None) -> LineResult:
    """line/app: typed claim fields; record: G2 CwRecord or None (record_exists: a file with that ticket exists).
    band_pct: the annual quantity-band percentage when known (a case input); None = G4 state not known here."""
    T = T or terms.cw()
    code, wd = line["item_code"], line["work_date"]
    r = LineResult("CW", line["line_ref"], code)
    sch = T.sch1.get(code)
    if sch is None:
        r.add("identification", "unresolved", "CW-R23", "Cl.26 (p6); Sch 6-8 (pp28-31); P24 (p14)", "code_not_in_schedule_1",
              "no Schedule 1 rate; valued only by its own route (daywork/provisional/preliminaries) whose conditions are not evidenced")
        r.amount_status, r.payable, r.family = "unresolved", None, "CW-UNSCHEDULED"
        r.reasons.append("code outside Schedule 1 (CW-R23)")
        return r
    payable, reasons = True, []

    # 1 identity ----------------------------------------------------------------------------------------------
    if app["contract_ref"] != CONTRACT_REF:
        r.add("identity", "finding", "CW-R01", "p1 particulars; Cl.40 (p8)", "contract_ref_variant", app["contract_ref"])
    elif (app.get("subcontractor") or "").upper() != SUBCONTRACTOR:
        r.add("identity", "finding", "CW-R01", "p1 particulars", "subcontractor_mismatch", app.get("subcontractor"))
    else:
        r.add("identity", "pass", "CW-R01", "p1 particulars")
    # 2 term ------------------------------------------------------------------------------------------------
    if not (T.commencement <= wd <= T.completion):
        r.add("term", "finding", "CW-R03", "p1 particulars; A2 2.1 (p42)", "out_of_term", f"{wd} outside {T.commencement}..{T.completion}")
        payable = False
        reasons.append("work outside the term as extended is not measurable (A1/A2)")
    else:
        r.add("term", "pass", "CW-R03", "p1 particulars; A1 (p40); A2 (p42)")
    # 3 window and period (application header) -----------------------------------------------------------------
    pto, adate = app["period_to"], app["application_date"]
    if adate < pto:
        r.add("window", "finding", "CW-R04", "Cl.41 (p8)", "submitted_early", f"{adate} before period end {pto}")
    elif adate > pto + dt.timedelta(days=T.window_days):
        r.add("window", "finding", "CW-R04", "Cl.41 (p8)", "submitted_late", f"{(adate - pto).days} days after period end")
    else:
        r.add("window", "pass", "CW-R04", "Cl.41 (p8)")
    if not (app["period_from"] <= wd <= pto):
        r.add("period", "finding", "CW-R02", "Cl.41 (p8)", "outside_period", f"{wd} outside {app['period_from']}..{pto}")
    else:
        r.add("period", "pass", "CW-R02", "Cl.40, Cl.41 (p8)")
    # 4 unit ------------------------------------------------------------------------------------------------
    if line["unit"] != sch["unit"]:
        r.add("unit", "finding", "CW-R08", "Cl.26 (p6)", "wrong_unit", f"billed {line['unit']}, Schedule 1 {sch['unit']}")
        payable = False
        reasons.append("quantity in a unit other than Schedule 1's is rejected in its entirety (Cl.26)")
    else:
        r.add("unit", "pass", "CW-R08", "Cl.26 (p6)")
    # 5 evidence (Schedule 5) ----------------------------------------------------------------------------------
    series = T.records.get(code)
    ref = line.get("record_ref")
    evidence_ok = True
    if series:
        ev = []
        if not ref or not record_exists or record is None:
            ev.append(("record_missing", f"reference '{ref or ''}' names no delivered record"))
        else:
            if not ref.startswith(series + "-"):
                ev.append(("record_wrong_series", f"{ref} is not a {series} record"))
            if not (record.foreman_signed and record.engineer_signed):
                ev.append(("record_unsigned", "foreman/Engineer's representative signature missing or placeholder"))
            if record.family == "DW" or record.week_beginning:
                wb = record.week_beginning
                if not (wb and wb <= wd <= wb + dt.timedelta(days=6)):
                    ev.append(("record_date_mismatch", f"work date {wd} outside the week beginning {wb}"))
            elif record.date != wd:
                ev.append(("record_date_mismatch", f"record dated {record.date}, line {wd}"))
            area = (line.get("site") or "").split(" ")[0]
            if record.area is None:
                ev.append(("record_area_unresolved", "record work area not parsed (G2 queue)"))
            elif record.area != area:
                ev.append(("record_area_mismatch", f"record area {record.area}, line {area}"))
            if code not in (record.candidates or []):
                ev.append(("item_not_supported_by_record", f"record evidences {record.candidates}, line bills {code}"))
            elif record.unit != sch["unit"]:
                ev.append(("item_not_supported_by_record", f"record quantity in {record.unit}, Schedule 1 unit {sch['unit']}"))
        for f, d in ev:
            r.add("evidence", "finding", "CW-R05", "Cl.46, Cl.47 (p8); Sch 5 (p27); 47A (p33)", f, d)
        if ev:
            evidence_ok = payable = False
            reasons.append("Schedule 5 item not payable in any valuation until its record is delivered (Cl.46; Q3 reading A)")
            r.readings.append("Q3:A")
            r.g4_dependencies.append("p23_link (Q3: no second deduction if later recovered under P23)")
        else:
            r.add("evidence", "pass", "CW-R05", "Cl.46, Cl.47 (p8); Sch 5 (p27)")
    else:
        r.add("evidence", "n/a", "CW-R05", "Sch 5 (p27)", detail="no Schedule 5 record prescribed")
    # 6 quantity ----------------------------------------------------------------------------------------------
    r.family = ("CW-HOUR" if series and code in T.hourly else "CW-WEEK" if series and code in T.weekly_record else
                "CW-SURV" if series and code in T.surveyed else "CW-REC" if series else "CW-MEAS")
    billed = line["quantity"]
    allowed = billed
    if series and evidence_ok:
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
        if allowed < billed and not over and code in T.weekly_record:
            pass
    elif series:
        allowed = Decimal("0")
        r.add("quantity", "n/a", "CW-R07", "Cl.46 (p8)", detail="no deliverable record: nothing measurable")
    else:
        r.add("quantity", "pass", "CW-R07", "Cl.25 (p6)", detail="no record prescribed; quantity as measured in the application")
    # 7-8 rate ------------------------------------------------------------------------------------------------
    zone = (line.get("site_zone") or "").split(" ")[0] or None
    if sch["series"] in T.zone_series and zone:
        r.readings.append(f"zone {zone} as stated in the application (Cl.4, Cl.42; no supplied record states the zone; G3-D3)")
    if line.get("night_work") == "Y" and code in T.night:
        r.readings.append("night work as stated in the application (Cl.7; no supplied record states the time of work; G3-D3)")
    # ground classification (F2): the authority is the classification recorded at excavation (S4; Cl.5 the Engineer's
    # written confirmation, not supplied) - never the application's own statement (Cl.42 requires it to state one)
    ground_opts = [(None, None)]
    if code in T.ground_items:
        claimed = (line.get("ground_class") or "").split(" ")[0] or None
        applies, why = record_applies(record, line, code) if record is not None else (False, "no record referenced")
        # the classification is the Engineer's (Cl.5; App A 'Ground classification'; 27A 'whatever the Engineer recorded on
        # the day'; App A 'Engineer' includes the representative): a record without the representative's countersignature
        # is the Subcontractor's own statement, not that classification
        engineers = bool(record is not None and record.engineer_signed)
        recorded = bool(applies and evidence_ok and record.ground and engineers)
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
    try:
        for (gl, g), (bl, pct) in itertools.product(ground_opts, band_opts):
            rate_k, tr_k, rd_k = price(code, wd, app["application_date"], zone, g, line.get("night_work") == "Y", pct, T)
            priced["|".join(x for x in (gl, bl) if x)] = (rate_k, tr_k, [x for x in rd_k if not (band_unknown and x.startswith("band_pct="))])
    except KeyError as e:
        priced = {}
        tr = Trace()
        tr.note(f"not priced: no published index/FX for {e} (outside the tables)", "Sch 2A, 2B (pp21-22)")
    if priced:
        first = next(iter(priced))
        rate, tr, readings = priced[first]
        r.readings += readings
    else:
        rate = None
    multi = len(priced) > 1
    r.unit_rate = None if multi else rate
    if rate is None:
        r.add("rate", "n/a", "CW-R10", "Sch 2A/2B", detail="no published index/FX month")
    elif multi:
        match = [k for k, (rt, _t, _r) in priced.items() if rt == line["rate_applied"]]
        rates = ", ".join(f"{k} {rt}" for k, (rt, _t, _r) in priced.items())
        if match:
            r.add("rate", "unresolved", "CW-R10", "Cl.27, Cl.28 (p6); Sch 3, Sch 4 Part 3 (pp23-25)", "rate_differs",
                  f"billed {line['rate_applied']} is the rate under {', '.join(match)} ({rates}); which applies is not "
                  f"established at G3 ({', '.join(c['dimension'] + ': ' + c['owner'] for c in r.conditions)})")
        else:
            r.add("rate", "finding", "CW-R10", "Cl.27, Cl.28 (p6); Sch 3, Sch 4 Part 3 (pp23-25)", "rate_differs",
                  f"billed {line['rate_applied']} is the rate under no admissible alternative ({rates})")
    elif line["rate_applied"] != rate:
        r.add("rate", "finding", "CW-R10", "Cl.27, Cl.28 (p6); instruments pp38-43", "rate_differs", f"billed {line['rate_applied']}, contract {rate}")
    else:
        r.add("rate", "pass", "CW-R10", "Cl.27, Cl.28 (p6)")
    # 9 arithmetic (F3): Cl.28 divides a quantity at a band edge and sums the parts, so where the band is G4 state an
    # amount that a division at contract band rates reproduces exactly is not an established arithmetic error
    if billed * line["rate_applied"] == line["amount"]:
        r.add("arithmetic", "pass", "CW-R20", "Cl.28 (p6)")
    else:
        split = band_split(code, billed, line["amount"], priced, T) if band_unknown and priced else None
        if split:
            r.add("arithmetic", "unresolved", "CW-R20", "Cl.28 (p6); Sch 4 Part 3 (pp24-25)", "amount_arithmetic",
                  f"{billed} x {line['rate_applied']} = {billed * line['rate_applied']}, billed {line['amount']}; the amount equals "
                  f"a division at a band edge ({split}); whether that division is the right one is G4 state")
        else:
            r.add("arithmetic", "finding", "CW-R20", "Cl.28 (p6); Cl.43 (p8)", "amount_arithmetic",
                  f"{billed} x {line['rate_applied']} = {billed * line['rate_applied']}, billed {line['amount']}"
                  + ("; no division at a band edge at the contract's band rates reproduces it" if band_unknown else ""))
    # amount --------------------------------------------------------------------------------------------------
    if payable and allowed == 0:
        payable = False
        reasons.append("nothing chargeable under the quantity rules (" + "; ".join(
            c.detail for c in r.checks if c.check == "quantity" and c.detail) + ")")
    r.payable = payable
    r.reasons = reasons
    if payable and multi:
        r.allowed_quantity, r.amount_status = allowed, "conditional"
        reasons.append("the rate depends on facts or state G3 does not have (" + "; ".join(
            f"{c['dimension']}: {c['owner']}" for c in r.conditions) + "): the amount under each admissible alternative is "
            "carried; a quantity crossing a band edge is divided at the edge, between the band amounts")
        for k, (rt, trk, _r) in priced.items():
            amt = trk.amount(allowed, rt, "Cl.28 (p6): quantity x rounded rate")
            r.alternatives[k] = {"unit_rate": rt, "allowed_quantity": allowed, "amount": amt, "trace": trk.steps}
        tr.note("conditional: one full trace per alternative in alternatives", "Sch 3 (p23); Sch 4 Part 3 (pp24-25)")
    elif payable:
        r.allowed_quantity = allowed
        r.amount = tr.amount(allowed, rate, "Cl.28 (p6): quantity x rounded rate")
    else:
        r.allowed_quantity, r.amount = Decimal("0"), Decimal("0.00")
        tr.note("not payable: " + "; ".join(reasons), "; ".join(sorted({c.clause for c in r.checks if c.status == 'finding'})))
        r.amount_status = "not_payable"
        r.conditions = []              # 0.00 whatever the band or ground: nothing is conditional
    r.trace = tr.steps
    # G4 dependencies (never applied here) --------------------------------------------------------------------
    if code in T.banded:
        r.g4_dependencies.append("band_state (CW-R14): " + ("every band priced; G4 selects or divides" if band_pct is None
                                                            else f"band {band_pct}% given as an input"))
    if code in T.limited:
        r.g4_dependencies.append("daily_limit (CW-R15)")
    if code in T.excluded:
        r.g4_dependencies.append("exclusion (CW-R16)")
    if any("31A protection" in x for x in r.readings):
        r.g4_dependencies.append("a3_adjustment (CW-R22): difference posted once on a later application")
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
        why.append(f"area {record.area}, work {area}")
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
    """(line, app, record, record_exists) for every civil line of the G2 world."""
    apps = {h.ident: h.values for h in w.claims.rows["cw_headers"]}
    for row in w.claims.rows["cw_lines"]:
        v = row.values
        ref = v.get("record_ref")
        rec = w.cw.get(ref) if ref else None
        yield v, apps[v["application_no"]], rec, rec is not None


def run(w, T=None) -> dict[str, LineResult]:
    out = {}
    for line, app, rec, exists in inputs_from_world(w):
        res = evaluate(line, app, rec, exists, T=T)
        res.ctx = w.run_context["id"] if w.run_context else None
        out[res.line_ref] = res
    return out
