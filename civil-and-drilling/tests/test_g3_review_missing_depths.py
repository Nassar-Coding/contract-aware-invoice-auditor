"""G3 missing inputs: a PD-210 charge without its depths - start
only, end only, or both - returns an explicit missing-depth finding and an owned unresolved result, through the case
path, the typed G2 handoff (the real claims loader on a CSV with the cells blank) and the batch; the batch carries on;
the earlier, unguarded engine fails the same controls."""
import copy
import csv
import datetime as dt
import io
import prior_code
import types
from decimal import Decimal
from pathlib import Path

import pytest

import g3_case_compare as gcc
import verify_g3 as vg
from audit import build, g3_dds
from audit.common import SNAPSHOT

ROOT = Path(__file__).resolve().parents[1]
OLD = "g3_unguarded"                                  # the reviewed, unguarded engine
LINES = "drilling_services/invoices/invoice_lines.csv"
PROBES = {"MDS-00018-023": ("depth_from_m",), "MDS-00018-039": ("depth_to_m",), "MDS-00018-054": ("depth_from_m", "depth_to_m")}


def old_module(name: str, rev: str = OLD):
    """The module as committed at `rev` (default g3_unguarded), executed inside the audit package (its relative imports
    resolve)."""
    src = prior_code.source(rev, f"audit/{name}.py")
    mod = types.ModuleType(f"audit._old_{name}")
    mod.__package__ = "audit"
    exec(compile(src, f"{rev}:audit/{name}.py", "exec"), mod.__dict__)
    return mod


def snapshot_with(root: Path, rel: str, edit) -> Path:
    """A copy of the pinned snapshot (symlinks) in which one file is replaced by edit(text)."""
    for p in SNAPSHOT.rglob("*"):
        r = p.relative_to(SNAPSHOT)
        if ".git" in r.parts:
            continue
        dst = root / r
        if p.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if str(r) == rel:
            dst.write_text(edit(p.read_text(encoding="utf-8")), encoding="utf-8")
        else:
            dst.symlink_to(p)
    return root


def blank_depths(text: str) -> str:
    rows = list(csv.DictReader(io.StringIO(text)))
    for r in rows:
        for f in PROBES.get(r["line_ref"], ()):
            r[f] = ""
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()


@pytest.fixture(scope="module")
def base():
    w = build.build()
    return w, g3_dds.run(w)


@pytest.fixture(scope="module")
def blanked(tmp_path_factory):
    snap = snapshot_with(tmp_path_factory.mktemp("snap"), LINES, blank_depths)
    w = build.build(snap)
    return w, g3_dds.run(w)


def _case(**line):
    c = copy.deepcopy(gcc.load_cases()["DDS-S72"])
    c["line"].update(line)
    return c


def _explicit_unresolved(r, gone):
    assert r.amount_status == "unresolved" and r.payable is None
    assert r.amount is None and r.allowed_quantity is None and not r.alternatives          # no value guessed, no zero
    assert not [s for s in r.trace if s["op"] in ("part", "amount", "sum_parts")]
    assert "depths_missing" in r.findings
    named = {c.detail.split(":")[0] for c in r.checks if c.check == "input" and c.finding == "depths_missing"}
    assert named == set(gone)
    assert any(c["dimension"] == "input" and c["owner"] == "G5" and "Cl.34" in c["basis"] for c in r.conditions)
    assert any("not guessed from the report, not zero" in x for x in r.reasons)


# ---------------------------------------------------------------------------------------------------- case path
@pytest.mark.parametrize("gone", [("depth_from_m",), ("depth_to_m",), ("depth_from_m", "depth_to_m")])
def test_missing_depths_case_path(gone):
    """The review's probe (DDS-S72 with depth_from_m = None) and its end-only and both-missing companions."""
    r = gcc.engine_result(_case(**{g: None for g in gone}))
    _explicit_unresolved(r, gone)
    # what holds for every interval inside the report's 1,450-1,550 m is kept
    assert any("the report measures 1450-1550 m, 100 m drilled on the day" in x for x in r.readings)
    assert vg.x3({"DDS": {"case": r}}) == []


