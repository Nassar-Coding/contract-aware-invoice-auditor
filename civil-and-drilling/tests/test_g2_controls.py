"""G2 negative and structural controls: the unresolved queue and conflict list fire on bad input; nothing is
defaulted; reference validity and semantic facts stay separate; repeated run metadata is one fact."""
import csv
import datetime as dt
import shutil
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from audit import build, claims, events, links, records_cw, records_dds
from audit.common import SNAPSHOT, Queue

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def world():
    return build.build()


def test_every_row_and_file_accounted_for(world):
    assert {k: len(v) for k, v in world.claims.rows.items()} == {"cw_headers": 900, "cw_lines": 7746, "dds_headers": 1906, "dds_lines": 91244}
    assert all(world.claims.assertions.values())
    assert len(world.cw) == 2169 and len(world.ddr_by_file) == 8151 and len(world.ddr) == 8151
    assert len(world.cw_links) == 7746 and len(world.dds_links) == 91244
    assert links.accounting(world.cw_links, world.cw)["unreferenced"] == []
    assert links.accounting(world.dds_links, world.ddr)["unreferenced"] == []


def test_queue_empty_on_corpus_but_contradictions_visible(world):
    assert world.queue.items == []
    checks = {c.check for c in world.queue.conflicts}
    assert checks == {"gyro_surveys_without_part_C"}      # loss hours agree with each lost tool's own history


def test_civil_bad_input_is_queued_not_defaulted():
    q = Queue()
    bad = ("DAILY EXCAVATION RECORD\nTicket: DX-99998\nJob: J\nArea: S-09 Nowhere\nDate: 31/02/2025\nGround: G9 Mud\n\n"
           "dug a big hole\nand another\n\nSigned (foreman): A. Foreman\n")
    r = records_cw.parse_file("civilwork/records/DX-99999.txt", bad, q)
    got = {(u.field, u.reason.split(" ")[0]) for u in q.items}
    assert ("Area", "not") in got and ("Date", "unparseable") in got and ("Ground", "not") in got
    assert ("narrative", "expected") in got and ("Countersigned (Engineer's representative)", "required") in got
    assert r.area is None and r.date is None and r.ground is None and r.quantity is None and r.candidates == []
    assert {c.check for c in q.conflicts} == {"ticket_vs_filename"}
    q = Queue()
    r = records_cw.parse_file("civilwork/records/XX-00001.txt", "SITE DIARY\nTicket: XX-00001\n\nsomething\n", q)
    assert r.family is None and any(u.field == "title" for u in q.items)
    q = Queue()
    records_cw.parse_file("civilwork/records/CT-99999.txt",
                          "COMPACTION TEST CERTIFICATE\nTicket: CT-99999\nJob: J\nArea: S-01 Platform North\nDate: 01/02/2025\n\n"
                          "rolled some stone\n\nSigned (foreman): A. Foreman\nCountersigned (Engineer's representative): B. Engineer\n", q)
    assert [u.reason.split(" ")[0] for u in q.items] == ["no"]                # unmatched narrative never defaulted


def test_weekly_day_list_checks():
    q = Queue()
    days = records_cw.reconstruct_days(dt.date(2025, 1, 27), "Mon 27/01, Wed 28/01, Mon 03/02, Xyz 01/01", q, "DW-X", None)
    assert days == [dt.date(2025, 1, 27), dt.date(2025, 1, 28), dt.date(2025, 2, 3)]
    assert {c.check for c in q.conflicts} == {"weekday_name", "day_outside_week"}
    assert [u.reason.split(" ")[0] for u in q.items] == ["unparseable"]
    q = Queue()
    assert records_cw.reconstruct_days(dt.date(2026, 12, 28), "Thu 31/12, Fri 01/01", q, "DW-Y", None) == [dt.date(2026, 12, 31), dt.date(2027, 1, 1)]


