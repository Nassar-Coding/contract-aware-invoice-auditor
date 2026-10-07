"""G4 exit checks (tools/verify_g4.py): each check passes on a correct state and FAILS on a controlled defect (negative
controls). A check that cannot fail proves nothing, so every check Y1-Y9 has at least one control here. The controls run
on the committed multi-invoice histories (fast, exact); tools/verify_g4.py runs the same checks on the population."""
import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

import g4_histories as H
import g4_history_compare as HC
import verify_g4 as vg
from audit import g3_cw, g3_dds, g4_cw
from audit.g3_core import result_keys

ROOT = Path(__file__).resolve().parents[1]


def _world(hid):
    w, _g3, _st = HC.run_history(H.load_packets()[hid])
    res = {"CW": g3_cw.run(w), "DDS": g3_dds.run(w)}
    for lk in ("cw_lines", "dds_lines"):
        for k, row in zip(result_keys(w.claims.rows[lk]), w.claims.rows[lk]):
            vg._CLAIMS[k] = row.values
    vg.load_heads(w)
    return w, res


@pytest.fixture(scope="module")
def cw08():
    return _world("CW-H08")


@pytest.fixture(scope="module")
def cw02():
    return _world("CW-H02")


@pytest.fixture(scope="module")
def dds03():
    return _world("DDS-H03")


@pytest.fixture(scope="module")
def dds06():
    return _world("DDS-H06")


@pytest.fixture(scope="module")
def cmp_():
    return HC.compare()


@pytest.fixture(scope="module")
def disp():
    return yaml.safe_load((ROOT / "verification" / "g4" / "history_dispositions.yaml").read_text())["dispositions"]


def _st(wr):
    w, res = wr
    return vg.run_state(w, res)


# ---------------------------------------------------------------------------------------------------------- Y1
def test_y1_passes(cmp_, disp):
    assert vg.y1(cmp_, disp) == []
    assert vg.comparison_reproduces(cmp_) == []


def test_y1_fails_without_settlements(cmp_):
    errs = vg.y1(cmp_, {})
    assert any("DDS-H01|r5|" in e and "no settlement" in e for e in errs)


def test_y1_fails_on_a_stale_settlement(cmp_, disp):
    d = dict(disp)
    d["CW-H01|r1|PA-91001-01: amount: engine ['1.00'] reader ['2.00']"] = {"settled": "engine", "basis": "x"}
    assert any("stale" in e for e in vg.y1(cmp_, d))


def test_y1_fails_when_the_engine_value_changes(cmp_, disp):
    c = copy.deepcopy(cmp_)
    c["CW-H01"]["readers"]["r1"].append("PA-91001-01: amount: engine ['36356.01'] reader ['36356.00']")
    assert any("no settlement" in e for e in vg.y1(c, disp))


def test_y1_fails_when_readers_came_after_the_engine(cmp_, disp):
    order = {"verification/g4/histories/packet_cw.jsonl": "a", "verification/g4/histories/packet_dds.jsonl": "a"}
    errs = vg.y1(cmp_, disp, first_commit=lambda p: order.get(p, "z" if "expected" in p else "m"),
                 before=lambda a, b: a < b)
    assert any("was not committed before audit/g4_cw.py" in e for e in errs)


def test_y1_fails_on_a_reader_with_no_result(cmp_, disp):
    c = copy.deepcopy(cmp_)
    c["CW-H01"]["readers"] = {}
    assert any("no independent expected result" in e for e in vg.y1(c, disp))


# ---------------------------------------------------------------------------------------------------------- Y2
def test_y2_passes(cw08, cw02, dds03):
    for wr in (cw08, cw02, dds03):
        assert vg.y2(wr[0], wr[1], _st(wr)) == []


def test_y2_fails_when_row_order_decides_the_standing_measurement(cw08, monkeypatch):
    w, res = cw08
    st = _st(cw08)
    orig = g4_cw.duplicates

    def by_row(lines, w_, T, st_):
        out = orig(lines, w_, T, st_)
        # defect: the first row of each group stands, whatever was submitted first
        first = {}
        for ln in lines:
            d = out.get(ln.key)
            if d:
                first.setdefault(d["group"], ln.key)
        return {k: {**d, "standing": first[d["group"]], "ties": None} for k, d in out.items()}
    monkeypatch.setattr(g4_cw, "duplicates", by_row)
    assert vg.y2(w, res, st) != []


