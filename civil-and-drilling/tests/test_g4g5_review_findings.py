"""Independent review of G4/G5 (findings A-n, B-n, C-n): each finding's reproduction
through the production pipeline (or the engine objects it produces), the corrected result, and a negative control - the
reviewed code (tests/fixtures/prior_code/g4g5_reviewed/) on the same input, failing for the finding's reason
and rejected by the corrected oracle where one applies."""
import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

import prior_code

import g4_histories as H
import g4g5_regression_fixtures as F
import test_g5_falsification as U
import verify_g4 as vg4
import verify_g5 as vg5
from audit import g4_cw, g4_dds, g5_outcomes, g5_run
from audit.g3_core import Check
from test_g4g5_regressions import _claims, _hist, cw_hist, prior_module

ROOT = Path(__file__).resolve().parents[1]
PRE = "g4g5_reviewed"          # the commit the independent reviewers examined
D = Decimal
CREW = F.CREW


def _pre(path, name):
    return prior_module(PRE, path, name)


def _ddr(well, date, d0, d1, run=1, section=H.S12):
    return H.ddr(well, "NG-Rig 97", date, section, "Operating", d0, d1, 20, run, H.BASIC, CREW,
                 part_b=(date, date, H.BASIC, 20, False))


def _pd_inv(no, well, idate, charges):
    """charges: [(service date, from, to)] PD-210 at the band-1 rate 98.70, amounts as billed."""
    return H.dds_inv(no, well, "NG-Rig 97", idate, [(sd, "PD-210", str(D(t) - D(f)), "98.70", H.S12, "Operating", str(f), str(t))
                                                   for sd, f, t in charges])


def _run(h, g4dds=g4_dds, g4c=g4_cw, g5=g5_outcomes):
    w, res, _st = F.run(h)
    _claims(w)
    st = {"CW": g4c.run(w, res["CW"]), "DDS": g4dds.run(w, res["DDS"])}
    e = g5.Engine(w, st)
    out = {no: e.outcome(i) for c in ("CW", "DDS") for no, i in e.inv[c].items()}
    return w, st, out, e


# ================================================================================================= A-1 PD-210 without depths
def h_a1():
    well = "NGP-ZZ-972"

    def inv(no, idate, depths):
        return H.dds_inv(no, well, "NG-Rig 97", idate, [("2025-07-01", "PD-210", "100", "58.15", H.S12, "Operating",
                                                         "1500" if depths else "", "1600" if depths else "")])
    return {"id": "A1", "documents": [inv("MDS-97201", "2025-07-05", False), inv("MDS-97202", "2025-07-12", True)],
            "reports": H.reports(_ddr(well, "2025-07-01", 1500, 1600)), "records": {}, "given": []}


def test_a1_a_charge_without_depths_contests_the_days_metres():
    w, st, out, _e = _run(h_a1())
    g = F.by_ref(st, "DDS")["MDS-97202-001"]
    assert D("0.00") in F.amounts(g) and D("5815.00") in F.amounts(g)
    assert any(x.finding == "charged_twice" and x.status == "unresolved" for x in g.state)
    # the earlier-submitted charge would stand (Q7 C) but its metres are not established: which keeps them is open
    o = out["MDS-97202"]
    assert o["flagged"] == 1 and o["confidence"] == D("0.50") and any(k.startswith("alloc@") for k in o["open_readings"])
    assert vg4.pd210_coverage_errors(st["DDS"]) == []


def test_a1_control_audited_code_passes_the_repeat():
    old4, old5 = _pre("audit/g4_dds.py", "g4_dds_pre"), _pre("audit/g5_outcomes.py", "g5_outcomes_pre")
    w, st, out, _e = _run(h_a1(), g4dds=old4, g5=old5)
    assert not F.by_ref(st, "DDS")["MDS-97202-001"].r.alternatives
    assert out["MDS-97202"]["flagged"] == 0 and out["MDS-97202"]["confidence"] == D("0.80")     # the review's silent pass
    assert any("allocations carried, the joint domain has 2" in e for e in vg4.pd210_coverage_errors(st["DDS"]))


