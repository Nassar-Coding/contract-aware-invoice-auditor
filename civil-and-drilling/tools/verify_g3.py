"""G3 exit checks: local entitlement and pricing.

Exit condition: "Independently calculated clause-based cases pass, including exceptions and boundaries; all billed code
families are implemented or explicitly marked unresolved; each amount has an explainable calculation trace." Checks:

  X1 independent cases: every reference case agrees with the readers' expected values or carries a disposition bound
     to both values; every item of the G3 scope has a case per contract that exercises it (boundary pairs included, and
     the rounding cases sit on an exact half where the two modes differ); inputs were committed before the readers'
     outputs, and the first case set before the pricing code (git order).
  X2 code families: every billed code is in exactly one family of spec/g3_code_families.yaml (membership from the
     verified tables); the engine valued every line under that family; implemented families have a value or a reason on
     every line and an agreeing reference case; deferred/unresolved families carry that status and an owner; every
     pricing table that applies to a line (FX, index, zone, ground, bands, uplifts, discount, section, class, standby,
     depth band, loss value) appears in its trace.
  X3 traces: every trace replays with independent arithmetic (every step's value, every rounding), starts from a figure
     of the verified tables, cites a source on every step, ends at the line's rate and amount (every alternative too),
     and follows the contract's rounding: CW one half-up rounding last, half-even only after FX (26A) or index (29A);
     DDS half-even after every step (Cl.17).
  X4 decisions: every entry of spec/g3_decisions.yaml has basis with pages, alternatives, known cases that pass, and a
     computed scope giving lines decided and the effect of the adopted reading and of every alternative; the question
     and carried-item registers agree with it; no question still blocks G3.
  X5 boundary: no line's result depends on any other line (evaluation in reverse order gives identical results), and
     the G3 modules produce no classification, flag, invoice total or audit result.
  X6 billing independence: changing every billed rate and amount changes no contract value (rate, quantity, amount,
     payability, alternatives); billed figures only produce findings.
  X7 reproduction and context: the committed verification/g3 outputs reproduce; every result and output carries the
     current run context (code + reviewed inputs + snapshot).

Usage::  python tools/verify_g3.py
"""
from __future__ import annotations

import copy
import json
import math
import re
import sys
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import g3_case_compare as gcc  # noqa: E402
import spec_lib as sl  # noqa: E402
from audit import build, g3_cw, g3_dds, g3_run, terms  # noqa: E402

OUT = ROOT / "verification" / "g3"
FAMILIES = ROOT / "spec" / "g3_code_families.yaml"
DECISIONS = ROOT / "spec" / "g3_decisions.yaml"
QUESTIONS = ROOT / "spec" / "open_questions.yaml"
CARRIED = ROOT / "spec" / "carried_items.yaml"
G3_MODULES = ["audit/terms.py", "audit/g3_core.py", "audit/g3_cw.py", "audit/g3_dds.py", "audit/g3_run.py"]
MODES = {"half_up": ROUND_HALF_UP, "half_even": ROUND_HALF_EVEN}
CENT = Decimal("0.01")


def D(x) -> Decimal:
    return Decimal(str(x))


# ============================================================================== X1 independent cases
# G3 scope item -> (case, what the engine result must show). Pairs are boundaries: both sides must be present.
SCOPE = {
    "identity": [("CW-S56", "finding:contract_ref_variant"), ("CW-S57", "finding:subcontractor_mismatch"),
                 ("DDS-S57", "finding:contract_ref_variant"), ("DDS-S58", "finding:contract_ref_variant"),
                 ("DDS-S59", "finding:contractor_mismatch"), ("DDS-S60", "finding:line_well_differs_from_invoice")],
    "period": [("CW-S47", "finding:outside_period"), ("DDS-S47", "finding:outside_period")],
    "term (both ends)": [("CW-S41", "no:out_of_term"), ("CW-S42", "finding:out_of_term"), ("CW-S43", "finding:out_of_term"),
                         ("DDS-S41", "no:out_of_term"), ("DDS-S42", "finding:out_of_term"), ("DDS-S43", "finding:out_of_term")],
    "submission window (boundary day)": [("CW-S44", "no:submitted_late"), ("CW-S45", "finding:submitted_late"),
                                         ("CW-S46", "finding:submitted_early"), ("DDS-S44", "no:submitted_late"),
                                         ("DDS-S45", "finding:submitted_late"), ("DDS-S46", "finding:submitted_early")],
    "evidence and signatures": [("CW-S48", "finding:record_missing"), ("CW-S49", "finding:record_unsigned"),
                                ("CW-S50", "finding:record_wrong_series"), ("CW-S52", "finding:item_not_supported_by_record"),
                                ("CW-S55", "finding:record_date_mismatch"), ("CW-S58", "finding:record_area_mismatch"),
                                ("DDS-S48", "finding:required_part_missing"), ("DDS-S49", "finding:required_part_missing"),
                                ("DDS-S50", "finding:report_unsigned"), ("DDS-S61", "finding:well_mismatch"),
                                ("DDS-S62", "finding:report_date_mismatch"), ("DDS-S63", "finding:report_missing")],
    "quantity: first hour": [("CW-S31", "not_payable"), ("CW-S32", "allowed:6"), ("DDS-S28", "allowed:6")],
    "quantity: 2% survey tolerance (boundary)": [("CW-S35", "allowed:102"), ("CW-S36", "allowed:100")],
    "quantity: 1% metre tolerance (boundary)": [("DDS-S32", "allowed:101"), ("DDS-S33", "allowed:100")],
    "quantity: five-day week (boundary)": [("CW-S33", "finding:week_not_measurable"), ("CW-S34", "allowed:1")],
    "quantity: six-hour minimum": [("DDS-S29", "allowed:6"), ("DDS-S30", "allowed:5")],
    "unit: civil Cl.26 full rejection": [("CW-S39", "finding:wrong_unit"), ("CW-S39", "not_payable"), ("CW-R16", "not_payable")],
    "unit: drilling Cl.35 (remedy Q11 open)": [("DDS-S53", "finding:wrong_unit")],
    "rate version by work date (boundary pairs)": [("CW-S01|CW-S02", "rates_differ"), ("CW-S03|CW-S04", "rates_differ"),
                                                   ("CW-S05|CW-S06", "rates_differ"), ("CW-S07|CW-S08", "rates_differ"),
                                                   ("DDS-S01|DDS-S02", "rates_differ"), ("DDS-S03|DDS-S04", "rates_differ")],
    "retrospective amendment and its protection": [("CW-S15", "reading:31A protection"), ("CW-S16", "no_reading:31A protection"),
                                                   ("DDS-S05", "reading:36A protection"), ("DDS-S06", "no_reading:36A protection")],
    "civil FX half-even (26A)": [("CW-S19", "rounds_after:26A:half_even"), ("CW-R01", "rounds_after:26A:half_even")],
    "civil indexation half-even (29A)": [("CW-S20", "rounds_after:29A:half_even"), ("CW-R02", "rounds_after:29A:half_even")],
    "civil one half-up rounding, on an exact half": [("CW-S29", "mode_at_half:half_up"), ("CW-S30", "mode_at_half:half_up")],
    "drilling half-even every step, on an exact half": [("DDS-S26", "mode_at_half:half_even"), ("DDS-S27", "mode_at_half:half_even")],
    "build-up order and exceptions": [("CW-S21", "trace:CW.T08_GROUND_FACTORS"), ("CW-S22", "trace:night uplift not payable"),
                                      ("CW-S24", "reading:P11 rest-day alone"), ("DDS-S13", "trace:DDS.T08_SECTION_FACTORS"),
                                      ("DDS-S19", "trace:no hole-section factor on a Standby day")],
    # review findings F1-F4 ------------------------------------------------------------------------------------
    "F1 the invoice header is not the call-off: every well class carried": [
        ("DDS-S66", "conditional_on:class"), ("DDS-S67", "conditional_on:class"), ("DDS-R23", "conditional_on:class")],
    "F2 the application is not the excavation record: every ground class carried; record or 27A where they apply": [
        ("CW-S59", "conditional_on:ground"), ("CW-R23", "conditional_on:ground"), ("CW-S60", "finding:ground_differs_from_record"),
        ("CW-S61", "reading:27A")],
    "F3 arithmetic where the band is not known (Cl.28 division)": [
        ("CW-R21", "unresolved:amount_arithmetic"), ("CW-S62", "finding:amount_arithmetic"), ("CW-S63", "no:amount_arithmetic"),
        ("CW-R22", "finding:amount_arithmetic")],
    "F4 PD-210: allowed metres, parts and amount agree under 25A": [
        ("DDS-S68", "allowed:101"), ("DDS-S69", "allowed:100"), ("DDS-S70", "allowed:99"), ("DDS-S71", "conditional_on:tolerance")],
    "Q5 residual kept as scoped alternatives": [("DDS-S64", "conditional_on:Q5-DD120"), ("DDS-S65", "conditional_on:Q5-HC630")],
    # review findings B1-B2 ------------------------------------------------------------------------------------
    "B1 a record settles the ground class only for the work it records (same day, area and item)": [
        ("CW-S64", "no_conditional_on:ground"), ("CW-S64", "trace:ground G3"), ("CW-S68", "trace:ground G5"),
        ("CW-R25", "no_conditional_on:ground"), ("CW-S65", "conditional_on:ground"), ("CW-S66", "conditional_on:ground"),
        ("CW-S67", "conditional_on:ground"), ("CW-R24", "conditional_on:ground")],
    "B2 PD-210 crossing charge: the complete allocation domain (listed, or bounds with the domain stated)": [
        ("DDS-S71", "domain:continuous:5067.35:5083.15:exact"), ("DDS-S72", "domain:continuous:4908.70:4940.30:exact"),
        ("DDS-S73", "domain:continuous:1694.00:2326.00:exact"), ("DDS-S74", "domain:continuous:10134.70:10166.30:exact"),
        ("DDS-S75", "domain:continuous:87648.49:87989.51:conservative"), ("DDS-S76", "domain:continuous:4937.77:4961.48:exact")],
}