def test_missing_depths_keeps_findings_that_hold_for_every_interval():
    """A charge above the day's metres is above the report whatever depths it would state (25A); a report inside one band
    fixes the rate whatever the interval (Cl.23). Both stay findings although the value is unresolved."""
    c = copy.deepcopy(gcc.load_cases()["DDS-S68"])                     # report 2,900-3,000 m: band 2 only
    c["line"].update(depth_from_m=None, quantity="150", unit_rate="76.45", amount="11467.50")
    r = gcc.engine_result(c)
    _explicit_unresolved(r, ("depth_from_m",))
    assert {"quantity_above_report", "rate_differs"} <= set(r.findings)
    ok = copy.deepcopy(gcc.load_cases()["DDS-S68"])
    ok["line"].update(depth_to_m=None, quantity="100", unit_rate="58.15", amount="5815.00")
    r2 = gcc.engine_result(ok)
    _explicit_unresolved(r2, ("depth_to_m",))
    assert not {"quantity_above_report", "rate_differs"} & set(r2.findings)


def test_missing_depths_established_consequences_still_stand():
    """Not a performance section: not payable whatever the depths (Cl.23; App A) - an established fact, not a guess."""
    c = _case(depth_from_m=None)
    c["report"] = c["report"].replace('Hole section: 12-1/4"', 'Hole section: 17-1/2"')
    c["line"]["hole_section"] = '17-1/2"'
    r = gcc.engine_result(c)
    assert r.amount_status == "not_payable" and "not_performance_section" in r.findings


# ---------------------------------------------------------------------------------------------------- typed G2 handoff + batch
def test_missing_depths_through_the_g2_loader_and_the_batch(base, blanked):
    """The CSV cells blanked in a copy of the snapshot: the real G2 loader types them None and queues them ('required field
    blank'); the production batch g3_dds.run carries on and gives each probe line an explicit owned
    unresolved result with its source line; every other drilling line is unchanged."""
    w0, res0 = base
    w1, res1 = blanked
    queued = {(u.ident, u.field) for u in w1.queue.items if u.kind == "dds_lines"}
    for ref, gone in PROBES.items():
        row = next(x for x in w1.claims.rows["dds_lines"] if x.ident == ref)
        assert all(row.values[g] is None for g in gone)
        assert all((ref, g) in queued for g in gone)
        r = res1[ref]
        _explicit_unresolved(r, gone)
        src = f"{LINES}:{row.source.line}"
        assert all(src in c.detail for c in r.checks if c.check == "input")                 # provenance
    assert len(res1) == len(res0) == 91244
    changed = [ref for ref in res0 if ref not in PROBES and
               {k: v for k, v in res0[ref].to_json().items() if k != "ctx"} != {k: v for k, v in res1[ref].to_json().items() if k != "ctx"}]
    assert changed == []
    assert vg.x3({"DDS": {ref: res1[ref] for ref in PROBES}}) == []


def test_mds_00018_023_start_depth_none_in_the_population_batch(base):
    """The review's second probe exactly: the typed start depth of MDS-00018-023 set to None in memory, then the
    production batch."""
    w, res0 = base
    row = next(x for x in w.claims.rows["dds_lines"] if x.ident == "MDS-00018-023")
    keep = row.values["depth_from_m"]
    row.values["depth_from_m"] = None
    try:
        res = g3_dds.run(w)
    finally:
        row.values["depth_from_m"] = keep
    _explicit_unresolved(res["MDS-00018-023"], ("depth_from_m",))
    assert len(res) == len(res0)


# ---------------------------------------------------------------------------------------------------- controls
@pytest.mark.parametrize("gone", [("depth_from_m",), ("depth_to_m",), ("depth_from_m", "depth_to_m")])
def test_control_the_old_engine_fails_the_case_path(gone, monkeypatch):
    """The unguarded engine, called as it was designed (no G2 provenance argument): the complete case values; the same
    case without its depths crashes on the missing depth itself."""
    old = old_module("g3_dds")
    monkeypatch.setattr(gcc, "g3_dds", old)
    assert gcc.engine_result(_case()).amount_status == "conditional"
    with pytest.raises(TypeError, match="not supported between instances of 'decimal.Decimal' and 'NoneType'"):
        gcc.engine_result(_case(**{g: None for g in gone}))


