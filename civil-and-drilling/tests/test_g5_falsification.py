"""G5 falsification: invoices built to break the outcome rules on branches no pinned invoice or sampled reader exercises -
the DS-900 threshold crossed by a correction, VAT/DS-900 half-even boundaries, offsetting line errors that leave the
total unchanged, a class statement the pricing contradicts, a line that cannot be valued, a same-day Q7 C tie between two
invoices, an A3 adjustment that IS carried, a procedural-only breach. Expectations are computed here by hand from the
contract (DDS Cl.36-40 p8; Q9 rule), not from the engine."""
from decimal import Decimal

from audit.g3_core import Check, LineResult
from audit.g4_core import G4Line
from audit.g5_outcomes import Engine, Invoice, Policy

D = Decimal


def _line(contract, ref, code, billed, value=None, alts=None, findings=(), payable=True, conditions=()):
    r = LineResult(contract=contract, line_ref=ref, code=code, family="x")
    r.payable = payable
    r.amount = None if alts else (D(value) if value is not None else None)
    r.allowed_quantity = D("1")
    r.alternatives = {k: {"amount": D(v) if v is not None else None, "allowed_quantity": D("1"), "trace": []}
                      for k, v in (alts or {}).items()}
    r.amount_status = "alternatives" if alts else "determined"
    r.conditions = list(conditions)
    r.checks = [Check("identity", "pass", "R", "c")] + [Check("rate", "finding", "R", "c", f) for f in findings]
    g = G4Line(ref, r)
    return (ref, {"line_ref": ref, "amount": D(billed), "quantity": D("1"), "ground_class": ""}, g)


def _engine(invs, a3=None, stands=None, policy=None):
    e = Engine.__new__(Engine)
    e.policy = policy or Policy()
    e.inv = {"CW": {}, "DDS": {}}
    for i in invs:
        e.inv[i.contract][i.id] = i
    e.stands = stands or {}
    e.a3 = a3 or {"CW": {}, "DDS": {}}
    e.release = {}
    e.inv_date = {}
    return e


def _dds(no, lines, header_extra=None, cls="Standard"):
    svc = sum(D(v["amount"]) for _k, v, g in lines if g.g3.code != "DS-900")
    ds = sum(D(v["amount"]) for _k, v, g in lines if g.g3.code == "DS-900")
    net = svc + ds
    vat = (net * D("0.15")).quantize(D("0.01"))
    h = {"invoice_no": no, "well_class": cls, "net_amount": net, "vat_amount": vat, "invoice_total": net + vat,
         "adjustment": D("0.00"), **(header_extra or {})}
    return Invoice("DDS", no, h, lines)


def test_correction_crossing_the_ds900_threshold_recomputes_discount_and_vat():
    # billed: 260,000.00 of services, DS-900 -400.00 (4% of 10,000), net 259,600.00, VAT 38,940.00, total 298,540.00
    # contract: one charge is worth 20,000 less -> services 240,000.00, no DS-900, net 240,000.00, VAT 36,000.00
    lines = [_line("DDS", "X-001", "DD-101", "200000.00", "200000.00"),
             _line("DDS", "X-002", "MW-301", "60000.00", "40000.00", findings=["quantity_above_report"]),
             _line("DDS", "X-003", "DS-900", "-400.00")]
    inv = _dds("X", lines)
    o = _engine([inv]).outcome(inv)
    assert o["flagged"] == 1 and o["expected_total"] == D("276000.00")      # 240,000 + 36,000 (not 298,540 - 23,000)


def test_exactly_the_threshold_gives_no_discount_and_vat_rounds_half_even():
    # services exactly 250,000.00: no DS-900 (Cl.38 'exceeds'); a billed DS-900 of -0.00 is none
    inv = _dds("Y", [_line("DDS", "Y-001", "DD-101", "250000.00", "250000.00")])
    o = _engine([inv]).outcome(inv)
    assert o["flagged"] == 0 and o["expected_total"] == D("287500.00")
    # VAT on 100.03: 15.0045 -> 15.00; on 100.10: 15.015 -> 15.02 (half to even: 1 is odd -> up)
    inv2 = _dds("Z", [_line("DDS", "Z-001", "DD-101", "100.10", "100.10")],
                header_extra={"vat_amount": D("15.02"), "invoice_total": D("115.12")})
    o2 = _engine([inv2]).outcome(inv2)
    assert o2["expected_total"] == D("115.12") and o2["flagged"] == 0