def _show(res, want: str, results: dict) -> bool:
    kind, _, arg = want.partition(":")
    if kind == "finding":
        return arg in res.findings
    if kind == "no":
        return arg not in res.findings
    if kind == "not_payable":
        return res.payable is False
    if kind == "allowed":
        return res.allowed_quantity == D(arg)
    if kind == "reading":
        return any(arg in x for x in res.readings)
    if kind == "no_reading":
        return not any(arg in x for x in res.readings)
    if kind == "trace":
        return any(arg in (s.get("label", "") + " " + s.get("source", "")) for t in all_traces(res) for s in t)
    if kind == "rounds_after":
        clause, mode = arg.split(":")
        return any(st[i]["op"] == "mul" and clause in st[i]["source"] and st[i + 1]["op"] == "round" and st[i + 1]["mode"] == mode
                   for st in all_traces(res) for i in range(len(st) - 1))
    if kind == "mode_at_half":
        # a rounding in the stated mode applied to a value on an exact half cent, where half-up and half-even differ
        for t in all_traces(res):
            st = [s for s in t if s["op"] != "note"]
            if any(st[i]["op"] == "round" and st[i]["mode"] == arg and (D(st[i - 1]["value"]) * 100) % 1 == Decimal("0.5")
                   and D(st[i - 1]["value"]).quantize(CENT, ROUND_HALF_UP) != D(st[i - 1]["value"]).quantize(CENT, ROUND_HALF_EVEN)
                   for i in range(1, len(st))):
                return True
        return False
    if kind == "unresolved":
        return arg in res.unresolved
    if kind == "conditional_on":
        dims = {x.split(":", 1)[0] for k in res.alternatives for x in (k or "").split("|") if x}
        return arg in dims and res.amount is None
    if kind == "no_conditional_on":
        return arg not in {x.split(":", 1)[0] for k in res.alternatives for x in (k or "").split("|") if x} and \
            not any(c["dimension"] == arg for c in res.conditions)
    if kind == "domain":
        mode, lo, hi, bkind = arg.split(":")           # FD07: continuous, no step or count; its amount bounds
        dom = next((c.get("domain") for c in res.conditions if c.get("dimension") == "tolerance"), None) or {}
        b = dom.get("amount_bounds_usd") or {}
        return dom.get("mode") == mode and dom.get("step_m") is None and dom.get("count") is None and \
            dom.get("exhaustive") is False and (b.get("min"), b.get("max"), b.get("kind")) == (lo, hi, bkind) and \
            res.amount is None and len(res.alternatives) >= 2
    if kind == "amount":
        return res.amount == D(arg)
    raise ValueError(want)


def all_traces(res) -> list[list[dict]]:
    return [res.trace] + [a["trace"] for a in res.alternatives.values() if isinstance(a, dict) and a.get("trace")]


# The first case set (153 cases) was written before any pricing code existed; the identity, correction and re-read
# packets came later, when the engines existed (they must still precede their readers' outputs; disclosed).
FIRST_SET = {"packet_cw_synthetic_1.jsonl", "packet_cw_synthetic_2.jsonl", "packet_dds_synthetic_1.jsonl",
             "packet_dds_synthetic_2.jsonl", "packet_cw_real.jsonl", "packet_dds_real.jsonl"}


def _rates_by_fact(res) -> dict:
    """The line's rate, or its rate under each factual alternative (class/ground/band) where it has no single rate."""
    if res.unit_rate is not None:
        return {"": res.unit_rate}
    out = {}
    for k, a in res.alternatives.items():
        fact = "|".join(sorted(x for x in (k or "").split("|") if x.split(":", 1)[0] in ("class", "ground", "band")))
        out[fact] = a.get("unit_rate")
    return out


import commit_order  # noqa: E402


# The order of first commits in the original development history, recorded in verification/commit_order.json
# (tools/commit_order.py re-derives it from a clone of that repository).
recorded_first_commit = commit_order.first_commit
recorded_before = commit_order.committed_before


def x1(comparison: dict, cases: dict, results: dict, first_commit=recorded_first_commit, before=recorded_before) -> list[str]:
    errs = list(comparison["failures"])
    bad_case = {x["id"] for x in comparison["rows"] if not x["agree"] and "disposition" not in x}
    for item, wants in SCOPE.items():
        for cid, want in wants:
            ids = cid.split("|")
            if any(i not in results for i in ids):
                errs.append(f"scope '{item}': case {cid} missing")
                continue
            if set(ids) & bad_case:
                errs.append(f"scope '{item}': case {cid} does not agree with its independent expected value")
            if want == "rates_differ":
                ra, rb = (_rates_by_fact(results[i]) for i in ids)
                if not ra or set(ra) != set(rb) or any(ra[k] is None or ra[k] == rb[k] for k in ra):
                    errs.append(f"scope '{item}': boundary pair {cid} does not change the rate (under every admissible class/ground/band)")
            elif not _show(results[ids[0]], want, results):
                errs.append(f"scope '{item}': case {cid} does not show {want}")
    # provenance: inputs committed before outputs; the first case set before the pricing code
    for f in sorted((ROOT / "verification" / "g3" / "cases").glob("packet_*.jsonl")):
        exp = f.with_name(f.name.replace("packet_", "expected_"))
        pc, ec = first_commit(str(f.relative_to(ROOT))), first_commit(str(exp.relative_to(ROOT)))
        if not before(pc, ec):
            errs.append(f"{f.name}: inputs not committed strictly before the readers' output {exp.name}")
        if f.name in FIRST_SET:
            for mod in ("audit/g3_cw.py", "audit/g3_dds.py"):
                if not before(pc, first_commit(mod)):
                    errs.append(f"{f.name}: not committed before the pricing code {mod}")
    return errs


# ============================================================================== X2 code families
def family_of(contract: str, code: str, fams: list[dict], tables: dict) -> list[str]:
    """Families whose rule the code satisfies, in registry order (membership from the verified tables)."""
    sch1 = {r[0]: r for r in tables[f"{contract}.T01_SCH1"]["rows"]}
    sch8 = {r[0]: r[1] for r in tables.get("DDS.T23_SCH8", {}).get("rows", [])} if contract == "DDS" else {}
    col = lambda t: {r[0] for r in tables[t]["rows"]}  # noqa: E731
    out = []
    for f in fams:
        rule, ok = f["rule"], True
        if "table" in rule:
            ok &= code in col(rule["table"])
        if "also_table" in rule:
            ok &= code in col(rule["also_table"])
        if "sch1_unit" in rule:
            ok &= code in sch1 and sch1[code][2] == rule["sch1_unit"]
        if "codes" in rule:
            ok &= code in rule["codes"]
        if "sch8_contains" in rule:
            ok &= rule["sch8_contains"] in sch8.get(code, "") or code in rule.get("or_codes", [])
        if rule.get("not_in_sch1"):
            ok &= code not in sch1
        if ok:
            out.append(f["id"])
    return out


