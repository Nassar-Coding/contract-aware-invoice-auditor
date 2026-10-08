"""G3 review findings FD01-FD08: evidence parsing and the engines that read it.

Every test runs the real G2 parsers on mutated source text (records, reports, claim CSV rows) and the G3 engines on
what G2 hands over. Each negative control runs the code as it stood before these fixes - its G2 parsers with their own helpers and
its engines - on the same input and shows the defect the review reproduced."""
import copy
import prior_code
import sys
import types
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

import g3_case_compare as gcc
import null_sweep as ns
from audit import build, g3_cw, g3_dds, records_cw, records_dds
from audit.common import Queue

ROOT = Path(__file__).resolve().parents[1]
R3 = "g3_before_parsing_fixes"                                    # the reviewed code


def r3_module(name: str, patch: dict | None = None):
    """audit/<name>.py as it stood before the parsing fixes, executed inside the audit package; `patch` replaces module globals
    (the earlier helpers it imported from modules changed since)."""
    src = prior_code.source(R3, f"audit/{name}.py")
    mod = types.ModuleType(f"audit._r3_{name}")
    mod.__package__ = "audit"
    mod.__file__ = str(ROOT / "audit" / f"{name}.py")
    sys.modules[mod.__name__] = mod                                  # dataclasses resolve their module
    exec(compile(src, f"{R3}:audit/{name}.py", "exec"), mod.__dict__)
    mod.__dict__.update(patch or {})
    return mod


@pytest.fixture(scope="module")
def r3():
    common = r3_module("common")
    rec_dds = r3_module("records_dds", {"is_signature": common.is_signature})
    rec_cw = r3_module("records_cw", {"is_signature": common.is_signature})
    return types.SimpleNamespace(common=common, records_dds=rec_dds, records_cw=rec_cw,
                                 g3_dds=r3_module("g3_dds"), g3_cw=r3_module("g3_cw"))


@pytest.fixture(scope="module")
def world():
    return build.build()


def _dds_line(w, ref):
    row = next(x for x in w.claims.rows["dds_lines"] if x.ident == ref)
    inv = {h.ident: h.values for h in w.claims.rows["dds_headers"]}[row.values["invoice_no"]]
    return row.values, inv, w.ddr[row.values["report_ref"]]


def _cw_line(w, ref):
    row = next(x for x in w.claims.rows["cw_lines"] if x.ident == ref)
    app = {h.ident: h.values for h in w.claims.rows["cw_headers"]}[row.values["application_no"]]
    return row.values, app, w.cw[row.values["record_ref"]]


_CTX = {}


def _ctx(w, c):
    """G2's input context for the world (computed once per world and contract)."""
    if (id(w), c) not in _CTX:
        _CTX[(id(w), c)] = (g3_dds if c == "DDS" else g3_cw).input_context(w)
    return _CTX[(id(w), c)]


def dds_eval(w, ref, text_fn, engine=g3_dds, parser=records_dds):
    """The line evaluated on its report after text_fn(report text), re-parsed by `parser` with a fresh G2 queue."""
    line, inv, ddr = _dds_line(w, ref)
    q = Queue()
    d2 = parser.parse_file(ddr.path, text_fn(ns.doc_text(ddr)), q)
    mine = [u for u in q.items if u.ident == d2.file]
    indexed = d2 if d2.report == line.get("report_ref") else None       # G2 indexes reports by their Report number
    if engine is g3_dds:
        ctx = _ctx(w, "DDS")[ref]
        inp = replace(ctx, doc_gaps=frozenset(u.field for u in mine), unindexed_reports=d2.report is None,
                      doc_repeated=frozenset(u.field for u in mine if u.reason == "key repeated"))
        return engine.evaluate(line, inv, indexed, inputs=inp), q
    return engine.evaluate(line, inv, indexed), q


def cw_eval(w, ref, text_fn, engine=g3_cw, parser=records_cw, line_patch=None):
    line, app, rec = _cw_line(w, ref)
    line = {**line, **(line_patch or {})}
    q = Queue()
    r2 = parser.parse_file(rec.path, text_fn(ns.doc_text(rec)), q)
    mine = [u for u in q.items if u.ident == r2.ticket]
    if engine is g3_cw:
        ctx = _ctx(w, "CW")[ref]
        inp = replace(ctx, doc_gaps=frozenset(u.field for u in mine),
                      doc_repeated=frozenset(u.field for u in mine if u.reason == "key repeated"))
        return engine.evaluate(line, app, r2, True, inputs=inp), q
    return engine.evaluate(line, app, r2, True), q


def swap(key, value):
    return lambda t: ns._swap_value(t, key, value)


# ============================================================================================ FD01 signatures
DDS_SIGS = ["Signed (Company Representative)", "Signed (lead directional driller)"]
CW_SIGS = ["Signed (foreman)", "Countersigned (Engineer's representative)"]
UNKNOWN = ["??", "illegible", "12345", "x", "Signed", "Pending Review"]
UNSIGNED = ["unsigned", "N/A", "pending", "not signed", "TBC", "____________________", ""]


@pytest.mark.parametrize("key", DDS_SIGS)
@pytest.mark.parametrize("token", UNKNOWN)
def test_fd01_dds_unreadable_signature_is_unresolved(world, key, token):
    r, q = dds_eval(world, "MDS-00001-013", swap(key, token))
    assert r.amount_status == "unresolved" and r.amount is None and r.payable is None
    assert any(c.check == "input" and c.detail.startswith(key) for c in r.checks)
    assert any(u.field == key and "does not establish a signature" in u.reason for u in q.items)
    assert "report_unsigned" not in r.findings