def test_control_the_old_engine_stops_the_batch(blanked):
    w1, _ = blanked
    with pytest.raises(TypeError, match="not supported between instances of 'decimal.Decimal' and 'NoneType'"):
        old_module("g3_dds").run(w1)


def test_control_x3_rejects_a_contained_engine_error(base):
    w, _ = base
    line = next(x for x in w.claims.rows["dds_lines"] if x.ident == "MDS-00018-023").values
    r = g3_dds.engine_error(line, "MDS-00018-023", TypeError("boom"))
    assert any("engine error: TypeError: boom" in e for e in vg.x3({"DDS": {"MDS-00018-023": r}}))


def test_batch_contains_an_engine_error_and_carries_on(base, monkeypatch):
    """A defect on one line never stops the others: the batch records it as an explicit engine_error (which X3 rejects)."""
    w, res0 = base
    real = g3_dds.evaluate

    def broken(line, inv, ddr, **kw):
        if line["line_ref"] == "MDS-00018-023":
            raise TypeError("injected")
        return real(line, inv, ddr, **kw)
    monkeypatch.setattr(g3_dds, "evaluate", broken)
    res = g3_dds.run(w)
    assert res["MDS-00018-023"].amount_status == "unresolved" and "engine_error" in res["MDS-00018-023"].unresolved
    assert len(res) == len(res0) and res["MDS-00018-039"].amount == res0["MDS-00018-039"].amount


# ---------------------------------------------------------------------------------------------------- X8 nullable sweep
import yaml  # noqa: E402

import null_sweep as ns  # noqa: E402
from audit import g3_cw, g3_run  # noqa: E402
from audit.g3_core import Inputs, result_keys  # noqa: E402


@pytest.fixture(scope="module")
def both(base):
    w, dds = base
    return w, {"CW": g3_cw.run(w), "DDS": dds}


@pytest.fixture(scope="module")
def old_x8(both):
    w, res = both
    return ns.x8(w, res, ns.Engines(old_module("g3_cw"), old_module("g3_dds")))


def test_x8_passes_on_every_code_family(both):
    w, res = both
    stats = {}
    assert vg.x8(w, res, stats=stats) == []
    fams = yaml.safe_load((ROOT / "spec/g3_code_families.yaml").read_text())["contracts"]
    want = {f"{c} family {f['id']}" for c, fs in fams.items() for f in fs}
    assert want == {k for k in stats if " family " in k}                      # all 18 families, incl. no-billed-line ones
    assert stats["mutations"] > 9000 and stats["batch_runs"] > 190
    assert stats["explicit_unresolved"] > 1000 and stats["registered"] > 100 and stats["irrelevant_unchanged"] > 5000


def test_x8_control_the_unguarded_engine_fails(old_x8):
    """The engine the audit tested: exceptions (the PD-210 depths among them), rejections without a contract rule,
    and a batch that stops."""
    errs, stats = old_x8
    assert stats["exceptions"] > 900 and stats["silent"] > 1000
    assert any("depth_from_m" in e and "exception TypeError" in e for e in errs)
    assert any("CW" in e and " unit=" in e and "not payable without the contract's own rule" in e for e in errs)
    assert any(e.startswith("DDS batch with line depth_from_m empty: exception TypeError") for e in errs)


def _cw_variant(evaluate, run=None):
    m = types.SimpleNamespace(evaluate=evaluate, input_context=g3_cw.input_context, inputs_from_world=g3_cw.inputs_from_world)

    def default_run(w):
        ctx, out, rows = g3_cw.input_context(w), {}, w.claims.rows["cw_lines"]
        for key, (line, app, rec, ex) in zip(result_keys(rows), g3_cw.inputs_from_world(w)):
            out[key] = evaluate(line, app, rec, ex, inputs=ctx[key])
        return out
    m.run = run or default_run
    return m