def test_y2_fails_on_a_ledger_out_of_date_order(cw02):
    st = _st(cw02)
    lk = next(k for k, v in st["CW"].ledgers.items() if len(v) > 1 and v[0]["date"] != v[-1]["date"])
    st["CW"].ledgers[lk] = list(reversed(st["CW"].ledgers[lk]))
    assert any("execution-date order" in e for e in vg.ledger_order_errors(st["CW"]))


def test_y2_fails_when_only_one_order_is_carried(cw02):
    st = _st(cw02)
    g = next(g for g in st["CW"].lines.values() if any("order@" in k for k in g.r.alternatives))
    keep = next(k for k in g.r.alternatives if "order@" in k)
    g.r.alternatives = {keep: g.r.alternatives[keep]}
    assert any("one order carried" in e for e in vg.ledger_order_errors(st["CW"]))


# ---------------------------------------------------------------------------------------------------------- Y3
def test_y3_passes(cw02, dds06):
    assert vg.y3(_st(cw02)["CW"], _st(dds06)["DDS"]) == []


def test_y3_fails_when_a_ledger_does_not_restart(cw02, dds06):
    st = _st(cw02)
    lk = next(iter(st["CW"].ledgers))
    st["CW"].ledgers[lk][0]["before"] = ["5", "5"]
    assert any("does not start at zero" in e for e in vg.y3(st["CW"], _st(dds06)["DDS"]))


def test_y3_fails_when_the_count_skips_a_measurement(cw02, dds06):
    st = _st(cw02)
    lk = next(k for k, v in st["CW"].ledgers.items() if len(v) > 2)
    st["CW"].ledgers[lk][2]["before"] = [str(Decimal(st["CW"].ledgers[lk][2]["before"][0]) - 1)] * 2
    assert any("the ledger before it sums" in e for e in vg.y3(st["CW"], _st(dds06)["DDS"]))


def test_y3_fails_on_a_contract_year_under_no_reset(cw02, dds06):
    st = _st(cw02)
    lk = next(k for k in st["CW"].ledgers if "Q12:B" in k)
    st["CW"].ledgers[lk.replace("CY1", "CY2")] = []
    assert any("new Contract Year under the no-reset" in e for e in vg.y3(st["CW"], _st(dds06)["DDS"]))


def test_y3_fails_when_footage_does_not_restart(cw02, dds06):
    st = _st(dds06)
    well, acc = next((w, a) for w, a in st["DDS"].footage.items() if a)
    k = next(k for k in acc if k.endswith("Q14:A|Q11:A"))
    lo, hi = acc[k]
    acc[k] = [str(Decimal(lo) + 7), hi]
    assert any("the unique-metre recount gives" in e for e in vg.footage_reset_errors(st["DDS"]))


# ---------------------------------------------------------------------------------------------------------- Y4
def test_y4_passes(cw08, dds03):
    assert vg.y4(_st(cw08)["CW"], _st(dds03)["DDS"]) == []


def test_y4_fails_when_two_charges_stand(cw08, dds03):
    st = _st(dds03)
    grp = next(g for g in st["DDS"].groups if len(g.get("candidates", [])) > 1)
    lines = {g.g3.line_ref: g for g in st["DDS"].lines.values()}
    for ref in grp["candidates"]:
        for v in lines[ref].r.alternatives.values():
            v["amount"] = Decimal("1.00")
    assert any("charges stand" in e for e in vg.y4(_st(cw08)["CW"], st["DDS"]))


def test_y4_fails_when_a_later_duplicate_keeps_its_value(cw08, dds03):
    st = _st(cw08)
    d = st["CW"].duplicates[0]
    lines = {g.g3.line_ref: g for g in st["CW"].lines.values()}
    for m in d["members"]:
        lines[m].r.payable, lines[m].r.amount = True, Decimal("389.00")
    assert any("charges stand" in e for e in vg.y4(st["CW"], _st(dds03)["DDS"]))


def test_y4_fails_on_a_charge_off_its_day(dds06):
    wr = _world("DDS-H04")
    st = _st(wr)
    grp = next(g for g in st["DDS"].groups if g.get("not_on_day"))
    ref = next(iter(grp["not_on_day"]))
    g = next(g for g in st["DDS"].lines.values() if g.g3.line_ref == ref)
    g.r.payable = True
    assert any("off its contractual day" in e for e in vg.y4(st["CW"], st["DDS"]))


