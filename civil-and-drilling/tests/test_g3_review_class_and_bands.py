"""G3 review findings F1-F4 (class authority, band arithmetic, PD-210 tolerance): each finding's counterexample is
handled correctly, and each strengthened check fails on the defect the review reproduced (negative controls)."""
import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

import g3_case_compare as gcc
import verify_g3 as vg
from audit import build, g3_cw, g3_dds, g3_run

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def world():
    return build.build()


@pytest.fixture(scope="module")
def res(world):
    return {"CW": g3_cw.run(world), "DDS": g3_dds.run(world)}


@pytest.fixture(scope="module")
def cmp_():
    return gcc.run()


def _dds_input(world, ref):
    return next((l, i, d) for l, i, d in g3_dds.inputs_from_world(world) if l["line_ref"] == ref)


def _cw_input(world, ref):
    return next(x for x in g3_cw.inputs_from_world(world) if x[0]["line_ref"] == ref)


def _values(r):
    return vg._value(r)


# ---------------------------------------------------------------------------------------------------- F1 well class
def test_f1_header_class_does_not_set_the_rate(world):
    """Audit counterexample: MDS-00001-008 / MW-310 with the header changed Standard -> HPHT gave 2,953.29 vs 3,913.11,
    both 'determined'. Now both carry every class and the values are identical."""
    line, inv, ddr = _dds_input(world, "MDS-00001-008")
    a = g3_dds.evaluate(line, inv, ddr)
    b = g3_dds.evaluate(line, {**inv, "well_class": "HPHT"}, ddr)
    assert a.amount_status == b.amount_status == "conditional" and a.amount is None and a.unit_rate is None
    assert {k: str(v["amount"]) for k, v in a.alternatives.items()} == {
        "class:Standard": "2953.29", "class:Extended Reach": "3470.12", "class:HPHT": "3913.11"}
    assert _values(a) == _values(b)
    assert a.conditions[0]["dimension"] == "class" and a.conditions[0]["owner"] == "G5"


def _header_class_engine(line, inv, ddr, **kw):
    """The defect: the invoice header's class taken as the call-off (the earlier G3 engine)."""
    r = g3_dds.evaluate(line, inv, ddr, **kw)
    key = f"class:{inv.get('well_class')}"
    if key in r.alternatives and r.amount is None:
        a = r.alternatives[key]
        r.unit_rate, r.amount, r.trace, r.amount_status = a["unit_rate"], a["amount"], a["trace"], "determined"
        r.alternatives, r.conditions = {}, []
    return r


def test_f1_control_x2_rejects_a_header_class_rate(world, res, cmp_):
    ref = "MDS-00001-008"
    line, inv, ddr = _dds_input(world, ref)
    bad = {"CW": {}, "DDS": {ref: _header_class_engine(line, inv, ddr)}}
    bad["DDS"][ref].family = res["DDS"][ref].family
    errs = vg.x2(world, bad, yaml.safe_load((ROOT / "spec/g3_code_families.yaml").read_text()), cmp_)
    assert any(ref in e and "without every admissible class" in e for e in errs)


def test_f1_control_x6_rejects_a_header_class_rate(world, res):
    only = {r for r in sorted(res["DDS"]) if res["DDS"][r].code in ("MW-310", "DD-120")}
    only = set(sorted(only)[:60])
    fwd = vg.evaluate_all(world, dds_eval=_header_class_engine, only=only)
    errs = vg.x6(world, fwd, mutate=vg.perturb_claim_classes, dds_eval=_header_class_engine, only=only)
    assert errs and all("claim-stated classification" in e for e in errs)


def test_f1_pd210_stays_conditional_on_nomination(res):
    pd = [r for r in res["DDS"].values() if r.code == "PD-210"]
    assert len(pd) == 2390 and all(r.amount_status == "conditional" for r in pd)
    assert all(any(c["dimension"] == "nomination" and c["owner"] == "G5" for c in r.conditions) for r in pd)


# ---------------------------------------------------------------------------------------------------- F2 ground class
def test_f2_application_ground_class_does_not_set_the_rate(world):
    """Audit counterexample: PA-00031-06 / A.12.050 claims G4 with no record: 72.05 determined; claim G2 -> 52.40."""
    line, app, rec, ex = _cw_input(world, "PA-00031-06")
    a = g3_cw.evaluate(line, app, rec, ex)
    b = g3_cw.evaluate({**line, "ground_class": "G2 Firm Sabkha"}, app, rec, ex)
    assert a.amount_status == "conditional" and a.amount is None
    assert {k: str(v["unit_rate"]) for k, v in a.alternatives.items()} == {
        "ground:G1": "49.26", "ground:G2": "52.40", "ground:G3": "58.69", "ground:G4": "72.05", "ground:G5": "85.41"}
    assert _values(a) == _values(b)
    assert any("G2 if not recorded on the day (S4)" in x for x in a.readings)