def _x8_with(both, cw_mod):
    w, res = both
    return ns.x8(w, res, ns.Engines(cw_mod, g3_dds))[0]


def test_x8_control_a_silently_defaulted_zone(both):
    def zone_z1(line, app, record, exists, band_pct=None, T=None, inputs=None):
        if not (line.get("site_zone") or "").strip():
            line = {**line, "site_zone": "Z1 Compound"}                          # the default X8 must catch
        return g3_cw.evaluate(line, app, record, exists, band_pct=band_pct, T=T, inputs=inputs)
    errs = _x8_with(both, _cw_variant(zone_z1))
    assert any("site_zone" in e and "does not name it (silent)" in e for e in errs)


def test_x8_control_a_named_default_is_still_a_default(both):
    def zone_z1_named(line, app, record, exists, band_pct=None, T=None, inputs=None):
        blank = not (line.get("site_zone") or "").strip()
        r = g3_cw.evaluate({**line, "site_zone": "Z1 Compound"} if blank else line, app, record, exists, band_pct=band_pct, T=T, inputs=inputs)
        if blank:
            r.readings.append("site_zone not stated: taken as Z1")
        return r
    errs = _x8_with(both, _cw_variant(zone_z1_named))
    assert any("site_zone" in e and "fixes a value" in e for e in errs)


def test_x8_control_an_overcautious_engine_loses_a_known_value(both):
    def overcautious(line, app, record, exists, band_pct=None, T=None, inputs=None):
        r = g3_cw.evaluate(line, app, record, exists, band_pct=band_pct, T=T, inputs=inputs)
        if app is not None and not (app.get("contract_ref") or "").strip():
            r.amount_status, r.payable, r.amount, r.allowed_quantity, r.unit_rate, r.alternatives = "unresolved", None, None, None, None, {}
        return r
    errs = _x8_with(both, _cw_variant(overcautious))
    assert any("contract_ref" in e and "a known value lost" in e for e in errs)


def test_x8_control_a_batch_that_drops_lines(both):
    def dropping_run(w):
        rows = dict(zip(result_keys(w.claims.rows["cw_lines"]), w.claims.rows["cw_lines"]))
        return {k: r for k, r in g3_cw.run(w).items() if rows[k].values.get("quantity") is not None}
    errs = _x8_with(both, _cw_variant(g3_cw.evaluate, dropping_run))
    assert any("CW batch with line quantity empty" in e and "results for" in e for e in errs)


def test_x8_control_a_batch_that_is_not_the_direct_evaluation(both):
    def provenance_lost_run(w):
        rows = w.claims.rows["cw_lines"]
        return {key: g3_cw.evaluate(line, app, rec, ex)                          # G2's provenance dropped on the way
                for key, (line, app, rec, ex) in zip(result_keys(rows), g3_cw.inputs_from_world(w))}
    errs = _x8_with(both, _cw_variant(g3_cw.evaluate, provenance_lost_run))
    assert any("differs from its direct evaluation" in e for e in errs)


# ---------------------------------------------------------------------------------------------------- falsification of fix 2
# Found after fix 2 by trying to break it: the output stage crashed on an empty
# quantity; a line with no value at G3 was valued as 0 or left out silently; a repeated or blank line reference let one
# line's result replace another's; a report number carried by two files was resolved by G2's first-file default; a
# missing countersignature was hidden by a twice-written foreman line; an empty civil code hid an established out-of-term.
def _one_blank(w, lk, ref, field):
    row = next(x for x in w.claims.rows[lk] if x.ident == ref)
    r2 = copy.copy(row)
    r2.values = {**row.values, field: None}
    sub = copy.copy(w)
    sub.claims = copy.copy(w.claims)
    sub.claims.rows = {**w.claims.rows, lk: [r2 if x is row else x for x in w.claims.rows[lk]]}
    return sub


