"""G3 exit checks (tools/verify_g3.py): each check passes on the committed state and FAILS on a controlled defect
(negative controls). A check that cannot fail proves nothing, so every check X1-X7 has at least one control here."""
import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

import g3_case_compare as gcc
import verify_g3 as vg
from audit import build, g3_cw, g3_dds, terms

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


@pytest.fixture(scope="module")
def cases():
    return gcc.load_cases()


@pytest.fixture(scope="module")
def case_results(cases):
    return {cid: gcc.engine_result(c) for cid, c in cases.items()}


def _load(name):
    return yaml.safe_load((ROOT / "spec" / name).read_text())


def _sample(res, n=400):
    return {ref for c in res for ref in sorted(res[c])[:n]}


# ---------------------------------------------------------------------------------------------------------- X1
def test_x1_passes(cmp_, cases, case_results):
    assert vg.x1(cmp_, cases, case_results) == []


def test_x1_fails_on_an_undisposed_disagreement(cases, case_results):
    c = gcc.run(dispositions={})
    errs = vg.x1(c, cases, case_results)
    assert any(e.startswith("CW-S16 payable") for e in errs)
    assert any("does not agree with its independent expected value" in e for e in errs)


def test_x1_fails_when_the_engine_is_wrong(monkeypatch, cases, case_results):
    """A mutated engine (the civil first-hour rule dropped) no longer matches the readers."""
    bad = copy.copy(terms.cw())
    bad.first_hour = Decimal("0")
    monkeypatch.setitem(terms._CACHE, "CW", bad)
    c = gcc.run()
    assert any(f.startswith("CW-S31 allowed_quantity") for f in c["failures"])   # 1 hour attended: nothing chargeable (6A)
    assert vg.x1(c, cases, case_results)


def test_x1_fails_when_a_scope_case_is_missing(cmp_, cases, case_results):
    cr = {k: v for k, v in case_results.items() if k != "CW-S45"}
    assert "scope 'submission window (boundary day)': case CW-S45 missing" in vg.x1(cmp_, cases, cr)


def test_x1_fails_when_a_rounding_case_does_not_discriminate(cmp_, cases, case_results):
    cr = dict(case_results)
    r = copy.deepcopy(cr["CW-S29"])
    for t in vg.all_traces(r):                  # the line's own trace and every alternative's
        for s in t:
            if s["op"] == "round":
                s["mode"] = "half_even"
    cr["CW-S29"] = r
    assert any("CW-S29 does not show mode_at_half:half_up" in e for e in vg.x1(cmp_, cases, cr))


def test_x1_fails_when_outputs_precede_inputs(cmp_, cases, case_results):
    errs = vg.x1(cmp_, cases, case_results, before=lambda a, b: False)
    assert "packet_cw_synthetic_1.jsonl: inputs not committed strictly before the readers' output expected_cw_synthetic_1.jsonl" in errs


# ---------------------------------------------------------------------------------------------------------- X2
def test_x2_passes(world, res, cmp_):
    assert vg.x2(world, res, _load("g3_code_families.yaml"), cmp_) == []


def test_x2_fails_when_a_family_is_missing(world, res, cmp_):
    fam = _load("g3_code_families.yaml")
    fam["contracts"]["DDS"] = [f for f in fam["contracts"]["DDS"] if f["id"] != "DDS-HOURLY"]
    errs = vg.x2(world, res, fam, cmp_)
    assert any("DD-120: valued as DDS-HOURLY, registry family DDS-COUNTS" in e for e in errs)


def test_x2_fails_when_a_code_is_in_no_family(world, res, cmp_):
    fam = _load("g3_code_families.yaml")
    fam["contracts"]["DDS"] = [f for f in fam["contracts"]["DDS"] if f["id"] not in ("DDS-LOSS", "DDS-UNSCHEDULED")]
    assert "DDS LH-711: billed code in no family" in vg.x2(world, res, fam, cmp_)


def test_x2_fails_when_a_deferred_family_is_declared_implemented(world, res, cmp_):
    fam = _load("g3_code_families.yaml")
    next(f for f in fam["contracts"]["DDS"] if f["id"] == "DDS-DISCOUNT").update(status="implemented", cases=["DDS-S55"])
    assert any("deferred in implemented family DDS-DISCOUNT" in e for e in vg.x2(world, res, fam, cmp_))


def test_x2_fails_without_an_owner_gate(world, res, cmp_):
    fam = _load("g3_code_families.yaml")
    next(f for f in fam["contracts"]["DDS"] if f["id"] == "DDS-DISCOUNT").pop("owner")
    assert "DDS-DISCOUNT: deferred family names no owning gate (G4-G7)" in vg.x2(world, res, fam, cmp_)