def test_f2_exposure_matches_the_audit(res, world):
    lines = {r.ident: r.values for r in world.claims.rows["cw_lines"]}
    grd = [r for r in res["CW"].values() if any(c["dimension"] == "ground" for c in r.conditions)]
    assert len(grd) == 649                              # 650 exposed lines less the one not payable (record missing)
    assert len([r for r in grd if (lines[r.line_ref]["ground_class"] or "")[:2] not in ("", "G2")]) == 520


def _claimed_ground_engine(line, app, rec, exists, **kw):
    """The defect: the application's ground class taken where no record states one."""
    r = g3_cw.evaluate(line, app, rec, exists, **kw)
    g = (line.get("ground_class") or "").split(" ")[0]
    keys = [k for k in r.alternatives if f"ground:{g}" in k.split("|")]
    if keys and "band" not in {x.split(":")[0] for k in r.alternatives for x in k.split("|")}:
        a = r.alternatives[keys[0]]
        r.unit_rate, r.amount, r.trace, r.amount_status = a["unit_rate"], a["amount"], a["trace"], "determined"
        r.alternatives, r.conditions = {}, []
    return r


def test_f2_control_x6_rejects_a_claimed_ground_class(world, res):
    only = set(sorted(r for r, x in res["CW"].items() if any(c["dimension"] == "ground" for c in x.conditions))[:60])
    fwd = vg.evaluate_all(world, cw_eval=_claimed_ground_engine, only=only)
    assert vg.x6(world, fwd, mutate=vg.perturb_claim_classes, cw_eval=_claimed_ground_engine, only=only)


def test_f2_control_x2_rejects_a_claimed_ground_class(world, res, cmp_):
    line, app, rec, ex = _cw_input(world, "PA-00031-06")
    r = _claimed_ground_engine(line, app, rec, ex)
    r.family = res["CW"]["PA-00031-06"].family
    errs = vg.x2(world, {"CW": {"PA-00031-06": r}, "DDS": {}}, yaml.safe_load((ROOT / "spec/g3_code_families.yaml").read_text()), cmp_)
    assert any("without every ground class" in e for e in errs)


# ---------------------------------------------------------------------------------------------------- F3 band arithmetic
def test_f3_split_line_is_unresolved_and_the_rest_reproduce(res):
    r = res["CW"]["PA-00076-08"]
    assert "amount_arithmetic" in r.unresolved and "amount_arithmetic" not in r.findings
    detail = next(c.detail for c in r.checks if c.check == "arithmetic")
    # FD07: the admissible range, never a division selected from the billed amount (the earlier detail named one)
    assert "the amount lies between SAR" in detail and "(band 1) +" not in detail
    unresolved = [x for x in res["CW"].values() if "amount_arithmetic" in x.unresolved]
    established = [x for x in res["CW"].values() if "amount_arithmetic" in x.findings]
    assert len(unresolved) == 29 and len(established) == 3


def test_f3_control_without_the_division_every_line_is_a_finding(world, monkeypatch):
    monkeypatch.setattr(g3_cw, "band_split", lambda *a, **k: None)
    line, app, rec, ex = _cw_input(world, "PA-00076-08")
    assert "amount_arithmetic" in g3_cw.evaluate(line, app, rec, ex).findings


def test_f3_inconsistent_band_line_stays_a_finding(world):
    line, app, rec, ex = _cw_input(world, "PA-00076-08")
    r = g3_cw.evaluate({**line, "amount": Decimal("9000.00")}, app, rec, ex)   # above every band's whole-quantity amount
    assert "amount_arithmetic" in r.findings


# ---------------------------------------------------------------------------------------------------- F4 PD-210
def _pd210_case(qty):
    c = copy.deepcopy(gcc.load_cases()["DDS-S68"])
    c["line"]["quantity"] = qty
    return gcc.engine_result(c)


def test_f4_allowed_quantity_parts_and_amount_agree():
    r = _pd210_case("101")
    parts = [s for s in r.trace if s["op"] == "part"]
    assert r.allowed_quantity == Decimal("101") and r.amount == Decimal("5873.15")
    assert sum(Decimal(p["quantity"]) for p in parts) == r.allowed_quantity
    assert vg.x3({"DDS": {"S68": r}}) == []


def _coherent(r, rate=None, qty=None):
    """Mutate the single PD-210 part coherently: its rate or quantity, the part value, the sum and the result."""
    r = copy.deepcopy(r)
    part = next(s for s in r.trace if s["op"] == "part")
    part["rate"] = str(rate) if rate is not None else part["rate"]
    part["quantity"] = str(qty) if qty is not None else part["quantity"]
    v = Decimal(part["quantity"]) * Decimal(part["rate"])
    part["value"] = str(v)
    next(s for s in r.trace if s["op"] == "sum_parts")["value"] = str(v)
    r.amount = v
    if rate is not None:
        r.unit_rate = Decimal(str(rate))
    return r


