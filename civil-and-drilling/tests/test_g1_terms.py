"""G1 terms: completeness against the table inventory, internal consistency of the transcribed tables, and the
contracts' own worked examples recomputed independently with exact Decimal arithmetic.

Expected values are taken from the printed examples and the contract text, computed here by hand-written
arithmetic; no production valuation code exists or is called."""
import datetime as dt
import json
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal as D
from pathlib import Path

import pytest

import spec_lib as sl

ROOT = Path(__file__).resolve().parents[1]
CW = sl.load_terms("CW")["tables"]
DDS = sl.load_terms("DDS")["tables"]
INST = {i["id"]: i for i in sl.load_instruments()["instruments"]}
INV = json.loads((ROOT / "source" / "inventory.json").read_text())
C = D("0.01")


def col(table, name):
    i = table["columns"].index(name)
    return [r[i] for r in table["rows"]]


def rate(table, code, name="rate"):
    i = table["columns"].index(name)
    return sl.dec(next(r[i] for r in table["rows"] if r[0] == code))


# ---------------------------------------------------------------- completeness (table inventory)
def test_cw_inventory_counts():
    assert len(CW["CW.T01_SCH1"]["rows"]) == 60
    assert set(col(CW["CW.T01_SCH1"], "code")) == set(INV["civil"]["item_codes"])
    assert len(CW["CW.T09_GROUND_ITEMS"]["rows"]) == 15
    assert len(CW["CW.T10_NIGHT"]["rows"]) == 13 and len(CW["CW.T11_REST"]["rows"]) == 4
    assert len(CW["CW.T12_BANDS"]["rows"]) == 8
    assert len(CW["CW.T13_DAILY_LIMITS"]["rows"]) == 9
    assert len(CW["CW.T16_RECORDS"]["rows"]) == 17
    assert len(CW["CW.T15_SURVEYED"]["rows"]) == 3
    assert len(CW["CW.T05_SMI"]["rows"]) == 24 and len(CW["CW.T07_FX"]["rows"]) == 24


def test_dds_inventory_counts():
    codes = col(DDS["DDS.T01_SCH1"], "code")
    assert len(codes) == 38
    assert set(codes) | {"DS-900"} == set(INV["drilling"]["service_codes"])
    assert len(DDS["DDS.T12_STANDBY"]["rows"]) == 29
    assert len(DDS["DDS.T13_DAILY_LIMITS"]["rows"]) == 22
    assert len(DDS["DDS.T14_ONCE_PER_WELL"]["rows"]) == 4
    assert len(DDS["DDS.T18_SCH5_CODES"]["rows"]) == 12
    assert len(DDS["DDS.T20_GLOSSARY"]["rows"]) == 24
    assert len(DDS["DDS.T23_SCH8"]["rows"]) == 38 and col(DDS["DDS.T23_SCH8"], "code") == codes


def test_every_numeric_cell_is_exact_decimal():
    for _, tid, t in sl.all_tables():
        for _, _, v in sl.numeric_cells(t):
            x = sl.dec(v)                      # parsed from the printed string, never via float
            assert x.is_finite() and "e" not in v.lower(), (tid, v)


def test_eligibility_lists_reference_scheduled_codes():
    cw = set(col(CW["CW.T01_SCH1"], "code"))
    for tid in ("CW.T09_GROUND_ITEMS", "CW.T10_NIGHT", "CW.T11_REST", "CW.T12_BANDS", "CW.T13_DAILY_LIMITS",
                "CW.T15_SURVEYED", "CW.T16_RECORDS", "CW.T04_SMI_ITEMS", "CW.T06_USD_ITEMS"):
        assert set(col(CW[tid], "code")) <= cw, tid
    dds = set(col(DDS["DDS.T01_SCH1"], "code"))
    for tid in ("DDS.T09_SECTION_RATED", "DDS.T11_CLASS_RATED", "DDS.T12_STANDBY", "DDS.T13_DAILY_LIMITS",
                "DDS.T14_ONCE_PER_WELL", "DDS.T18_SCH5_CODES", "DDS.T20_GLOSSARY", "DDS.T04_RSI_ITEMS"):
        assert set(col(DDS[tid], "code")) <= dds, tid
    # Sch 2A/2B base figures equal the Schedule 1 figures they qualify
    for code in col(CW["CW.T04_SMI_ITEMS"], "code"):
        assert rate(CW["CW.T04_SMI_ITEMS"], code, "base_rate") == rate(CW["CW.T01_SCH1"], code)
    for code in col(CW["CW.T06_USD_ITEMS"], "code"):
        assert rate(CW["CW.T06_USD_ITEMS"], code, "usd_rate") == rate(CW["CW.T01_SCH1"], code)
    for code in col(DDS["DDS.T04_RSI_ITEMS"], "code"):
        assert rate(DDS["DDS.T04_RSI_ITEMS"], code, "base_rate") == rate(DDS["DDS.T01_SCH1"], code)