FEATURES = {
    "CW": {"FX (26A)": "CW.T07_FX", "index (29A)": "CW.T05_SMI", "ground (27A, S4)": "CW.T08_GROUND_FACTORS",
           "zone (Cl.27)": "CW.T02_ZONES", "rest-day uplift": "CW.T11_REST", "night uplift": "CW.T10_NIGHT",
           "annual band": "CW.T12_BANDS", "discount (S2/A2)": "discount "},
    "DDS": {"index (17A)": "DDS.T05_RSI", "section (Cl.18)": "DDS.T08_SECTION_FACTORS", "class (Cl.18)": "DDS.T10_CLASS_FACTORS",
            "standby (Cl.20)": "DDS.T12_STANDBY", "depth band (Sch 2)": "DDS.T02_DEPTH_BANDS", "loss value (Sch 2D)": "DDS.T06_SAR_VALUES",
            "loss FX (Sch 2D)": "DDS.T07_FX", "discount (S2/A2)": "discount "},
}


def applicable_features(contract: str, r, line: dict, ctx: dict, T) -> set[str]:
    """Pricing tables that the verified terms apply to this priced line (independent of the engine's own branching)."""
    f = set()
    if contract == "CW":
        code, wd = line["item_code"], line["work_date"]
        series = code[0]
        zone = (line.get("site_zone") or "").split(" ")[0]
        zf = T.zones.get(zone) if series in T.zone_series and zone else None
        rest = wd.strftime("%A") in T.rest_days and code in T.rest
        f |= {"FX (26A)"} if code in T.usd_items else set()
        f |= {"index (29A)"} if code in T.smi_items else set()
        f |= {"ground (27A, S4)"} if code in T.ground_items else set()
        f |= {"zone (Cl.27)"} if zf is not None else set()
        f |= {"rest-day uplift"} if rest else set()
        if line.get("night_work") == "Y" and code in T.night and not rest and not (zf is not None and zf > T.night_suppressed_above):
            f.add("night uplift")
        f |= {"annual band"} if code in T.banded else set()
        f |= {"discount (S2/A2)"} if T.discount(code, wd) else set()
    else:
        code, sd, status = line["service_code"], line["service_date"], ctx.get("status")
        if code in g3_dds.LOSS:
            return {"loss value (Sch 2D)", "loss FX (Sch 2D)"}
        if code == "PD-210":
            return {"depth band (Sch 2)"}
        f |= {"index (17A)"} if code in T.rsi_items else set()
        f |= {"section (Cl.18)"} if code in T.section_rated and status != "Standby" else set()
        f |= {"class (Cl.18)"} if code in T.class_rated else set()
        f |= {"standby (Cl.20)"} if status == "Standby" and T.standby.get(code) is not None else set()
        f |= {"discount (S2/A2)"} if T.discount(code, sd) else set()
    return f


def _dim_values(r, dim: str) -> set[str]:
    return {x.split(":", 1)[1] for k in r.alternatives for x in (k or "").split("|") if x.startswith(dim + ":")}


def _applies_to_work(rec, line: dict) -> bool:
    """Independent of the engine: a record is evidence about this work only for the same day (or its week), the same work
    area and an item basis covering the billed item (B1; CW Cl.5, S4) - for every record, required or optional."""
    wd, area = line["work_date"], (line.get("site") or "").split(" ")[0]
    if rec.week_beginning:
        same_day = (wd - rec.week_beginning).days in range(7)
    else:
        same_day = rec.date == wd
    return same_day and rec.area is not None and rec.area == area and line["item_code"] in (rec.candidates or [])


def pd210_measured_errors(r, ddr) -> list[str]:
    """B2: on a charge crossing band boundaries, each part's metres can lie only where the report measures them
    within that part of the charged interval (Cl.23 'taken from the measured depths on the Daily Drilling Report'). The
    range each part states (or, stated nothing, its whole interval) must equal the report's depths within the part -
    read here from the report itself, not from the engine."""
    if not r.payable or ddr is None:
        return []
    a = ddr.parts.get("A", {})
    try:
        start, end = D(a["Depth start (m MD)"]), D(a["Depth end (m MD)"])
    except (KeyError, TypeError, ArithmeticError):
        return []
    e = []
    for t in _traces(r):
        steps = [s for s in t if s["op"] == "part" and PART_LABEL.search(s.get("label", ""))]
        if len(steps) < 2:
            continue                                        # one band: one rate wherever the metres lie
        for s in steps:
            m = PART_LABEL.search(s["label"])
            pa, pb = D(m.group(2)), D(m.group(3))
            lo, hi = max(pa, start), min(pb, end)
            true = (lo, hi) if hi > lo else None
            w = MEASURED.search(s["label"])
            stated = ((D(w.group(1)), D(w.group(2))) if w.group(1) else None) if w else (pa, pb)
            if stated != true:
                e.append(f"PD-210 part {pa}-{pb} m: allocation bounded by {stated}, the report measures {true} there")
    return sorted(set(e))


def admissible_errors(contract: str, r, line: dict, T, w) -> list[str]:
    """F1: the well class is the call-off's (DDS Cl.4; P2, P3) - no call-off is supplied, so a class-rated service carries
    every class. F2: the ground class is the one recorded at excavation (CW S4; Cl.5) - where no supplied record states
    it (and 27A does not apply) the line carries every class. The claim's statement never selects one."""
    e = []
    if contract == "DDS" and r.code in T.class_rated and r.code != "PD-210":
        if _dim_values(r, "class") != set(T.class_factor):
            e.append(f"class-rated service without every admissible class (has {sorted(_dim_values(r, 'class'))}): the "
                     "invoice header is not the call-off")
    if contract == "CW" and r.code in T.ground_items and line["work_date"] <= T.g2_after:
        rec = w.cw.get(line.get("record_ref") or "")
        # authority: a record of this work, stating a class, countersigned by the Engineer's representative (Cl.5; App A;
        # 27A: the classification is the Engineer's) - read from the record, not from the engine
        if not (rec is not None and rec.ground and _applies_to_work(rec, line) and rec.engineer_signed):
            if _dim_values(r, "ground") != set(T.ground):
                e.append(f"ground item with no recorded classification without every ground class (has "
                         f"{sorted(_dim_values(r, 'ground'))}): the application's class is not authority")
        elif r.amount_status != "not_payable":
            # the positive side (B1): a record that applies to this work settles the class - one class, the record's
            used = {s["label"] for t in _traces(r) for s in t if s.get("label", "").startswith("ground G")}
            if _dim_values(r, "ground") or used != {f"ground {rec.ground}"}:
                e.append(f"ground item whose record for this work states {rec.ground} priced under {sorted(used)} "
                         f"(alternatives over {sorted(_dim_values(r, 'ground'))}): an applicable record settles the class")
    if contract == "DDS" and r.code == "PD-210":
        e += pd210_measured_errors(r, w.ddr.get(line.get("report_ref") or ""))
    left = {x.split(":", 1)[0] for k in r.alternatives for x in (k or "").split("|") if x}
    owned = {c["dimension"]: c["owner"] for c in r.conditions}
    for d in left:
        if not re.fullmatch(r"G[4-7]", owned.get(d, "")):
            e.append(f"alternatives over '{d}' without a condition owned by a later gate")
    return e


def _traces(r) -> list[list[dict]]:
    alts = [a["trace"] for a in r.alternatives.values() if isinstance(a, dict) and a.get("trace")]
    return alts or [r.trace]