def test_ddr_bad_input_is_queued():
    q = Queue()
    bad = ("DAILY DRILLING REPORT\nReport: DDR-999-20250101\nContract: DDS-2025-118\nWell: NGP-BD-999\nRig: R\nDate: 01-Jan-2025\n\n"
           "PART A — OPERATIONS SUMMARY\nHole section: 7\"\nStatus: Sleeping\nDepth start (m MD): ten\nIn the hole: sky hook, mud motor\n"
           "Crew on tour: 3 wizards\nMystery: 1\n\nPART Z — EXTRAS\n\nSigned (Company Representative): X\n")
    d = records_dds.parse_file("drilling_services/records/DDR_NGP-BD-999_20250101.txt", bad, q)
    reasons = " | ".join(f"{u.field}: {u.reason}" for u in q.items)
    for needle in ("A.Hole section: unparseable", "A.Status: unparseable", "A.Depth start (m MD): unparseable",
                   "'sky hook' is not an Appendix G tool term", "unrecognised crew entry '3 wizards'", "A.Mystery: unknown key",
                   "unknown part heading", "Part B missing", "signature line missing", "A.Circulating hours: key missing"):
        assert needle in reasons, needle
    assert d.tools_in_hole == {"sky hook": None, "mud motor": "DD-110"}
    assert d.parts["A"]["Depth start (m MD)"] is None                         # not zero


def test_ddr_internal_contradictions_become_conflicts():
    q = Queue()
    txt = ("DAILY DRILLING REPORT\nReport: DDR-998-20250102\nContract: DDS-2025-181\nWell: NGP-BD-999\nRig: R\nDate: 02-Jan-2025\n\n"
           "PART A — OPERATIONS SUMMARY\nHole section: 6\"\nStatus: Standby\nDepth start (m MD): 100\nDepth end (m MD): 90\nCirculating hours: 25\n"
           "BHA run: 2\nIn the hole: mud motor\nCrew on tour: 2 directional hands\nGyro surveys: 0\nPressure points: 0\nWiper trips: 0\n"
           "Back-reaming hours: 0\nClean-out runs: 0\n\nPART B — BHA RUN RECORD\nRun: 3\nRun first day: 05-Jan-2025\nRun last day: 06-Jan-2025\n"
           "Tools in run: MWD collar\nRun circulating hours: 5\nMetres logged: 0\nMetres reamed: 0\nRadioactive source carried: No\n\n"
           "PART D — RADIOACTIVE SOURCE HANDLING\nSource run: 3\nSources handled: x\nSource handling certified: Yes\n\n"
           "PART E — LOST IN HOLE\nLost in hole run: 2\nLost in hole tool: gamma tool\nCirculating hours accumulated on the well: 5\n\n"
           "Signed (Company Representative): ____\nSigned (lead directional driller): K. Doyle\n")
    d = records_dds.parse_file("drilling_services/records/DDR_NGP-BD-999_20250101.txt", txt, q)
    checks = {c.check for c in q.conflicts}
    assert {"filename_vs_header", "report_number_vs_well_date", "A_run_vs_B_run", "A_tools_vs_B_tools", "report_date_outside_run",
            "depth_decreases", "standby_with_depth_change", "Circulating hours_over_24", "part_D_but_no_source_carried",
            "E_run_vs_B_run", "lost_tool_not_in_run", "contract_reference"} <= checks
    assert d.lost_tool_code == "LH-714" and not d.company_signed and d.driller_signed


def test_claims_loader_queues_blank_and_unparseable(tmp_path):
    for rel in list(claims.FILES.values()) + ["submission_template.csv"]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(SNAPSHOT / rel, tmp_path / rel)

    def corrupt(rel, fn):
        path = tmp_path / rel
        rows = list(csv.DictReader(path.open(newline="")))
        cols = list(rows[0])
        fn(rows)
        with path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, cols)
            w.writeheader()
            w.writerows(rows)

    def dds(rows):
        rows[0]["report_ref"] = ""                 # MDS-00001-001 DD-101: needs its report
        rows[1]["quantity"] = "abc"                # MDS-00001-002
        rows[2]["service_date"] = "2025-01-01"     # wrong format for drilling
    def cw(rows):
        rows[0]["amount"] = ""                     # PA-00001-01
        rows[1]["quantity"] = "0"                  # PA-00001-02: a real zero stays zero
    corrupt(claims.FILES["dds_lines"], dds)
    corrupt(claims.FILES["cw_lines"], cw)
    c = claims.load(tmp_path)
    got = {(u.ident, u.field, u.reason.split(" ")[0]) for u in c.queue.items}
    assert ("MDS-00001-001", "report_ref", "required") in got
    assert ("MDS-00001-002", "quantity", "unparseable") in got
    assert ("MDS-00001-003", "service_date", "unparseable") in got
    assert ("PA-00001-01", "amount", "required") in got
    rows = {r.ident: r for r in c.rows["cw_lines"]}
    assert rows["PA-00001-01"].values["amount"] is None and rows["PA-00001-02"].values["quantity"] == Decimal("0")
    assert not any(u.field == "report_ref" and u.ident != "MDS-00001-001" for u in c.queue.items)   # DS-900 exempt
    assert len(c.queue.items) == 4