@pytest.mark.parametrize("key", DDS_SIGS)
@pytest.mark.parametrize("token", UNSIGNED)
def test_fd01_dds_non_signing_text_is_unsigned(world, key, token):
    r, _ = dds_eval(world, "MDS-00001-013", swap(key, token))
    assert r.amount_status == "not_payable" and "report_unsigned" in r.findings


@pytest.mark.parametrize("key", CW_SIGS)
@pytest.mark.parametrize("token", UNKNOWN)
def test_fd01_cw_unreadable_signature_is_unresolved(world, key, token):
    r, q = cw_eval(world, "PA-00001-04", swap(key, token))
    assert r.amount_status == "unresolved" and r.amount is None
    assert any(u.field == key and "does not establish a signature" in u.reason for u in q.items)
    assert "record_unsigned" not in r.findings


@pytest.mark.parametrize("key", CW_SIGS)
@pytest.mark.parametrize("token", UNSIGNED)
def test_fd01_cw_non_signing_text_is_unsigned(world, key, token):
    r, _ = cw_eval(world, "PA-00001-04", swap(key, token))
    assert r.amount_status == "not_payable" and "record_unsigned" in r.findings


def test_fd01_real_names_still_sign(world):
    r, q = dds_eval(world, "MDS-00001-013", swap(DDS_SIGS[0], "M. Al-Harbi"))
    assert r.amount == Decimal("4892.30") and not q.items
    r, q = cw_eval(world, "PA-00001-04", swap(CW_SIGS[1], "N. Basri"))
    assert r.amount == Decimal("17730.62")


def _s64_with_countersignature(value):
    c = copy.deepcopy(gcc.load_cases()["CW-S64"])
    c["record"] = ns._swap_value(c["record"], "Countersigned (Engineer's representative)", value)
    return c


def test_fd01_ground_authority_needs_an_established_countersignature():
    """CW-S64 (A.12.020, no Schedule 5 record needed, citing a record of the same day, area and work): a countersignature
    that is a name settles G3; '??' does not establish the Engineer's classification - every class is carried, named."""
    assert gcc.engine_result(_s64_with_countersignature("N. Basri")).amount_status == "determined"
    r = gcc.engine_result(_s64_with_countersignature("??"))
    assert r.amount is None and {k.split(":")[1] for k in r.alternatives} == {"G1", "G2", "G3", "G4", "G5"}
    assert any("does not establish a countersignature" in x for x in r.readings)


def test_fd01_control_prior_code_promotes_unreadable_text_to_approval(world, r3):
    """The review's reproductions on the earlier parser and engine: '??' in either required signature leaves MDS-00001-013
    payable at USD 4,892.30 and PA-00001-04 at SAR 17,730.62, with no finding, condition or queue item."""
    for key in DDS_SIGS:
        r, q = dds_eval(world, "MDS-00001-013", swap(key, "??"), engine=r3.g3_dds, parser=r3.records_dds)
        assert r.amount == Decimal("4892.30") and r.amount_status == "determined" and not q.items
    r, q = cw_eval(world, "PA-00001-04", swap(CW_SIGS[1], "??"), engine=r3.g3_cw, parser=r3.records_cw)
    assert r.amount == Decimal("17730.62") and not q.items
    c = _s64_with_countersignature("??")
    rec = r3.records_cw.parse_file(f"civilwork/records/{c['line']['record_ref']}.txt", c["record"], Queue())
    assert rec.engineer_signed                                        # earlier code: '??' is the Engineer's approval


# ============================================================================================ FD02 repeated evidence
def _insert_before(key, line):
    return lambda t: t.replace(f"\n{key}:", f"\n{line}\n{key}:", 1)


def _insert_after(key, line):
    def f(t):
        ls = t.split("\n")
        i = next(j for j, x in enumerate(ls) if x.startswith(key + ":"))
        return "\n".join(ls[:i + 1] + [line] + ls[i + 1:])
    return f


def _dup_part(p, mutate=lambda x: x):
    """The report with Part p written a second time (the copy passed through `mutate`, line by line)."""
    def f(t):
        ls = t.split("\n")
        i = next(j for j, x in enumerate(ls) if x.startswith(f"PART {p} "))
        j = i + 1
        while j < len(ls) and ls[j].strip() and not ls[j].startswith(("PART", "Signed")):
            j += 1
        return "\n".join(ls[:j] + [""] + [mutate(x) for x in ls[i:j]] + ls[j:])
    return f


ONE_HAND = lambda t: t.replace("2 directional hands", "1 directional hands", 1)  # noqa: E731


@pytest.mark.parametrize("order", [_insert_before, _insert_after])
def test_fd02_conflicting_header_date_is_unresolved_in_either_order(world, order):
    r, q = dds_eval(world, "MDS-00001-013", order("Date", "Date: 03-Jan-2025"))
    assert r.amount_status == "unresolved" and "report_date_mismatch" not in r.findings
    assert any(u.field == "Date" and u.reason == "key repeated" for u in q.items)


@pytest.mark.parametrize("key, other", [("Report", "DDR-194-20250103"), ("Well", "NGP-BD-195")])
def test_fd02_other_conflicting_header_keys(world, key, other):
    r, q = dds_eval(world, "MDS-00001-013", _insert_before(key, f"{key}: {other}"))
    assert r.amount_status == "unresolved" and any(u.field == key and u.reason == "key repeated" for u in q.items)


@pytest.mark.parametrize("order", [_insert_before, _insert_after])
@pytest.mark.parametrize("key", DDS_SIGS)
def test_fd02_conflicting_signature_lines_are_unknown(world, order, key):
    r, q = dds_eval(world, "MDS-00001-013", order(key, f"{key}: ____________________"))
    assert r.amount_status == "unresolved" and "report_unsigned" not in r.findings