def x2(w, results: dict, families: dict, comparison: dict, T=None) -> list[str]:
    errs = []
    tables = {**sl.load_terms("CW")["tables"], **sl.load_terms("DDS")["tables"]}
    T = T or {"CW": terms.cw(), "DDS": terms.dds()}
    bad_case = {x["id"] for x in comparison["rows"] if not x["agree"] and "disposition" not in x}
    cases = {x["id"] for x in comparison["rows"]}
    lines = {"CW": {r.ident: r.values for r in w.claims.rows["cw_lines"]}, "DDS": {r.ident: r.values for r in w.claims.rows["dds_lines"]}}
    for contract, fams in families["contracts"].items():
        byid = {f["id"]: f for f in fams}
        for f in fams:
            if f["status"] == "implemented":
                if not f.get("cases"):
                    errs.append(f"{f['id']}: implemented family without a reference case")
                for c in f.get("cases", []):
                    if c not in cases:
                        errs.append(f"{f['id']}: reference case {c} does not exist")
                    elif c in bad_case:
                        errs.append(f"{f['id']}: reference case {c} does not agree")
            elif not re.fullmatch(r"G[4-7]", str(f.get("owner", ""))):
                errs.append(f"{f['id']}: {f['status']} family names no owning gate (G4-G7)")
        codes = sorted({r.code for r in results[contract].values()})
        member = {}
        for code in codes:
            m = family_of(contract, code, fams, tables)
            if not m:
                errs.append(f"{contract} {code}: billed code in no family")
            else:
                member[code] = m[0]
        for ref, r in results[contract].items():
            fam = member.get(r.code)
            if fam is None:
                continue
            if r.family != fam:
                errs.append(f"{ref} {r.code}: valued as {r.family}, registry family {fam}")
                continue
            st = byid[fam]["status"]
            if st == "implemented":
                if r.amount_status in ("deferred", "unresolved"):
                    errs.append(f"{ref} {r.code}: {r.amount_status} in implemented family {fam}")
                elif r.payable and r.amount is None and not any(isinstance(a, dict) and a.get("amount") is not None
                                                                for a in r.alternatives.values()):
                    errs.append(f"{ref} {r.code}: payable with neither an amount nor alternatives")
                elif r.payable is False and not r.reasons:
                    errs.append(f"{ref} {r.code}: not payable without a reason")
            elif r.amount_status != st:
                errs.append(f"{ref} {r.code}: family {fam} is {st} but the line is {r.amount_status}")
            # a value that depends on an unsupplied authority carries every admissible value, owned by a later gate (F1, F2)
            if r.payable:
                e2 = admissible_errors(contract, r, lines[contract][ref], T[contract], w)
                errs += [f"{ref} {r.code}: {x}" for x in e2]
            # pricing features that apply must appear in the trace(s)
            if not any(s["op"] == "start" for t in _traces(r) for s in t):
                continue
            if any(s["op"] == "note" and s["label"].startswith("not priced") for s in r.trace):
                if r.payable:
                    errs.append(f"{ref} {r.code}: payable although it could not be priced")
                continue                    # an explicit 'not priced' (no published month) on a non-payable line
            line = lines[contract][ref]
            ctx = {}
            if contract == "DDS":
                ddr = w.ddr.get(line.get("report_ref") or "")
                ctx["status"] = ddr.parts.get("A", {}).get("Status") if ddr else None
                if r.code == "PD-210" and ddr and ddr.parts.get("A", {}).get("Hole section") not in g3_dds.PERFORMANCE_SECTIONS:
                    continue
            for feat in applicable_features(contract, r, line, ctx, T[contract]):
                marker = FEATURES[contract][feat]
                for tr in _traces(r):
                    if not any(marker in (s.get("source", "") if marker != "discount " else s.get("label", "")) for s in tr):
                        errs.append(f"{ref} {r.code}: {feat} applies but its table is not in the trace")
                        break
    return errs


# ============================================================================== X3 traces
def replay(steps: list[dict]) -> tuple[list[str], dict]:
    """Recompute every step with independent arithmetic. Returns (errors, {rate, amount, parts})."""
    errs, v, parts, out = [], None, [], {"rate": None, "amount": None, "quantity": None}
    part_q = Decimal(0)
    for i, s in enumerate(steps):
        op = s["op"]
        if op == "note":
            continue
        if not re.search(r"\d", s.get("source", "")):
            errs.append(f"step {i} ({op}) cites no clause, table or page")
        if op == "start":
            v = D(s["value"])
        elif op == "mul":
            v = v * D(s["factor"])
        elif op == "div":
            v = v / D(s["divisor"])
        elif op == "round":
            v = v.quantize(CENT, rounding=MODES[s["mode"]])
        elif op in ("amount", "part"):
            x = D(s["quantity"]) * D(s["rate"])
            if s.get("round"):
                x = x.quantize(CENT, rounding=MODES[s["round"]])
            if op == "amount":
                if v is not None and D(s["rate"]) != v:
                    errs.append(f"step {i}: amount uses rate {s['rate']}, the trace built {v}")
                out.update(amount=x, quantity=D(s["quantity"]))
            else:
                parts.append(x)
                part_q += D(s["quantity"])
                out["quantity"] = part_q
            if D(s["value"]) != x:
                errs.append(f"step {i} ({op}): recorded {s['value']}, replayed {x}")
            continue
        elif op == "sum_parts":
            x = sum(parts, Decimal(0))
            if D(s["value"]) != x:
                errs.append(f"step {i} (sum_parts): recorded {s['value']}, replayed {x}")
            out["amount"] = x
            continue
        else:
            errs.append(f"step {i}: unknown op {op}")
            continue
        if D(s["value"]) != v:
            errs.append(f"step {i} ({op} {s.get('label', '')}): recorded {s['value']}, replayed {v}")
            v = D(s["value"])
    out["rate"] = v
    out["parts"] = parts
    return errs, out


def rounding_errors(contract: str, steps: list[dict]) -> list[str]:
    ops = [s for s in steps if s["op"] in ("mul", "div", "round")]
    errs = []
    if contract == "CW":
        ups = [i for i, s in enumerate(ops) if s["op"] == "round" and s["mode"] == "half_up"]
        if len(ups) != 1:
            errs.append(f"{len(ups)} half-up roundings (Cl.28: the built-up rate is rounded once)")
        elif ups[0] != len(ops) - 1:
            errs.append("a factor is applied after the Cl.28 rounding")
        for i, s in enumerate(ops):
            if s["op"] == "round" and s["mode"] == "half_even":
                prev = ops[i - 1] if i else None
                if not (prev and prev["op"] == "mul" and ("26A" in prev["source"] or "29A" in prev["source"])):
                    errs.append("a half-even rounding not directly after an FX (26A) or index (29A) step")
    else:
        for i, s in enumerate(ops):
            if s["op"] in ("mul", "div") and not (i + 1 < len(ops) and ops[i + 1]["op"] == "round" and ops[i + 1]["mode"] == "half_even"):
                errs.append(f"step '{s.get('label')}' not rounded half to even at once (Cl.17)")
            if s["op"] == "round" and s["mode"] != "half_even":
                errs.append(f"rounding '{s.get('label')}' is {s['mode']} (Cl.17: half to even)")
    return errs


def table_figures(T, contract: str, code: str) -> set[Decimal]:
    vals = set()
    s = T.sch1.get(code)
    if s and s.get("rate") is not None:
        vals.add(s["rate"])
    for ins in T.instruments:
        vals |= {to for c, _e, to in ins.rate_rows if c == code}
        if ins.monthly_code == code:
            vals |= {v for _m, v in ins.monthly}
    if contract == "DDS" and code in T.sar:
        vals.add(T.sar[code])
    return vals


PART_LABEL = re.compile(r"band (\d): (\d+(?:\.\d+)?)-(\d+(?:\.\d+)?) m")
MEASURED = re.compile(r"report measures (?:(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?) m|no metres)")