def test_reference_and_semantic_are_separate(world):
    for l in list(world.cw_links.values()) + list(world.dds_links.values()):
        if l.reference in links.RESOLVED_STATES:
            assert l.semantic and "reference" not in l.semantic
        else:
            assert l.semantic == {}
    l = world.dds_links["MDS-00164-050"]                       # a mismatch is not a duplicate
    assert (l.reference, l.semantic["date_match"], l.semantic["well_match"]) == ("resolved", False, True)
    assert {l.reference for l in world.cw_links.values()} <= links.CW_REFERENCE_STATES
    assert {l.reference for l in world.dds_links.values()} <= links.DDS_REFERENCE_STATES


def test_repeated_run_metadata_is_one_fact(world):
    r = world.runs[("NGP-BD-011", 1)]
    assert len(r.reports) >= 2 and r.all_days_reported
    assert r.metadata["Run circulating hours"] == 59 == r.daily_circulating_hours   # run total, not per report


def test_run_metadata_disagreement_and_loss_hours_are_conflicts():
    def mk(rid, date, run_hours, a_hours, loss=None, tools=("mud motor",)):
        d = records_dds.Ddr(file=rid + ".txt", path="x", report=rid, well="W-1", date=date)
        d.parts = {"A": {"Circulating hours": a_hours, "Depth start (m MD)": 0, "Depth end (m MD)": 10},
                   "B": {"Run": 1, "Run first day": dt.date(2025, 1, 1), "Run last day": dt.date(2025, 1, 2), "Tools in run": ["mud motor"],
                         "Run circulating hours": run_hours, "Metres logged": 0, "Metres reamed": 0, "Radioactive source carried": False}}
        d.tools_in_hole = {t: None for t in tools}
        if loss is not None:
            d.parts["E"] = {"Circulating hours accumulated on the well": loss}
            d.lost_tool_term, d.lost_tool_code = "mud motor", "LH-711"
        return d
    q = Queue()
    ddrs = {"R1": mk("R1", dt.date(2025, 1, 1), 20, 10), "R2": mk("R2", dt.date(2025, 1, 2), 21, 10, loss=15)}
    runs, _ = events.identify(ddrs, q)
    checks = {c.check for c in q.conflicts}
    assert "part_B_Run circulating hours_differs" in checks
    assert "part_E_hours_vs_tool_daily_sum" in checks             # 15 stated, the tool accumulated 20
    loss = runs[("W-1", 1)].losses[0]
    assert (loss["well_daily_hours_through_loss_day"], loss["run_daily_hours_through_loss_day"], loss["tool_daily_hours_through_loss_day"]) == (20, 20, 20)
    # the tool's own history, not the well's: tool only in the hole on the loss day -> 10 h corroborates Part E = 10
    q = Queue()
    ddrs = {"R1": mk("R1", dt.date(2025, 1, 1), 20, 10, tools=("gamma tool",)), "R2": mk("R2", dt.date(2025, 1, 2), 20, 10, loss=10)}
    runs, _ = events.identify(ddrs, q)
    assert not any(c.check == "part_E_hours_vs_tool_daily_sum" for c in q.conflicts)
    assert runs[("W-1", 1)].losses[0]["well_daily_hours_through_loss_day"] == 20


def test_blind_comparator_detects_planted_errors(world, tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "tools"))
    import json
    import compare_blind_g2 as cb
    src = ROOT / "verification" / "g2" / "blind"
    rows = [json.loads(x) for x in (src / "cw_annotations.jsonl").read_text().splitlines()]
    rows[0]["quantity"] = "99999"
    (tmp_path / "cw_annotations.jsonl").write_text("\n".join(json.dumps(x) for x in rows))
    drows = [json.loads(x) for x in (src / "dds_annotations.jsonl").read_text().splitlines()]
    drows[0]["B"]["Metres logged"] = "12345"
    (tmp_path / "dds_annotations.jsonl").write_text("\n".join(json.dumps(x) for x in drows))
    monkeypatch.setattr(cb, "BLIND", tmp_path)
    bad = [x for x in cb.compare_cw(world.cw) + cb.compare_dds(world.ddr_by_file) if not x["agree"]]
    assert [(x["field"]) for x in bad] == ["quantity", "B.Metres logged"]


