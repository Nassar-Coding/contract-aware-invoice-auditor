"""G4 falsification: histories built to break the state engines on branches no committed history or pinned line
exercises - same-day duplicate ties inside a band ledger, a disallowed duplicate as an exclusion trigger, a large
same-date group at a band edge, a 45A release tie, reordered submissions, a correction to an early measurement, a well
with no report, a repeated loss. Each expectation is computed here by hand from the contract text, not from the engine."""
from decimal import Decimal

import g4_histories as H
import g4_history_compare as HC
import verify_g4 as vg
from audit import g3_cw, g3_dds


def _run(h):
    w, _g3, _st = HC.run_history(h)
    res = {"CW": g3_cw.run(w), "DDS": g3_dds.run(w)}
    return vg.run_state(w, res)


def _lines(st, c="CW"):
    return {g.g3.line_ref: g for g in st[c].lines.values()}


def _amounts(g):
    if g.r.alternatives:
        return sorted({str(v["amount"]) for v in g.r.alternatives.values()})
    return [str(g.r.amount)]


# B.22.010: base 74.50, band 1 to 1,200 at 100%, 1,201 to 4,800 at 96% (71.52) - CW-H01, readers r1/r2
def test_same_day_tie_counts_once_in_the_band():
    h = {"id": "CW-F01", "records": {}, "documents": [
        H.cw_app("PA-F0101", "2025-03-20", [("2025-03-02", "S-05", "B.22.010", "600", "74.50", "")]),
        H.cw_app("PA-F0102", "2025-03-20", [("2025-03-02", "S-05", "B.22.010", "600", "74.50", "")]),
        H.cw_app("PA-F0103", "2025-04-20", [("2025-03-10", "S-05", "B.22.010", "800", "74.50", "")])]}
    st = _run(h)
    L = _lines(st)
    a, b, c = L["PA-F0101-01"], L["PA-F0102-01"], L["PA-F0103-01"]
    # submitted the same day: which is later is not established - each stands in one alternative, never both
    assert _amounts(a) == ["0.00", "44700.00"] and _amounts(b) == ["0.00", "44700.00"]
    assert vg.y4(st["CW"], st["DDS"]) == [] and vg.y6(st["CW"], st["DDS"]) == []
    # exactly 600 counted before the later line: 600 at 74.50 + 200 at 71.52 (counting both would put all 800 at 71.52)
    assert _amounts(c) == ["59004.00"], _amounts(c)


def test_a_rejected_measurement_does_not_exclude():
    # A.14.010 billed in the wrong unit on 06-Oct is rejected in its entirety (Cl.26): it is not a measurement, so it
    # does not exclude A.14.020 on 07-Oct; the same line in the right unit does (D+1, Cl.32 / Sch 4 Part 5).
    recs = {"CT-F22": H.ct("CT-F22", "S-04", "2025-10-06", "brought in 100 cube of fill, compacted in layers")}
    def hist(unit):
        extra = (unit,) if unit else ()
        return {"id": "CW-F02", "records": recs, "documents": [
            H.cw_app("PA-F0201", "2025-10-20", [("2025-10-06", "S-04", "A.14.010", "100", "53.20", "CT-F22", *extra),
                                                ("2025-10-07", "S-04", "A.14.020", "50", "22.40", "")], site="S-04")]}
    rejected = _lines(_run(hist("m2")))
    assert rejected["PA-F0201-01"].r.payable is False
    assert rejected["PA-F0201-02"].r.payable is True and rejected["PA-F0201-02"].r.amount != 0
    kept = _lines(_run(hist(None)))
    assert kept["PA-F0201-02"].r.payable is False and "excluded_by_other_item" in kept["PA-F0201-02"].state_findings