# ================================================================================================= A-2 unresolved prior metres
def h_a2(status):
    well = "NGP-ZZ-973"
    r1 = _ddr(well, "2025-07-01", 4500, 44490)
    r1 = (r1[0], r1[1].replace("Status: Operating", status))
    return {"id": "A2", "documents": [_pd_inv("MDS-97301", well, "2025-07-10", [("2025-07-01", 4500, 44490)]),
                                      _pd_inv("MDS-97302", well, "2025-07-12", [("2025-07-02", 44490, 44510)])],
            "reports": dict([r1, _ddr(well, "2025-07-02", 44490, 44510)]), "records": {}, "given": []}


def test_a2_admissibility_not_established_is_a_possible_contributor_only():
    w, st, out, _e = _run(h_a2("Status: "))
    g = F.by_ref(st, "DDS")["MDS-97302-001"]
    assert g.r.payable is None and g.r.amount_status == "unresolved"          # 1,974.00 or 1,934.50: not established
    assert st["DDS"].footage["NGP-ZZ-973"]["2025-07-02|44490-44510|Q14:A|Q11:A"] == ["0", "39990"]
    assert out["MDS-97302"]["flagged"] == 0 and out["MDS-97302"]["confidence"] == D("0.30")
    assert vg4.footage_reset_errors(st["DDS"]) == []
    # control inputs: established not payable (Standby) and established payable (Operating) stay determined
    _w, st2, _o, _e2 = _run(h_a2("Status: Operating"))
    assert F.by_ref(st2, "DDS")["MDS-97302-001"].r.amount == D("1934.50")


def test_a2_control_audited_code_counts_the_unknown_metres_as_certain():
    w, st, out, _e = _run(h_a2("Status: "), g4dds=_pre("audit/g4_dds.py", "g4_dds_pre"))
    g = F.by_ref(st, "DDS")["MDS-97302-001"]
    assert g.r.amount == D("1934.50") and any(x.finding == "footage_band_divided" and x.status == "finding" for x in g.state)
    assert any("the unique-metre recount gives [0, 39990]" in e for e in vg4.footage_reset_errors(st["DDS"]))


# ================================================================================================= A-3 Q11 B report gap
def h_a3(blank):
    well = "NGP-ZZ-974"
    r0 = H.ddr(well, "NG-Rig 97", "2025-06-30", H.S17, "Operating", 4000, 4500, 14, 1, H.BASIC, CREW,
               part_b=("2025-06-30", "2025-06-30", H.BASIC, 14, False))
    if blank:
        r0 = (r0[0], r0[1].replace("Depth end (m MD): 4500", "Depth end (m MD): "))
    return {"id": "A3", "documents": [_pd_inv("MDS-97401", well, "2025-07-10", [("2025-07-01", 4500, 44400)]),
                                      _pd_inv("MDS-97402", well, "2025-07-12", [("2025-07-02", 44400, 44420)])],
            "reports": dict([r0, _ddr(well, "2025-07-01", 4500, 44400, run=2), _ddr(well, "2025-07-02", 44400, 44420, run=2)]),
            "records": {}, "given": []}


def test_a3_a_report_without_depths_leaves_q11b_unbounded():
    _w, st, _o, _e = _run(h_a3(False))
    assert F.amounts(F.by_ref(st, "DDS")["MDS-97402-001"]) == [D("1895.00"), D("1974.00")]       # Q11 B / Q11 A
    _w, st, _o, _e = _run(h_a3(True))
    g = F.by_ref(st, "DDS")["MDS-97402-001"]
    assert g.r.payable is None and g.r.amount_status == "unresolved"
    assert st["DDS"].footage["NGP-ZZ-974"]["2025-07-02|44400-44420|Q14:A|Q11:B"] == ["39900", None]
    assert vg4.footage_reset_errors(st["DDS"]) == []


def test_a3_control_audited_code_drops_the_report():
    _w, st, _o, _e = _run(h_a3(True), g4dds=_pre("audit/g4_dds.py", "g4_dds_pre"))
    assert F.by_ref(st, "DDS")["MDS-97402-001"].r.amount == D("1974.00")        # the Q11 alternative has collapsed
    assert any("Q11:B" in e and "the report recount gives [39900, None]" in e for e in vg4.footage_reset_errors(st["DDS"]))