def test_fixture_coverage_check_detects_a_missing_branch(world):
    """E4 negative control: dropping the only reviewed example of a branch leaves that branch uncovered."""
    import verify_g2 as vg
    conflicts = {}
    for c in world.queue.conflicts:
        conflicts.setdefault(c.ident, set()).add(c.check)
    corpus = set().union(*(vg.ddr_branches(d, conflicts) for d in world.ddr_by_file.values()))
    fixtures = [f for f in vg.yaml.safe_load(vg.FIXTURES.read_text())["drilling_reports"] if f != "DDR_NGP-WS-009_20251211.txt"]
    covered = set().union(*(vg.ddr_branches(world.ddr_by_file[f], conflicts) for f in fixtures))
    assert "ddr.conflict:gyro_surveys_without_part_C" in corpus - covered


G3_MODULES = {"terms.py", "g3_core.py", "g3_cw.py", "g3_dds.py", "g3_run.py"}   # the pricing layer G3 adds on top
# the state layer G4 adds on top of G3 (it consumes G3 results; its own boundary - no G5 construct, G3 untouched - is
# tools/verify_g4.py Y8 and tests/test_g4_gate.py). Named, not globbed, like the G3 set.
G4_MODULES = {"g4_core.py", "g4_cw.py", "g4_dds.py", "g4_run.py"}
# the outcome layer G5 adds on top of G3 and G4 (its boundary: tools/verify_g5.py Z9, tests/test_g5_gate.py). Named.
G5_MODULES = {"g5_outcomes.py", "g5_run.py"}


def test_boundary_no_pricing_or_outcomes():
    """G2 stops at evidence: its modules define no valuation, flag, total or submission output, and never import the
    G3 pricing layer (which may price; its own boundary is tests/test_g3_gate.py). The G3 set is named, not globbed,
    so pricing added to an evidence module is still caught."""
    g2 = [p for p in (ROOT / "audit").glob("*.py") if p.name not in G3_MODULES | G4_MODULES | G5_MODULES]
    assert {p.name for p in g2} >= {"build.py", "claims.py", "events.py", "links.py", "records_cw.py", "records_dds.py"}
    text = " ".join(p.read_text() for p in g2).lower()
    for word in ("audit_results.csv", "expected_total", "flagged", "load_instruments", "rate_version", "def price"):
        assert word not in text, word
    import ast
    g3 = {m[:-3] for m in G3_MODULES}
    for p in g2:
        for node in ast.walk(ast.parse(p.read_text())):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            assert not {n.split(".")[-1] for n in names} & g3, (p.name, names)


def test_dd121_standby_charge_is_evidenced_by_the_rotary_steerable(world):
    """Review finding 2: DD-121 replaces DD-120 on a Standby day (Cl.21 p6; Sch 3 Part 4 p21), so its tool is DD-120's."""
    lines = {r.ident: r for r in world.claims.rows["dds_lines"]}
    dd121 = [l for i, l in world.dds_links.items() if lines[i].values["service_code"] == "DD-121"]
    assert len(dd121) == 245
    assert all(l.semantic["tool_basis_code"] == "DD-120" and l.semantic["tool_in_hole"] is True for l in dd121)
    assert all("rotary steerable" in world.ddr[l.record].tools_in_hole for l in dd121)


def test_reintroduced_dd121_defect_fails_population_check(world, monkeypatch):
    """Negative control: keying tool presence on the billed code again (the defect) must fail the G2 check."""
    assert links.tool_presence_failures(links.tool_presence_population(world.dds_links, world.claims.rows["dds_lines"])) == []
    monkeypatch.setitem(links.TOOL_BASIS, "DD-121", "DD-121")
    relinked = links.link_dds(world.claims.rows["dds_lines"], world.claims.rows["dds_headers"], world.ddr)
    errs = links.tool_presence_failures(links.tool_presence_population(relinked, world.claims.rows["dds_lines"]))
    assert errs == ["DD-121: tool not established on 245/245 lines and no cited explanation"]


def test_undeclared_tool_day_service_fails(monkeypatch):
    """Negative control: a tool-day service with no Appendix G term and no declared basis cannot pass silently."""
    monkeypatch.setitem(links.TOOL_PRESENCE, "substitutes", {})
    basis, undeclared = links._tool_basis()
    assert undeclared == ["DD-121"] and "DD-121" not in basis
    monkeypatch.setattr(links, "TOOL_BASIS_UNDECLARED", undeclared)
    assert links.tool_presence_failures({}) == ["DD-121: tool-day service without an Appendix G term and without a declared basis"]