def test_f4_control_part_rate_999_is_rejected():
    r = _coherent(_pd210_case("101"), rate=Decimal("999.00"))
    assert any("PD-210 part rate 999.00 is not band 2's 58.15" in e for e in vg.x3({"DDS": {"S68": r}}))


def test_f4_control_part_quantity_mismatch_is_rejected():
    r = _coherent(_pd210_case("101"), qty=Decimal("100"))          # prices 100 m while allowing 101 m (the review's defect)
    assert any("trace quantity 100 != allowed quantity 101" in e for e in vg.x3({"DDS": {"S68": r}}))


def test_f4_crossing_charge_with_a_tolerance_difference_is_exposed():
    c = copy.deepcopy(gcc.load_cases()["DDS-S71"])
    r = gcc.engine_result(c)
    # B2: labelled by the full allocation; 101 m on 50 + 50 m: the one excess metre anywhere in the two bands
    # (FD07: any real split, stated as a continuous domain; the whole-metre ends are its witnesses)
    assert r.amount is None and {k.split("|")[0].split(", ", 1)[1] for k in r.alternatives} == {
        "51 m in band 1 + 50 m in band 2", "50 m in band 1 + 51 m in band 2"}
    assert any(x["dimension"] == "tolerance" and x["owner"] == "G5" and x["domain"]["count"] is None and
               x["domain"]["mode"] == "continuous" and [(p["min_m"], p["max_m"]) for p in x["domain"]["parts"]] ==
               [("50", "51"), ("50", "51")] for x in r.conditions)


# ---------------------------------------------------------------------------------------------------- Q5 residual
def test_q5_residual_is_computed_on_every_line(res):
    q5 = [r for r in res["DDS"].values() if any(c["dimension"].startswith("Q5-") for c in r.conditions)]
    assert sorted(r.line_ref for r in q5) == ["MDS-00856-039", "MDS-01338-026", "MDS-01651-025"]
    hc = [r for r in res["DDS"].values() if r.code == "HC-630"]
    assert len(hc) == 428 and all(any("once_per_run (HC-630" in d for d in r.g4_dependencies) for r in hc)


def test_q5_dd102_stays_decided(res):
    assert all("Q5:A" in r.readings for r in res["DDS"].values() if r.code == "DD-102")


# ---------------------------------------------------------------------------------------------------- X4 registers
def _x4(cmp_, **over):
    args = {"decisions": yaml.safe_load((ROOT / "spec/g3_decisions.yaml").read_text()),
            "scopes": json.loads((ROOT / "verification/g3/decision_scopes.json").read_text()),
            "questions": yaml.safe_load((ROOT / "spec/open_questions.yaml").read_text()),
            "carried": yaml.safe_load((ROOT / "spec/carried_items.yaml").read_text()), "comparison": cmp_}
    for k, f in over.items():
        f(args[k])
    return vg.x4(**args)


def test_x4_control_mixed_currency_value_is_rejected(cmp_):
    def s(sc):
        sc["G3-D1"]["effect_by_reading"]["adopted"]["value"] = "1698500.78"      # SAR + USD in one number (the review's D1)
    assert "G3-D1: reading adopted reports value without separating currencies (SAR civil, USD drilling)" in _x4(cmp_, scopes=s)


def test_x4_control_q11_without_a_later_owner_is_rejected(cmp_):
    def q(qs):
        next(x for x in qs["questions"] if x["id"] == "Q11")["blocks"] = "G3"
    assert "Q11: decided in part without a later owning gate (blocks G3)" in _x4(cmp_, questions=q)


def test_x4_control_decided_in_part_without_residual_owner(cmp_):
    def d(ds):
        next(x for x in ds["decisions"] if x["id"] == "Q5").pop("residual_owner")
    assert "Q5: decided in part without a later owner for the residual" in _x4(cmp_, decisions=d)


def test_d1_value_payment_distinction_and_currencies():
    sc = json.loads((ROOT / "verification/g3/decision_scopes.json").read_text())["G3-D1"]
    adopted = sc["effect_by_reading"]["adopted"]
    assert set(adopted["value"]) == {"SAR", "USD"} and "payment timing" in adopted["note"]
    d1 = next(x for x in yaml.safe_load((ROOT / "spec/g3_decisions.yaml").read_text())["decisions"] if x["id"] == "G3-D1")
    assert "must not be read from this flag" in d1["value_vs_payment"]


# ---------------------------------------------------------------------------------------------------- X1 boundary under classes
def test_x1_control_rate_boundary_must_change_under_every_class():
    cases = gcc.load_cases()
    cr = {k: gcc.engine_result(v) for k, v in cases.items()}
    s02 = copy.deepcopy(cr["DDS-S02"])
    s02.alternatives["class:HPHT"]["unit_rate"] = cr["DDS-S01"].alternatives["class:HPHT"]["unit_rate"]
    cr["DDS-S02"] = s02
    errs = vg.x1(gcc.run(), cases, cr)
    assert any("boundary pair DDS-S01|DDS-S02 does not change the rate" in e for e in errs)
