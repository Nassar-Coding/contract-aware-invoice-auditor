"""G5 exit checks (tools/verify_g5.py): each check passes on the committed outcomes and FAILS on a controlled defect
(negative controls). A check that cannot fail proves nothing, so every check Z1-Z9 has at least one control here.
Controls that need the engine run on committed multi-invoice histories (fast); verify_g5 runs them on the population."""
import copy
import csv
import io
import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

import g4_histories as H
import g4_history_compare as HC
import g5_sample_compare as SC
import verify_g5 as vg
from audit import g3_cw, g3_dds, g4_cw, g4_dds, g5_run
from audit.common import SNAPSHOT
from audit.g5_outcomes import Engine, template_ids

ROOT = Path(__file__).resolve().parents[1]
G5 = ROOT / "verification" / "g5"


@pytest.fixture(scope="module")
def out():
    return SC.load_outcomes()


@pytest.fixture(scope="module")
def ids():
    return template_ids(SNAPSHOT)


@pytest.fixture(scope="module")
def headers():
    h = {}
    for rel, key in (("civilwork/invoices/applications.csv", "application_no"), ("drilling_services/invoices/invoices.csv", "invoice_no")):
        with (SNAPSHOT / rel).open(newline="") as fh:
            for r in csv.DictReader(fh):
                h[r[key]] = r
    return h


@pytest.fixture(scope="module")
def sub():
    return (G5 / "audit_results.csv").read_text()


def _history(hid):
    w, _g3, _st = HC.run_history(H.load_packets()[hid])
    res = {"CW": g3_cw.run(w), "DDS": g3_dds.run(w)}
    st = {"CW": g4_cw.run(w, res["CW"]), "DDS": g4_dds.run(w, res["DDS"])}
    return w, res, st


@pytest.fixture(scope="module")
def hist():
    return _history("CW-H08")


@pytest.fixture(scope="module")
def dhist():
    return _history("DDS-H01")


# ---------------------------------------------------------------------------------------------------------- Z1
def test_z1_passes(out, ids, headers, sub):
    assert vg.z1(out, ids, headers, sub) == []


def test_z1_fails_on_a_missing_row_and_a_bad_format(out, ids, headers, sub):
    lines = sub.splitlines()
    assert any("not the template ids" in e for e in vg.z1(out, ids, headers, "\n".join(lines[:-1]) + "\n"))
    rows = list(csv.DictReader(io.StringIO(sub)))
    r = next(x for x in rows if x["flagged"] == "1")
    r["error_category"] = ""
    r2 = next(x for x in rows if x["flagged"] == "0")
    r2["billed_total_cents"] = str(int(r2["billed_total_cents"]) + 1)
    rows[0]["confidence"] = "1.5"
    buf = io.StringIO()
    wr = csv.DictWriter(buf, fieldnames=g5_run.COLUMNS, lineterminator="\n")
    wr.writeheader()
    wr.writerows(rows)
    errs = vg.z1(out, ids, headers, buf.getvalue())
    assert any("category missing" in e for e in errs)
    assert any("is not 100 x" in e for e in errs)
    assert any("outside [0, 1]" in e for e in errs)
    o = dict(out)
    o.pop(ids[0])
    assert any("no outcome" in e for e in vg.z1(o, ids, headers, sub))


# ---------------------------------------------------------------------------------------------------------- Z2
def test_z2_passes_and_fails_on_a_lost_line_or_check(hist):
    w, _res, st = hist
    eng = Engine(w, st)
    o = {i: g5_run.serial(x) for i, x in eng.run().items()}
    for i, x in o.items():
        x["coverage"] = g5_run.coverage(eng.inv[x["contract"]][i])
    assert vg.z2(o, eng, w) == []
    inv = next(iter(eng.inv["CW"].values()))
    inv.lines.pop()
    assert any("not the claim lines" in e for e in vg.z2(o, eng, w))
    k = next(iter(o))
    o[k]["coverage"]["9"] = "not made"
    assert any("check 9 has no recorded status" in e for e in vg.z2(o, eng, w))


# ---------------------------------------------------------------------------------------------------------- Z3
def test_z3_passes(out):
    assert vg.z3(out) == []