def test_x2_fails_when_an_applicable_table_is_skipped(world, res, cmp_):
    """An engine that forgot the USD conversion on one line: the FX table applies but is not in its trace."""
    ref = sorted(k for k, r in res["CW"].items() if r.code == "C.32.040")[0]
    r2 = copy.deepcopy(res["CW"][ref])
    r2.trace = [s for s in r2.trace if "CW.T07_FX" not in s.get("source", "")]
    bad = {"CW": {**res["CW"], ref: r2}, "DDS": res["DDS"]}
    assert f"{ref} C.32.040: FX (26A) applies but its table is not in the trace" in vg.x2(world, bad, _load("g3_code_families.yaml"), cmp_)


def test_x2_fails_when_the_engine_misroutes_a_line(world, res, cmp_):
    ref = sorted(k for k, r in res["CW"].items() if r.code == "E.51.010")[0]
    r2 = copy.deepcopy(res["CW"][ref])
    r2.family = "CW-MEAS"
    bad = {"CW": {**res["CW"], ref: r2}, "DDS": res["DDS"]}
    assert f"{ref} E.51.010: valued as CW-MEAS, registry family CW-HOUR" in vg.x2(world, bad, _load("g3_code_families.yaml"), cmp_)


# ---------------------------------------------------------------------------------------------------------- X3
def _one(res, contract, pred):
    ref = sorted(k for k, r in res[contract].items() if pred(r))[0]
    return ref, copy.deepcopy(res[contract][ref])


def test_x3_passes(res, case_results):
    assert vg.x3(res) == []
    assert vg.x3({"CW": {k: v for k, v in case_results.items() if k.startswith("CW")},
                  "DDS": {k: v for k, v in case_results.items() if k.startswith("DDS")}}) == []


def test_x3_fails_on_a_tampered_step(res):
    ref, r = _one(res, "DDS", lambda r: r.code == "HC-620" and r.payable and r.amount is not None)   # indexed, not class-rated
    step = next(s for s in r.trace if s["op"] == "mul")
    step["value"] = str(Decimal(step["value"]) + Decimal("0.01"))
    assert any("recorded" in e and "replayed" in e for e in vg.x3({"DDS": {ref: r}}))


def test_x3_fails_when_drilling_rounds_half_up(res):
    """Cl.17: every step half to even. A trace that rounds half up (value consistent with half up) fails."""
    ref, r = _one(res, "DDS", lambda r: r.code == "HC-620" and r.payable and r.amount is not None)
    for s in r.trace:
        if s["op"] == "round":
            s["mode"] = "half_up"
    assert any("(Cl.17: half to even)" in e for e in vg.x3({"DDS": {ref: r}}))


def test_x3_fails_when_civil_rounds_twice(res):
    ref, r = _one(res, "CW", lambda r: r.payable and r.amount is not None)
    i = next(i for i, s in enumerate(r.trace) if s["op"] == "round" and s["mode"] == "half_up")
    r.trace.insert(i, dict(r.trace[i]))
    assert any("2 half-up roundings" in e for e in vg.x3({"CW": {ref: r}}))


def test_x3_fails_without_a_source(res):
    ref, r = _one(res, "CW", lambda r: r.payable and r.amount is not None)
    r.trace[0]["source"] = ""
    assert any("cites no clause, table or page" in e for e in vg.x3({"CW": {ref: r}}))


def test_x3_fails_when_the_trace_does_not_start_at_a_contract_figure(res):
    ref, r = _one(res, "CW", lambda r: r.code == "D.41.020" and r.payable)
    r.trace[0]["value"] = "99.99"
    assert any("not a figure of the verified tables" in e for e in vg.x3({"CW": {ref: r}}))


def test_x3_fails_when_the_amount_does_not_follow_the_trace(res):
    ref, r = _one(res, "DDS", lambda r: r.code == "DD-101" and r.payable)
    r.amount += Decimal("1.00")
    assert any("trace amount" in e and f"!= amount {r.amount}" in e for e in vg.x3({"DDS": {ref: r}}))


def test_x3_fails_on_a_band_alternative_that_does_not_follow_its_trace(res):
    ref, r = _one(res, "CW", lambda r: "band:2" in r.alternatives)
    r.alternatives["band:2"]["amount"] += Decimal("0.01")
    assert any("band:2: trace amount" in e for e in vg.x3({"CW": {ref: r}}))


# ---------------------------------------------------------------------------------------------------------- X4
def _x4(cmp_, **over):
    args = {"decisions": _load("g3_decisions.yaml"), "scopes": json.loads((ROOT / "verification/g3/decision_scopes.json").read_text()),
            "questions": _load("open_questions.yaml"), "carried": _load("carried_items.yaml"), "comparison": cmp_}
    for k, f in over.items():
        f(args[k])
    return vg.x4(**args)


def test_x4_passes(cmp_):
    assert _x4(cmp_) == []