# ---------------------------------------------------------------- internal consistency
def test_sch2d_equals_sch6_times_3_75_and_fx_tables_identical():
    for code in col(DDS["DDS.T06_SAR_VALUES"], "code"):
        assert rate(DDS["DDS.T06_SAR_VALUES"], code, "sar_value") == rate(DDS["DDS.T19_SCH6_USD"], code, "usd_value") * D("3.75")
    assert CW["CW.T07_FX"]["rows"] == DDS["DDS.T07_FX"]["rows"]


def test_monthly_tables_are_complete_consecutive_months():
    for t in (CW["CW.T05_SMI"], CW["CW.T07_FX"], DDS["DDS.T05_RSI"], DDS["DDS.T07_FX"]):
        months = [r[0] for r in t["rows"]]
        assert months == [f"{y}-{m:02d}" for y in (2025, 2026) for m in range(1, 13)]
    assert CW["CW.T05_SMI"]["rows"][0][1] == "100.00" and DDS["DDS.T05_RSI"]["rows"][0][1] == "100.00"


def test_rate_previously_payable_chains():
    s1 = {r["code"]: r for r in INST["CW.S1"]["rate_rows"]}
    a1 = {r["code"]: r for r in INST["CW.A1"]["rate_rows"]}
    sch1 = lambda code: rate(CW["CW.T01_SCH1"], code)
    for code, r in s1.items():
        assert sl.dec(r["from"]) == sch1(code)
    assert sl.dec(a1["E.54.010"]["from"]) == sch1("E.54.010")
    assert a1["B.23.010"]["from"] == s1["B.23.010"]["to"]
    assert INST["CW.S2"]["rate_rows"][0]["from"] == INST["CW.S1"]["monthly_rows"]["rows"][-1][1]   # 220.50 = Sep-2025
    for r in INST["CW.A3"]["rate_rows"]:
        assert sl.dec(r["from"]) == sch1(r["code"])
    dsch1 = lambda code: rate(DDS["DDS.T01_SCH1"], code)
    for r in INST["DDS.S1"]["rate_rows"] + INST["DDS.A1"]["rate_rows"]:
        assert sl.dec(r["from"]) == dsch1(r["code"])
    a2 = {r["code"]: r for r in INST["DDS.A2"]["rate_rows"]}
    assert a2["MW-301"]["from"] == next(r["to"] for r in INST["DDS.A1"]["rate_rows"] if r["code"] == "MW-301")
    assert sl.dec(a2["MB-701"]["from"]) == dsch1("MB-701")
    a3 = {r["code"]: r for r in INST["DDS.A3"]["rate_rows"]}
    assert a3["DD-120"]["from"] == INST["DDS.S1"]["rate_rows"][0]["to"]          # 398.50: ignores S2 monthly (OV-DDS-07)
    assert a3["DD-101"]["from"] == next(r["to"] for r in INST["DDS.A1"]["rate_rows"] if r["code"] == "DD-101")


@pytest.mark.parametrize("iid,prev_end,new_end,days", [
    ("CW.A1", "2025-09-27", "2026-03-31", 185), ("CW.A2", "2026-03-31", "2026-09-30", 183),
    ("DDS.A1", "2025-12-31", "2026-06-30", 181), ("DDS.A2", "2026-06-30", "2026-12-31", 184)])
def test_extension_days_as_printed(iid, prev_end, new_end, days):
    i = INST[iid]
    assert (i.get("completion_extended_to") or i.get("expiry_extended_to")) == new_end
    assert (dt.date.fromisoformat(new_end) - dt.date.fromisoformat(prev_end)).days == days == int(i["extension_days_as_printed"])


def test_instruments_issue_order_and_retrospective_flags():
    for contract in ("CW", "DDS"):
        seq = [i for i in INST.values() if i["contract"] == contract]
        assert [i["issued"] for i in seq] == sorted(i["issued"] for i in seq)
        for i in seq:
            assert i.get("retrospective", False) == (i["effective"] < i["issued"])


def test_discount_codes_and_percentages():
    assert INST["CW.S2"]["discount"]["codes"] == INST["CW.A2"]["discount"]["codes"]
    assert (INST["CW.S2"]["discount"]["pct"], INST["CW.A2"]["discount"]["pct"]) == ("5", "8")
    assert INST["DDS.S2"]["discount"]["codes"] == INST["DDS.A2"]["discount"]["codes"]
    assert (INST["DDS.S2"]["discount"]["pct"], INST["DDS.A2"]["discount"]["pct"]) == ("4", "7")