def pd210_part_errors(steps: list[dict], T) -> list[str]:
    """PD-210 (F4): every part at its Schedule 2 band's rate, its depths inside that band (a boundary depth belongs to the
    shallower band), its quantity the interval length unless 25A's tolerance is named, the total within 25A's 1%."""
    errs, total_q, total_len = [], Decimal(0), Decimal(0)
    bands = {b: (lo, hi, rate) for lo, hi, rate, b in T.depth_bands}
    for s in steps:
        if s["op"] != "part":
            continue
        m = PART_LABEL.search(s.get("label", ""))
        if not m or m.group(1) not in bands:
            errs.append(f"PD-210 part '{s.get('label')}' names no Schedule 2 band and depths")
            continue
        lo, hi, rate = bands[m.group(1)]
        a, b = D(m.group(2)), D(m.group(3))
        if D(s["rate"]) != rate:
            errs.append(f"PD-210 part rate {s['rate']} is not band {m.group(1)}'s {rate} (Sch 2)")
        if not (lo <= a < b and (hi is None or b <= hi)):
            errs.append(f"PD-210 part {a}-{b} m is not inside band {m.group(1)}")
        q = D(s["quantity"])
        total_q, total_len = total_q + q, total_len + (b - a)
        if q != b - a and "(25A)" not in s.get("label", ""):
            errs.append(f"PD-210 part quantity {q} differs from its interval {b - a} without the 25A tolerance")
    if total_q != total_len and total_q > total_len * (1 + T.metre_tolerance / 100):
        errs.append(f"PD-210 parts carry {total_q} m on {total_len} m of interval: beyond 25A")
    return errs


def _cent_from_below(v: Fraction) -> int:
    """The cents a product approaching v from below rounds to (half to even; a half cent is not reached)."""
    k = v * 100
    return math.floor(k) if (k - Fraction(1, 2)).denominator == 1 else round(k)


def _cent_from_above(v: Fraction) -> int:
    k = v * 100
    return math.ceil(k) if (k - Fraction(1, 2)).denominator == 1 else round(k)


def pd210_exact_bounds(r1: Fraction, r2: Fraction, a: Fraction, b: Fraction, A: Fraction) -> tuple[Decimal, Decimal]:
    """Lowest and highest amount over every real x in [a, b] (band 1 carries x, band 2 A - x), each band in cents half
    to even (DDS Cl.17) and then added (Cl.23). Independent of the engine's point sampling: it runs over the cent values
    c1 band 1 can take, and for each takes the extreme band-2 cents over the (possibly half-open) set of x giving c1.
    Only x within 0.02 / |r1 - r2| m of the linear extreme's vertex can attain a rounded extreme (each rounding moves an
    amount by at most half a cent), so c1 runs over that window only."""
    out = []
    width = Fraction(1, 50) / abs(r1 - r2)
    for lowest in (True, False):
        at_b = (r1 < r2) == lowest
        L, U = (max(a, b - width), b) if at_b else (a, min(b, a + width))
        best = None
        for c1 in range(round(L * r1 * 100), round(U * r1 * 100) + 1):
            closed = c1 % 2 == 0                         # a half cent rounds to the even cent
            x0, x1 = (c1 - Fraction(1, 2)) / 100 / r1, (c1 + Fraction(1, 2)) / 100 / r1
            lo_in, hi_in = (x0, closed) if x0 >= L else (L, True), (x1, closed) if x1 <= U else (U, True)
            if lo_in[0] > hi_in[0] or (lo_in[0] == hi_in[0] and not (lo_in[1] and hi_in[1])):
                continue
            if lowest:                                   # band 2 falls as x rises: its least at the right end
                y, inc = hi_in
                c2 = round((A - y) * r2 * 100) if inc else _cent_from_above((A - y) * r2)
                best = c1 + c2 if best is None else min(best, c1 + c2)
            else:
                y, inc = lo_in
                c2 = round((A - y) * r2 * 100) if inc else _cent_from_below((A - y) * r2)
                best = c1 + c2 if best is None else max(best, c1 + c2)
        out.append(Decimal(best).scaleb(-2))
    return out[0], out[1]


def pd210_enclosure(rates: list[Fraction], lo: list[Fraction], hi: list[Fraction], A: Fraction) -> tuple[Decimal, Decimal]:
    """An enclosure of every rounded amount for any number of bands: the linear extremes (the cheapest or dearest bands
    filled first) widened by half a cent per band, taken to whole cents."""
    def lin(order):
        qs, left = list(lo), A - sum(lo)
        for i in order:
            add = min(hi[i] - lo[i], left)
            qs[i] += add
            left -= add
        return sum(q * r for q, r in zip(qs, rates))
    order = sorted(range(len(rates)), key=lambda i: rates[i])
    n = len(rates)
    return (Decimal(math.ceil((lin(order) - Fraction(n, 200)) * 100)).scaleb(-2),
            Decimal(math.floor((lin(order[::-1]) + Fraction(n, 200)) * 100)).scaleb(-2))