def test_output_stage_on_an_empty_quantity(both):
    """The falsification probe: a Q3 line (no record; a single contract rate) with its quantity empty. The output stage
    completes, keeps the line's adopted value (not payable: no record, whatever the quantity) and lists it as not valued
    under reading B (no billed quantity to price) instead of crashing."""
    w, res0 = both
    ref = next(k for k in g3_run.decision_scopes(w, res0)["Q3"]["lines"] if k in res0["CW"] and res0["CW"][k].unit_rate is not None)
    sub = _one_blank(w, "cw_lines", ref, "quantity")
    res = {"CW": g3_cw.run(sub), "DDS": res0["DDS"]}
    assert res["CW"][ref].amount_status == "not_payable"
    sc = g3_run.decision_scopes(sub, res)
    assert sc["Q3"]["effect_by_reading"]["B"]["lines_not_valued"] == {"SAR": [ref]}
    g3_run.summary(sub, res), g3_run.trace_sample(res)
    old = old_module("g3_run")                                                # the unguarded output stage:
    old.decision_scopes(w, res0)                                              # completes on the complete world,
    with pytest.raises(TypeError, match=r"unsupported operand type\(s\) for \*: 'NoneType' and 'decimal.Decimal'"):
        old.decision_scopes(sub, res)                                         # crashes on the empty quantity


def test_q3_reading_b_values_every_line(both):
    """Reading B prices each Q3 line at its billed quantity and every admissible contract rate its rate check formed -
    the three civil lines whose rate is conditional (ground or band) were silently left out before."""
    w, res = both
    sc = g3_run.decision_scopes(w, res)["Q3"]
    assert "lines_not_valued" not in sc["effect_by_reading"]["B"] and "lines_not_valued" not in sc
    lines = g3_run.claim_lines(w)["CW"]
    old = old_module("g3_run")
    for ref in ("PA-00111-13", "PA-00609-01", "PA-00613-01"):
        r, q = res["CW"][ref], lines[ref]["quantity"]
        assert r.unit_rate is None and len(set(r.rates.values())) > 1
        assert g3_run.evaluate_as_payable(w, r, q) == (q * min(r.rates.values()), q * max(r.rates.values()))
        assert old.evaluate_as_payable(w, r, q) is None                        # the omission (control)


def test_a_line_with_no_value_is_never_counted(both):
    """_money never gives a line without a value at G3 a value (0 or otherwise); the scope lists it by status."""
    w, res = both
    ds900 = next(r for r in res["DDS"].values() if r.code == "DS-900")
    assert ds900.payable is None and g3_run._money([ds900]) == {}
    assert g3_run._not_valued([ds900], lambda r: r.line_ref) == {"deferred": [ds900.line_ref]}
    assert old_module("g3_run")._money([ds900]) == {"USD": "0"}                # the old stage valued it as 0 (control)
    s = g3_run.summary(w, res)["contracts"]["DDS"]["codes"]["DS-900"]
    assert s["value_not_formed"] == s["lines"] and s["rate_not_single"] == 0


def test_batch_never_lets_one_line_replace_another(both):
    """Two lines sharing a reference, and a line with none (G2 then identifies it by its source position): every line
    has its own result and its own provenance; the unguarded batch loses one."""
    w, _ = both
    rows = w.claims.rows["cw_lines"][:4]
    twin, blank = copy.copy(rows[1]), copy.copy(rows[2])
    twin.values, twin.ident = {**rows[1].values, "line_ref": rows[0].values["line_ref"]}, rows[0].values["line_ref"]
    blank.values = {**rows[2].values, "line_ref": ""}
    blank.ident = f"{rows[2].source.path}:{rows[2].source.line}"
    sub = copy.copy(w)
    sub.claims = copy.copy(w.claims)
    sub.claims.rows = {**w.claims.rows, "cw_lines": [rows[0], twin, blank, rows[3]]}
    keys = result_keys(sub.claims.rows["cw_lines"])
    assert keys == [f"{rows[0].source.path}:{rows[0].source.line}", f"{rows[1].source.path}:{rows[1].source.line}",
                    blank.ident, rows[3].values["line_ref"]]
    out = g3_cw.run(sub)
    assert list(out) == keys
    ctx = g3_cw.input_context(sub)
    assert [ctx[k].line_src for k in keys] == [f"{x.source.path}:{x.source.line}" for x in (rows[0], twin, blank, rows[3])]
    assert len(old_module("g3_cw").run(sub)) == 3                              # control: one line lost
    g3_run.summary(sub, {"CW": out, "DDS": {}}), g3_run.trace_sample({"CW": out, "DDS": {}})