# ---------------------------------------------------------------- worked examples (independent arithmetic)
def half_up(x):
    return x.quantize(C, ROUND_HALF_UP)


def half_even(x):
    return x.quantize(C, ROUND_HALF_EVEN)


def test_cw_appendix_b_worked_example():
    z2 = rate(CW["CW.T02_ZONES"], "Z2", "factor")
    g4 = rate(CW["CW.T08_GROUND_FACTORS"], "G4", "factor")
    sch1 = lambda c: rate(CW["CW.T01_SCH1"], c)
    r1 = half_up(sch1("A.12.020") * z2 * g4)          # single rounding after zone then ground (Cl.27-28)
    r2 = half_up(sch1("A.14.020") * z2)
    r3 = half_up(sch1("C.31.010") * z2)               # example omits 29A indexation (OV-CW-07 / D3)
    ex = CW["CW.T22_APPB_EXAMPLE"]
    assert [r1, r2, r3] == [sl.dec(v) for v in col(ex, "rate")]
    amounts = [D(180) * r1, D(96) * r2, D(48) * r3]
    assert amounts == [sl.dec(v) for v in col(ex, "amount")]
    total = sum(amounts)
    assert total == sl.dec(ex["totals"]["total"])
    retention = (total * D("0.05")).quantize(C, ROUND_DOWN)
    assert retention == sl.dec(ex["totals"]["retention"]) and total - retention == sl.dec(ex["totals"]["net"])
    # the operative indexed rate for May 2025 that D3 applies instead
    smi_may = sl.dec(dict(CW["CW.T05_SMI"]["rows"])["2025-05"])
    assert half_even(sch1("C.31.010") * smi_may / 100) == D("88.44")


def test_dds_appendix_b_worked_example():
    sch1 = lambda c: rate(DDS["DDS.T01_SCH1"], c)
    hpht = rate(DDS["DDS.T10_CLASS_FACTORS"], "HPHT", "factor")
    s1225 = rate(DDS["DDS.T08_SECTION_FACTORS"], '12-1/4"', "factor")
    standby_dd101 = sl.dec(dict(DDS["DDS.T12_STANDBY"]["rows"])["DD-101"]) / 100
    dd101 = half_even(sch1("DD-101") * standby_dd101)                     # Standby: no section factor (17B)
    mw310 = half_even(half_even(sch1("MW-310") * s1225) * hpht)          # example omits 17A indexation (D3)
    dd120 = half_even(half_even(sch1("DD-120") * s1225) * hpht)
    band2, band3 = rate(DDS["DDS.T02_DEPTH_BANDS"], "2"), rate(DDS["DDS.T02_DEPTH_BANDS"], "3")   # no class factor (17B)
    ex = DDS["DDS.T21_APPB_EXAMPLE"]
    assert [dd101, mw310, dd120, band2, band3] == [sl.dec(v) for v in col(ex, "rate")]
    amounts = [2 * dd101, mw310, 18 * dd120, 70 * band2, 85 * band3]
    assert amounts == [sl.dec(v) for v in col(ex, "amount")]
    net = sum(amounts)
    vat = half_even(net * D("0.15"))
    assert (net, vat, net + vat) == tuple(sl.dec(ex["totals"][k]) for k in ("net", "vat", "total"))
    # Clause 38 example: 4% of the excess over 250,000
    svc = sl.dec(ex["discount_example"]["services_total"])
    assert -half_even((svc - D("250000")) * D("0.04")) == sl.dec(ex["discount_example"]["ds900"])


def test_dds_appendix_f_depreciation():
    row = DDS["DDS.T22_APPF_EXAMPLE"]["rows"][0]
    assert min(int(row[1]) // 25, 50) == int(row[2]) == 16


def test_decimal_half_even_beats_float():
    # 423.00 x 0.945 = 399.735: an exact half cent that binary floats misrepresent.
    assert half_even(D("423.00") * D("0.945")) == D("399.74")
    assert round(423.00 * 0.945, 2) == 399.73          # the float pitfall this spec avoids


def test_question_scopes_reproduce(tmp_path, monkeypatch, capsys):
    import question_scopes
    frozen = json.loads((ROOT / "spec" / "question_scopes.json").read_text())
    monkeypatch.setattr(sl, "SPEC", tmp_path)
    monkeypatch.setattr(question_scopes.sl, "SPEC", tmp_path)
    for f in (ROOT / "spec").glob("*.yaml"):          # Q11/Q13/D7 scopes read the records through the G2 parser
        (tmp_path / f.name).write_text(f.read_text())
    assert question_scopes.main() == 0
    capsys.readouterr()
    assert json.loads((tmp_path / "question_scopes.json").read_text()) == frozen