@pytest.mark.parametrize("order", [_insert_before, _insert_after])
@pytest.mark.parametrize("key", CW_SIGS)
def test_fd02_conflicting_civil_signature_lines_are_unknown(world, order, key):
    r, q = cw_eval(world, "PA-00001-04", order(key, f"{key}: ____________________"))
    assert r.amount_status == "unresolved" and "record_unsigned" not in r.findings


def test_fd02_identical_repeats_are_read_once(world):
    """An identical repeated header line, signature or whole Part states one fact once: the value is the single copy's."""
    base, _ = dds_eval(world, "MDS-00001-013", lambda t: t)
    sig = next(x for x in ns.doc_text(_dds_line(world, "MDS-00001-013")[2]).split("\n") if x.startswith(DDS_SIGS[0]))
    for fn in (_insert_after("Date", "Date: 02-Jan-2025"), _insert_after(DDS_SIGS[0], sig),
               _dup_part("A"), _dup_part("B")):
        r, q = dds_eval(world, "MDS-00001-013", fn)
        assert (r.amount_status, r.amount) == (base.amount_status, base.amount) and not q.items


def test_fd02_repeated_part_a_does_not_count_the_crew_twice(world):
    """The review's MDS-00001-010: Part A stating one directional hand supports quantity 1 (USD 1,847.35, the two-person
    claim above the record); the same Part A repeated identically is still one hand; a copy stating a different crew
    leaves the crew unresolved in either order."""
    one, _ = dds_eval(world, "MDS-00001-010", ONE_HAND)
    assert (one.allowed_quantity, one.amount) == (Decimal("1"), Decimal("1847.35")) and "quantity_above_report" in one.findings
    twice, q = dds_eval(world, "MDS-00001-010", lambda t: _dup_part("A")(ONE_HAND(t)))
    assert (twice.allowed_quantity, twice.amount, twice.findings) == (one.allowed_quantity, one.amount, one.findings)
    for fn in (lambda t: _dup_part("A", lambda x: x.replace("1 directional", "2 directional"))(ONE_HAND(t)),
               _dup_part("A", lambda x: x.replace("2 directional", "1 directional"))):
        r, q = dds_eval(world, "MDS-00001-010", fn)
        assert r.amount_status == "unresolved" and any(u.field == "A.Crew on tour" and u.reason == "key repeated" for u in q.items)


def test_fd02_civil_conflicting_key_in_either_order_and_identical_repeat(world):
    base, _ = cw_eval(world, "PA-00001-04", lambda t: t)
    date = next(x for x in ns.doc_text(_cw_line(world, "PA-00001-04")[2]).split("\n") if x.startswith("Date:"))
    for fn in (_insert_before("Date", "Date: 01/01/2025"), _insert_after("Date", "Date: 01/01/2025")):
        r, q = cw_eval(world, "PA-00001-04", fn)
        assert r.amount_status == "unresolved" and any(u.field == "Date" and u.reason == "key repeated" for u in q.items)
    r, q = cw_eval(world, "PA-00001-04", _insert_after("Date", date))
    assert (r.amount_status, r.amount) == (base.amount_status, base.amount) and not q.items


def test_fd02_control_prior_code(world, r3):
    """Earlier code: a conflicting date prepended (original last) is silently resolved to the last - the line stays payable
    at USD 4,892.30 with no queue item; an unsigned line followed by a named one is signed; an identical Part A repeated
    doubles the crew (quantity 2, USD 3,694.70, no finding)."""
    r, q = dds_eval(world, "MDS-00001-013", _insert_before("Date", "Date: 03-Jan-2025"), engine=r3.g3_dds, parser=r3.records_dds)
    assert r.amount == Decimal("4892.30") and not q.items
    k = DDS_SIGS[0]
    r, q = dds_eval(world, "MDS-00001-013", _insert_before(k, f"{k}: ____________________"), engine=r3.g3_dds, parser=r3.records_dds)
    assert r.amount == Decimal("4892.30") and not q.items
    r, q = dds_eval(world, "MDS-00001-010", lambda t: _dup_part("A")(ONE_HAND(t)), engine=r3.g3_dds, parser=r3.records_dds)
    assert (r.allowed_quantity, r.amount, r.findings) == (Decimal("2"), Decimal("3694.70"), [])


# ============================================================================================ FD03 civil item attributes
# (claim, record narrative text in the record, attribute text, a different valid value, an unreadable value)
FD03 = [
    ("PA-00002-07", "1800 dia", "1200 dia", "?? dia"),                    # PT1 precast chamber diameter, C.32.030
    ("PA-00002-09", "400 ductile", "600 ductile", "4OO ductile"),         # PT2 ductile main diameter, C.31.020
    ("PA-00001-10", "32/40 mix", "20/25 mix", "32/?? mix"),               # PR1 wall mix, B.21.040
    ("PA-00003-07", "A393 mesh", "A142 mesh", "A3?3 mesh"),               # JS1 mesh, B.23.020
    ("PA-00008-06", "of Type 1", "of Type 2", "of Type ?"),               # CT4 sub-base type, D.41.010
]


def _narrative(w, ref, old, new):
    rec = _cw_line(w, ref)[2]
    assert old in ns.doc_text(rec), (ref, old)
    return lambda t: t.replace(old, new, 1)