# ================================================================================================= A-4 A3 account, undated documents
@pytest.mark.parametrize("hid,doc,field", [("CW-H09", "PA-99001", "application_date"), ("DDS-H05", "MDS-90041", "invoice_date")])
def test_a4_undated_document_is_a_possible_a3_contributor(hid, doc, field):
    c = "CW" if hid.startswith("CW") else "DDS"
    _w, st, _o, _e = _run(_hist(hid, doc, field))
    a = st[c].adjustments[0]
    assert a["total"]["lines_not_established"] >= 1 and a["possible_lines"] >= 1 and a["total"]["unknown_hi_open"]
    assert vg4.a3_contributor_errors(st["CW"], st["DDS"]) == []


@pytest.mark.parametrize("hid,doc,field", [("CW-H09", "PA-99001", "application_date"), ("DDS-H05", "MDS-90041", "invoice_date")])
def test_a4_control_audited_code_states_an_exact_account(hid, doc, field):
    c = "CW" if hid.startswith("CW") else "DDS"
    _w, st, _o, _e = _run(_hist(hid, doc, field), g4c=_pre("audit/g4_cw.py", "g4_cw_pre"),
                          g4dds=_pre("audit/g4_dds.py", "g4_dds_pre"))
    assert st[c].adjustments[0]["total"]["lines_not_established"] == 0
    assert any("is not in the account" in e for e in vg4.a3_contributor_errors(st["CW"], st["DDS"]))


# ================================================================================================= A-5 release with an undated Q12 line
def h_a5():
    h = copy.deepcopy(next(x for x in H.CW_HISTORIES if x["id"] == "CW-H10"))
    h["documents"].insert(0, H.cw_app("PA-9A010", "2025-12-28", [("2025-12-20", "S-05", "B.22.010", "1200", "74.50", "")]))
    h["documents"].insert(1, H.cw_app("PA-9A011", "", [("2026-01-10", "S-05", "B.22.010", "100", "74.50", "")]))
    return h


def test_a5_release_with_an_undated_reading_dependent_application_runs():
    _w, st, _o, _e = _run(h_a5())
    rel = st["CW"].retention["release"]
    assert rel["recipient"]["not_established"] == ["PA-9A003", "PA-9A011"]
    assert set(rel["released"]["by_reading"]) == {"Q12:A", "Q12:B"}


def test_a5_control_audited_code_crashes():
    with pytest.raises(StopIteration):
        _run(h_a5(), g4c=_pre("audit/g4_cw.py", "g4_cw_pre"))


# ================================================================================================= A-6 divided line, displayed rate
@pytest.mark.parametrize("rate,verdict", [("74.50", "pass"), ("71.52", "pass"), ("69.29", "finding"), ("72.00", "finding")])
def test_a6_a_divided_line_shows_one_of_its_parts_rates_or_the_built_up_rate(rate, verdict):
    h = cw_hist("CW-A6", [("2025-03-02", "1000", "74.50", "74500.00"), ("2025-03-03", "400", rate, "29204.00")])
    _w, st, out, _e = _run(h)
    assert [x.status for x in F.by_ref(st, "CW")["PA-97001-02"].state if x.family == "band_rate"] == [verdict]
    assert out["PA-97001"]["flagged"] == (verdict == "finding")
    assert vg4.deferred_rate_errors(st["CW"]) == []


def test_a6_control_audited_code_passes_a_band_the_line_does_not_reach():
    h = cw_hist("CW-A6", [("2025-03-02", "1000", "74.50", "74500.00"), ("2025-03-03", "400", "69.29", "29204.00")])
    _w, st, out, _e = _run(h, g4c=_pre("audit/g4_cw.py", "g4_cw_pre"), g5=_pre("audit/g5_outcomes.py", "g5_outcomes_pre"))
    assert [x.status for x in F.by_ref(st, "CW")["PA-97001-02"].state if x.family == "band_rate"] == ["pass"]
    assert out["PA-97001"]["flagged"] == 0
    assert any("expected finding" in e for e in vg4.deferred_rate_errors(st["CW"]))