def test_z3_fails_on_a_flag_without_finding_and_a_wrong_category(out):
    o = copy.deepcopy(out)
    a = next(x for x in o.values() if x["flagged"])
    a["findings"] = []
    b = next(x for x in o.values() if x["flagged"] and x["findings"])
    b["error_category"] = "rate; duplicate; nonsense"
    c = next(x for x in o.values() if not x["flagged"])
    c["wrong_under"] = [{}]
    errs = vg.z3(o)
    assert any("flagged without a finding" in e for e in errs)
    assert any("is not the root categories" in e for e in errs)
    assert any("unflagged although wrong" in e for e in errs)


# ---------------------------------------------------------------------------------------------------------- Z4
def test_z4_passes(out):
    assert vg.z4(out) == []


def test_z4_fails_on_a_zeroed_procedural_invoice(out):
    o = copy.deepcopy(out)
    x = next(x for x in o.values() if x["flagged"] and all(f.split("@")[0] in ("submitted_late", "submitted_early") for f in x["findings"]))
    x["expected_total"] = "0.00"
    assert any("procedural" in e for e in vg.z4(o))


def test_z4_billing_never_authority_and_fails_when_it_is(dhist):
    w, _res, st = dhist
    base = {i: g5_run.serial(x) for i, x in Engine(w, st).run().items()}
    pw = vg.perturb_billing(w)
    pe = Engine(pw, st)
    pert = {i: g5_run.serial(x) for i, x in pe.run().items()}
    assert vg.z4(base, pert) == []
    # defect: an engine that takes the (changed) billed total as its expected total
    bad = copy.deepcopy(pert)
    for i, x in bad.items():
        h = pe.inv[x["contract"]][i].header or {}
        x["expected_total"] = str(h.get("invoice_total" if x["contract"] == "DDS" else "application_total"))
    assert any("moves with the billed figures" in e or "exports the bill" in e for e in vg.z4(base, bad))


def test_z4_export_errors_pass_and_fail_on_an_unnamed_or_inconsistent_export(dhist):
    """EXPORT-E (Q9-7): every exported line value is an admissible contract value under the export values, a flagged
    row's differences from its bill are named, one class per well - each can fail."""
    w, _res, st = dhist
    e = Engine(w, st)
    out = e.run()
    assert vg.export_errors(out, e) == []
    i = next(i for i, x in out.items() if x["flagged"] and x["formed"] and x["line_values"])
    bad = copy.deepcopy(out)
    ref = next(iter(bad[i]["line_values"]))
    bad[i]["line_values"][ref] = str(Decimal(bad[i]["line_values"][ref] or "0") + Decimal("13.37"))
    assert any("not an admissible value of the line" in x for x in vg.export_errors(bad, e))
    j = next((j for j, x in out.items() if x.get("export_facts") and x["export_facts"].get("class")), None)
    if j is not None:
        bad = copy.deepcopy(out)
        k = next(k for k, x in bad.items() if k != j and x["contract"] == "DDS")
        bad[k]["export_facts"] = {**(bad[k].get("export_facts") or {"nominated": {}, "ground": {}}), "class": "HPHT"
                                  if bad[j]["export_facts"]["class"] != "HPHT" else "Standard"}
        e.inv["DDS"][k].header = {**(e.inv["DDS"][k].header or {}), "well_name": e.inv["DDS"][j].header.get("well_name")}
        assert any("different well classes" in x for x in vg.export_errors(bad, e))


# ---------------------------------------------------------------------------------------------------------- Z5
def _disp():
    return yaml.safe_load((G5 / "sample_dispositions.yaml").read_text())["dispositions"]


def test_z5_passes(out):
    assert vg.z5(SC.compare(out), _disp()) == []


def test_z5_fails_without_settlements_or_on_a_changed_engine_value(out):
    cmp_ = SC.compare(out)
    assert any("no settlement" in e for e in vg.z5(cmp_, {}))
    o = copy.deepcopy(out)
    o["MDS-00072"]["expected_total"] = "1455037.85"
    assert any("MDS-00072" in e and "no settlement" in e for e in vg.z5(SC.compare(o), _disp()))
    d = dict(_disp())
    d["PA-00047|cw_a_r1|flag: engine 1 reader 0"] = {"settled": "engine", "basis": "x"}
    assert any("stale" in e for e in vg.z5(cmp_, d))