def _with_copy(w, ref, date_shift=1):
    """The world with a second delivered file carrying report `ref`'s number (a report of another day), as G2 loads it:
    indexed only the first, the second queued."""
    d = w.ddr[ref]
    text = ns.doc_text(d)
    other = ns.reparse("DDS", d, ns._swap_value(text, "Date", f"{d.date + dt.timedelta(days=date_shift):%d-%b-%Y}"))[0]
    sub = copy.copy(w)
    sub.ddr_by_file = {**w.ddr_by_file, f"COPY-{d.file}": other}
    return sub


def test_report_number_repeated_is_not_resolved_by_g2s_first_file(both):
    """G2 indexes a report by its Report number and keeps the first file carrying it: which file is the report for a
    charge is then open, and no fact is taken from either (identity is never inferred from content)."""
    w, res = both
    ref = "MDS-00018-023"
    line = next(x for x in w.claims.rows["dds_lines"] if x.ident == ref).values
    sub = _with_copy(w, line["report_ref"])
    r = g3_dds.run(sub)[ref]
    assert res["DDS"][ref].payable is True and r.amount_status == "unresolved" and r.payable is None
    gap = [c for c in r.checks if c.check == "input" and c.detail.startswith("report_ref:")]
    assert gap and "COPY-" in gap[0].detail and "Report number" in gap[0].detail
    assert any(c["dimension"] == "input" and c["owner"] == "G5" for c in r.conditions)
    assert old_module("g3_dds").run(sub)[ref].payable is True                  # control: the first file silently used


def test_established_consequences_dominate_an_open_identity(both):
    """Outside the term, a charge is not payable whatever its report or code: an open report identity or an empty
    civil item code does not turn that into 'unresolved'. An empty drilling code stays unresolved: it may be the
    invoice-level DS-900, which no finding on the day decides (Cl.38)."""
    w, _ = both
    T = g3_dds.terms.dds()
    ref = "MDS-00018-023"
    row = next(x for x in w.claims.rows["dds_lines"] if x.ident == ref)
    late = T.expiry + dt.timedelta(days=1)
    inv = {h.ident: h.values for h in w.claims.rows["dds_headers"]}[row.values["invoice_no"]]
    ddr = w.ddr[row.values["report_ref"]]
    r = g3_dds.evaluate({**row.values, "service_date": late}, inv, ddr, inputs=Inputs(report_copies=("COPY",)))
    assert r.amount_status == "not_payable" and "out_of_term" in r.findings
    r = g3_dds.evaluate({**row.values, "service_date": late, "service_code": "ZZ-999"}, inv, ddr, inputs=Inputs())
    assert r.amount_status == "not_payable" and "out_of_term" in r.findings
    r = g3_dds.evaluate({**row.values, "service_date": late, "service_code": None}, inv, ddr, inputs=Inputs())
    assert r.amount_status == "unresolved" and "out_of_term" in r.findings
    cw = next(x for x in w.claims.rows["cw_lines"] if x.ident == "PA-00375-07")          # out of term in the population
    app = {h.ident: h.values for h in w.claims.rows["cw_headers"]}[cw.values["application_no"]]
    rec = w.cw.get(cw.values.get("record_ref") or "")
    r = g3_cw.evaluate({**cw.values, "item_code": None}, app, rec, rec is not None, inputs=Inputs())
    assert r.amount_status == "not_payable" and "out_of_term" in r.findings
    assert old_module("g3_cw", "g3_missing_inputs_first_fix").evaluate({**cw.values, "item_code": None}, app, rec, rec is not None,
                                                   inputs=Inputs()).amount_status == "unresolved"   # control