def pd210_domain_errors(r, T) -> list[str]:
    """B2 and FD07: where the allowed metres differ from a charge interval that crosses band boundaries, the result must
    carry the COMPLETE admissible allocation domain. No source fixes a metre granularity (DDS Cl.23 p6, R3 p14, 25A p35,
    Sch 2 p17), so every real split within the coupled per-band bounds is admissible: a nondegenerate domain is stated
    symbolically (no step, no count, not enumerable, not exhaustive, unresolved, owner G5) with amount bounds covering
    every rounded outcome, and its traces are witnesses; a domain the constraints reduce to one allocation is that
    allocation alone. Recomputed here from the parts' intervals and measured depths, the allowed metres and the verified
    band rates: the constraints, each witness's membership and cents, and the bounds (exact by an independent method)."""
    if r.code != "PD-210" or not r.payable:
        return []
    alts = [(k, a) for k, a in r.alternatives.items() if a.get("trace") and any(s["op"] == "part" for s in a["trace"])]
    sets = alts or [(None, {"trace": r.trace, "amount": r.amount})]
    parsed, where = {}, {}
    for k, a in sets:
        ps = []
        for s in a["trace"]:
            m = PART_LABEL.search(s.get("label", "")) if s["op"] == "part" else None
            if m:
                ps.append((m.group(1), D(m.group(2)), D(m.group(3)), D(s["quantity"])))
                w = MEASURED.search(s["label"])
                where[m.group(1)] = (D(w.group(1)), D(w.group(2))) if w and w.group(1) else \
                    None if w else (D(m.group(2)), D(m.group(3)))
        parsed[k] = ps
    intervals = {tuple(p[:3] for p in ps) for ps in parsed.values()}
    if len(intervals) != 1:
        return ["PD-210 alternatives priced on different intervals"]
    iv = next(iter(intervals))
    errs = [f"PD-210 band {b}: measured metres {where[b]} outside the charged part {a}-{e}"
            for b, a, e in iv if where.get(b) and not (a <= where[b][0] <= where[b][1] <= e)]
    # the metres can lie only where the report measures them within the charged interval (Cl.23); default the interval
    lens = [(where[b][1] - where[b][0]) if where.get(b) else Decimal(0) for b, _a, _e in iv]
    allowed = r.allowed_quantity
    if allowed is None:
        return ["PD-210 alternatives without a common allowed quantity"]
    total = sum(lens, Decimal(0))
    tol = next((c for c in r.conditions if c.get("dimension") == "tolerance"), None)
    dom = (tol or {}).get("domain")
    if len(iv) < 2 or (total == allowed and lens == [e - a for _b, a, e in iv]):
        return errs + (["PD-210 allocation domain stated where the depths place every metre"] if dom else [])
    if allowed <= total:
        bounds = [(max(Decimal(0), allowed - (total - x)), min(x, allowed)) for x in lens]
    else:
        bounds = [(x, x + allowed - total) for x in lens]
    slo, shi = sum(lo for lo, _hi in bounds), sum(hi for _lo, hi in bounds)
    bounds = [(max(lo, allowed - (shi - hi)), min(hi, allowed - (slo - lo))) for lo, hi in bounds]   # coupled by the sum
    rate = {b: rt for _lo, _hi, rt, b in T.depth_bands}

    def amount(qs):
        return sum(((q * rate[band]).quantize(CENT, rounding=ROUND_HALF_EVEN) for q, (band, _a, _b) in zip(qs, iv)), Decimal(0))

    got = {k: tuple(p[3] for p in ps) for k, ps in parsed.items()}
    for k, g in got.items():
        if sum(g, Decimal(0)) != allowed:
            errs.append(f"PD-210 allocation {g} does not sum to the allowed {allowed} m")
        if not all(lo <= q <= hi for q, (lo, hi) in zip(g, bounds)):
            errs.append(f"PD-210 allocation {g} is outside the domain's bounds {bounds} (sum {allowed})")
        have = dict(sets)[k].get("amount")
        if have is not None and D(have) != amount(g):
            errs.append(f"PD-210 allocation {g}: amount {have}, band parts in cents half to even give {amount(g)}")
    if all(lo == hi for lo, hi in bounds):              # the constraints force one allocation: exhaustive, no domain
        if r.amount is None or dom:
            errs.append(f"PD-210 allocation forced by the constraints ({[lo for lo, _hi in bounds]}) carried as an "
                        f"unresolved domain")
        return errs
    if not tol or tol.get("owner") != "G5":
        errs.append("PD-210 allocation domain without an owned 'tolerance' condition (G5)")
    if r.amount is not None:
        errs.append(f"PD-210 allocation domain of more than one allocation given the single amount {r.amount}")
    if not dom:
        return errs + ["PD-210 allocation domain not stated on its condition"]
    finite = {k: dom.get(k) for k in ("mode", "step_m", "count", "enumerable", "exhaustive", "state", "owner")}
    if finite != {"mode": "continuous", "step_m": None, "count": None, "enumerable": False, "exhaustive": False,
                  "state": "unresolved", "owner": "G5"} or dom.get("not_listed") == "none":
        errs.append(f"PD-210 allocation domain is continuous and unresolved (owner G5; no source fixes a metre step), "
                    f"the result states {finite}" + (", nothing unlisted" if dom.get("not_listed") == "none" else ""))
    try:
        stated = [(D(p["min_m"]), D(p["max_m"])) for p in dom.get("parts", [])]
        stated_sum = D(dom.get("sum_m"))
        stated_rates = [D(p["rate"]) for p in dom.get("parts", [])]
    except (KeyError, TypeError, ArithmeticError):
        return errs + [f"PD-210 allocation domain without per-band bounds, rates or sum: {dom}"]
    if stated_sum != allowed or stated != bounds:
        errs.append(f"PD-210 allocation domain states {stated_sum} m in {stated}; the constraints give {allowed} m in {bounds}")
    if stated_rates != [rate[b] for b, _a, _e in iv]:
        errs.append(f"PD-210 allocation domain rates {stated_rates} are not the bands' {[rate[b] for b, _a, _e in iv]} (Sch 2)")
    fr = [Fraction(rate[b]) for b, _a, _e in iv]
    lo_f, hi_f = [Fraction(lo) for lo, _hi in bounds], [Fraction(hi) for _lo, hi in bounds]
    exact = pd210_exact_bounds(fr[0], fr[1], lo_f[0], hi_f[0], Fraction(allowed)) if len(iv) == 2 and fr[0] != fr[1] else None
    enclosing = exact or pd210_enclosure(fr, lo_f, hi_f, Fraction(allowed))
    b = dom.get("amount_bounds_usd") or {}
    try:
        bmin, bmax = D(b["min"]), D(b["max"])
    except (KeyError, TypeError, ArithmeticError):
        return errs + [f"PD-210 allocation domain without amount bounds: {b}"]
    if b.get("kind") == "exact":
        if exact is None or (bmin, bmax) != exact:
            errs.append(f"PD-210 exact amount bounds {bmin}..{bmax} are not the domain's lowest and highest rounded amounts "
                        f"{exact[0]}..{exact[1]}" if exact else f"PD-210 bounds {bmin}..{bmax} called exact over {len(iv)} bands")
    elif b.get("kind") == "conservative":
        if not (bmin <= enclosing[0] and bmax >= enclosing[1]):
            errs.append(f"PD-210 conservative bounds {bmin}..{bmax} do not enclose every admissible amount "
                        f"{enclosing[0]}..{enclosing[1]}")
    else:
        errs.append(f"PD-210 amount bounds of kind {b.get('kind')!r} (exact or conservative)")
    errs += [f"PD-210 bounds {bmin}..{bmax} exclude the admissible allocation {g} at {amount(g)}"
             for g in got.values() if not bmin <= amount(g) <= bmax]
    if len(set(got.values())) < 2:
        errs.append("PD-210 allocation domain sampled by fewer than two allocations")
    return errs


def _check_trace(contract, code, steps, T, want_amount, want_quantity, want_rate) -> list[str]:
    e, rep = replay(steps)
    starts = [s for s in steps if s["op"] == "start"]
    if starts:
        if D(starts[0]["value"]) not in table_figures(T[contract], contract, code):
            e.append(f"trace starts at {starts[0]['value']}, not a figure of the verified tables for {code}")
        e += rounding_errors(contract, [s for s in steps if s["op"] != "amount"])
    if code == "PD-210":
        e += pd210_part_errors(steps, T[contract])
    if contract == "DDS":
        for s in steps:
            if s["op"] in ("amount", "part", "sum_parts") and D(s["value"]) != D(s["value"]).quantize(CENT):
                e.append(f"{s['op']} {s['value']} is not ascertained in cents (DDS Cl.17)")
            if s.get("round") and s["round"] != "half_even":
                e.append(f"{s['op']} rounded {s['round']}, not half to even (DDS Cl.17)")
    if rep["amount"] != want_amount:
        e.append(f"trace amount {rep['amount']} != amount {want_amount}")
    if rep["quantity"] != want_quantity:
        e.append(f"trace quantity {rep['quantity']} != allowed quantity {want_quantity}")
    if want_rate is not None:
        got = rep["rate"] if code != "PD-210" else (D(next(s["rate"] for s in steps if s["op"] == "part"))
                                                       if len(rep["parts"]) == 1 else None)
        if got != want_rate:
            e.append(f"trace rate {got} != unit_rate {want_rate}")
    return e


def x3(results: dict, T=None) -> list[str]:
    """Every amount - the line's own and every alternative's - replays from its trace with independent arithmetic."""
    errs = []
    T = T or {"CW": terms.cw(), "DDS": terms.dds()}
    for contract, rs in results.items():
        for ref, r in rs.items():
            e = [f"engine error: {c.detail}" for c in r.checks if c.finding == "engine_error"]   # contained, never passed
            if r.amount_status in ("determined", "conditional", "alternatives") and r.payable:
                if r.amount is not None:
                    e += _check_trace(contract, r.code, r.trace, T, r.amount, r.allowed_quantity, r.unit_rate)
                elif not r.alternatives:
                    e.append("payable without an amount or alternatives")
                if contract == "DDS" and r.code == "PD-210":
                    e += pd210_domain_errors(r, T[contract])
                for k, a in r.alternatives.items():
                    if a.get("trace") and any(s["op"] in ("amount", "sum_parts") for s in a["trace"]):
                        e += [f"{k}: {x}" for x in _check_trace(contract, r.code, a["trace"], T, a["amount"],
                                                                 a["allowed_quantity"], a.get("unit_rate"))]
                    elif not (a.get("amount") == Decimal("0.00") and a.get("allowed_quantity") == 0 and a.get("trace")):
                        e.append(f"{k}: alternative without a replayable trace")
                if r.amount is None and r.alternatives:
                    common = {a["allowed_quantity"] for a in r.alternatives.values()}
                    if r.allowed_quantity is not None and common != {r.allowed_quantity}:
                        e.append("allowed quantity stated but the alternatives differ")
                if (r.amount is None or r.amount_status != "determined") and not r.conditions and r.amount_status != "determined":
                    e.append(f"{r.amount_status} without a stated condition and owner")
            elif r.payable is False:
                if r.amount != Decimal("0.00") or r.allowed_quantity != 0 or not r.reasons:
                    e.append("not payable without a 0.00 amount, 0 quantity and a reason")
            elif r.amount_status in ("deferred", "unresolved"):
                if r.amount is not None or not (r.reasons or any(c.status == "unresolved" for c in r.checks)):
                    e.append(f"{r.amount_status} line with an amount or without a reason")
            else:
                e.append(f"amount status {r.amount_status} with payable {r.payable}")
            errs += [f"{contract} {ref} {r.code}: {x}" for x in e]
    return errs