def test_z5_fails_when_readers_came_after_the_engine(out):
    errs = vg.z5(SC.compare(out), _disp(), first_commit=lambda p: "z" if "expected_" in p else ("a" if "packet_" in p else "m"),
                 before=lambda a, b: a < b)
    assert any("was not committed before audit/g5_outcomes.py" in e for e in errs)


# ---------------------------------------------------------------------------------------------------------- Z6
def test_z6_passes(out):
    assert vg.z6(out) == []


def test_z6_fails_on_a_total_that_does_not_reconcile(out):
    o = copy.deepcopy(out)
    cw = next(x for x in o.values() if x["contract"] == "CW")
    cw["expected_total"] = str(Decimal(cw["expected_total"]) + Decimal("0.01"))
    dds = next(x for x in o.values() if x["contract"] == "DDS" and Decimal(x["expected_total"]) > 300000)
    k = next(iter(dds["line_values"]))
    dds["line_values"][k] = str(Decimal(dds["line_values"][k]) + 1)      # a line changed but not the total
    errs = vg.z6(o)
    assert any(cw["invoice_id"] in e for e in errs) and any(dds["invoice_id"] in e for e in errs)


def test_z6_half_even_is_independent():
    from fractions import Fraction
    assert vg._half_even_frac(Fraction(12345, 1000)) == Fraction(1234, 100)
    assert vg._half_even_frac(Fraction(12355, 1000)) == Fraction(1236, 100)


# ---------------------------------------------------------------------------------------------------------- Z7
def test_z7_passes(out):
    assert vg.z7(out) == []


def test_z7_fails_on_the_matching_reading_chosen_and_a_wrong_confidence(out):
    o = copy.deepcopy(out)
    a = next(x for x in o.values() if x["wrong_under"] and x["right_under"])
    a["flagged"] = 0                                 # defect: the reading that matches the bill picked
    b = next(x for x in o.values() if x["fact_dependent"] and x["confidence"] == "0.80")
    b["confidence"] = "0.95"
    c = next(x for x in o.values() if x["wrong_under"] and x["right_under"] and x is not a)
    c["confidence"] = "0.95"
    errs = vg.z7(o)
    assert any("not flagged although wrong" in e for e in errs)
    assert any("unsupplied fact" in e for e in errs)
    assert any("not 0.50" in e for e in errs)


# ---------------------------------------------------------------------------------------------------------- Z8
def _regs():
    load = lambda p: yaml.safe_load(p.read_text())  # noqa: E731
    return load(vg.DECISIONS), json.loads((G5 / "decision_effects.json").read_text()), load(vg.QUESTIONS)


def test_z8_passes():
    assert vg.z8(*_regs()) == []


def test_z8_fails_on_an_uncomputed_effect_a_blocking_question_and_an_owner_decision_unrecorded():
    d, e, q = _regs()
    e = dict(e)
    e.pop("Q12")
    qq = next(x for x in q["questions"] if x["id"] == "Q14")
    qq["blocks"] = "G5"
    qq.pop("g5_disposition")
    next(x for x in d["decisions"] if x["id"] == "Q7C").pop("decided_by")
    errs = vg.z8(d, e, q)
    assert any("Q12" in x and "not computed" in x for x in errs)
    assert any("still blocks G5" in x for x in errs)
    assert any("Q14" in x and "g5_disposition" in x for x in errs)
    assert any("policy decision" in x for x in errs)


# ---------------------------------------------------------------------------------------------------------- Z9
def test_z9_fails_on_a_non_reproducing_output_and_a_modified_g3():
    assert vg.z9({"summary.json": "a"}, {"summary.json": "a"}, "x", "x") == []
    assert vg.z9({"summary.json": "a"}, {"summary.json": "b"}) != []
    assert any("G3 results or G4 state changed" in e for e in vg.z9({}, {}, "x", "y"))


def test_z9_g5_leaves_g3_and_g4_untouched(hist):
    w, res, st = hist
    before = vg.g34_digest(res, st)
    Engine(w, st).run()
    assert vg.g34_digest(res, st) == before