def test_offsetting_line_errors_are_flagged_though_the_total_is_unchanged():
    lines = [_line("CW", "P-01", "A.11.010", "100.00", "90.00"), _line("CW", "P-02", "A.11.020", "50.00", "60.00")]
    inv = Invoice("CW", "P", {"application_total": D("150.00"), "retention": D("7.50"), "net_payable": D("142.50"),
                              "adjustment": D("0"), "retention_released": D("0")}, lines)
    o = _engine([inv]).outcome(inv)
    assert o["flagged"] == 1 and o["expected_total"] == D("150.00") and "rate" in o["error_category"]


def test_the_invoices_class_statement_never_selects_its_value():
    # G5-B01 (Q9-3 E): the call-off is not supplied; the header's well class is the contractor's statement. It neither
    # selects the priced alternative nor is itself tested: the outcome is the same whichever class the header states.
    alts = {"class:Standard": "1000.00", "class:Extended Reach": "1100.00", "class:HPHT": "1250.00"}
    for billed in ("1000.00", "1250.00"):
        outs = []
        for cls in ("HPHT", "Standard", "Extended Reach", ""):
            inv = _dds("C", [_line("DDS", "C-001", "DD-101", billed, alts=alts)], cls=cls)
            o = _engine([inv]).outcome(inv)
            outs.append((o["flagged"], o["expected_total"], o["confidence"]))
        assert len(set(outs)) == 1
        assert outs[0] == (0, (D(billed) * D("1.15")).quantize(D("0.01")), D("0.80"))   # right under an admissible call-off
    # priced at no admissible class: wrong under every call-off; expected total on the absent-document value (Standard,
    # P2/P3 'where the call-off so states'), confidence 0.60 (the total depends on the unsupplied call-off)
    inv = _dds("C3", [_line("DDS", "C3-001", "DD-101", "1111.00", alts=alts)], cls="HPHT")
    o = _engine([inv]).outcome(inv)
    assert o["flagged"] == 1 and o["expected_total"] == D("1150.00") and o["confidence"] == D("0.60")


def test_a_line_that_cannot_be_valued_is_never_a_confident_pass():
    lines = [_line("CW", "U-01", "A.11.010", "100.00", None, payable=None)]
    lines[0][2].r.payable = None
    inv = Invoice("CW", "U", {"application_total": D("100.00"), "retention": D("5.00"), "net_payable": D("95.00"),
                              "adjustment": D("0"), "retention_released": D("0")}, lines)
    o = _engine([inv]).outcome(inv)
    # Q9-4: not formed -> confidence 0.30; flagged only when a check fails (none does here). G5-B02: the total is not
    # established - never the billed 100.00: the export is the lower bound (EXPORT-U), the upper bound is disclosed (none)
    assert o["formed"] is False and o["confidence"] == D("0.30") and o["flagged"] == 0
    assert o["expected_total"] == D("0.00") and o["expected_bounds"] == ["0", None] and o["expected_status"] == "bounded"
    lines2 = [lines[0], _line("CW", "U-02", "A.11.020", "50.00", "40.00")]
    inv2 = Invoice("CW", "U2", {**inv.header, "application_total": D("150.00"), "retention": D("7.50"),
                                "net_payable": D("142.50")}, lines2)
    o2 = _engine([inv2]).outcome(inv2)
    assert o2["flagged"] == 1 and o2["confidence"] == D("0.30") and o2["expected_total"] == D("40.00")   # 40 + [0, -]