def test_y4_fails_above_a_daily_limit(dds03):
    wr = _world("CW-H05")
    st = _st(wr)
    g = next(g for g in st["CW"].lines.values() if any(x.finding == "above_daily_limit" for x in g.state))
    g.r.allowed_quantity = g.g3.allowed_quantity
    assert any("above the daily limitation" in e for e in vg.limit_errors(st["CW"], st["DDS"]))


def test_y4_fails_when_a3_is_posted_on_several_documents():
    wr = _world("CW-H09")
    st = _st(wr)
    a = st["CW"].adjustments[0]
    k = next(iter(a["recipient"]))
    a["recipient"][k] = ["PA-99003", "PA-99004"]
    assert any("several documents" in e for e in vg.a3_posting_errors(st["CW"]))


# ---------------------------------------------------------------------------------------------------------- Y5
def test_y5_passes(cw02):
    w, res = cw02
    assert vg._diff_states(vg.snapshot_state(_st(cw02)), vg.snapshot_state(vg.run_state(w, res)), "x") == []
    assert vg.history_removal_errors() == []


def test_y5_fails_on_a_stale_rerun(cw02):
    w, res = cw02
    st = _st(cw02)

    def stale(w_, res_, order, a3_rerun=True):
        s = vg.run_state(w_, res_, order, a3_rerun)
        g = next(iter(s["CW"].lines.values()))
        g.r.amount = (g.r.amount or Decimal(0)) + 1
        return s
    assert vg.y5(w, res, st, runner=stale) != []


def test_y5_correction_replays_and_fails_on_a_cached_state(monkeypatch):
    # on a history with a long ledger: the replay passes, and a cached (non-replaying) engine fails it
    wr = _world("CW-H01")
    w, res = wr
    assert vg.correction_replay_errors(w, res) == []
    cache = {}
    orig = g4_cw.run

    def cached(w_, r_, T=None, a3_rerun=True):
        if "st" not in cache:
            cache["st"] = orig(w_, r_, T, a3_rerun)
        return copy.deepcopy(cache["st"])
    monkeypatch.setattr(g4_cw, "run", cached)
    assert vg.correction_replay_errors(w, res) != []


# ---------------------------------------------------------------------------------------------------------- Y6
def test_y6_passes(cw08, dds03):
    assert vg.y6(_st(cw08)["CW"], _st(dds03)["DDS"]) == []


def test_y6_fails_on_a_line_counted_twice_in_a_ledger(cw02, dds03):
    st = _st(cw02)
    lk = next(iter(st["CW"].ledgers))
    st["CW"].ledgers[lk].append(dict(st["CW"].ledgers[lk][0]))
    assert any("counted twice" in e for e in vg.y6(st["CW"], _st(dds03)["DDS"]))


def test_y6_fails_on_two_standing_measurements(cw08, dds03):
    st = _st(cw08)
    for g in st["CW"].lines.values():
        if g.r.payable is False and any(x.finding == "duplicate_measurement" for x in g.state):
            g.r.payable, g.r.amount = True, Decimal("389.00")
    assert any("charges stand" in e for e in vg.y6(st["CW"], _st(dds03)["DDS"]))


def test_y6_fails_on_p23_beside_an_exclusion(dds03):
    wr = _world("CW-H11")
    st = _st(wr)
    x = st["CW"].p23[0]
    x["status"] = "posted"
    assert any("also excluded" in e for e in vg.y6(st["CW"], _st(dds03)["DDS"]))


def test_y6_a3_account_is_separate_and_fails_when_embedded():
    w, res = _world("CW-H09")
    st = vg.run_state(w, res)
    without = vg.snapshot_state(vg.run_state(w, res, a3_rerun=False))
    assert vg.y6(st["CW"], st["DDS"], without) == []
    a = st["CW"].adjustments[0]
    ref, v = next(iter(a["by_line"].items()))
    diff = v["difference"] if isinstance(v["difference"], str) else next(iter(v["difference"].values()))
    g = next(g for g in st["CW"].lines.values() if g.g3.line_ref == ref)
    g.r.amount = g.r.amount + Decimal(diff)          # defect: the difference put into the line itself
    assert any("A3 account" in e for e in vg.y6(st["CW"], st["DDS"], without))
    st["CW"].adjustments.append(copy.deepcopy(a))               # defect: the account posted twice
    assert any("posted more than once" in e for e in vg.y6(st["CW"], st["DDS"], without))