def test_a_twice_written_foreman_line_does_not_hide_a_missing_countersignature(both):
    """PA-00613-01's record DX-00089 has no Engineer's countersignature: unsigned (Cl.47) whatever its foreman line says."""
    w, res = both
    row = next(x for x in w.claims.rows["cw_lines"] if x.ident == "PA-00613-01")
    app = {h.ident: h.values for h in w.claims.rows["cw_headers"]}[row.values["application_no"]]
    rec = w.cw[row.values["record_ref"]]
    twice = Inputs(doc_repeated=frozenset({"Signed (foreman)"}))
    r = g3_cw.evaluate(row.values, app, rec, True, inputs=twice)
    assert r.amount_status == "not_payable" and "record_unsigned" in r.findings
    assert "Countersigned (Engineer's representative) missing" in next(c.detail for c in r.checks if c.finding == "record_unsigned")
    assert old_module("g3_cw", "g3_missing_inputs_first_fix").evaluate(row.values, app, rec, True, inputs=twice).amount_status == "unresolved"


def test_x8_output_stage_control_the_unguarded_output_stage(both):
    w, res = both
    errs, _ = ns.batch(w, res, ns.Engines(g3_cw, g3_dds, old_module("g3_run")), fields={"quantity", "line_ref", "service_date"})
    assert any(e.startswith("CW outputs with line quantity empty: exception TypeError") for e in errs)
    assert any(e.startswith("CW outputs with line line_ref empty: exception KeyError") for e in errs)
    assert any("the summary compares or values" in e for e in errs)


def test_x8_output_stage_control_a_scope_that_hides_unvalued_lines(both, monkeypatch):
    w, res = both
    monkeypatch.setattr(g3_run, "_not_valued", lambda rs, key: {})
    errs, _ = ns.batch(w, res, ns.Engines(g3_cw, g3_dds), fields={"service_date"})
    assert any("reaches" in e and "lists 0 as not valued" in e for e in errs)


def test_x8_control_an_engine_that_ignores_a_repeated_report_number(old_x8):
    """X8 carries the repeated-number state: the unguarded drilling engine, which takes G2's first file silently, fails."""
    errs, _ = old_x8
    assert any("doc Report='repeated'" in e for e in errs)


def test_x8_coverage_control():
    """X8 fails when a claim field is never emptied on a family's line (here: a family seen, no field emptied on it)."""
    errs = ns.coverage({"CW family CW-MEAS": 1, "CW cell CW-MEAS|line|quantity": 2})
    assert "X8 coverage: CW line quantity never emptied on families ['CW-MEAS']" not in errs
    assert "X8 coverage: CW line unit never emptied on families ['CW-MEAS']" in errs


def test_a_header_number_two_rows_carry_gives_no_header_fact(both):
    """Two application rows with one number (G2's input assertion 'ids unique' then fails; G2 hands both over): the line
    takes no fact from either and names both rows; the batch equals the direct evaluation. The engine before this fix took
    the last row silently (control)."""
    w, _ = both
    row = next(x for x in w.claims.rows["cw_lines"] if x.ident == "PA-00001-01")
    h = next(x for x in w.claims.rows["cw_headers"] if x.ident == row.values["application_no"])
    sub = copy.copy(w)
    sub.claims = copy.copy(w.claims)
    sub.claims.rows = {**w.claims.rows, "cw_headers": list(w.claims.rows["cw_headers"]) + [ns._later_copy(h)]}
    r = g3_cw.run(sub)["PA-00001-01"]
    gap = next(c for c in r.checks if c.check == "input" and c.detail.startswith("application_no:"))
    assert "carried by 2 application rows" in gap.detail and f"{h.source.path}:{h.source.line}" in gap.detail
    assert not any(c.check == "window" and c.status in ("pass", "finding") for c in r.checks)      # no header fact used
    old = old_module("g3_cw", "g3_missing_inputs_second_fix").run(sub)["PA-00001-01"]
    assert any(c.check == "window" and c.status in ("pass", "finding") for c in old.checks)        # the last row, silently