# ================================================================================================= A-7 / A-8 oracles
def h_a7():
    well = "NGP-ZZ-976"
    a = _pd_inv("MDS-97601", well, "2025-07-10", [("2025-07-01", 1500, 1600)])
    b = _pd_inv("MDS-97602", well, "2025-07-12", [("2025-07-01", 1550, 1650)])
    b["lines"][0]["quantity"] = "99"
    return {"id": "A7", "documents": [a, b], "reports": H.reports(_ddr(well, "2025-07-01", 1500, 1700)), "records": {},
            "given": []}


def test_a7_coverage_oracle_accepts_an_honestly_unresolved_member():
    _w, st, _o, _e = _run(h_a7())
    assert F.by_ref(st, "DDS")["MDS-97602-001"].r.payable is None
    assert vg4.pd210_coverage_errors(st["DDS"]) == []


def _pre_tool(path, name):
    """The audited commit's version of a tool module (it reads its own location, so it is given the repository path)."""
    import importlib.util
    import sys
    src = prior_code.source(PRE, path)
    spec = importlib.util.spec_from_loader(name, loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(ROOT / path)
    sys.modules[name] = mod
    exec(compile(src, f"{PRE}:{path}", "exec"), mod.__dict__)
    return mod


def test_a7_control_audited_oracle_raises_a_false_alarm():
    old = _pre_tool("tools/verify_g4.py", "verify_g4_pre")
    _w, st, _o, _e = _run(h_a7())
    old._CLAIMS.update(vg4._CLAIMS)
    assert any("has no single kept quantity" in e for e in old.pd210_coverage_errors(st["DDS"]))


def test_a8_y6_fails_on_footage_outside_the_stated_intervals():
    _w, st, _o, _e = _run(h_b01())
    assert vg4.y6(st["CW"], st["DDS"]) == []
    acc = st["DDS"].footage["NGP-ZZ-951"]
    acc["2025-07-02|50000-50010|Q14:A|Q11:A"] = ["0", "0"]          # metres no admissible interval of the day states
    assert any("no admissible PD-210 interval of the day contains" in e for e in vg4.y6(st["CW"], st["DDS"]))


def h_b01():
    return F.pd210_history("A8", "NGP-ZZ-951", [("2025-07-01", 4500, 44490), ("2025-07-02", 44490, 44510)],
                           [("MDS-95101", "2025-07-01", 4500, 44490), ("MDS-95102", "2025-07-02", 44490, 44510)])


def test_a8_footage_oracle_checks_the_upper_bound_and_q11b():
    _w, st, _o, _e = _run(h_b01())
    assert vg4.footage_reset_errors(st["DDS"]) == []
    acc = st["DDS"].footage["NGP-ZZ-951"]
    k = "2025-07-02|44490-44510|Q14:A|Q11:A"
    acc[k] = [acc[k][0], None]                                        # an upper bound lost
    assert any("Q11:A" in e for e in vg4.footage_reset_errors(st["DDS"]))
    k = "2025-07-02|44490-44510|Q14:A|Q11:B"
    acc["2025-07-02|44490-44510|Q14:A|Q11:A"] = ["39990", "39990"]
    acc[k] = ["0", "0"]                                               # the reported metres not counted under Q11 B
    assert any("Q11:B" in e for e in vg4.footage_reset_errors(st["DDS"]))


# ================================================================================================= B-1 / C-1 payment bounds
def h_b1(release, blank=True):
    h = copy.deepcopy(next(x for x in H.CW_HISTORIES if x["id"] == "CW-H10"))
    if blank:
        h["documents"][0]["lines"][0]["work_date"] = ""
    for d in h["documents"]:
        if d["header"]["application_no"] == "PA-9A003":
            hd = d["header"]
            hd["retention_released"] = release
            hd["net_payable"] = f"{D(hd['application_total']) - D(hd['retention']) + D(release):.2f}"
    return h


def test_b1_release_bounded_below_by_the_established_retention():
    _w, st, out, _e = _run(h_b1("0.00"))
    assert st["CW"].retention["release"]["released"] == {"by_reading": {"": {"min": "96.15", "max": None}}}
    o = out["PA-9A003"]
    assert o["flagged"] == 1 and "release_omitted" in o["findings"]
    _w, st, out, _e = _run(h_b1("192.49"))
    o = out["PA-9A003"]
    assert o["flagged"] == 0 and o["not_established"] == ["release_not_established"] and o["confidence"] == D("0.50")


def test_b1_control_audited_code_passes_the_omission_at_095():
    _w, st, out, _e = _run(h_b1("0.00"), g4c=_pre("audit/g4_cw.py", "g4_cw_pre"), g5=_pre("audit/g5_outcomes.py", "g5_outcomes_pre"))
    o = out["PA-9A003"]
    assert o["flagged"] == 0 and o["confidence"] == D("0.95")
    assert any("not established" in e for e in vg5.z7({"PA-9A003": g5_run.serial(o)}))


def h_b2(adj, mutate):
    h = copy.deepcopy(next(x for x in H.CW_HISTORIES if x["id"] == "CW-H09"))
    mutate(h)
    for d in h["documents"]:
        hd = d["header"]
        if hd["application_no"] in ("PA-99003", "PA-99004"):
            hd["application_date"] = "2026-05-20"
        if hd["application_no"] == "PA-99005":
            hd["adjustment"] = adj
            hd["net_payable"] = f"{D(hd['application_total']) - D(hd['retention']) + D(adj):.2f}"
    return h


def _no_work_date(h):
    h["documents"][0]["lines"][1]["work_date"] = ""


def _no_quantity(h):
    h["documents"][0]["lines"][1]["quantity"] = ""


@pytest.mark.parametrize("mutate", [_no_work_date, _no_quantity])
def test_b2_c1_a3_account_with_an_unvalued_line_is_bounded_not_exact(mutate):
    _w, st, out, _e = _run(h_b2("0.00", mutate))
    o = out["PA-99005"]
    assert o["flagged"] == 1 and "adjustment_omitted" in o["findings"]          # at least 117.80 is due; 0.00 claimed
    for adj in ("117.80", "489.80"):
        _w, st, out, _e = _run(h_b2(adj, mutate))
        o = out["PA-99005"]
        assert o["flagged"] == 0 and o["not_established"] == ["adjustment_not_established"] and o["confidence"] == D("0.50")
    assert vg5.payment_errors({"PA-99005": o}, _e) == []


def test_b2_control_audited_code_takes_the_account_as_exact():
    pre = dict(g4c=_pre("audit/g4_cw.py", "g4_cw_pre"), g5=_pre("audit/g5_outcomes.py", "g5_outcomes_pre"))
    _w, st, out, _e = _run(h_b2("117.80", _no_work_date), **pre)
    o = out["PA-99005"]
    assert o["flagged"] == 0 and o["confidence"] == D("0.95") and o["not_established"] == []      # 117.80 'exact'
    assert any("is not in the account" in e for e in vg4.a3_contributor_errors(st["CW"], st["DDS"]))


def test_c1_control_audited_code_erases_the_known_part():
    pre = dict(g4c=_pre("audit/g4_cw.py", "g4_cw_pre"), g5=_pre("audit/g5_outcomes.py", "g5_outcomes_pre"))
    _w, st, out, _e = _run(h_b2("0.00", _no_quantity), **pre)
    o = out["PA-99005"]
    assert o["flagged"] == 0 and o["confidence"] == D("0.95")                  # the established omission hidden
    assert any("not established" in e for e in vg5.z7({"PA-99005": g5_run.serial(o)}))


# ================================================================================================= B-3 / B-4 / C-2 export and category
def _cls_line(ref, billed, qty="1", allowed="1", rate_deferred=False):
    rates = {"class:Standard": "1000.00", "class:Extended Reach": "1175.00", "class:HPHT": "1325.00"}
    k, v, g = U._line("DDS", ref, "MW-310", str(D(billed) * D(qty)), alts=rates)
    for kk, rt in rates.items():
        a = g.r.alternatives[kk]
        a.update({"unit_rate": D(rt), "allowed_quantity": D(allowed), "amount": D(rt) * D(allowed)})
    v.update({"unit_rate": D(billed), "quantity": D(qty)})
    if rate_deferred:
        g.g3.checks.append(Check("rate", "unresolved", "DDS-R10", "Cl.2", "rate_differs"))
    return k, v, g


def _b3_invoices():
    a = U._dds("A", [_cls_line("A-001", "1325.00")], header_extra={"well_name": "W-1"}, cls="HPHT")
    b = U._dds("B", [_cls_line("B-001", "1325.00"),
                     U._line("DDS", "B-002", "DD-102", "948.60", None, findings=["report_unsigned"], payable=False)],
               header_extra={"well_name": "W-1"}, cls="HPHT")
    b.lines[1][2].r.amount = D("0.00")
    return a, b


def test_b3_flagged_row_exported_under_the_wells_class_with_its_findings():
    a, b = _b3_invoices()
    e = U._engine([a, b])
    oa, ob = e.outcome(a), e.outcome(b)
    assert (oa["flagged"], oa["expected_total"]) == (0, D("1523.75"))
    assert ob["flagged"] == 1 and ob["error_category"] == "signature" and ob["expected_total"] == D("1523.75")
    assert ob["export_facts"]["class"] == oa["export_facts"]["class"] == "HPHT"
    assert vg5.export_errors({"A": oa, "B": ob}, e) == []


def test_b3_control_audited_code_exports_a_class_no_finding_names():
    a, b = _b3_invoices()
    e = U._engine([a, b])
    old = _pre("audit/g5_outcomes.py", "g5_outcomes_pre")
    e2 = old.Engine.__new__(old.Engine)
    e2.__dict__.update({k: v for k, v in e.__dict__.items() if not k.startswith("_")})
    ob = e2.outcome(b)
    assert ob["error_category"] == "signature" and ob["expected_total"] == D("1150.00")     # Standard, named by nothing
    assert any("no finding names the line" in x for x in vg5.export_errors({"B": ob}, e))


def _b4_invoice(extra):
    lines = [_cls_line("X-001", "1000.00", rate_deferred=True), _cls_line("X-002", "1325.00", rate_deferred=True)]
    if extra:
        lines.append(U._line("DDS", "X-003", "DD-102", "948.60", "948.60", findings=[extra]))
    return U._dds("X", lines)


@pytest.mark.parametrize("extra,cats", [(None, "rate"), ("submitted_late", "timing; rate"), ("report_unsigned", "signature; rate")])
def test_b4_a_rate_error_under_every_class_is_kept_beside_another_finding(extra, cats):
    inv = _b4_invoice(extra)
    o = U._engine([inv]).outcome(inv)
    assert o["flagged"] == 1 and o["error_category"] == cats


def test_b4_control_audited_code_loses_the_rate_error():
    inv = _b4_invoice("submitted_late")
    e = U._engine([inv])
    old = _pre("audit/g5_outcomes.py", "g5_outcomes_pre")
    e2 = old.Engine.__new__(old.Engine)
    e2.__dict__.update(e.__dict__)
    assert e2.outcome(inv)["error_category"] == "timing"


def _c2_invoice():
    # 20 hours billed at the Standard rate, 19 chargeable (a quantity breach under every class); under the other classes
    # the displayed rate also differs
    return U._dds("Q", [_cls_line("Q-001", "1000.00", qty="20", allowed="19", rate_deferred=True)])


def test_c2_quantity_breach_keeps_its_cause_under_every_class():
    inv = _c2_invoice()
    o = U._engine([inv]).outcome(inv)
    assert o["flagged"] == 1 and o["error_category"] == "quantity" and o["findings"] == ["quantity_above_record@Q-001"]
    assert o["findings_every_value"] == ["quantity_above_record@Q-001"]


def test_c2_control_audited_code_reports_rate():
    inv = _c2_invoice()
    e = U._engine([inv])
    old = _pre("audit/g5_outcomes.py", "g5_outcomes_pre")
    e2 = old.Engine.__new__(old.Engine)
    e2.__dict__.update(e.__dict__)
    o = e2.outcome(inv)
    assert o["error_category"] == "rate" and o["findings"] == ["no_admissible_document"]


def test_c2_population_rows_are_quantity_again():
    out = {json.loads(x)["invoice_id"]: json.loads(x) for x in (ROOT / "verification" / "g5" / "outcomes.jsonl").read_text().splitlines()}
    for i, ref in (("MDS-01877", "MDS-01877-046"), ("MDS-01651", "MDS-01651-025")):
        assert "quantity" in out[i]["error_category"].split("; ") and f"quantity_above_record@{ref}" in out[i]["findings"]