def test_x4_fails_without_the_effect_of_an_alternative(cmp_):
    assert "Q13: no effect computed for reading(s) ['B']" in _x4(cmp_, scopes=lambda s: s["Q13"]["effect_by_reading"].pop("B"))


def test_x4_fails_when_a_large_decision_reports_no_lines_under_a_reading(cmp_):
    assert "G3-D1: decides 471 lines but reading not_payable_now reports no lines/value" in _x4(
        cmp_, scopes=lambda s: s["G3-D1"]["effect_by_reading"]["not_payable_now"].pop("lines"))


def test_x4_fails_when_registers_disagree(cmp_):
    def q(qs):
        next(x for x in qs["questions"] if x["id"] == "Q5")["status"] = "open"
    assert "Q5: register status 'open' vs decision 'decided in part'" in _x4(cmp_, questions=q)


def test_x4_fails_when_an_open_question_still_blocks_g3(cmp_):
    def q(qs):
        next(x for x in qs["questions"] if x["id"] == "Q4")["blocks"] = "G3"
    assert "Q4: still open and still blocks G3" in _x4(cmp_, questions=q)


def test_x4_fails_on_an_unknown_case_or_missing_page(cmp_):
    def d(ds):
        q3 = next(x for x in ds["decisions"] if x["id"] == "Q3")
        q3["cases"].append("CW-S99")
        q3["basis"].append("a reading without a page")
    errs = _x4(cmp_, decisions=d)
    assert "Q3: case CW-S99 does not exist" in errs and "Q3: basis missing or without a page reference" in errs


def test_x4_fails_when_a_carried_item_is_not_decided(cmp_):
    def c(reg):
        next(x for x in reg["items"] if x["id"] == "CI-02")["status"] = "open"
    assert "CI-02: owned by G3 but not decided with a treatment" in _x4(cmp_, carried=c)


# ---------------------------------------------------------------------------------------------------------- X5
def test_x5_passes(world, res):
    only = _sample(res)
    fwd = vg.evaluate_all(world, only=only)
    assert vg.x5_order(world, fwd, only=only) == [] and vg.x5_text() == []


def test_x5_fails_on_cross_line_state(world, res):
    """An engine that remembers earlier lines (e.g. a duplicate rule leaking into G3) is order dependent."""
    seen = []

    def stateful(line, inv, ddr, **kw):
        r = g3_dds.evaluate(line, inv, ddr, **kw)
        seen.append(r.line_ref)
        r.readings.append(f"seen before: {len(seen) - 1}")
        return r
    only = _sample(res, 50)
    fwd = vg.evaluate_all(world, dds_eval=stateful, only=only)
    assert vg.x5_order(world, fwd, dds_eval=stateful, only=only)


def test_x5_fails_on_a_g5_construct(tmp_path):
    f = tmp_path / "g3_extra.py"
    f.write_text("OUT = 'audit_results.csv'\n")
    assert vg.x5_text([f]) == ["g3_extra.py: 'audit_results.csv' (G5+ construct)"]


# ---------------------------------------------------------------------------------------------------------- X6
def test_x6_passes(world, res):
    only = _sample(res)
    assert vg.x6(world, vg.evaluate_all(world, only=only), only=only) == []


def test_x6_fails_when_billing_sets_the_rate(world, res):
    """An engine that accepts the billed rate when it is 'close' to the contract rate is not billing independent."""
    def lenient(line, app, rec, exists, **kw):
        r = g3_cw.evaluate(line, app, rec, exists, **kw)
        if r.unit_rate is not None and abs(line["rate_applied"] - r.unit_rate) <= r.unit_rate * Decimal("0.5"):
            r.unit_rate = line["rate_applied"]
        return r
    only = _sample(res)
    fwd = vg.evaluate_all(world, cw_eval=lenient, only=only)
    assert vg.x6(world, fwd, cw_eval=lenient, only=only)


# ---------------------------------------------------------------------------------------------------------- X7
def test_x7_passes(world, res):
    assert vg.x7(world, res) == []


def test_x7_fails_on_a_stale_output(world, res):
    committed = {k: (vg.OUT / k).read_text() for k in ("summary.json", "decision_scopes.json", "trace_sample.jsonl")}
    committed["decision_scopes.json"] = committed["decision_scopes.json"].replace('"lines_decided": 54', '"lines_decided": 53')
    assert vg.x7(world, res, committed) == ["verification/g3/decision_scopes.json does not reproduce from the current code and inputs"]


def test_x7_fails_on_a_result_without_the_run_context(world, res):
    ref = sorted(res["DDS"])[0]
    r2 = copy.copy(res["DDS"][ref])
    r2.ctx = None
    errs = vg.x7(world, {"CW": res["CW"], "DDS": {**res["DDS"], ref: r2}})
    assert f"DDS: 1 results without the current run context (e.g. {ref})" in errs