# ============================================================================== X4 decisions
def x4(decisions: dict, scopes: dict, questions: dict, carried: dict, comparison: dict) -> list[str]:
    errs = []
    cases = {x["id"] for x in comparison["rows"]}
    bad_case = {x["id"] for x in comparison["rows"] if not x["agree"] and "disposition" not in x}
    ids = set()
    for d in decisions["decisions"]:
        i = d["id"]
        ids.add(i)
        if d.get("status") not in ("decided", "decided in part", "open"):
            errs.append(f"{i}: status {d.get('status')!r}")
        if not d.get("reading"):
            errs.append(f"{i}: no reading")
        if not d.get("basis") or any(not re.search(r"\bpp?\d+", b) for b in d["basis"]):
            errs.append(f"{i}: basis missing or without a page reference")
        alts = d.get("alternatives") or {}
        if not alts:
            errs.append(f"{i}: no recorded alternative")
        for c in d.get("cases", []):
            if c not in cases:
                errs.append(f"{i}: case {c} does not exist")
            elif c in bad_case:
                errs.append(f"{i}: case {c} does not agree with its independent expected value")
        if not d.get("cases"):
            errs.append(f"{i}: no discriminating case")
        sc = scopes.get(d.get("scope_key"))
        if sc is None:
            errs.append(f"{i}: scope {d.get('scope_key')} not computed")
            continue
        n, eff = sc.get("lines_decided"), sc.get("effect_by_reading") or {}
        if not isinstance(n, int):
            errs.append(f"{i}: lines decided not computed")
            continue
        need = set(alts) | ({"adopted"} if d["status"] != "open" else set())   # open: every recorded reading
        missing = sorted(k for k in need if k not in eff)
        if missing:
            errs.append(f"{i}: no effect computed for reading(s) {missing}")
        if d["status"] == "decided in part" and not re.search(r"G[4-7]", str(d.get("residual_owner", ""))):
            errs.append(f"{i}: decided in part without a later owner for the residual")
        for k, e in eff.items():
            for fk, fv in (e.items() if isinstance(e, dict) else []):
                if fk.startswith("value") and fv is not None and not (isinstance(fv, dict) and set(fv) <= {"SAR", "USD"}):
                    errs.append(f"{i}: reading {k} reports {fk} without separating currencies (SAR civil, USD drilling)")
        if n >= 100:
            for k, e in eff.items():
                if not isinstance(e, dict) or not isinstance(e.get("lines"), int) or not ({"value", "note"} & set(e) or
                                                                                         any(x.startswith("value") for x in e)):
                    errs.append(f"{i}: decides {n} lines but reading {k} reports no lines/value")
    # registers agree with the decisions
    by = {d["id"]: d for d in decisions["decisions"]}
    for q in questions["questions"]:
        owned = q.get("blocks") == "G3" or q.get("originally_blocks") == "G3"
        if not owned:
            continue
        d = by.get(q["id"])
        if d is None:
            errs.append(f"{q['id']}: owned by G3 but no G3 decision entry")
            continue
        if q["status"] != d["status"]:
            errs.append(f"{q['id']}: register status {q['status']!r} vs decision {d['status']!r}")
        if q["status"] == "open" and q.get("blocks") == "G3":
            errs.append(f"{q['id']}: still open and still blocks G3")
        if q["status"] in ("open", "decided in part") and not re.fullmatch(r"G[4-7]", str(q.get("blocks"))):
            errs.append(f"{q['id']}: {q['status']} without a later owning gate (blocks {q.get('blocks')})")
        if q["status"] != "open" and q.get("decided_at") != "G3":
            errs.append(f"{q['id']}: decided without decided_at G3")
    # interpretations (D8): a reading the text does not compel is recorded as such - two or more readings, each
    # with an explicit weight and effect, open, owned by a later gate, and cited by every decision it bears on
    for dd in questions.get("decisions", []):
        for it in dd.get("interpretations", []):
            rd = it.get("readings") or []
            name = f"{dd['id']}/{it.get('id')}"
            if len(rd) < 2 or not all(x.get("reading") and x.get("weight") and x.get("effect") for x in rd):
                errs.append(f"{name}: interpretation without two or more readings each with a weight and an effect")
            if it.get("status") != "open" or not re.fullmatch(r"G[4-7]", str(it.get("owner", ""))):
                errs.append(f"{name}: interpretation not open with a later owner")
            for target in it.get("applies_to", []):
                d = by.get(target)
                if d is None or it["id"] not in json.dumps(d):
                    errs.append(f"{name}: decision {target} does not cite the interpretation it depends on")
                elif d.get("status") == "decided":
                    errs.append(f"{name}: decision {target} is fully decided although it depends on an open interpretation")
    listed = {c for d in decisions["decisions"] for c in d.get("carried_items", [])}
    for it in carried.get("items", []):
        if it.get("owner_gate") == "G3":
            if it.get("status") != "decided at G3" or not it.get("g3_treatment"):
                errs.append(f"{it['id']}: owned by G3 but not decided with a treatment")
            if it["id"] not in listed:
                errs.append(f"{it['id']}: not referenced by any G3 decision")
    for it in carried.get("resolved", []):
        if it.get("resolved_at") == "G3" and it["id"] not in listed:
            errs.append(f"{it['id']}: resolved at G3 but not referenced by any G3 decision")
    return errs


# ============================================================================== X5 boundary
FORBIDDEN = ["audit_results.csv", "expected_total", "invoice_total", "flagged", "def classify", "is_flagged", "apparent_pass"]


def x5_text(paths=None) -> list[str]:
    errs = []
    for p in paths or [ROOT / m for m in G3_MODULES]:
        t = p.read_text().lower()
        errs += [f"{p.name}: '{w}' (G5+ construct)" for w in FORBIDDEN if w in t]
    return errs


def evaluate_all(w, order=1, cw_eval=None, dds_eval=None, mutate=None, only=None) -> dict:
    """Evaluate every line one by one (order=-1: reverse), optionally mutating the claim (line and its header) first;
    `only` limits the run to those line refs (tests)."""
    cw_eval, dds_eval = cw_eval or g3_cw.evaluate, dds_eval or g3_dds.evaluate
    out = {"CW": {}, "DDS": {}}
    for ref in UNRELATED_RECORDS:
        if ref in w.cw:
            _REC_FACTS[ref] = (w.cw[ref].date, w.cw[ref].area)
    for line, app, rec, exists in list(g3_cw.inputs_from_world(w))[::order]:
        if only is None or line["line_ref"] in only:
            if mutate:
                line, app = mutate("CW", line, app)
                rec = w.cw.get(line.get("record_ref") or "")
                exists = rec is not None
            r = cw_eval(line, app, rec, exists)
            out["CW"][r.line_ref] = r
    for line, inv, ddr in list(g3_dds.inputs_from_world(w))[::order]:
        if only is None or line["line_ref"] in only:
            line, inv = mutate("DDS", line, inv) if mutate else (line, inv)
            r = dds_eval(line, inv, ddr)
            out["DDS"][r.line_ref] = r
    return out


def _value(r) -> dict:
    j = r.to_json()
    return {k: j[k] for k in ("unit_rate", "allowed_quantity", "amount", "payable", "amount_status", "alternatives", "family")}


def x5_order(w, forward: dict, **kw) -> list[str]:
    rev = evaluate_all(w, order=-1, only=kw.pop("only", None), **kw)
    errs = []
    for c in forward:
        for ref, r in forward[c].items():
            a, b = r.to_json(), rev[c][ref].to_json()
            a.pop("ctx"), b.pop("ctx")
            if a != b:
                errs.append(f"{c} {ref}: result depends on evaluation order (cross-line state)")
    return errs


# ============================================================================== X6 billing and claim independence
def perturb_billing(contract: str, line: dict, header: dict):
    line = dict(line)
    rk = "rate_applied" if contract == "CW" else "unit_rate"
    line[rk] = (line[rk] or Decimal(0)) * Decimal("1.37") + Decimal("0.01")
    line["amount"] = (line["amount"] or Decimal(0)) + Decimal("12345.67")
    return line, header


CLASSES = ["Standard", "Extended Reach", "HPHT"]
GROUNDS = ["G1 Loose Sand", "G2 Firm Sabkha", "G3 Cemented Fill", "G4 Weathered Rock", "G5 Sound Rock"]
SECTIONS = ['26"', '17-1/2"', '12-1/4"', '8-1/2"', '6"']