@pytest.mark.parametrize("ref, same, other, unreadable", FD03)
def test_fd03_each_attribute_role_matching_different_unreadable(world, ref, same, other, unreadable):
    base, _ = cw_eval(world, ref, lambda t: t)
    r, q = cw_eval(world, ref, _narrative(world, ref, same, same))
    assert (r.amount_status, r.amount, r.findings) == (base.amount_status, base.amount, base.findings) and not q.items
    r, q = cw_eval(world, ref, _narrative(world, ref, same, other))       # a different valid specification
    assert r.amount_status == "not_payable" and "item_not_supported_by_record" in r.findings and not q.items
    assert "Schedule 1" in next(c.detail for c in r.checks if c.finding == "item_not_supported_by_record")
    assert not r.alternatives                                            # never repriced as another item
    r, q = cw_eval(world, ref, _narrative(world, ref, same, unreadable))  # unreadable: the narrative is not established
    assert r.amount_status == "unresolved" and any(u.field == "narrative" for u in q.items)


# every PR template variant: the concrete grade of each pour record form
PR_VARIANTS = [("wall pour 12 m3, 32/40 mix", "B.21.040"), ("slab pour 12 m3, 32/40", "B.21.030"),
               ("poured 12 m3 into the foundations, 32/40 mix", "B.21.020"), ("poured the slab, 12 cube of C32/40", "B.21.030"),
               ("foundation pour 12 cube, C32/40 off the truck", "B.21.020")]


@pytest.mark.parametrize("narrative, item", PR_VARIANTS)
@pytest.mark.parametrize("grade, ok", [("32/40", True), ("20/25", False), ("40/50", False)])
def test_fd03_every_pour_record_variant(narrative, item, grade, ok):
    text = f"CONCRETE POUR RECORD\nTicket: PR-99999\nJob: J\nArea: S-01 Platform North\nDate: 01/02/2025\n\n" \
           f"{narrative.replace('32/40', grade)}\n\nSigned (foreman): A. Foreman\nCountersigned (Engineer's representative): B. Engineer\n"
    q = Queue()
    r = records_cw.parse_file("civilwork/records/PR-99999.txt", text, q)
    assert not q.items and r.rule and (r.candidates == [item]) == ok and bool(r.spec_mismatch) != ok


def test_fd03_control_prior_code_ignores_the_specification(world, r3):
    """The review's table: on the earlier code each changed specification keeps its candidate and its value."""
    for ref, same, other, _u in FD03:
        base, _ = cw_eval(world, ref, lambda t: t, engine=r3.g3_cw, parser=r3.records_cw)
        r, q = cw_eval(world, ref, _narrative(world, ref, same, other), engine=r3.g3_cw, parser=r3.records_cw)
        assert (r.amount_status, r.amount, r.findings) == (base.amount_status, base.amount, base.findings) and not q.items


# ============================================================================================ FD04 required Parts
SCH5_LINES = {'DD-130': 'MDS-00001-013', 'DD-111': 'MDS-00001-021', 'RM-510': 'MDS-00018-026', 'LH-712': 'MDS-00018-033',
              'LW-410': 'MDS-00043-053', 'LW-411': 'MDS-00043-054', 'LW-412': 'MDS-00043-055', 'LW-413': 'MDS-00043-056',
              'LW-420': 'MDS-00043-057', 'LH-711': 'MDS-00251-033', 'LH-713': 'MDS-00282-016', 'LH-714': 'MDS-00383-050'}
FD04_CASES = [(code, ref, k) for code, ref in SCH5_LINES.items()
              for k in records_dds.part_keys(g3_dds.terms.dds().sch5[code])]


def _drop(key):
    return lambda t: "\n".join(x for x in t.split("\n") if not x.startswith(key + ":"))


@pytest.mark.parametrize("code, ref, key", FD04_CASES)
def test_fd04_every_required_content_line_missing_or_unreadable(world, code, ref, key):
    """Every Schedule 5 service, every content line of its Part (priced or not): missing -> unresolved; unreadable ->
    unresolved; the line's value is never kept on a Part that is not shown complete."""
    base, _ = dds_eval(world, ref, lambda t: t)
    assert base.payable is True and base.code == code
    part = g3_dds.terms.dds().sch5[code]
    for fn in (_drop(key), swap(key, "??")):
        r, q = dds_eval(world, ref, fn)
        assert r.amount_status == "unresolved" and r.amount is None and not r.alternatives, (code, key)
        assert any(c.check == "input" and c.detail.startswith(f"{part}.{key}") for c in r.checks)


def test_fd04_known_failure_takes_the_contractual_consequence(world):
    r, q = dds_eval(world, "MDS-00043-057", swap("Source handling certified", "No"))
    assert r.amount_status == "not_payable" and "source_handling_not_certified" in r.findings and not q.items


def test_fd04_unrelated_services_on_the_report_are_unaffected(world):
    """DD-101, DD-102, DD-120, HC-620, LW-401 on the same report as LW-420: Part D is not their condition of payment."""
    for ref in ("MDS-00043-048", "MDS-00043-049", "MDS-00043-050", "MDS-00043-051", "MDS-00043-052"):
        base, _ = dds_eval(world, ref, lambda t: t)
        for fn in (_drop("Sources handled"), swap("Source handling certified", "No"), swap("Source handling certified", "??")):
            r, _ = dds_eval(world, ref, fn)
            assert (r.amount_status, r.amount) == (base.amount_status, base.amount), ref


def test_fd04_control_prior_code_accepts_an_incomplete_or_negative_part(world, r3):
    for fn in (swap("Source handling certified", "No"), swap("Source handling certified", "??"),
               swap("Source handling certified", ""), _drop("Sources handled")):
        r, _ = dds_eval(world, "MDS-00043-057", fn, engine=r3.g3_dds, parser=r3.records_dds)
        assert (r.amount_status, r.amount, r.findings) == ("determined", Decimal("2746.55"), [])
    r, _ = dds_eval(world, "MDS-00001-021", _drop("Run circulating hours"), engine=r3.g3_dds, parser=r3.records_dds)
    assert (r.amount_status, r.amount) == ("determined", Decimal("3589.45"))
    r, _ = dds_eval(world, "MDS-00043-053", _drop("Run last day"), engine=r3.g3_dds, parser=r3.records_dds)
    assert r.amount_status == "conditional" and not any(c.check == "input" for c in r.checks)


