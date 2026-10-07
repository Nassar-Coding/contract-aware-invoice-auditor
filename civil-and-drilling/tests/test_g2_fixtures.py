"""G2 reviewed fixtures: the parser must reproduce every value typed by hand from the raw text
(tests/fixtures/g2_reviewed.yaml). Dates are compared as ISO strings, money/quantities as exact Decimal."""
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from audit import build

FIX = yaml.safe_load((Path(__file__).parent / "fixtures" / "g2_reviewed.yaml").read_text())


@pytest.fixture(scope="module")
def world():
    return build.build()


def norm(v):
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, list):
        return [norm(x) for x in v]
    return v


@pytest.mark.parametrize("ticket", sorted(FIX["civil_records"]))
def test_civil_record(world, ticket):
    exp = FIX["civil_records"][ticket]
    r = world.cw[ticket]
    for k, v in exp.items():
        got = norm(getattr(r, k))
        assert got == v, (ticket, k, got, v)
    assert r.foreman_signed is True
    assert r.engineer_signed is exp.get("engineer_signed", True)
    assert r.job == "Northern Access Road, Package 4"
    assert r.spans["narrative"].path == f"civilwork/records/{ticket}.txt"      # provenance kept


@pytest.mark.parametrize("fname", sorted(FIX["drilling_reports"]))
def test_drilling_report(world, fname):
    exp = FIX["drilling_reports"][fname]
    d = world.ddr_by_file[fname]
    for k in ("report", "contract", "well", "rig", "company_rep", "lead_dd"):
        if k in exp:
            assert getattr(d, k) == exp[k], (fname, k)
    assert d.date.isoformat() == exp["date"]
    assert sorted(d.parts) == exp["parts"]
    for p in "ABCDE":
        if p in exp:
            got = {k: norm(v) for k, v in d.parts[p].items() if k not in ("In the hole", "Tools in run", "Crew on tour")}
            assert got == exp[p], (fname, p, got)
    assert d.parts["A"]["In the hole"] == exp["in_the_hole"] == d.parts["B"]["Tools in run"]
    assert d.crew_terms == exp["crew"]
    assert d.lost_tool_code == exp.get("lost_code")
    assert d.company_signed is exp.get("company_signed", True) and d.driller_signed is exp.get("driller_signed", True)
    assert world.ddr[exp["report"]] is d                                     # indexed by internal report number
    got_conflicts = {c.check for c in world.queue.conflicts if c.ident == fname}
    assert got_conflicts == set(exp.get("conflicts", []))


def test_appendix_g_wording_including_loss_context(world):
    d = world.ddr_by_file["DDR_NGP-BD-152_20260410.txt"]
    assert d.tools_in_hole["gamma tool"] == "LW-410" and d.lost_tool_code == "LH-714"
    d = world.ddr_by_file["DDR_NGP-BD-020_20250421.txt"]
    assert d.tools_in_hole["rotary steerable"] == "DD-120" and d.lost_tool_code == "LH-712"
    d = world.ddr_by_file["DDR_NGP-BD-011_20260318.txt"]
    assert d.crew == {"DD-101": 2, "DD-102": 1, "LW-401": 1, "MW-301": 2}               # 'night man' -> DD-102
    d = world.ddr_by_file["DDR_NGP-BD-020_20250421.txt"]
    assert d.tools_in_hole["float sub"] == "HC-640" and d.tools_in_hole["MWD collar"] == "MW-310"
    d = world.ddr_by_file["DDR_NGP-BD-018_20250117.txt"]
    assert d.tools_in_hole["hole opener"] == "RM-511"


@pytest.mark.parametrize("kind", ["cw_headers", "cw_lines", "dds_headers", "dds_lines"])
def test_claims(world, kind):
    rows = {r.ident: r for r in world.claims.rows[kind]}
    for ident, exp in FIX["claims"][kind].items():
        r = rows[ident]
        assert r.source.line == exp["line"]
        for k, v in exp.items():
            if k == "line":
                continue
            got = norm(r.values[k])
            assert got == v, (ident, k, got, v)
            if isinstance(r.values[k], Decimal):
                assert r.raw[k] == v                                        # original string kept verbatim


def test_links(world):
    for contract, links in (("cw", world.cw_links), ("dds", world.dds_links)):
        for ident, exp in FIX["links"][contract].items():
            l = links[ident]
            assert l.reference == exp["reference"], ident
            assert l.record == exp.get("record"), ident
            for k, v in exp.items():
                if k not in ("reference", "record"):
                    assert norm(l.semantic[k]) == v, (ident, k)