def test_same_date_group_at_an_edge_orders_only_what_counts():
    # 200 on 01-Mar, then five work areas of 250 on 02-Mar (+ two copies of one of them, disallowed by Cl.44): the count
    # reaches 1,200 exactly after four of the five, so whichever is counted last lies wholly in band 2 (71.52), the others
    # in band 1 (74.50). No order is stated (Sch 4 Part 3 substitutes Cl.30): each of the five carries both values; the
    # disallowed copies add nothing and do not multiply the orders.
    same = [("2025-03-02", a, "B.22.010", "250", "74.50", "") for a in ("S-01", "S-02", "S-03", "S-04", "S-05")]
    copies = [("2025-03-02", "S-05", "B.22.010", "250", "74.50", "")] * 2
    h = {"id": "CW-F03", "records": {}, "documents": [
        H.cw_app("PA-F0301", "2025-03-10", [("2025-03-01", "S-05", "B.22.010", "200", "74.50", "")] + same),
        H.cw_app("PA-F0302", "2025-03-20", copies)]}
    st = _run(h)
    L = _lines(st)
    assert L["PA-F0301-01"].r.amount == Decimal("14900.00")
    for i in range(2, 7):
        assert _amounts(L[f"PA-F0301-0{i}"]) == ["17880.00", "18625.00"], (i, _amounts(L[f"PA-F0301-0{i}"]))
    assert L["PA-F0302-01"].r.payable is False and L["PA-F0302-02"].r.payable is False
    assert vg.y3(st["CW"], st["DDS"]) == [] and vg.y6(st["CW"], st["DDS"]) == []


def test_reordered_submission_changes_nothing_but_submission_facts():
    # CW-H01 with the applications' rows listed in the reverse order: the ledger counts by execution date either way
    h = H.histories()["CW-H01"]
    a = _run(h)
    b = _run({**h, "documents": list(reversed(h["documents"]))})
    assert vg.snapshot_state(a) == vg.snapshot_state(b)


def test_release_tie_is_not_posted_twice():
    h = {"id": "CW-F05", "records": {}, "documents": [
        H.cw_app("PA-F0501", "2026-08-10", [("2026-08-03", "S-01", "A.11.010", "1000", "3.85", "")], site="S-01"),
        H.cw_app("PA-F0502", "2026-10-05", [("2026-09-30", "S-02", "A.11.010", "100", "3.85", "")], site="S-02"),
        H.cw_app("PA-F0503", "2026-10-05", [("2026-09-30", "S-03", "A.11.010", "100", "3.85", "")], site="S-03")]}
    st = _run(h)
    rel = st["CW"].retention["release"]
    assert rel["recipient"] == {"tie": ["PA-F0502", "PA-F0503"]}
    assert vg.y4(st["CW"], st["DDS"]) == []


def test_well_with_no_report_leaves_the_well_event_open():
    h = H.histories()["DDS-H04"]
    h2 = {**h, "reports": {}}
    st = _run(h2)
    g = _lines(st, "DDS")["MDS-90031-001"]
    # no report: G3 already holds the line (report missing); G4 never makes it the standing first-day charge
    assert g.r.payable is not True or any(x.status == "unresolved" for x in g.state)


def test_correction_to_an_early_measurement_replays_later_divisions():
    h = H.histories()["CW-H01"]
    base = _lines(_run(h))
    # the first measurement (600 m3 on 02-Mar) corrected to 100: PA-91001-01 (12-Mar) now lies wholly in band 1
    docs = [dict(d) for d in h["documents"]]
    first = {**docs[0], "lines": [dict(x) for x in docs[0]["lines"]]}
    first["lines"][0].update(quantity="100", amount="7450.00")
    after = _lines(_run({**h, "documents": [first] + docs[1:]}))
    assert base["PA-91001-01"].r.amount == Decimal("36356.00")
    assert after["PA-91001-01"].r.amount == Decimal("37250.00")        # 500 x 74.50, from 500 to 1,000
    assert after["PA-91003-01"].r.amount != base["PA-91003-01"].r.amount


def test_repeated_loss_on_one_run_stands_once():
    st = _run(H.histories()["DDS-H07"])
    assert vg.y4(st["CW"], st["DDS"]) == []
    g = [g for g in st["DDS"].lines.values() if g.g3.code and g.g3.code.startswith("LH-")]
    assert len(g) == 2 and all(set(_amounts(x)) == {"0.00", "377300.00"} for x in g)