# ============================================================================================ X8 evidence obligations
def test_x8_obligations_are_source_derived():
    """The obligation set comes from the specs (Sch 5 Part per code; the civil record layout), not from the engine."""
    ob = ns.obligations("DDS", "LW-420")
    assert {("doc", "D.Sources handled"), ("doc", "D.Source handling certified"), ("doc", "D.Source run"), ("part", "D")} <= ob
    assert ("doc", "B.Metres logged") in ns.obligations("DDS", "DD-111") and ns.obligations("DDS", "DD-101") == set()
    assert {("doc", "Date"), ("doc", "narrative"), ("doc", "Countersigned (Engineer's representative)")} <= ns.obligations("CW", "B.21.040")
    assert ns.obligations("CW", "A.12.020") == set()                     # no Schedule 5 record required


@pytest.fixture(scope="module")
def x8_r3_engine(world, r3):
    res = {"CW": g3_cw.run(world), "DDS": g3_dds.run(world)}
    return ns.x8(world, res, ns.Engines(r3.g3_cw, r3.g3_dds))


def test_x8_control_prior_code_engine_ignores_obligations(x8_r3_engine):
    """X8 on the earlier engines (G2 as now): required Part content removed or unreadable leaves a payable value -
    rejected as an ignored obligation (the review's FD04 note: an engine-derived relevance test would have accepted it)."""
    errs, stats = x8_r3_engine
    assert stats["obligation_ignored"] > 50
    assert any("B.Run circulating hours" in e and "source evidence obligation" in e for e in errs)
    assert any("D.Sources handled" in e and "source evidence obligation" in e for e in errs)


# ============================================================================================ FD05 night work
import csv  # noqa: E402
import io  # noqa: E402

import test_g3_review_missing_depths as r3t  # noqa: E402

CW_LINES = "civilwork/invoices/application_lines.csv"
NIGHT = {"PA-00001-04": "??", "PA-00001-01": "yes", "PA-00001-06": "1", "PA-00001-10": "n", "PA-00007-07": "",
         "PA-00001-02": "??", "PA-00023-03": "??", "PA-00002-05": "", "PA-00004-01": "??"}
ELIGIBLE_UNKNOWN = ["PA-00001-04", "PA-00001-01", "PA-00001-06", "PA-00001-10", "PA-00007-07"]
NIGHT_IRRELEVANT = {"PA-00001-02": "item without a night uplift", "PA-00023-03": "zone factor above 1.10 suppresses it (27A)",
                    "PA-00002-05": "Z4 Escarpment: zone factor above 1.10 suppresses it (27A)",
                    "PA-00004-01": "Z3 in 2026: zone factor 1.145 suppresses it (27A)"}