# ---------------------------------------------------------------------------------------------------------- Y7
def test_y7_passes(cw02, dds06):
    assert vg.y7(_st(cw02)["CW"], _st(dds06)["DDS"]) == []


def test_y7_fails_on_a_tampered_trace(cw02, dds06):
    st = _st(cw02)
    g = next(g for g in st["CW"].lines.values() if g.changed and g.r.alternatives)
    v = next(iter(g.r.alternatives.values()))
    step = next(s for s in v["trace"] if s["op"] in ("part", "amount"))
    step["value"] = str(Decimal(step["value"]) + 1)
    assert vg.y7(st["CW"], _st(dds06)["DDS"]) != []


def test_y7_fails_when_the_value_is_not_the_trace(cw02, dds06):
    st = _st(cw02)
    g = next(g for g in st["CW"].lines.values() if g.changed and g.r.alternatives)
    v = next(iter(g.r.alternatives.values()))
    v["amount"] = v["amount"] + 1
    assert any("trace ends at" in e for e in vg.y7(st["CW"], _st(dds06)["DDS"]))


# ---------------------------------------------------------------------------------------------------------- Y8
def _g3_json(res):
    return {c: {k: json.dumps(r.to_json(), sort_keys=True, default=str) for k, r in rs.items()} for c, rs in res.items()}


def test_y8_fails_when_g4_modifies_g3(cw02):
    w, res = cw02
    before = _g3_json(res)
    st = _st(cw02)
    k = next(iter(res["CW"]))
    res["CW"][k].amount = Decimal("1.23")
    try:
        errs = vg.y8(w, res, st, before, committed={})
    finally:
        res["CW"][k].amount = json.loads(before["CW"][k])["amount"] and Decimal(json.loads(before["CW"][k])["amount"])
    assert any("was modified by G4" in e for e in errs)


def test_y8_fails_on_an_unanswered_dependency(cw02):
    w, res = cw02
    st = _st(cw02)
    g = next(g for g in st["CW"].lines.values() if any(d.startswith("band_state") for d in g.g3.g4_dependencies))
    g.state = [x for x in g.state if x.family != "band"]
    errs = vg.y8(w, res, st, _g3_json(res), committed={})
    assert any("without a G4 state check" in e for e in errs)


def test_y8_fails_when_outputs_do_not_reproduce(cw02):
    w, res = cw02
    errs = vg.y8(w, res, _st(cw02), _g3_json(res), committed={})
    assert any("does not reproduce" in e for e in errs)


def test_y8_fails_on_a_g5_construct(tmp_path):
    p = tmp_path / "g4_x.py"
    p.write_text("def write(): open('audit_results.csv', 'w')\n")
    assert vg.text_errors([p]) != []
    assert vg.text_errors() == []


# ---------------------------------------------------------------------------------------------------------- Y9
def _regs():
    load = lambda n: yaml.safe_load((ROOT / "spec" / n).read_text())  # noqa: E731
    scopes = json.loads((ROOT / "verification" / "g4" / "decision_scopes.json").read_text())
    return load("g4_decisions.yaml"), scopes, load("open_questions.yaml"), load("carried_items.yaml"), H.load_packets()


def test_y9_passes():
    assert vg.y9(*_regs()) == []


def test_y9_fails_on_a_decision_without_pages_or_history():
    d, s, q, c, p = _regs()
    d["decisions"][0]["basis"] = ["Sch 4 Part 3"]
    d["decisions"][1]["histories"] = ["CW-H99"]
    d["decisions"][2]["scope_key"] = "nope"
    errs = vg.y9(d, s, q, c, p)
    assert any("basis without pages" in e for e in errs)
    assert any("does not exist" in e for e in errs)
    assert any("not computed" in e for e in errs)


def test_y9_fails_on_a_question_still_blocking_g4():
    d, s, q, c, p = _regs()
    qq = next(x for x in q["questions"] if x["id"] == "Q12")
    qq["status"] = "open"            # the defect: a G4 question left open, still blocking G4, without its disposition
    qq["blocks"] = "G4"
    qq.pop("g4_disposition")
    errs = vg.y9(d, s, q, c, p)
    assert any("still blocks G4" in e for e in errs)
    assert any("without a G4 disposition" in e for e in errs)


def test_y9_fails_on_a_g4_carried_item_without_treatment():
    d, s, q, c, p = _regs()
    c["items"].append({"id": "CI-X", "owner_gate": "G4"})
    assert any("CI-X" in e for e in vg.y9(d, s, q, c, p))