def perturb_claim_classes(contract: str, line: dict, header: dict):
    """Classifications the claim states but the contract assigns elsewhere: the well class (DDS Cl.4: the call-off), the
    ground class (CW S4, Cl.5: the excavation record / the Engineer), the hole section and day status (DDS Cl.19: as the
    report records) - and, for a ground item that needs no Schedule 5 record, the record the claim cites, pointed at a
    record of another day and area (B1). Shifting each must change no contract value."""
    line, header = dict(line), dict(header)
    if contract == "DDS":
        header["well_class"] = CLASSES[(CLASSES.index(header["well_class"]) + 1) % 3] if header.get("well_class") in CLASSES else "HPHT"
        sec = line.get("hole_section")
        line["hole_section"] = SECTIONS[(SECTIONS.index(sec) + 1) % len(SECTIONS)] if sec in SECTIONS else '12-1/4"'
        line["day_status"] = "Standby" if line.get("day_status") == "Operating" else "Operating"
    else:
        g = line.get("ground_class") or ""
        line["ground_class"] = GROUNDS[(GROUNDS.index(g) + 2) % 5] if g in GROUNDS else "G4 Weathered Rock"
        # a record the claim may cite for an item that needs none: point it at a record of another day and area that
        # states a class (DX-00007: 25 Jan 2025, S-04, G5; or DX-00008 when the line is itself on that day or area)
        if line["item_code"] in terms.cw().ground_items and line["item_code"] not in terms.cw().records:
            line["record_ref"] = UNRELATED_RECORDS[0] if not _near(line, UNRELATED_RECORDS[0]) else UNRELATED_RECORDS[1]
    return line, header


UNRELATED_RECORDS = ["DX-00007", "DX-00008"]
_REC_FACTS = {}


def _near(line, ref) -> bool:
    import datetime as _dt
    d, a = _REC_FACTS.get(ref, (None, None))
    return line["work_date"] == d or (line.get("site") or "").startswith(a or "@")


# Claim facts the contract names no other evidence for; they are disclosed on every result that relies on them and
# decided explicitly in spec/g3_decisions.yaml (G3-D3) rather than perturbed here.
CLAIM_FACTS_NOT_PERTURBED = {"CW site_zone": "Cl.4, Cl.42: the zone of physical execution; no supplied record states it",
                             "CW night_work": "Cl.7: time of execution; no supplied record states it"}


def x6(w, forward: dict, mutate=perturb_billing, **kw) -> list[str]:
    pert = evaluate_all(w, mutate=mutate, only=kw.pop("only", None), **kw)
    errs = []
    what = "the billed rate/amount" if mutate is perturb_billing else "a claim-stated classification"
    for c in forward:
        for ref, r in forward[c].items():
            if _value(r) != _value(pert[c][ref]):
                errs.append(f"{c} {ref}: a contract value changes with {what}")
    return errs


# ============================================================================== X7 reproduction and context
def x7(w, res: dict, committed: dict | None = None) -> list[str]:
    errs = []
    ctx = w.run_context["id"]
    fresh = {
        "summary.json": json.dumps(g3_run.summary(w, res), indent=1, default=str) + "\n",
        "decision_scopes.json": json.dumps(g3_run.decision_scopes(w, res), indent=1, default=str) + "\n",
        "trace_sample.jsonl": "".join(json.dumps(x, default=str) + "\n" for x in g3_run.trace_sample(res)),
    }
    committed = committed if committed is not None else {k: (OUT / k).read_text() if (OUT / k).exists() else None for k in fresh}
    for k, v in fresh.items():
        if committed.get(k) != v:
            errs.append(f"verification/g3/{k} does not reproduce from the current code and inputs")
    if json.loads(fresh["summary.json"])["run_context"] != ctx:
        errs.append("summary run context is not the current run context")
    for c, rs in res.items():
        stale = [ref for ref, r in rs.items() if r.ctx != ctx]
        if stale:
            errs.append(f"{c}: {len(stale)} results without the current run context (e.g. {stale[0]})")
    return errs


# ============================================================================== X8 inputs G2 can leave empty
def x8(w, res: dict, cw=None, dds=None, stats: dict | None = None, outputs=None) -> list[str]:
    """Every field G2 can leave None or unresolved (claims, records, reports; G2's own inventory) x every code family,
    through the engines, the batch and the output stage: an explicit result - no exception, no silent default, no lost
    value (tools/null_sweep.py). cw/dds/outputs: the engine and output modules under test (an earlier version for the
    negative control)."""
    import null_sweep
    errs, st = null_sweep.x8(w, res, null_sweep.Engines(cw or g3_cw, dds or g3_dds, outputs))
    if stats is not None:
        stats.update(st)
    return errs


def comparison_reproduces(comparison: dict) -> list[str]:
    committed = (OUT / "case_comparison.json").read_text() if (OUT / "case_comparison.json").exists() else None
    fresh = json.dumps(comparison, indent=1, default=str) + "\n"
    return [] if committed == fresh else ["verification/g3/case_comparison.json does not reproduce"]


# ============================================================================== main
def main() -> int:
    w = build.build()
    res = {"CW": g3_cw.run(w), "DDS": g3_dds.run(w)}
    comparison = gcc.run()
    cases = gcc.load_cases()
    case_results = {cid: gcc.engine_result(c) for cid, c in cases.items()}
    by_contract = {c: {cid: r for cid, r in case_results.items() if cases[cid]["contract"] == c} for c in ("CW", "DDS")}
    load = lambda p: yaml.safe_load(p.read_text())  # noqa: E731
    checks = [
        ("X1 independent clause-based cases pass, incl. exceptions and boundaries; inputs before outputs (git)",
         lambda: x1(comparison, cases, case_results) + comparison_reproduces(comparison)),
        ("X2 every billed code family implemented or explicitly deferred/unresolved; pricing tables applied where they apply",
         lambda: x2(w, res, load(FAMILIES), comparison)),
        ("X3 every amount's trace replays independently, cites its sources and follows the contract's rounding",
         lambda: x3(res) + x3(by_contract)),
        ("X4 decisions and open questions: basis, alternatives, cases and lines affected under every reading",
         lambda: x4(load(DECISIONS), json.loads((OUT / "decision_scopes.json").read_text()), load(QUESTIONS), load(CARRIED), comparison)),
        ("X5 boundary: no cross-line state (reverse-order evaluation identical); no classification/flag/total/audit result",
         lambda: x5_order(w, res) + x5_text()),
        ("X6 billed rate/amount and claim-stated classifications (well class, ground class, section, status) never change a contract value",
         lambda: x6(w, res) + x6(w, res, mutate=perturb_claim_classes)),
        ("X7 committed G3 outputs reproduce; every result carries the current run context", lambda: x7(w, res)),
        ("X8 every input G2 can leave empty or unresolved, in every code family, gives an explicit result through the "
         "engines, the batch and the output stage: no exception, no silent default, no lost value", lambda: x8(w, res, stats=x8_stats)),
    ]
    x8_stats = {}
    ok = True
    n_lines = sum(len(v) for v in res.values())
    print(f"G3 population: {n_lines} lines (CW {len(res['CW'])}, DDS {len(res['DDS'])}); cases {comparison['cases']}; "
          f"run context {w.run_context['id']}")
    for name, fn in checks:
        e = fn()
        print(("PASS " if not e else "FAIL ") + name)
        if name.startswith("X8") and x8_stats:
            fams = sorted(k.split(" family ")[1] for k in x8_stats if " family " in k)
            print(f"     {x8_stats.get('mutations')} input states on {len(fams)} code families; "
                  f"{x8_stats.get('explicit_unresolved', 0)} unresolved and named, {x8_stats.get('registered', 0)} not payable "
                  f"by the contract's rule, {x8_stats.get('all_values_carried', 0)} carrying every value, "
                  f"{x8_stats.get('irrelevant_unchanged', 0)} unchanged (input not used); "
                  f"{x8_stats.get('out_of_scope_value_accepted_by_G2', 0)} out of scope; {x8_stats.get('batch_runs')} batch runs")
        for x in e[:20]:
            print("     -", x)
        if len(e) > 20:
            print(f"     ... {len(e) - 20} more")
        ok &= not e
    print("G3 VERIFY " + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