def _night_csv(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    for r in rows:
        r["night_work"] = NIGHT.get(r["line_ref"], r["night_work"])
    out = io.StringIO()
    wr = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    wr.writeheader()
    wr.writerows(rows)
    return out.getvalue()


@pytest.fixture(scope="module")
def night_world(tmp_path_factory):
    snap = r3t.snapshot_with(tmp_path_factory.mktemp("night"), CW_LINES, _night_csv)
    w = build.build(snap)
    return snap, w, g3_cw.run(w)


def test_fd05_unknown_night_statement_through_the_csv_loader(world, night_world):
    """Raw CSV -> G2 loader -> G3 batch: only Y or N states the fact. '??', 'yes', '1', 'n' and blank are queued by G2
    and handed over as None; where the night uplift changes the rate the line is unresolved and names night_work."""
    _snap, w, res = night_world
    base = g3_cw.run(world)
    queued = {u.ident for u in w.queue.items if u.kind == "cw_lines" and u.field == "night_work"}
    assert set(NIGHT) <= queued
    for ref in ELIGIBLE_UNKNOWN:
        r = res[ref]
        assert r.amount_status == "unresolved" and r.amount is None, ref
        assert any(c.detail.startswith("night_work") for c in r.checks) and any("night_work" in x for x in r.reasons)
        assert base[ref].amount_status == "determined"


def test_fd05_where_the_night_fact_cannot_change_the_value_it_stays_determined(world, night_world):
    _snap, w, res = night_world
    base = g3_cw.run(world)
    for ref, why in NIGHT_IRRELEVANT.items():
        assert (res[ref].amount_status, res[ref].amount) == (base[ref].amount_status, base[ref].amount), (ref, why)


def test_fd05_recognised_values_state_the_fact(world):
    line, app, rec = _cw_line(world, "PA-00001-04")
    y = g3_cw.evaluate({**line, "night_work": "Y"}, app, rec, True)
    n = g3_cw.evaluate({**line, "night_work": "N"}, app, rec, True)
    assert (y.amount, n.amount) == (Decimal("21631.15"), Decimal("17730.62"))


def test_fd05_control_prior_code_reads_unknown_as_daytime(night_world, r3):
    snap, _w, _res = night_world
    old_claims = r3_module("claims")
    c = old_claims.load(snap, Queue())
    line = next(x for x in c.rows["cw_lines"] if x.ident == "PA-00001-04").values
    app = next(h for h in c.rows["cw_headers"] if h.ident == line["application_no"]).values
    rec = build.build().cw[line["record_ref"]]
    assert line["night_work"] == "??" and not any(u.field == "night_work" and u.ident == "PA-00001-04" for u in c.queue.items)
    r = r3.g3_cw.evaluate(line, app, rec, True)
    assert (r.amount_status, r.amount) == ("determined", Decimal("17730.62"))


# ============================================================================================ FD06 DDS arithmetic
def _pd210_charge(w, qty, amount, engine=g3_dds):
    """MDS-00018-023 (PD-210, report 1792-1944 m) charged at `qty` m, USD 58.15, `amount`."""
    line, inv, ddr = _dds_line(w, "MDS-00018-023")
    line = {**line, "quantity": Decimal(qty), "amount": Decimal(amount)}
    kw = {"inputs": _ctx(w, "DDS")["MDS-00018-023"]} if engine is g3_dds else {}
    return engine.evaluate(line, inv, ddr, **kw)


# (quantity, billed amount, arithmetic finding expected): 152.5 x 58.15 = 8,867.875 -> 8,867.88 (tie, up to even);
# 152.3 x 58.15 = 8,856.245 -> 8,856.24 (tie, down to even); 152.2 x 58.15 = 8,850.43 (exact); 152.33 x 58.15 =
# 8,857.9895 -> 8,857.99 (not a tie); wrong cents on each
FD06 = [("152.5", "8867.88", False), ("152.3", "8856.24", False), ("152.2", "8850.43", False), ("152.33", "8857.99", False),
        ("152.5", "8867.87", True), ("152.3", "8856.25", True), ("152.2", "8850.44", True), ("152.33", "8857.98", True),
        ("152.5", "8867.875", False)]      # the exact product billed to a fraction of a cent: the multiplication is right


@pytest.mark.parametrize("qty, amount, wrong", FD06)
def test_fd06_arithmetic_uses_the_contracts_cent_rounding(world, qty, amount, wrong):
    r = _pd210_charge(world, qty, amount)
    assert ("amount_arithmetic" in r.findings) is wrong
    arith = next(c for c in r.checks if c.check == "arithmetic")
    assert arith.status == ("finding" if wrong else "pass")


def test_fd06_other_findings_are_kept(world):
    """152.5 m is within 25A's 1% of the report's 152 m: no arithmetic finding; the nomination condition stays."""
    r = _pd210_charge(world, "152.5", "8867.88")
    assert "amount_arithmetic" not in r.findings and any(c["dimension"] == "nomination" for c in r.conditions)


def test_fd06_control_prior_code_flags_a_correctly_rounded_amount(world, r3):
    for qty, amount in (("152.5", "8867.88"), ("152.3", "8856.24")):
        assert "amount_arithmetic" in _pd210_charge(world, qty, amount, engine=r3.g3_dds).findings


# ============================================================================================ FD07 allocation granularity
import verify_g3 as vg  # noqa: E402


def _cw_q(w, ref, qty, amount, engine=g3_cw):
    line, app, rec = _cw_line(w, ref)
    return engine.evaluate({**line, "quantity": Decimal(qty), "amount": Decimal(amount)}, app, rec, True)


def _arith(r):
    c = next(c for c in r.checks if c.check == "arithmetic")
    return c.status, c.finding


@pytest.mark.parametrize("amount, status", [("14894.14", "unresolved"), ("16051.20", "unresolved"), ("14768.64", "unresolved"),
                                            ("14000.00", "finding"), ("16100.00", "finding")])
def test_fd07_civil_status_is_independent_of_the_quantitys_spelling(world, amount, status):
    """PA-00008-06 (D.41.010, band state unknown): 384, 384.0 and 384.00 m2 are one quantity. An amount inside the range
    a division at the band edges gives (SAR 14,768.64-16,051.20 over every cumulative start) is unresolved, none selected;
    an amount outside it is a finding."""
    got = {_arith(_cw_q(world, "PA-00008-06", q, amount)) for q in ("384", "384.0", "384.00")}
    assert got == {(status, "amount_arithmetic")}


def test_fd07_civil_fractional_division_constructed_independently(world):
    """100.4 m2 at band 2 (39.71) + 283.6 m2 at band 3 (38.46) = 14,894.14: admissible (an earlier cumulative quantity of
    17,899.6 m2), so unresolved - and the reading names the range, not that division."""
    assert Decimal("100.4") * Decimal("39.71") + Decimal("283.6") * Decimal("38.46") == Decimal("14894.14")
    r = _cw_q(world, "PA-00008-06", "384", "14894.14")
    det = next(c.detail for c in r.checks if c.check == "arithmetic")
    assert "between SAR 14768.64 and SAR 16051.20" in det and "100.4" not in det


DDS_Q = ["98", "98.0", "98.00"]


def _s72(q, depths=None):
    c = copy.deepcopy(gcc.load_cases()["DDS-S72"])
    c["line"]["quantity"] = q
    if depths:
        c["line"]["depth_from_m"], c["line"]["depth_to_m"] = depths
    return c


def _domain(r):
    return next((x["domain"] for x in r.conditions if x.get("domain")), {})


def test_fd07_dds_domain_is_independent_of_spelling():
    rs = [gcc.engine_result(_s72(q)) for q in DDS_Q]
    doms = [(_domain(r)["step_m"], _domain(r)["count"], _domain(r)["mode"]) for r in rs]
    assert len(set(doms)) == 1 and doms[0] == (None, None, "continuous")          # FD07: no metre grid, no count
    assert all(_domain(r) == _domain(rs[0]) for r in rs)
    amts = [(_domain(r)["amount_bounds_usd"]["min"], _domain(r)["amount_bounds_usd"]["max"]) for r in rs]
    assert all(a == amts[0] for a in amts) and amts[0] == ("4908.70", "4940.30")
    for r in rs:
        assert vg.x3({"DDS": {"case": r}}) == []


def test_fd07_dds_fractional_charge_constructed_independently():
    """98.5 m charged on 1,450-1,550 m (band edge 1,500): band 1 carries 48.5 to 50 m, any real split (FD07). The
    unrounded extremes put every movable metre in band 1 (42.35) or band 2 (58.15); rounded, the lowest amount is one
    cent below that vertex (49.99985 / 48.50015 m: 2117.49 + 2820.28 = 4937.77)."""
    r = gcc.engine_result(_s72("98.5"))
    dom = _domain(r)
    assert dom["step_m"] is None and vg.x3({"DDS": {"case": r}}) == []
    amts = (Decimal(dom["amount_bounds_usd"]["min"]), Decimal(dom["amount_bounds_usd"]["max"]))
    lo = (Decimal("50") * Decimal("42.35")).quantize(Decimal("0.01")) + (Decimal("48.5") * Decimal("58.15")).quantize(Decimal("0.01"))
    hi = (Decimal("48.5") * Decimal("42.35")).quantize(Decimal("0.01")) + (Decimal("50") * Decimal("58.15")).quantize(Decimal("0.01"))
    assert (amts[0], amts[1]) == (lo - Decimal("0.01"), hi) == (Decimal("4937.77"), Decimal("4961.48"))
    same = gcc.engine_result(_s72("98.50"))
    assert _domain(same)["step_m"] is None and _domain(same) == dom


def test_fd07_control_prior_code_depends_on_spelling(world, r3):
    assert {_arith(_cw_q(world, "PA-00008-06", q, "14894.14", engine=r3.g3_cw)) for q in ("384", "384.0")} == {
        ("finding", "amount_arithmetic"), ("unresolved", "amount_arithmetic")}
    assert [str(r3.g3_dds.pd210_step(Decimal(q))) for q in DDS_Q] == ["1", "0.1", "0.01"]


R4 = "g3_before_pd210_domain_fix"   # the reviewed code FD07 (DDS) reopens


def _at_r4(path: str, name: str):
    """`path` as it stood before the PD-210 domain fix, executed as `name` (inside the audit package for audit modules)."""
    src = prior_code.source(R4, path)
    mod = types.ModuleType(name)
    if path.startswith("audit/"):
        mod.__package__ = "audit"
    mod.__file__ = str(ROOT / path)
    sys.modules[name] = mod
    exec(compile(src, f"{R4}:{path}", "exec"), mod.__dict__)
    return mod


# the earlier code's negative-control messages, as the runs below produce them
R4_CLOSURE_FAILURE = "domain claimed finite or complete"
R4_OFF_GRID = ("PD-210 allocation (Decimal('49.5'), Decimal('48.5')) is not admissible (sum 98, bands "
               "[(Decimal('48'), Decimal('50')), (Decimal('48'), Decimal('50'))], step 1)")
R4_COUNT = "PD-210 allocation domain incomplete: 4 distinct allocation(s) listed, the domain holds 3 (step 1); amounts 4908.70..4940.30"


def test_fd07_control_prior_code_claims_a_complete_three_point_domain(monkeypatch):
    """The FD07 closure controls, on the engine and verifier as they stood before the PD-210 domain fix: (1) its unmodified DDS-S72 path
    reaches the 98 m valuation and the three-point output; (2) the continuous-domain closure assertion fails on it;
    (3) a correctly formed 49.5 / 48.5 m witness at 4,916.60 is rejected by its pd210_domain_errors as off the 1 m grid
    and beyond its count of three; (4) the corrected verifier accepts that witness on the corrected result and rejects
    the invalid mutations (tests/test_g3_pd210_allocation_domain.py has each one)."""
    import test_g3_pd210_allocation_domain as fd7
    from audit import terms
    old_dds = _at_r4("audit/g3_dds.py", "audit._r4_g3_dds")
    old_vg = _at_r4("tools/verify_g3.py", "_r4_verify_g3")
    monkeypatch.setattr(gcc, "g3_dds", old_dds)
    old = gcc.engine_result(gcc.load_cases()["DDS-S72"])                    # (1) its own path, the packet unchanged
    monkeypatch.undo()
    dom = _domain(old)
    assert old.payable and old.allowed_quantity == Decimal("98") and old.amount is None
    assert (dom["mode"], dom["step_m"], dom["count"], dom["listed"], dom["not_listed"]) == ("enumerated", "1", 3, 3, "none")
    assert sorted(str(a["amount"]) for a in old.alternatives.values()) == ["4908.70", "4924.50", "4940.30"]
    assert old_vg.pd210_domain_errors(old, terms.dds()) == []                # consistent under its own grid
    with pytest.raises(AssertionError, match=R4_CLOSURE_FAILURE) as closure:  # (2)
        fd7.continuous_closure(old, "98", [("48", "50"), ("48", "50")], ("4908.70", "4940.30"))
    assert "{'mode': 'enumerated', 'allowed_m': '98', 'step_m': '1', 'count': 3" in str(closure.value)
    assert any("allocation domain is continuous and unresolved" in e for e in vg.x3({"DDS": {"case": old}}))
    w = fd7.with_witness(old, (Decimal("49.5"), Decimal("48.5")))            # (3) a valid trace: replays to 4916.60
    wa = next(a for k, a in w.alternatives.items() if "(test)" in k)
    assert wa["amount"] == Decimal("4916.60") and vg._check_trace("DDS", "PD-210", wa["trace"], terms_both(), Decimal("4916.60"),
                                                                  Decimal("98"), None) == []
    errs = old_vg.pd210_domain_errors(w, terms.dds())
    assert errs == [R4_OFF_GRID, R4_COUNT]
    new = gcc.engine_result(gcc.load_cases()["DDS-S72"])                    # (4)
    assert vg.x3({"DDS": {"case": fd7.with_witness(new, (Decimal("49.5"), Decimal("48.5")))}}) == []
    for bad in [fd7.with_witness(new, (Decimal("49.5"), Decimal("48.6"))), fd7.with_witness(new, (Decimal("50.1"), Decimal("47.9"))),
                fd7.with_witness(new, (Decimal("49.5"), Decimal("48.5")), rates={**fd7.RATE, "1": Decimal("42.36")}),
                fd7.with_witness(new, (Decimal("49.5"), Decimal("48.5")), total="4916.61"), fd7._three_point(new),
                fd7._drop_condition(new), fd7._bounds(new, "4908.70", "4940.29")]:
        assert vg.x3({"DDS": {"case": bad}})


def terms_both():
    from audit import terms
    return {"CW": terms.cw(), "DDS": terms.dds()}


# ============================================================================================ FD08 missing submission date
import datetime as dt  # noqa: E402

A3 = {"DDS": dt.date(2026, 8, 17), "CW": dt.date(2026, 5, 12)}       # retrospective A3 issue dates (DDS p42; CW p43)
FD08_LINES = {"DDS": {"after": "MDS-01650-048", "window": "MDS-01092-036", "unaffected": "MDS-01650-039"},
              "CW": {"after": "PA-00041-01", "window": "PA-00003-06", "unaffected": "PA-00005-03", "audit": "PA-00015-04"}}


def _with_submission(w, c, ref, sub, engine=None):
    if c == "DDS":
        line, inv, ddr = _dds_line(w, ref)
        eng = engine or g3_dds
        kw = {"inputs": _ctx(w, "DDS")[ref]} if eng is g3_dds else {}
        return eng.evaluate(line, {**inv, "invoice_date": sub}, ddr, **kw)
    row = next(x for x in w.claims.rows["cw_lines"] if x.ident == ref).values
    app = {h.ident: h.values for h in w.claims.rows["cw_headers"]}[row["application_no"]]
    rec = w.cw.get(row.get("record_ref") or "")
    eng = engine or g3_cw
    kw = {"inputs": _ctx(w, "CW")[ref]} if eng is g3_cw else {}
    return eng.evaluate(row, {**app, "application_date": sub}, rec, rec is not None, **kw)


def _a3(r):
    return any(x.startswith("a3_adjustment") for x in r.g4_dependencies)


@pytest.mark.parametrize("c, case", [(c, k) for c, d in FD08_LINES.items() for k in d])
def test_fd08_every_regime_before_on_after_and_missing(world, c, case):
    ref, issue = FD08_LINES[c][case], A3[c]
    before = _with_submission(world, c, ref, issue - dt.timedelta(days=1))
    on = _with_submission(world, c, ref, issue)
    after = _with_submission(world, c, ref, issue + dt.timedelta(days=30))
    missing = _with_submission(world, c, ref, None)
    v = ns.value_of                                                    # amount or every band alternative's amount
    assert v(on) == v(after) and not _a3(on)                           # on the issue day the instrument applies
    if case == "unaffected":
        assert v(before) == v(on) == v(missing) and missing.amount_status != "unresolved"
        return
    assert v(before) != v(on) and _a3(before)                          # submitted before issue: protected, adjustment later
    assert missing.amount_status == "unresolved" and missing.amount is None and _a3(missing)
    date = "invoice_date" if c == "DDS" else "application_date"
    assert any(x.startswith(date) for x in missing.reasons)


def _blank_date(field, ident_field, ident):
    def f(text):
        rows = list(csv.DictReader(io.StringIO(text)))
        for r in rows:
            if r[ident_field] == ident:
                r[field] = ""
        out = io.StringIO()
        wr = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
        wr.writeheader()
        wr.writerows(rows)
        return out.getvalue()
    return f


@pytest.mark.parametrize("c, rel, field, ref", [
    ("DDS", "drilling_services/invoices/invoices.csv", "invoice_date", "MDS-01650-048"),
    ("CW", "civilwork/invoices/applications.csv", "application_date", "PA-00015-04")])
def test_fd08_missing_date_through_the_csv_loader(world, tmp_path, c, rel, field, ref):
    lk, hk = ("dds_lines", "invoice_no") if c == "DDS" else ("cw_lines", "application_no")
    head = next(x for x in world.claims.rows[lk] if x.ident == ref).values[hk]
    snap = r3t.snapshot_with(tmp_path, rel, _blank_date(field, hk, head))
    w = build.build(snap)
    r = (g3_dds if c == "DDS" else g3_cw).run(w)[ref]
    assert any(u.field == field for u in w.queue.items if u.ident == head)
    assert r.amount_status == "unresolved" and _a3(r)


def test_fd08_control_prior_code_fixes_the_post_amendment_rate(world, r3):
    for c, ref, amount in (("DDS", "MDS-01650-048", Decimal("3634.44")), ("CW", "PA-00041-01", Decimal("10951.68"))):
        r = _with_submission(world, c, ref, None, engine=r3.g3_dds if c == "DDS" else r3.g3_cw)
        assert (r.amount_status, r.amount) == ("determined", amount) and not _a3(r)


# ============================================================================================ falsification (interactions)
def test_fd02_fd04_certification_written_both_ways_is_unresolved_not_a_known_failure(world):
    """Part D repeated with 'certified: Yes' in one copy and 'No' in the other, in either order: which copy is the report
    is open - unresolved, never the 'No' consequence and never the 'Yes' value by position."""
    for fn in (_dup_part("D", lambda x: x.replace("certified: Yes", "certified: No")),
               lambda t: _dup_part("D")(t).replace("certified: Yes", "certified: No", 1)):
        r, q = dds_eval(world, "MDS-00043-057", fn)
        assert r.amount_status == "unresolved" and "source_handling_not_certified" not in r.findings
        assert any(u.field == "D.Source handling certified" and u.reason == "key repeated" for u in q.items)