def test_a_same_day_tie_between_two_invoices_stays_open_under_q7c():
    alts = {"stands:T1-001": "500.00", "stands:T2-001": "0.00"}
    alts2 = {"stands:T1-001": "0.00", "stands:T2-001": "500.00"}
    i1 = _dds("T1", [_line("DDS", "T1-001", "MW-301", "500.00", alts=alts)])
    i2 = _dds("T2", [_line("DDS", "T2-001", "MW-301", "500.00", alts=alts2)])
    e = _engine([i1, i2], stands={"T1-001": {"stands": ("T1-001", True)}, "T2-001": {"stands": ("T1-001", True)}})
    o1, o2 = e.outcome(i1), e.outcome(i2)
    # submitted the same day: which invoice is earlier is not established - each is wrong in one scenario: Q9-2
    assert o1["flagged"] == o2["flagged"] == 1 and o1["confidence"] == o2["confidence"] == D("0.50")
    e2 = _engine([i1, i2], stands={"T1-001": {"stands": ("T1-001", False)}, "T2-001": {"stands": ("T1-001", False)}})
    assert e2.outcome(i1)["flagged"] == 0 and e2.outcome(i2)["flagged"] == 1       # owner: the earlier stands


def test_an_a3_adjustment_that_is_carried_is_not_a_defect_and_one_that_is_omitted_is():
    a3 = {"CW": {"Q1:A": ["R"], "Q1:B": ["R"], "_total": {"by_reading": {"": {"min": "10.00", "max": "10.00"}}}}, "DDS": {}}
    line = [_line("CW", "R-01", "A.11.010", "100.00", "100.00")]
    head = {"application_total": D("100.00"), "retention": D("5.00"), "adjustment": D("10.00"),
            "net_payable": D("105.00"), "retention_released": D("0")}
    inv = Invoice("CW", "R", head, line)
    assert _engine([inv], a3=a3).outcome(inv)["flagged"] == 0
    inv0 = Invoice("CW", "R", {**head, "adjustment": D("0"), "net_payable": D("95.00")}, line)
    o = _engine([inv0], a3=a3).outcome(inv0)
    assert o["flagged"] == 1 and o["error_category"] == "adjustment" and o["expected_total"] == D("100.00")


def test_a_procedural_breach_keeps_the_contract_value():
    lines = [_line("CW", "L-01", "A.11.010", "100.00", "100.00", findings=["submitted_late"])]
    inv = Invoice("CW", "L", {"application_total": D("100.00"), "retention": D("5.00"), "net_payable": D("95.00"),
                              "adjustment": D("0"), "retention_released": D("0")}, lines)
    o = _engine([inv]).outcome(inv)
    assert o["flagged"] == 1 and o["expected_total"] == D("100.00") and o["error_category"] == "timing"
    assert o["confidence"] == D("0.80")
    ob = _engine([inv], policy=Policy(q9="B")).outcome(inv)
    assert ob["flagged"] == 0                                              # Q9 B: only a changed total


def test_where_readings_disagree_the_expected_total_never_follows_the_bill():
    # found by Z4 on the population: the expected total of a mixed invoice was taken from the first reading under which
    # it is wrong, so it moved with the bill. It is now the working reading's total whatever the bill says.
    alts = {"order:Cl.30 wording (application number, then line)": "100.00", "order:other": "90.00"}
    for billed in ("100.00", "90.00", "95.00"):
        inv = Invoice("CW", "M", {"application_total": D(billed), "retention": (D(billed) * D("0.05")).quantize(D("0.01")),
                                  "net_payable": D(billed) - (D(billed) * D("0.05")).quantize(D("0.01")),
                                  "adjustment": D("0"), "retention_released": D("0")},
                      [_line("CW", "M-01", "A.13.010", billed, alts=alts)])
        o = _engine([inv]).outcome(inv)
        assert o["expected_total"] == D("100.00") and o["flagged"] == 1
        assert o["confidence"] == (D("0.50") if billed != "95.00" else D("0.60"))   # 95: wrong under both, totals differ
