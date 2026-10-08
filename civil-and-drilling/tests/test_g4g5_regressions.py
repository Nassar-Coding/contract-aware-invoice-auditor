"""G4/G5 review blockers (G4-B01.., G5-B01..): each blocker's demonstrated variants through the
production pipeline, the corrected result, and a negative control - the G4/G5 code as first completed
(tests/fixtures/prior_code/), run on the same input and rejected by the corrected oracle for the blocker's reason."""
import importlib.util
import prior_code
import sys
import types
from decimal import Decimal
from pathlib import Path

import pytest

import g4g5_regression_fixtures as F
import verify_g4 as vg4
from audit import g3_cw, g3_dds, g4_cw, g4_dds
from audit.g3_core import result_keys

ROOT = Path(__file__).resolve().parents[1]
PRIOR_G4 = "g4_first_version"
PRIOR_G5 = "g5_first_version"
D = Decimal
W = "NGP-ZZ-951"


def prior_module(label: str, path: str, name: str) -> types.ModuleType:
    """The prior version of an audit module, loaded under the audit package (its relative imports resolve to the
    current G3 layer, which these corrections do not change)."""
    src = prior_code.source(label, path)
    spec = importlib.util.spec_from_loader(f"audit.{name}", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "audit"
    sys.modules[f"audit.{name}"] = mod
    exec(compile(src, f"{label}:{path}", "exec"), mod.__dict__)
    return mod


def _claims(w):
    for lk in ("cw_lines", "dds_lines"):
        for k, row in zip(result_keys(w.claims.rows[lk]), w.claims.rows[lk]):
            vg4._CLAIMS[k] = row.values
    vg4.load_heads(w)


def _with(engine_dds, h):
    w, res, _st = F.run(h)
    _claims(w)
    return w, res, {"DDS": engine_dds.run(w, res["DDS"])}


# ---------------------------------------------------------------------------------------------------------- G4-B01
def h_b01_v1():
    return F.pd210_history("B01-1", W, [("2025-07-01", 4500, 44490), ("2025-07-02", 44490, 44510)],
                           [("MDS-95101", "2025-07-01", 4500, 44490), ("MDS-95102", "2025-07-02", 44490, 44510),
                            ("MDS-95103", "2025-07-02", 44490, 44510)])


def h_b01_v2():
    return F.pd210_history("B01-2", W, [("2025-07-01", 4500, 44450), ("2025-07-02", 44450, 44470), ("2025-07-03", 44470, 44490)],
                           [("MDS-95201", "2025-07-01", 4500, 44450), ("MDS-95202", "2025-07-02", 44450, 44470),
                            ("MDS-95203", "2025-07-02", 44450, 44470), ("MDS-95204", "2025-07-03", 44470, 44490)])


def test_b01_duplicate_interval_counted_once_and_allocation_kept():
    w, res, st = _with(g4_dds, h_b01_v1())
    L = F.by_ref(st, "DDS")
    a, b = L["MDS-95102-001"], L["MDS-95103-001"]
    # 39,990 prior; 44,490-44,510: 10 m at 98.70 + 10 m at 94.75 (96%) = 1,934.50, kept by ONE of the two charges
    assert F.amounts(a) == [D("0.00"), D("1934.50")] and F.amounts(b) == [D("0.00"), D("1934.50")]
    for k, v in a.r.alternatives.items():
        other = b.r.alternatives[k]
        assert v["amount"] + other["amount"] == D("1934.50")
    assert vg4.footage_reset_errors(st["DDS"]) == [] and vg4.pd210_coverage_errors(st["DDS"]) == []


def test_b01_later_interval_uses_unique_prior_metres():
    w, res, st = _with(g4_dds, h_b01_v2())
    # unique prior 39,950 + 20 = 39,970: the last 20 m lie wholly below 40,000 -> 20 x 98.70 = 1,974.00
    assert F.amounts(F.by_ref(st, "DDS")["MDS-95204-001"]) == [D("1974.00")]
    assert vg4.footage_reset_errors(st["DDS"]) == []


def test_b01_replay_after_removing_the_earlier_duplicate():
    h = h_b01_v2()
    h["documents"] = [d for d in h["documents"] if d["header"]["invoice_no"] != "MDS-95203"]
    w, res, st = _with(g4_dds, h)
    L = F.by_ref(st, "DDS")
    assert F.amounts(L["MDS-95202-001"]) == [D("1974.00")] and F.amounts(L["MDS-95204-001"]) == [D("1974.00")]


def test_b01_control_prior_g4_counts_the_duplicate_twice():
    old = prior_module(PRIOR_G4, "audit/g4_dds.py", "g4_dds_prior_g4")
    w, res, st = _with(old, h_b01_v1())
    L = F.by_ref(st, "DDS")
    # the prior code restores both charges to the full 1,934.50 - the metres are charged twice
    assert F.amounts(L["MDS-95102-001"]) == [D("1934.50")] and F.amounts(L["MDS-95103-001"]) == [D("1934.50")]
    w2, res2, st2 = _with(old, h_b01_v2())
    last = F.by_ref(st2, "DDS")["MDS-95204-001"].r.alternatives
    assert last["Q11:A"]["amount"] == D("1934.50")          # adopted Q11 A used 39,990 prior, not 39,970: 1,974.00 absent


# ---------------------------------------------------------------------------------------------------------- G4-B02
def h_b02(extra=()):
    return F.pd210_history("B02", W, [("2025-07-01", 1500, 1700)],
                           [("MDS-95301", "2025-07-01", 1500, 1600), ("MDS-95302", "2025-07-01", 1550, 1650),
                            ("MDS-95303", "2025-07-01", 1600, 1700), *extra])


def test_b02_three_interval_chain_complete_joint_domain():
    w, res, st = _with(g4_dds, h_b02())
    L = F.by_ref(st, "DDS")
    assert F.amounts(L["MDS-95302-001"]) == [D("0.00"), D("2907.50"), D("5815.00")]     # zero, one half, whole
    assert len(L["MDS-95302-001"].r.alternatives) == 4
    for k in L["MDS-95301-001"].r.alternatives:
        assert sum(L[r].r.alternatives[k]["amount"] for r in ("MDS-95301-001", "MDS-95302-001", "MDS-95303-001")) == D("11630.00")
    assert vg4.pd210_coverage_errors(st["DDS"]) == []


def test_b02_contained_and_exact_duplicate():
    w, res, st = _with(g4_dds, h_b02(extra=[("MDS-95304", "2025-07-01", 1500, 1700), ("MDS-95305", "2025-07-01", 1500, 1600)]))
    assert vg4.pd210_coverage_errors(st["DDS"]) == []
    L = F.by_ref(st, "DDS")
    for k in L["MDS-95304-001"].r.alternatives:
        tot = sum(L[r].r.alternatives[k]["allowed_quantity"] for r in L if r.startswith("MDS-953"))
        assert tot == D("200")


def test_b02_control_prior_g4_pairwise_allocation_fails_the_metre_oracle():
    old = prior_module(PRIOR_G4, "audit/g4_dds.py", "g4_dds_prior_g4")
    w, res, st = _with(old, h_b02())
    L = F.by_ref(st, "DDS")
    assert D("0.00") not in F.amounts(L["MDS-95302-001"])            # the admissible zero outcome is missing
    st["DDS"].footage = {}                                             # old footage format: the coverage oracle alone
    assert vg4.pd210_coverage_errors(st["DDS"]) != []


def test_b02_band_interaction_duplicate_at_the_annual_edge():
    # the B01 fixture is also a contested segment at the annual edge: allocation and footage compose
    w, res, st = _with(g4_dds, h_b01_v1())
    assert vg4.pd210_coverage_errors(st["DDS"]) == []


# ---------------------------------------------------------------------------------------------------------- G4-B03
def _hist(hid, blank=None, field="application_date"):
    import copy
    import g4_histories as H
    h = copy.deepcopy(next(x for x in H.CW_HISTORIES + H.DDS_HISTORIES if x["id"] == hid))
    for d in h["documents"]:
        if blank and (d["header"].get("application_no") == blank or d["header"].get("invoice_no") == blank):
            d["header"][field] = ""
    return h


def _cw_with(engine_cw, h):
    w, res, _st = F.run(h)
    _claims(w)
    vg4.load_heads(w)
    return w, res, {"CW": engine_cw.run(w, res["CW"]), "DDS": g4_dds.run(w, res["DDS"])}


def h_b03_band():
    h = _hist("CW-H01")
    h["documents"][0]["lines"][0]["work_date"] = ""          # the first 600 m2 B.22.010: work date not established
    return h


def test_b03_undated_band_measurement_is_a_possible_contributor():
    w, res, st = _cw_with(g4_cw, h_b03_band())
    L = F.by_ref(st, "CW")
    later = L["PA-91001-01"]                                  # 500 m2: 37,250.00 without the 600 before it, 36,356.00 with
    assert later.r.amount != D("37250.00") and later.r.amount_status == "unresolved" and later.r.payable is None
    assert "band_state_unknown" in later.state_unresolved
    # the 400 m2 of 10 March lies in band 1 whatever the undated 600 adds (0..600 + 400 <= 1,000): provably determined
    assert D("29800.00") in F.amounts(L["PA-91002-02"])
    assert vg4.y3(st["CW"], st["DDS"]) == [] and vg4.chronology_errors(st["CW"], st["DDS"]) == []


def test_b03_control_prior_g4_omits_the_undated_measurement():
    old = prior_module(PRIOR_G4, "audit/g4_cw.py", "g4_cw_prior_g4")
    w, res, st = _cw_with(old, h_b03_band())
    later = F.by_ref(st, "CW")["PA-91001-01"]
    assert later.r.amount == D("37250.00") and later.r.amount_status == "determined"      # the review's silent value
    st["CW"].undated = {}
    assert any("undated measurements" in e for e in vg4.chronology_errors(st["CW"], st["DDS"]))


def test_b03_release_with_an_undated_application_is_not_established():
    w, res, st = _cw_with(g4_cw, _hist("CW-H10", "PA-9A001"))
    rel = st["CW"].retention["release"]
    assert rel["recipient"]["not_established"] == ["PA-9A001", "PA-9A003"]
    assert rel["released"] == {"by_reading": {"": {"min": "96.15", "max": "192.49"}}}       # never the bare 96.15
    assert vg4.chronology_errors(st["CW"], st["DDS"]) == []
    w, res, st = _cw_with(g4_cw, _hist("CW-H10"))
    assert st["CW"].retention["release"]["recipient"] == "PA-9A003" and st["CW"].retention["release"]["released"] == "192.49"


def test_b03_control_prior_g4_release_omits_the_undated_application():
    old = prior_module(PRIOR_G4, "audit/g4_cw.py", "g4_cw_prior_g4")
    w, res, st = _cw_with(old, _hist("CW-H10", "PA-9A001"))
    rel = st["CW"].retention["release"]
    assert rel["released"] == "96.15" and rel["not_established"] == [] and rel["recipient"] == "PA-9A003"
    st["CW"].undated = {}
    assert any("45A release recipient" in e for e in vg4.chronology_errors(st["CW"], st["DDS"]))


def _stands(o):
    return [k for k in o["open_readings"] if k.split("@")[0] == "stands"]


def _g5(mod, h, prior=False):
    w, res, st = F.run(h)
    if prior:            # the prior pair: the first G4 state under the first G5
        st = {"CW": prior_module(PRIOR_G4, "audit/g4_cw.py", "g4_cw_prior_g4").run(w, res["CW"]),
              "DDS": prior_module(PRIOR_G4, "audit/g4_dds.py", "g4_dds_prior_g4").run(w, res["DDS"])}
    e = mod.Engine(w, st)
    return {no: e.outcome(i) for no, i in e.inv["DDS"].items()}, e


def test_b03_undated_invoice_never_sorted_last_under_q7c():
    from audit import g5_outcomes
    out, e = _g5(g5_outcomes, _hist("DDS-H01", "MDS-90001", "invoice_date"))
    o = out["MDS-90002"]
    assert _stands(o) and o["confidence"] != D("0.80")
    # its own daily-limit breach makes it wrong whichever charge stands; the totals differ by the 4 March charge (Q9-5)
    assert o["flagged"] == 1 and o["confidence"] == D("0.60") and len(o["totals"]) == 2
    assert _stands(out["MDS-90001"]) and out["MDS-90001"]["confidence"] == D("0.50")
    assert all(t is True for v in e.stands.values() for d, (_f, t) in v.items() if d.startswith("stands@"))                          # open, not a keeper chosen by a sentinel date


def test_b03_control_prior_g5_sentinel_selects_a_keeper():
    old = prior_module(PRIOR_G5, "audit/g5_outcomes.py", "g5_outcomes_prior_g5")
    out, e = _g5(old, _hist("DDS-H01", "MDS-90001", "invoice_date"), prior=True)
    o = out["MDS-90002"]
    assert "stands" not in o["open_readings"] and o["confidence"] == D("0.80")      # the review's definite keeper
    # the undated invoice's charge is treated as the later one: a definite repeat at 0.80
    assert out["MDS-90001"]["flagged"] == 1 and out["MDS-90001"]["confidence"] == D("0.80")
    assert "charged_twice@MDS-90001-002" in out["MDS-90001"]["findings"]
    assert all(v["stands"][1] is False for v in e.stands.values() if "stands" in v)


def test_b03_undated_civil_measurement_may_be_the_earlier_duplicate():
    w, res, st = _cw_with(g4_cw, h_b03_band())
    g = F.by_ref(st, "CW")["PA-91002-02"]
    assert D("0.00") in F.amounts(g) and "duplicate_measurement" in g.state_unresolved


def test_b03_undated_dds_charge_is_a_possible_repeat_and_well_event_not_a_finding():
    h = _hist("DDS-H01")
    h["documents"][1]["lines"][0]["service_date"] = ""                  # MDS-90002's 4 March DD-101: date not established
    w, res, st = F.run(h)
    L = F.by_ref(st, "DDS")
    assert L["MDS-90002-001"].g3.payable is None                        # G3: the undated charge is not valued
    # it may be for any of the well's dated DD-101 days: each dated charge stands only in the scenario it is the one kept
    for ref in ("MDS-90001-001", "MDS-90001-002", "MDS-90002-002"):
        assert F.amounts(L[ref]) == [D("0.00"), D("3694.70")] and "charged_twice" in L[ref].state_unresolved
    poss = [gr for gr in st["DDS"].groups if gr.get("possible")]
    assert len(poss) == 3 and all(gr["possible"] == ["MDS-90002-001"] for gr in poss)


def test_b03_control_prior_g4_ignores_the_undated_dds_charge():
    old = prior_module(PRIOR_G4, "audit/g4_dds.py", "g4_dds_prior_g4")
    h = _hist("DDS-H01")
    h["documents"][1]["lines"][0]["service_date"] = ""
    w, res, st = _with(old, h)
    assert F.amounts(F.by_ref(st, "DDS")["MDS-90001-002"]) == [D("3694.70")]     # determined: the possible repeat omitted



# ---------------------------------------------------------------------------------------------------------- G4-B04
def cw_hist(hid, lines, adate="2025-03-20"):
    """One civil application of B.22.010 lines (work date, quantity, displayed rate, amount), header consistent with the
    amounts (no header defect of its own); zone Z1, no records needed."""
    import g4_histories as H
    doc = H.cw_app("PA-97001", adate, [(d, "S-05", "B.22.010", q, r, "") for d, q, r, _a in lines])
    tot = D(0)
    for row, (_d, _q, _r, a) in zip(doc["lines"], lines):
        row["amount"] = a
        tot += D(a)
    ret = (tot * D("0.05")).quantize(D("0.01"), rounding="ROUND_DOWN")
    doc["header"].update({"application_total": f"{tot:.2f}", "retention": f"{ret:.2f}", "net_payable": f"{tot - ret:.2f}"})
    return {"id": hid, "documents": [doc], "records": {}, "given": []}


def _cw_outcome(h, g4mod=g4_cw, g5mod=None):
    from audit import g5_outcomes
    g5mod = g5mod or g5_outcomes
    w, res, _st = F.run(h)
    st = {"CW": g4mod.run(w, res["CW"]), "DDS": g4_dds.run(w, res["DDS"])}
    e = g5mod.Engine(w, st)
    return st, {no: e.outcome(i) for no, i in e.inv["CW"].items()}


def _state(st, ref, fam):
    return [(x.status, x.finding) for x in F.by_ref(st, "CW")[ref].state if x.family == fam]


def test_b04_wrong_rate_with_the_correct_amount_is_a_finding():
    # first-band B.22.010, 600 m2, no earlier history: rate 74.50, amount 44,700.00; the claim states 71.52 (band 2)
    st, out = _cw_outcome(cw_hist("CW-B04-1", [("2025-03-02", "600", "71.52", "44700.00")]))
    assert _state(st, "PA-97001-01", "band_rate") == [("finding", "rate_differs")]
    assert _state(st, "PA-97001-01", "band_arithmetic") == [("finding", "amount_arithmetic")]   # 600 x 71.52 = 42,912.00
    o = out["PA-97001"]
    assert o["flagged"] == 1 and "rate" in o["error_category"] and o["expected_total"] == D("44700.00")


def test_b04_control_prior_g4_leaves_the_wrong_rate_unresolved_and_g5_passes_it():
    old = prior_module(PRIOR_G4, "audit/g4_cw.py", "g4_cw_prior_g4")
    old5 = prior_module(PRIOR_G5, "audit/g5_outcomes.py", "g5_outcomes_prior_g5")
    st, out = _cw_outcome(cw_hist("CW-B04-1", [("2025-03-02", "600", "71.52", "44700.00")]), old, old5)
    assert _state(st, "PA-97001-01", "band_rate") == [("unresolved", "rate_differs")]
    assert out["PA-97001"]["flagged"] == 0 and out["PA-97001"]["confidence"] == D("0.95")        # the review's false pass


def test_b04_rate_and_amount_are_judged_independently():
    cases = {("74.50", "44700.00"): ("pass", "pass", 0), ("74.50", "44000.00"): ("pass", "finding", 1),
             ("71.52", "42912.00"): ("finding", "pass", 1), ("71.52", "44700.00"): ("finding", "finding", 1)}
    for (rate, amt), (rs, ars, flag) in cases.items():
        st, out = _cw_outcome(cw_hist("CW-B04-x", [("2025-03-02", "600", rate, amt)]))
        got_r = [s for s, _f in _state(st, "PA-97001-01", "band_rate")]
        got_a = [s for s, _f in _state(st, "PA-97001-01", "band_arithmetic")] or ["pass"]      # G3 passed it outright
        assert (got_r, got_a[0], out["PA-97001"]["flagged"]) == ([rs], ars, flag), (rate, amt)


def test_b04_a_valid_split_is_not_a_finding():
    # 1,000 m2 in band 1, then 400 m2 from 1,000: 200 at 74.50 + 200 at 71.52 = 29,204.00 (Cl.28 division)
    for rate in ("74.50", "71.52"):
        st, out = _cw_outcome(cw_hist("CW-B04-s", [("2025-03-02", "1000", "74.50", "74500.00"),
                                                ("2025-03-03", "400", rate, "29204.00")]))
        assert _state(st, "PA-97001-02", "band_rate") == [("pass", None)]
        assert _state(st, "PA-97001-02", "band_arithmetic") in ([("pass", None)], [])
        assert out["PA-97001"]["flagged"] == 0
    st, out = _cw_outcome(cw_hist("CW-B04-s2", [("2025-03-02", "1000", "74.50", "74500.00"),
                                             ("2025-03-03", "400", "72.00", "29204.00")]))
    assert _state(st, "PA-97001-02", "band_rate") == [("finding", "rate_differs")]        # no band's rate


def test_b04_genuinely_unresolved_band_stays_open_never_a_pass():
    h = cw_hist("CW-B04-u", [("2025-03-02", "1000", "74.50", "74500.00"), ("2025-03-03", "500", "71.52", "37250.00")])
    h["documents"][0]["lines"][0]["work_date"] = ""
    st, out = _cw_outcome(h)
    g = F.by_ref(st, "CW")["PA-97001-02"]
    assert g.r.amount_status == "unresolved" and ("pass", None) not in _state(st, "PA-97001-02", "band_rate")
    assert out["PA-97001"]["confidence"] != D("0.95")


def test_b04_reading_dependent_band_carries_the_breach_per_scenario():
    # 1,200 m2 in December 2025, then 100 m2 on 10 January 2026 at 74.50: band 1 after the Q12 A reset, band 2 under Q12 B
    h = cw_hist("CW-B04-q", [("2025-12-20", "1200", "74.50", "89400.00"), ("2026-01-10", "100", "74.50", "7450.00")],
                adate="2026-01-20")
    st, out = _cw_outcome(h)
    g = F.by_ref(st, "CW")["PA-97001-02"]
    assert _state(st, "PA-97001-02", "band_rate") == [("unresolved", "rate_differs")]
    br = {k: v.get("breaches") for k, v in g.r.alternatives.items()}
    assert any("rate_differs" in (b or []) for k, b in br.items() if "Q12:B" in k)
    assert all(not b for k, b in br.items() if "Q12:A" in k)
    assert out["PA-97001"]["flagged"] == 0                                 # Q12 A decided (upheld): right
    from audit import g5_outcomes
    w, res, _st = F.run(h)
    e = g5_outcomes.Engine(w, st, g5_outcomes.Policy(decided={**g5_outcomes.DECIDED, "Q12": "B"}))
    o = e.outcome(e.inv["CW"]["PA-97001"])
    assert o["flagged"] == 1 and "rate_differs@PA-97001-02" in o["findings"]


def test_b04_oracle_accepts_the_fixed_state_and_rejects_prior_g4():
    h = cw_hist("CW-B04-o", [("2025-03-02", "600", "71.52", "44700.00")])
    w, res, _st = F.run(h)
    _claims(w)
    assert vg4.deferred_rate_errors(g4_cw.run(w, res["CW"])) == []
    old = prior_module(PRIOR_G4, "audit/g4_cw.py", "g4_cw_prior_g4")
    errs = vg4.deferred_rate_errors(old.run(w, res["CW"]))
    assert errs and "expected finding" in errs[0]


# ---------------------------------------------------------------------------------------------------------- G5-B04
def h_b04_witness():
    """The review's raw-history witness: Z1, March 2025; two independent band-edge order groups (B.22.010 on 2 March,
    A.13.010 on 4 March), each of two measurements on two applications in different work areas (not duplicates)."""
    import g4_histories as H
    x1 = H.cw_app("PA-9X001", "2025-03-20", [("2025-03-01", "S-05", "B.22.010", "1100", "74.50", ""),
                                             ("2025-03-02", "S-05", "B.22.010", "150", "74.50", ""),
                                             ("2025-03-03", "S-05", "A.13.010", "4900", "18.90", ""),
                                             ("2025-03-04", "S-05", "A.13.010", "150", "18.90", "")])
    x2 = H.cw_app("PA-9X002", "2025-03-25", [("2025-03-02", "S-04", "B.22.010", "150", "74.50", ""),
                                             ("2025-03-04", "S-04", "A.13.010", "150", "18.90", "")], site="S-04")
    return {"id": "CW-G5B04", "documents": [x1, x2], "records": {}, "given": []}


def test_g5b04_independent_local_groups_combine_into_four_totals():
    st, out = _cw_outcome(h_b04_witness())
    assert [D(t) for t in out["PA-9X001"]["totals"]] == [D("187953.50"), D("188066.50"), D("188251.50"), D("188364.50")]
    assert [D(t) for t in out["PA-9X002"]["totals"]] == [D("13393.50"), D("13506.50"), D("13691.50"), D("13804.50")]
    for o in out.values():
        assert o["formed"] is True and o["scenarios"] >= 4 and len([k for k in o["open_readings"] if k.startswith("order@")]) == 2


def test_g5b04_control_prior_g4_g5_merge_the_groups_into_one_order():
    old4 = prior_module(PRIOR_G4, "audit/g4_cw.py", "g4_cw_prior_g4")
    old5 = prior_module(PRIOR_G5, "audit/g5_outcomes.py", "g5_outcomes_prior_g5")
    st, out = _cw_outcome(h_b04_witness(), old4, old5)
    o = out["PA-9X001"]
    assert o["formed"] is False and o["confidence"] == D("0.30") and len(o["totals"]) <= 1      # the review's result


def test_g5b04_two_independent_tie_groups_and_q12():
    import g4_histories as H
    # two applications submitted the same day each measure A.11.010 on S-01 (3 June) and D.41.010 on S-02 (4 June):
    # two independent Cl.44 tie groups - each application is the earlier one in one scenario of EACH group
    a = H.cw_app("PA-9T001", "2025-06-10", [("2025-06-03", "S-01", "A.11.010", "100", "3.85", ""),
                                            ("2025-06-04", "S-02", "A.13.010", "50", "18.90", "")], site="S-01")
    b = H.cw_app("PA-9T002", "2025-06-10", [("2025-06-03", "S-01", "A.11.010", "100", "3.85", ""),
                                            ("2025-06-04", "S-02", "A.13.010", "50", "18.90", "")], site="S-01")
    st, out = _cw_outcome({"id": "CW-G5B04t", "documents": [a, b], "records": {}, "given": []})
    for o in out.values():
        groups = [k for k in o["open_readings"] if k.startswith("earlier@")]
        assert len(groups) == 2 and o["scenarios"] == 4 and o["formed"]
        # wrong where the other application's measurement is the earlier one in either group, right in one scenario
        assert len(o["right_under"]) == 1 and len(o["wrong_under"]) == 3 and o["confidence"] == D("0.50")
    # Q12 is a global reading: forcing B applies it to every group at once, never per group
    import g4g5_regression_fixtures as F2
    from audit import g5_outcomes
    w, res, st2 = F2.run(_cw_order_q12())
    e = g5_outcomes.Engine(w, st2)
    o = e.outcome(e.inv["CW"]["PA-9Q001"])
    assert o["open_readings"] == {}                  # Q12 A (decided): CY2 restarts at zero, no edge, no order group
    eb = g5_outcomes.Engine(w, st2, g5_outcomes.Policy(decided={**g5_outcomes.DECIDED, "Q12": "B"}))
    ob = eb.outcome(eb.inv["CW"]["PA-9Q001"])
    assert list(ob["open_readings"]) == ["order@B.22.010/2026-01-10"] and len(ob["open_readings"]["order@B.22.010/2026-01-10"]) == 2


def _cw_order_q12():
    import g4_histories as H
    # a same-date order group on 10 January 2026 at the B.22.010 edge only under Q12 B (no reset): Q12 decided A
    x = H.cw_app("PA-9Q001", "2026-01-20", [("2025-12-20", "S-05", "B.22.010", "1150", "74.50", ""),
                                            ("2026-01-10", "S-05", "B.22.010", "100", "74.50", "")])
    y = H.cw_app("PA-9Q002", "2026-01-22", [("2026-01-10", "S-04", "B.22.010", "100", "74.50", "")], site="S-04")
    return {"id": "CW-G5B04q", "documents": [x, y], "records": {}, "given": []}


def test_g5b04_oracle_joins_combinations_and_rejects_the_prior_pair():
    import verify_g5 as vg5
    from audit import g5_outcomes
    w, res, st = F.run(h_b04_witness())
    e = g5_outcomes.Engine(w, st)
    out = {no: e.outcome(i) for no, i in e.inv["CW"].items()}
    assert vg5.joint_errors(out, e) == []
    old4 = prior_module(PRIOR_G4, "audit/g4_cw.py", "g4_cw_prior_g4")
    old5 = prior_module(PRIOR_G5, "audit/g5_outcomes.py", "g5_outcomes_prior_g5")
    st_old = {"CW": old4.run(w, res["CW"]), "DDS": g4_dds.run(w, res["DDS"])}
    e_old = old5.Engine(w, st_old)
    out_old = {no: e_old.outcome(i) for no, i in e_old.inv["CW"].items()}
    errs = vg5.joint_errors(out_old, e_old)
    assert any("PA-9X001" in x and "formed=False" in x for x in errs)


# ---------------------------------------------------------------------------------------------------------- G5-B01
def _unit():
    import test_g5_falsification as U
    return U


def _nom_line(ref, billed, value="1000.00"):
    U = _unit()
    k, v, g = U._line("DDS", ref, "PD-210", billed, value)
    g.r.conditions = [{"dimension": "nomination", "owner": "G5", "basis": "Cl.23"}]
    g.r.amount_status = "conditional"
    v.update({"well_name": "W-1", "hole_section": "12-1/4 in"})
    return k, v, g


def _cls_line(ref, billed):
    U = _unit()
    alts = {"class:Standard": "1000.00", "class:Extended Reach": "1175.00", "class:HPHT": "1325.00"}
    return U._line("DDS", ref, "MW-310", billed, alts=alts)


def _o(invs, target, policy=None, mod=None):
    U = _unit()
    e = U._engine(invs, policy=policy)
    if mod is not None:
        e2 = mod.Engine.__new__(mod.Engine)
        e2.__dict__.update({k: v for k, v in e.__dict__.items() if not k.startswith("_")})
        e = e2
    return e.outcome(target)


def test_g5b01_claim_only_change_leaves_flag_total_and_confidence():
    U = _unit()
    for billed in ("1000.00", "1325.00", "1111.00"):
        res = set()
        for cls in ("HPHT", "Standard", "Extended Reach", ""):
            inv = U._dds("K", [_cls_line("K-001", billed)], cls=cls)
            o = _o([inv], inv)
            res.add((o["flagged"], o["expected_total"], o["confidence"], o["contract_total"]))
        assert len(res) == 1, (billed, res)


def test_g5b01_control_prior_g5_claim_selects_the_value():
    U = _unit()
    old5 = prior_module(PRIOR_G5, "audit/g5_outcomes.py", "g5_outcomes_prior_g5")
    res = set()
    for cls in ("HPHT", "Standard"):
        inv = old5.Invoice("DDS", "K", U._dds("K", [_cls_line("K-001", "1325.00")], cls=cls).header,
                           [_cls_line("K-001", "1325.00")])
        e = old5.Engine.__new__(old5.Engine)
        e.policy, e.inv, e.stands, e.a3, e.release, e.inv_date = old5.Policy(), {"CW": {}, "DDS": {"K": inv}}, {}, \
            {"CW": {}, "DDS": {}}, {}, {}
        o = e.outcome(inv)
        res.add((o["flagged"], o["expected_total"], o["confidence"]))
    assert len(res) == 2          # the header's class statement alone moves the outcome (the review's MDS-00753 pattern)


def test_g5b01_inconsistent_classes_for_one_well_stay_open():
    U = _unit()
    a = U._dds("KA", [_cls_line("KA-001", "1325.00")], header_extra={"well_name": "W-9"}, cls="HPHT")
    b = U._dds("KB", [_cls_line("KB-001", "1000.00")], header_extra={"well_name": "W-9"}, cls="Standard")
    oa, ob = _o([a, b], a), _o([a, b], b)
    # Cl.4: one class governs the well - no single call-off makes both right: the class is one open fact (Q9-2)
    for o in (oa, ob):
        assert o["class_conflict"] and o["flagged"] == 1 and o["confidence"] == D("0.50") and "class" in o["open_readings"]
    b2 = U._dds("KB", [_cls_line("KB-001", "1325.00")], header_extra={"well_name": "W-9"}, cls="Standard")
    for o in (_o([a, b2], a), _o([a, b2], b2)):
        assert not o["class_conflict"] and o["flagged"] == 0 and o["confidence"] == D("0.80")


def test_g5b01_both_nomination_outcomes():
    U = _unit()
    ok = U._dds("N1", [_nom_line("N1-001", "1000.00")])
    o = _o([ok], ok)
    assert o["flagged"] == 0 and o["confidence"] == D("0.80") and o["nomination_dependent"]    # right if nominated
    assert D(o["contract_total"]) == D("0.00")          # EXPORT-D: no supplied call-off nominates the section
    bad = U._dds("N2", [_nom_line("N2-001", "1200.00"),
                        U._line("DDS", "N2-002", "DD-101", "500.00", "500.00")])
    o = _o([bad], bad)
    # wrong whether or not the section is nominated (1,200 billed, 1,000 if nominated, 0 if not): flagged; exported
    # under the export value (Q9-7 EXPORT-E: nominated - the value under which the charge exists), (1,000 + 500) + VAT,
    # its difference named on the line; confidence 0.60 (the total depends on the call-off); EXPORT-D disclosed
    assert o["flagged"] == 1 and o["expected_total"] == D("1725.00") and o["confidence"] == D("0.60")
    assert D(o["contract_total"]) == D("575.00") and any(f.endswith("@N2-001") for f in o["findings"])
    assert "section_not_nominated@N2-001" not in o["findings"]          # evidence-dependent, never a finding


def test_g5b01_civil_ground_statement_is_not_authority():
    U = _unit()
    res = set()
    for stated in ("G4 Soft", "G2 Firm", ""):
        k, v, g = U._line("CW", "GR-01", "A.12.010", "150.00",
                          alts={"ground:G1": "90.00", "ground:G2": "100.00", "ground:G4": "150.00"})
        v["ground_class"] = stated
        inv = U.Invoice("CW", "GR", {"application_total": D("150.00"), "retention": D("7.50"), "net_payable": D("142.50"),
                                     "adjustment": D("0"), "retention_released": D("0")}, [(k, v, g)])
        o = _o([inv], inv)
        res.add((o["flagged"], o["expected_total"], o["confidence"], o["contract_total"]))
    assert res == {(0, D("150.00"), D("0.80"), D("100.00"))}          # right under G4; S4 fallback G2 gives 100.00


def test_g5b01_z4_claim_check_passes_fixed_and_fails_prior_g5():
    import verify_g5 as vg5
    U = _unit()
    old5 = prior_module(PRIOR_G5, "audit/g5_outcomes.py", "g5_outcomes_prior_g5")

    def run(mod, cls):
        lines = [_cls_line("K-001", "1325.00")]
        if mod is None:
            inv = U._dds("K", lines, cls=cls)
            return {"K": _o([inv], inv)}
        inv = old5.Invoice("DDS", "K", U._dds("K", lines, cls=cls).header, lines)
        e = old5.Engine.__new__(old5.Engine)
        e.policy, e.inv, e.stands, e.a3, e.release, e.inv_date = old5.Policy(), {"CW": {}, "DDS": {"K": inv}}, {}, \
            {"CW": {}, "DDS": {}}, {}, {}
        o = e.outcome(inv)
        o["contract_total"] = o["expected_total"]
        return {"K": o}
    assert [x for x in vg5.z4(run(None, "HPHT"), None, run(None, "Standard")) if "stated class" in x] == []
    errs = vg5.z4(run(old5, "HPHT"), None, run(old5, "Standard"))
    assert any("changing only its stated class/ground moves the outcome" in x for x in errs)


# ---------------------------------------------------------------------------------------------------------- G5-B02
def h_b02_missing_depth(billed="2117.50"):
    """The review's raw input: one PD-210 charge with no start depth, end 1,500 m, quantity 50 at 42.35 (2,117.50)."""
    import g4_histories as H
    doc = H.dds_inv("MDS-95901", W, "NG-Rig 97", "2025-04-20", [("2025-04-10", "PD-210", "50", "42.35", H.S12, "Operating",
                                                                "", "1500")])
    doc["lines"][0]["amount"] = billed
    reps = H.reports(H.ddr(W, "NG-Rig 97", "2025-04-10", H.S12, "Operating", 1450, 1500, 15, 1, H.BASIC, F.CREW,
                           part_b=("2025-04-10", "2025-04-10", H.BASIC, 15, False)))
    return {"id": "DDS-G5B02", "documents": [doc], "reports": reps, "records": {}, "given": []}


def _dds_outcome(h, g5mod=None):
    from audit import g5_outcomes
    g5mod = g5mod or g5_outcomes
    w, res, st = F.run(h)
    e = g5mod.Engine(w, st)
    return {no: e.outcome(i) for no, i in e.inv["DDS"].items()}


def test_g5b02_unvalued_line_never_takes_the_billed_amount():
    a = _dds_outcome(h_b02_missing_depth("2117.50"))["MDS-95901"]
    b = _dds_outcome(h_b02_missing_depth("9999.00"))["MDS-95901"]
    for o in (a, b):
        assert o["formed"] is False and o["flagged"] == 1 and o["confidence"] == D("0.30")
        assert "depths_missing@MDS-95901-001" in o["findings"]
        assert o["expected_status"] == "bounded" and o["expected_bounds"][1] is None       # no supported upper bound
        assert o["expected_basis"].startswith("EXPORT-U")
    assert a["expected_total"] == b["expected_total"] == D(a["expected_bounds"][0]) == D("0.00")
    assert a["expected_total"] not in (D("2117.50"), D("2435.13"))


def test_g5b02_control_prior_g5_copies_the_bill():
    old5 = prior_module(PRIOR_G5, "audit/g5_outcomes.py", "g5_outcomes_prior_g5")
    a = _dds_outcome(h_b02_missing_depth("2117.50"), old5)["MDS-95901"]
    b = _dds_outcome(h_b02_missing_depth("9999.00"), old5)["MDS-95901"]
    assert a["expected_total"] == D("2117.50") and b["expected_total"] == D("9999.00")     # the review's result


def _unvalued(ref, code, billed, findings=()):
    U = _unit()
    k, v, g = U._line("DDS" if ref.startswith("X") else "CW", ref, code, billed, None, findings=findings)
    g.r.payable, g.r.amount, g.r.amount_status = None, None, "unresolved"
    return k, v, g


def test_g5b02_partially_formed_drilling_invoice_keeps_ds900_and_vat():
    U = _unit()
    lines = [U._line("DDS", "X-001", "DD-101", "300000.00", "300000.00"), _unvalued("X-002", "MW-310", "5000.00"),
             U._line("DDS", "X-003", "DS-900", "-2200.00")]
    inv = U._dds("X", lines)
    o = _o([inv], inv)
    # lower bound: services 300,000.00 -> DS-900 -2,000.00 -> net 298,000.00 -> VAT 44,700.00 -> 342,700.00; no upper bound
    assert o["formed"] is False and o["expected_total"] == D("342700.00")
    assert o["expected_bounds"] == ["342700.00", None] and o["flagged"] == 0 and o["confidence"] == D("0.30")
    lines[1] = _unvalued("X-002", "MW-310", "5000.00", findings=["report_missing"])      # a failed check on the line
    inv = U._dds("X", lines)
    o = _o([inv], inv)
    assert o["flagged"] == 1 and o["expected_total"] == D("342700.00")


def test_g5b02_bounded_unvalued_line_and_civil_partial():
    U = _unit()
    # an unvalued line whose admissible values are known (an open reading left unfixed) carries min..max bounds
    k, v, g = U._line("CW", "P-02", "A.13.010", "100.00", alts={"Q4:A": "90.00", "Q4:B": None})
    lines = [U._line("CW", "P-01", "A.11.010", "50.00", "50.00"), (k, v, g), _unvalued("P-03", "A.12.010", "10.00")]
    inv = U.Invoice("CW", "P", {"application_total": D("160.00"), "retention": D("8.00"), "net_payable": D("152.00"),
                                "adjustment": D("0"), "retention_released": D("0")}, lines)
    o = _o([inv], inv)
    assert o["formed"] is False and o["expected_bounds"][1] is None and D(o["expected_bounds"][0]) == o["expected_total"]
    assert o["expected_total"] not in (D("160.00"),)


def test_g5b02_missing_billed_amount_is_not_a_zero_contract_value():
    U = _unit()
    k, v, g = U._line("CW", "M-01", "A.11.010", "0.00", "100.00")
    v["amount"] = None
    inv = U.Invoice("CW", "M", {"application_total": D("100.00"), "retention": D("5.00"), "net_payable": D("95.00"),
                                "adjustment": D("0"), "retention_released": D("0")}, [(k, v, g)])
    o = _o([inv], inv)
    assert o["expected_total"] == D("100.00") and o["contract_total"] == D("100.00") and o["flagged"] == 1


def test_g5b02_z4_unformed_branch_fails_with_prior_g5(monkeypatch):
    import verify_g5 as vg5
    assert vg5.unformed_branch_errors() == []
    old5 = prior_module(PRIOR_G5, "audit/g5_outcomes.py", "g5_outcomes_prior_g5")
    monkeypatch.setattr(vg5, "Engine", old5.Engine)
    errs = vg5.unformed_branch_errors()
    assert any("moves with the billed amount" in x for x in errs) and any("exports 2117.50" in x for x in errs)



# ---------------------------------------------------------------------------------------------------------- G5-B03
def _cw_inv(no, adj="0.00", rel="0.00", total="100.00"):
    U = _unit()
    t = D(total)
    ret = (t * D("0.05")).quantize(D("0.01"), rounding="ROUND_DOWN")
    head = {"application_total": t, "retention": ret, "adjustment": D(adj), "retention_released": D(rel),
            "net_payable": t + D(adj) - ret + D(rel)}
    return U.Invoice("CW", no, head, [U._line("CW", f"{no}-01", "A.11.010", total, total)])


def _pay(invs, target, a3=None, release=None):
    U = _unit()
    e = U._engine(invs, a3=a3 or {"CW": {}, "DDS": {}})
    e.release = release or {}
    return e.outcome(target)


A3_CW = {"CW": {"Q1:A": ["R1"], "Q1:B": ["R1"], "_total": {"by_reading": {"": {"min": "60508.18", "max": "60508.18"}},
                                                           "lines_not_established": 0}}, "DDS": {}}


def test_g5b03_adjustment_reconciled_to_the_account_not_to_nonzero():
    # recipient R1: one cent does not discharge a 60,508.18 adjustment (the review's PA-00443 perturbation)
    o = _pay([_cw_inv("R1", adj="0.01")], _cw_inv("R1", adj="0.01"), a3=A3_CW)
    assert o["flagged"] == 1 and "adjustment_differs" in o["findings"] and o["error_category"] == "adjustment"
    o = _pay([_cw_inv("R1", adj="60508.18")], _cw_inv("R1", adj="60508.18"), a3=A3_CW)
    assert o["flagged"] == 0
    o = _pay([_cw_inv("R1")], _cw_inv("R1"), a3=A3_CW)
    assert o["flagged"] == 1 and "adjustment_omitted" in o["findings"]
    # not the recipient: an adjustment of 12,345.67 has no account behind it (the review's PA-00047 perturbation)
    o = _pay([_cw_inv("P47", adj="12345.67")], _cw_inv("P47", adj="12345.67"), a3=A3_CW)
    assert o["flagged"] == 1 and "adjustment_unsupported" in o["findings"]


def test_g5b03_zero_account_is_not_an_omission():
    a3 = {"CW": {"Q1:A": ["R1"], "Q1:B": ["R1"], "_total": {"by_reading": {"": {"min": "0.00", "max": "0.00"}},
                                                            "lines_not_established": 0}}, "DDS": {}}
    assert _pay([_cw_inv("R1")], _cw_inv("R1"), a3=a3)["flagged"] == 0
    rel = {"recipient": "R1", "released": "0.00"}
    assert _pay([_cw_inv("R1")], _cw_inv("R1"), release=rel)["flagged"] == 0     # correctly zero release account


def test_g5b03_release_reconciled_to_amount_recipient_and_tie():
    rel = {"recipient": "R1", "released": "192.49"}
    assert _pay([_cw_inv("R1", rel="192.49")], _cw_inv("R1", rel="192.49"), release=rel)["flagged"] == 0
    o = _pay([_cw_inv("R1", rel="0.01")], _cw_inv("R1", rel="0.01"), release=rel)        # PA-00678-style partial
    assert o["flagged"] == 1 and "release_differs" in o["findings"]
    o = _pay([_cw_inv("P47", rel="12345.67")], _cw_inv("P47", rel="12345.67"), release=rel)   # not the recipient
    assert o["flagged"] == 1 and "release_unsupported" in o["findings"]
    # CW-H10 tie: PA-9A003 and PA-9A004 submitted the same first day after completion; both show zero release
    tie = {"recipient": {"tie": ["T3", "T4"]}, "released": "192.49"}
    for no in ("T3", "T4"):
        o = _pay([_cw_inv("T3"), _cw_inv("T4")], _cw_inv(no), release=tie)
        assert o["flagged"] == 1 and o["confidence"] == D("0.50") and "release_omitted" in o["findings"]
    # a release range (readings disagree on the amount) inside which the billed figure lies is not established
    rng = {"recipient": "R1", "released": {"by_reading": {"": {"min": "96.15", "max": "192.49"}}}}
    o = _pay([_cw_inv("R1", rel="150.00")], _cw_inv("R1", rel="150.00"), release=rng)
    assert o["flagged"] == 0 and "release_not_established" in o["not_established"]


def test_g5b03_control_prior_g5_checks_presence_only():
    old5 = prior_module(PRIOR_G5, "audit/g5_outcomes.py", "g5_outcomes_prior_g5")
    U = _unit()

    def run(inv, a3=None, release=None):
        e = old5.Engine.__new__(old5.Engine)
        e.policy, e.inv, e.stands, e.inv_date = old5.Policy(), {"CW": {inv.id: inv}, "DDS": {}}, {}, {}
        e.a3, e.release = a3 or {"CW": {}, "DDS": {}}, release or {}
        return e.outcome(inv)
    assert run(_cw_inv("R1", adj="0.01"), a3=A3_CW)["flagged"] == 0                     # one cent passes
    assert run(_cw_inv("P47", adj="12345.67"), a3=A3_CW)["flagged"] == 0                 # unsupported adjustment passes
    assert run(_cw_inv("T3"), release={"recipient": {"tie": ["T3", "T4"]}, "released": "192.49"})["flagged"] == 0


def test_g5b03_drilling_adjustment_path():
    U = _unit()
    a3 = {"CW": {}, "DDS": {"Q1:A": ["DR1"], "Q1:B": ["DR1"], "_total": {"by_reading": {"": {"min": "1234.56", "max": "1234.56"}},
                                                                         "lines_not_established": 0}}}
    ok = U._dds("DR1", [U._line("DDS", "DR1-001", "DD-101", "1000.00", "1000.00")], header_extra={"adjustment": D("1234.56")})
    assert _pay([ok], ok, a3=a3)["flagged"] == 0
    bad = U._dds("DR1", [U._line("DDS", "DR1-001", "DD-101", "1000.00", "1000.00")], header_extra={"adjustment": D("1.00")})
    o = _pay([bad], bad, a3=a3)
    assert o["flagged"] == 1 and "adjustment_differs" in o["findings"]
    other = U._dds("DX", [U._line("DDS", "DX-001", "DD-101", "1000.00", "1000.00")], header_extra={"adjustment": D("5.00")})
    assert "adjustment_unsupported" in _pay([other], other, a3=a3)["findings"]


def test_g5b03_payment_oracle_passes_fixed_and_fails_prior_g5():
    import verify_g5 as vg5
    U = _unit()
    rel = {"recipient": "R1", "released": "192.49"}
    invs = [_cw_inv("R1", rel="0.01", adj="0.01"), _cw_inv("P47", adj="12345.67", rel="12345.67")]
    e = U._engine(invs, a3=A3_CW)
    e.release = rel
    out = {i.id: e.outcome(i) for i in invs}
    assert vg5.payment_errors(out, e) == []
    old5 = prior_module(PRIOR_G5, "audit/g5_outcomes.py", "g5_outcomes_prior_g5")
    e2 = old5.Engine.__new__(old5.Engine)
    e2.policy, e2.inv, e2.stands, e2.inv_date, e2.a3, e2.release = old5.Policy(), {"CW": {i.id: i for i in invs}, "DDS": {}}, \
        {}, {}, A3_CW, rel
    out2 = {i.id: e2.outcome(i) for i in invs}
    errs = vg5.payment_errors(out2, e2)
    assert any("no reading makes the recipient" in x for x in errs) and any("not the 45A recipient" in x for x in errs)
    assert any("R1: adjustment 0.01 outside the account [60508.18, 60508.18]" in x for x in errs)


# ---------------------------------------------------------------------------------------------------------- G5-B05
def _ds_inv(no, ds_amounts, svc="300000.00"):
    """Services of `svc` (one line, correctly priced) and the given DS-900 lines; header coherent with the lines."""
    U = _unit()
    lines = [U._line("DDS", f"{no}-001", "DD-101", svc, svc)]
    lines += [U._line("DDS", f"{no}-{i + 2:03d}", "DS-900", a) for i, a in enumerate(ds_amounts)]
    return U._dds(no, lines)


def test_g5b05_ds900_single_negative_charge():
    ok = _ds_inv("S1", ["-2000.00"])                     # 4% of (300,000 - 250,000), one negative charge
    assert _o([ok], ok)["flagged"] == 0
    split = _ds_inv("S2", ["-1000.00", "-1000.00"])      # the review's MDS-00018 pattern: the sum right, two charges
    o = _o([split], split)
    assert o["flagged"] == 1 and "discount_split" in o["findings"] and o["error_category"] == "discount"
    assert o["expected_total"] == D("342700.00")         # judged total unchanged: a breach with no monetary effect
    offset = _ds_inv("S3", ["-2000.00", "500.00", "-500.00"])
    o = _o([offset], offset)
    assert {"discount_split", "discount_sign"} <= {f.split("@")[0] for f in o["findings"]}
    omitted = _ds_inv("S4", [])
    assert "discount" in _o([omitted], omitted)["findings"]
    wrong = _ds_inv("S5", ["-2000.01"])
    assert "discount" in _o([wrong], wrong)["findings"]
    positive = _ds_inv("S6", ["2000.00"])
    assert {"discount", "discount_sign"} <= {f.split("@")[0] for f in _o([positive], positive)["findings"]}


def test_g5b05_threshold_and_rounding():
    at = _ds_inv("T1", [], svc="250000.00")               # exactly the threshold: Cl.38 'exceeds' - no charge due
    assert _o([at], at)["flagged"] == 0
    at_charged = _ds_inv("T2", ["-0.04"], svc="250001.00")
    assert _o([at_charged], at_charged)["flagged"] == 0  # 4% of 1.00 = 0.04
    half = _ds_inv("T3", ["-0.02"], svc="250000.50")     # 4% of 0.50 = 0.02
    assert _o([half], half)["flagged"] == 0
    zero_line = _ds_inv("T4", ["0.00"], svc="200000.00")  # a zero DS-900 line where none is due is no charge
    assert _o([zero_line], zero_line)["flagged"] == 0


def test_g5b05_control_prior_g5_passes_the_split():
    old5 = prior_module(PRIOR_G5, "audit/g5_outcomes.py", "g5_outcomes_prior_g5")
    inv = _ds_inv("S2", ["-1000.00", "-1000.00"])
    e = old5.Engine.__new__(old5.Engine)
    e.policy, e.inv, e.stands, e.inv_date, e.a3, e.release = old5.Policy(), {"CW": {}, "DDS": {"S2": inv}}, {}, {}, \
        {"CW": {}, "DDS": {}}, {}
    assert e.outcome(inv)["flagged"] == 0



def test_g5b05_ds900_oracle_passes_fixed_and_fails_prior_g5():
    import verify_g5 as vg5
    invs = [_ds_inv("S2", ["-1000.00", "-1000.00"]), _ds_inv("S6", ["2000.00"]), _ds_inv("S1", ["-2000.00"])]
    U = _unit()
    e = U._engine(invs)
    assert vg5.ds900_errors({i.id: e.outcome(i) for i in invs}, e) == []
    old5 = prior_module(PRIOR_G5, "audit/g5_outcomes.py", "g5_outcomes_prior_g5")
    e2 = old5.Engine.__new__(old5.Engine)
    e2.policy, e2.inv, e2.stands, e2.inv_date, e2.a3, e2.release = old5.Policy(), {"CW": {}, "DDS": {i.id: i for i in invs}}, \
        {}, {}, {"CW": {}, "DDS": {}}, {}
    errs = vg5.ds900_errors({i.id: e2.outcome(i) for i in invs}, e2)
    assert any("S2: 2 DS-900 charges" in x for x in errs) and any("S6: a positive DS-900" in x for x in errs)


# ---------------------------------------------------------------------------------------------------------- G5-B06
def h_b06_out_of_term(rate="74.50", amount="44700.00"):
    # one B.22.010 line, 600 m2 at 74.50 = 44,700.00, dated 2 October 2026 - after completion as extended (30 September)
    return cw_hist("CW-G5B06", [("2026-10-02", "600", rate, amount)], adate="2026-10-10")


def test_g5b06_out_of_term_keeps_its_cause_without_a_rate_category():
    import verify_g5 as vg5
    st, out = _cw_outcome(h_b06_out_of_term())
    o = out["PA-97001"]
    assert o["flagged"] == 1 and o["error_category"] == "term" and o["expected_total"] == D("0.00")
    assert not [f for f in o["findings"] if f.startswith(("rate_differs", "release_omitted"))]
    assert [x for x in vg5.z4({"PA-97001": o}) if "PA-97001" in x] == []       # a monetary term consequence, not procedural


def test_g5b06_a_real_rate_error_is_kept():
    st, out = _cw_outcome(h_b06_out_of_term(rate="70.00", amount="42000.00"))
    o = out["PA-97001"]
    assert "rate_differs@PA-97001-01" in o["findings"] and "term" in o["error_category"]


def test_g5b06_control_prior_g5_invents_rate():
    old4 = prior_module(PRIOR_G4, "audit/g4_cw.py", "g4_cw_prior_g4")
    old5 = prior_module(PRIOR_G5, "audit/g5_outcomes.py", "g5_outcomes_prior_g5")
    st, out = _cw_outcome(h_b06_out_of_term(), old4, old5)
    assert "rate_differs@PA-97001-01" in out["PA-97001"]["findings"]


def test_g5b06_attribution_oracle_passes_fixed_and_fails_prior_g5():
    import verify_g5 as vg5
    from audit import g5_outcomes
    w, res, st = F.run(h_b06_out_of_term())
    e = g5_outcomes.Engine(w, st)
    assert vg5.attribution_errors({"PA-97001": e.outcome(e.inv["CW"]["PA-97001"])}, e) == []
    old4 = prior_module(PRIOR_G4, "audit/g4_cw.py", "g4_cw_prior_g4")
    old5 = prior_module(PRIOR_G5, "audit/g5_outcomes.py", "g5_outcomes_prior_g5")
    st_old = {"CW": old4.run(w, res["CW"]), "DDS": g4_dds.run(w, res["DDS"])}
    e_old = old5.Engine(w, st_old)
    errs = vg5.attribution_errors({"PA-97001": e_old.outcome(e_old.inv["CW"]["PA-97001"])}, e_old)
    assert any("admissible contract rate" in x for x in errs)


def test_g5b01_export_e_flag_never_depends_on_which_admissible_value_the_bill_matches():
    import verify_g5 as vg5
    U = _unit()
    # the same invoice billed exactly at Standard, then exactly at HPHT: both admissible - same flag and confidence,
    # the same contract total, each exporting its own total (EXPORT-E)
    a = U._dds("E", [_cls_line("E-001", "1000.00")], cls="HPHT")
    b = U._dds("E", [_cls_line("E-001", "1325.00")], cls="HPHT")
    oa, ob = _o([a], a), _o([b], b)
    assert (oa["flagged"], oa["confidence"], oa["contract_total"]) == (ob["flagged"], ob["confidence"], ob["contract_total"])
    assert oa["expected_total"] == D("1150.00") and ob["expected_total"] == D("1523.75")
    assert vg5.export_e_errors({"E": oa}, {"E": ob}, {"E": D("1523.75")}) == []
    # prior_g5 (the invoice's statement selects the class): the flag moves with the admissible value the bill matches
    old5 = prior_module(PRIOR_G5, "audit/g5_outcomes.py", "g5_outcomes_prior_g5")

    def g5(inv):
        e = old5.Engine.__new__(old5.Engine)
        e.policy, e.inv, e.stands, e.inv_date, e.a3, e.release = old5.Policy(), {"CW": {}, "DDS": {inv.id: inv}}, {}, {}, \
            {"CW": {}, "DDS": {}}, {}
        o = e.outcome(inv)
        o["contract_total"] = o["expected_total"]
        return o
    ga, gb = g5(old5.Invoice("DDS", "E", a.header, a.lines)), g5(old5.Invoice("DDS", "E", b.header, b.lines))
    assert any("flag/confidence" in x for x in vg5.export_e_errors({"E": ga}, {"E": gb}, {"E": D("1523.75")}))


def test_g5b01_robust_findings_per_ground_line():
    # PA-00659 pattern: displayed rate = the G4 rate, amount not quantity x rate - no ground makes the line right; the
    # findings reported are those under every ground (the established arithmetic), never one ground's rate difference
    U = _unit()
    k, v, g = U._line("CW", "PG-01", "C.32.010", "7667.50", alts={"ground:G2": "7200.00", "ground:G4": "7672.50"},
                      findings=["amount_arithmetic"])
    for kk, rate in (("ground:G2", "2400.00"), ("ground:G4", "2557.50")):
        g.r.alternatives[kk]["unit_rate"] = D(rate)
    v.update({"rate_applied": D("2557.50"), "quantity": D("3")})
    inv = U.Invoice("CW", "PG", {"application_total": D("7667.50"), "retention": D("383.37"), "net_payable": D("7284.13"),
                                 "adjustment": D("0"), "retention_released": D("0")}, [(k, v, g)])
    o = _o([inv], inv)
    assert o["flagged"] == 1 and "rate_differs@PG-01" not in o["findings"] and "amount_arithmetic@PG-01" in o["findings"]
