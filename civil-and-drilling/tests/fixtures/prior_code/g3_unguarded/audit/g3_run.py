"""G3 population run: every line of both contracts through the local engines (diagnostic, not a classification).

Writes verification/g3/:
  summary.json          per contract and code: lines, amount status, findings, G4 dependencies, billed-vs-contract
                        rate agreement (a DIAGNOSTIC only: billing is never the truth criterion), run context id
  decision_scopes.json  for every G3 decision and open question, the lines it decides and the amount under each
                        alternative (computed here, never taken from billing)
  trace_sample.jsonl    one full result with trace per code and amount status (reproducible choice: first line_ref)
With --dump also writes build/g3_results.jsonl (every line result with its trace; gitignored).
Nothing here flags an invoice, totals an invoice or writes a submission.

Usage::  python -m audit.g3_run [--dump]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal

from . import build, g3_cw, g3_dds
from .common import ROOT

OUT = ROOT / "verification" / "g3"
# G3-D1 scope: breaches of the submission rules (window, period, identity, one well per invoice) that the contract states
# no consequence for; spec/g3_decisions.yaml G3-D1.
PROCEDURAL = {"submitted_late", "submitted_early", "outside_period", "contract_ref_variant", "subcontractor_mismatch",
              "contractor_mismatch", "line_well_differs_from_invoice"}
CW_Q3 = {"record_missing", "record_wrong_series", "record_unsigned", "record_date_mismatch", "record_area_mismatch",
         "record_area_unresolved", "item_not_supported_by_record"}


def run_all(w=None):
    w = w or build.build()
    return w, {"CW": g3_cw.run(w), "DDS": g3_dds.run(w)}


def _s(x):
    return None if x is None else str(x)


def summary(w, res) -> dict:
    lines = {"CW": {r.ident: r.values for r in w.claims.rows["cw_lines"]},
             "DDS": {r.ident: r.values for r in w.claims.rows["dds_lines"]}}
    rate_key = {"CW": "rate_applied", "DDS": "unit_rate"}
    out = {"run_context": w.run_context["id"], "contracts": {}}
    for c, rs in res.items():
        codes = defaultdict(lambda: {"lines": 0, "amount_status": Counter(), "findings": Counter(), "g4": Counter(),
                                     "rate_agree": 0, "rate_differs": 0, "rate_not_single": 0, "billed_rate_is_an_admissible_alternative": 0})
        for ref, r in rs.items():
            e = codes[r.code]
            e["lines"] += 1
            e["amount_status"][r.amount_status] += 1
            e["findings"].update(r.findings)
            e["g4"].update(x.split(" ")[0] for x in r.g4_dependencies)
            if r.unit_rate is None:
                e["rate_not_single"] += 1
                if "rate_differs" in r.unresolved:
                    e["billed_rate_is_an_admissible_alternative"] += 1
            elif r.unit_rate == lines[c][ref][rate_key[c]]:
                e["rate_agree"] += 1
            else:
                e["rate_differs"] += 1
        out["contracts"][c] = {
            "lines": len(rs),
            "amount_status": dict(sorted(Counter(r.amount_status for r in rs.values()).items())),
            "findings": dict(sorted(Counter(f for r in rs.values() for f in r.findings).items())),
            "always_g4": (g3_cw if c == "CW" else g3_dds).ALWAYS_G4,
            "codes": {k: {**v, "amount_status": dict(v["amount_status"]), "findings": dict(v["findings"]), "g4": dict(v["g4"])}
                      for k, v in sorted(codes.items())},
            "note": "rate_agree/rate_differs compare the billed rate with the contract rate as a diagnostic only (billing is "
                    "never the truth criterion). A line whose rate depends on the band (G4), the ground class or the well "
                    "class (not evidenced; G5) has no single rate: it counts as rate_not_single, and "
                    "billed_rate_is_an_admissible_alternative counts those billed at one admissible alternative's rate.",
        }
    return out


CUR = {"CW": "SAR", "DDS": "USD"}       # CW Agreement p1; DDS Agreement p1: never summed together


def _money(rs, pick=None) -> dict:
    """Value per currency: a single amount where the line has one, else the range over its (picked) alternatives."""
    lo, hi = {}, {}
    for r in rs:
        if not r.payable:
            vals = [Decimal(0)]
        elif r.amount is not None:
            vals = [r.amount]
        else:
            vals = [a["amount"] for k, a in r.alternatives.items() if pick is None or pick(k)] or [a["amount"] for a in r.alternatives.values()]
        c = CUR[r.contract]
        lo[c] = lo.get(c, Decimal(0)) + min(vals)
        hi[c] = hi.get(c, Decimal(0)) + max(vals)
    return {c: (str(lo[c]) if lo[c] == hi[c] else {"min": str(lo[c]), "max": str(hi[c])}) for c in sorted(lo)}


def _has(dim, value):
    return lambda k: f"{dim}:{value}" in (k or "").split("|")


def _dims(r) -> set[str]:
    return {x.split(":", 1)[0] for k in r.alternatives for x in (k or "").split("|") if x}


def decision_scopes(w, res) -> dict:
    """For every G3 decision and open question: the lines it decides and, under the adopted reading and under every
    alternative, the lines affected and their value per currency (SAR civil, USD drilling; never summed together).
    Values are the engines' contract values, never billed amounts; billed quantities enter only as the contract's cap."""
    cw, dds = res["CW"], res["DDS"]
    allr = list(cw.values()) + list(dds.values())
    cl = {r.ident: r.values for r in w.claims.rows["cw_lines"]}
    dl = {r.ident: r.values for r in w.claims.rows["dds_lines"]}
    T = g3_dds.terms.dds()
    TC = g3_dds.terms.cw()
    sc = {}
    # Q3 --------------------------------------------------------------------------------------------------------
    q3 = [r for r in cw.values() if set(r.findings) & CW_Q3] + [r for r in dds.values() if "Q3:A" in r.readings]
    kept = {}
    for r in q3:                               # under B: the billed quantity (no record caps it) at the contract rate(s)
        q = (cl if r.contract == "CW" else dl)[r.line_ref]["quantity"]
        g = evaluate_as_payable(w, r, q)
        kept.setdefault(CUR[r.contract], []).append(g)
    sc["Q3"] = {"lines_decided": len(q3), "lines": sorted(r.line_ref for r in q3), "effect_by_reading": {
        "adopted": {"reading": "A", "lines": len(q3), "payable": sum(1 for r in q3 if r.payable), "value": _money(q3)},
        "B": {"lines": len(q3), "value": {c: _span(v) for c, v in sorted(kept.items())},
              "note": "no record caps the quantity: the billed quantity at the contract rate (every admissible rate where "
                      "the rate is conditional); CW P23 would deduct it from the next valuation (G4 event)"}}}
    # Q4 --------------------------------------------------------------------------------------------------------
    q4 = [r for r in dds.values() if "Q4" in _dims(r)]
    readings = sorted({x.split(":", 1)[1] for r in q4 for k in r.alternatives for x in k.split("|") if x.startswith("Q4:")})
    hourly = [r for r in dds.values() if r.code in g3_dds.HOURLY]
    sc["Q4"] = {"lines_decided": len(q4), "hourly_lines": len(hourly), "lines_where_readings_differ": sorted(r.line_ref for r in q4),
                "effect_by_reading": {x: {"lines": sum(1 for r in q4 if any(_has("Q4", x)(k) for k in r.alternatives)),
                                          "value": _money([r for r in q4 if any(_has("Q4", x)(k) for k in r.alternatives)], _has("Q4", x))}
                                      for x in readings}}
    # Q5 --------------------------------------------------------------------------------------------------------
    dd102 = [r for r in dds.values() if r.code == "DD-102"]
    hc630 = [r for r in dds.values() if r.code == "HC-630"]
    q5r = [r for r in dds.values() if _dims(r) & {"Q5-DD120", "Q5-RM530", "Q5-HC630"}]
    res5 = {}                                   # reading label -> the lines carrying it (each line once)
    for r in q5r:
        for x in sorted({x for k in r.alternatives for x in k.split("|")}):
            if x.split(":", 1)[0] in ("Q5-DD120", "Q5-RM530", "Q5-HC630"):
                res5.setdefault(x, []).append(r)
    sc["Q5"] = {"lines_decided": len(dd102) + len(q5r), "effect_by_reading": {
        "adopted": {"reading": "DD-102 A (decided); DD-120, RM-530, HC-630: every reading computed (residual open)",
                    "lines": len(dd102) + len(q5r), "DD-102": {"lines": len(dd102), "value": _money(dd102)},
                    "residual_lines_where_readings_differ": sorted(r.line_ref for r in q5r), "value": _money(dd102)},
        "B_DD102": {"lines": len(dd102), "value": {"USD": "0.00"},
                    "note": "the Sch 8 row's 'tool in the hole' names no tool for DD-102: not establishable on any line"},
        "HC630_per_run": {"lines": len(hc630), "lines_whose_amount_differs": sum(1 for r in hc630 if "Q5-HC630" in _dims(r)),
                          "value": _money(hc630, _has("Q5-HC630", "per BHA run (Sch 8)")),
                          "g4_event": "under the Schedule 8 reading HC-630 is once per BHA run: G4 checks one charge per run (all "
                                      f"{len(hc630)} lines carry the dependency)"},
        "DD120_back_reaming": {"lines": len(res5.get("Q5-DD120:circulating or back-reaming (Sch 8 row)", [])),
                               "value": _money(res5.get("Q5-DD120:circulating or back-reaming (Sch 8 row)", []),
                                               _has("Q5-DD120", "circulating or back-reaming (Sch 8 row)"))},
        "RM530_circulating_or_back_reaming": {"lines": len(res5.get("Q5-RM530:circulating or back-reaming (Sch 8 row)", [])),
                                              "value": _money(res5.get("Q5-RM530:circulating or back-reaming (Sch 8 row)", []),
                                                              _has("Q5-RM530", "circulating or back-reaming (Sch 8 row)"))}}}
    # Q8 --------------------------------------------------------------------------------------------------------
    cls = [r for r in dds.values() if "class" in _dims(r)]
    grd = [r for r in cw.values() if "ground" in _dims(r)]
    pd210 = [r for r in dds.values() if r.code == "PD-210" and r.payable]
    both = [r for r in cw.values() if any("P11 rest-day alone" in x for x in r.readings)]
    inv = {h.ident: h.values for h in w.claims.rows["dds_headers"]}
    proxy_cls = lambda r: _has("class", inv[dl[r.line_ref]["invoice_no"]]["well_class"])  # noqa: E731
    claimed_g = lambda r: _has("ground", ((cl[r.line_ref].get("ground_class") or "").split(" ")[0] or "G2"))  # noqa: E731
    sc["Q8"] = {"lines_decided": len(cls) + len(grd) + len(pd210) + len(both), "effect_by_reading": {
        "adopted": {"reading": "conditional across every admissible value where the contract's authority is not supplied; "
                               "explicit contractual defaults applied (P11 rest-day alone)",
                    "lines": len(cls) + len(grd) + len(pd210) + len(both),
                    "class_lines": len(cls), "value_class_lines": _money(cls),
                    "value_by_class": {"USD": {c: _money(cls, _has("class", c)).get("USD") for c in T.class_factor}},
                    "ground_lines": len(grd), "value_ground_lines": _money(grd),
                    "value_by_ground": {"SAR": {g: _money(grd, _has("ground", g)).get("SAR") for g in TC.ground}},
                    "pd210_conditional_on_nomination": len(pd210), "value_pd210_if_nominated": _money(pd210),
                    "cw_night_and_rest_day_rest_alone": len(both)},
        "A": {"lines": len(cls) + len(grd), "reading": "the claim's own class taken as a proxy (the G3 reading before correction)",
              "value": _merge(_money_each(cls, proxy_cls), _money_each(grd, claimed_g)),
              "note": "claim-stated classes as authority: rejected (DDS Cl.4, P2/P3; CW S4, Cl.5)"},
        "B": {"lines": len(cls) + len(grd) + len(pd210), "value": None,
              "note": "every amount depending on an unsupplied document unresolved: no value on these lines"}}}
    # Q11 -------------------------------------------------------------------------------------------------------
    wu = [r for r in dds.values() if "wrong_unit" in r.findings]
    sc["Q11"] = {"lines_decided": len(wu), "effect_by_reading": {
        "adopted": {"reading": "accumulator and 25A decided; wrong-unit remedy open (G5)", "lines": len(wu), "value": _money(wu)},
        "wrong_unit_A": {"lines": len(wu), "value": _money(wu, _has("Q11", "A"))},
        "wrong_unit_B": {"lines": len(wu), "value": {"USD": "0.00"} if wu else {}}},
        "pd210_quantity_above_report": sum(1 for r in dds.values() if r.code == "PD-210" and "quantity_above_report" in r.findings),
        "pd210_tolerance_alternatives": sum(1 for r in dds.values() if "tolerance" in _dims(r))}
    # Q13 -------------------------------------------------------------------------------------------------------
    losses = {l["report"]: l for run in w.runs.values() for l in run.losses}
    lh = [r for r in dds.values() if r.code in g3_dds.LOSS]
    lhp = [r for r in lh if r.payable]
    b_val, b_diff = Decimal(0), 0
    for r in lhp:
        ln = dl[r.line_ref]
        loss = losses[ln["report_ref"]]
        amt = r.allowed_quantity * g3_dds.loss_value(r.code, ln["service_date"], Decimal(loss["well_daily_hours_through_loss_day"]), T,
                                                      g3_dds.Trace(), "whole-well daily sum", "Q13 reading B")
        b_val += amt
        b_diff += amt != r.amount
    sc["Q13"] = {"lines_decided": len(lh), "losses": len(losses),
                 "part_e_equals_tool_history": sum(1 for l in losses.values() if l["hours_on_well"] == l["tool_daily_hours_through_loss_day"]),
                 "effect_by_reading": {
                     "adopted": {"reading": "A", "lines": len(lh), "payable": len(lhp), "value": _money(lh)},
                     "B": {"lines": len(lhp), "lines_whose_amount_differs": b_diff, "value": {"USD": str(b_val)}},
                     "C": {"lines": len(lhp), "note": "a query bounded by A and B: not needed (Part E equals the tool history on every loss)"}}}
    # G3-D1, G3-D2, G3-D3 ---------------------------------------------------------------------------------------
    d1 = [r for r in allr if set(r.findings) & PROCEDURAL]
    sc["G3-D1"] = {"lines_decided": len(d1), "by_contract": {c: sum(1 for r in d1 if r.contract == c) for c in CUR},
                   "effect_by_reading": {
                       "adopted": {"lines": len(d1), "payable": sum(1 for r in d1 if r.payable), "value": _money(d1),
                                   "note": "the line's supported contract value is kept; whether the non-compliant submission "
                                           "is payable now is not decided here (payment timing, G5)"},
                       "not_payable_now": {"lines": len(d1), "value": {c: "0.00" for c in sorted({CUR[r.contract] for r in d1})}}}}
    uns = [r for r in dds.values() if "report_unsigned" in r.findings]
    sc["G3-D2"] = {"lines_decided": len(uns), "effect_by_reading": {
        "adopted": {"lines": len(uns), "not_payable": sum(1 for r in uns if not r.payable), "value": _money(uns)},
        "all_lines": {"lines": len(uns), "value": {"USD": "0.00"}}}}
    sc["G3-D3"] = claim_fact_scope(w, cw)
    return sc


def evaluate_as_payable(w, r, q):
    """Q3 reading B: the line's value at the billed quantity (no record caps it) at its contract rate(s)."""
    if r.unit_rate is not None:
        return (q * r.unit_rate, q * r.unit_rate)
    rates = [a["unit_rate"] for a in r.alternatives.values() if a.get("unit_rate") is not None]
    if not rates:
        return None
    return (q * min(rates), q * max(rates))


def _span(pairs):
    pairs = [p for p in pairs if p is not None]
    lo, hi = sum((p[0] for p in pairs), Decimal(0)), sum((p[1] for p in pairs), Decimal(0))
    return str(lo) if lo == hi else {"min": str(lo), "max": str(hi)}


def _money_each(rs, pick_for):
    lo, hi = {}, {}
    for r in rs:
        if not r.payable:
            continue
        vals = [a["amount"] for k, a in r.alternatives.items() if pick_for(r)(k)] or [a["amount"] for a in r.alternatives.values()]
        c = CUR[r.contract]
        lo[c] = lo.get(c, Decimal(0)) + min(vals)
        hi[c] = hi.get(c, Decimal(0)) + max(vals)
    return {c: (str(lo[c]) if lo[c] == hi[c] else {"min": str(lo[c]), "max": str(hi[c])}) for c in sorted(lo)}


def _merge(a: dict, b: dict) -> dict:
    return {**a, **b}


def claim_fact_scope(w, cw) -> dict:
    """G3-D3: facts the claim states and the contract names no other evidence for - the civil zone of physical execution
    (Cl.4, Cl.42) and night work (Cl.7). Adopted: accepted as stated and disclosed on the line; alternative: the value
    under every zone / without the night uplift, computed by re-pricing the line."""
    zone_lines, night_lines = [], []
    zlo, zhi, nval, zadopt, nadopt = Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0)
    T = g3_cw.terms.cw()
    for line, app, rec, exists in g3_cw.inputs_from_world(w):
        r = cw[line["line_ref"]]
        if not r.payable:
            continue
        own = r.amount if r.amount is not None else min(a["amount"] for a in r.alternatives.values())
        if any(x.startswith("zone ") and "as stated in the application" in x for x in r.readings):
            zone_lines.append(r.line_ref)
            vals = []
            for z in T.zones:
                r2 = g3_cw.evaluate({**line, "site_zone": z}, app, rec, exists)
                vals.append(r2.amount if r2.amount is not None else min(a["amount"] for a in r2.alternatives.values()))
            zlo, zhi, zadopt = zlo + min(vals), zhi + max(vals), zadopt + own
        if any(x.startswith("night work as stated") for x in r.readings):
            night_lines.append(r.line_ref)
            r3 = g3_cw.evaluate({**line, "night_work": "N"}, app, rec, exists)
            nval += r3.amount if r3.amount is not None else min(a["amount"] for a in r3.alternatives.values())
            nadopt += own
    return {"lines_decided": len(zone_lines) + len(night_lines), "zone_lines": len(zone_lines), "night_lines": len(night_lines),
            "note": "values at the lowest admissible band/ground alternative where the line is conditional",
            "effect_by_reading": {
                "adopted": {"lines": len(zone_lines) + len(night_lines), "value": {"SAR": {"zone_lines": str(zadopt), "night_lines": str(nadopt)}},
                            "reading": "zone and night work as stated in the application, disclosed on each line"},
                "unverified_zone": {"lines": len(zone_lines), "value": {"SAR": {"min": str(zlo), "max": str(zhi)}},
                                    "note": "the value under every zone Z1-Z4"},
                "unverified_night": {"lines": len(night_lines), "value": {"SAR": str(nval)}, "note": "without the night uplift"}}}


def trace_sample(res) -> list[dict]:
    seen, out = set(), []
    for c, rs in res.items():
        for ref in sorted(rs):
            r = rs[ref]
            key = (c, r.code, r.amount_status)
            if key not in seen:
                seen.add(key)
                out.append(r.to_json())
    return out


def write(dump=False) -> dict:
    w, res = run_all()
    OUT.mkdir(parents=True, exist_ok=True)
    s = summary(w, res)
    (OUT / "summary.json").write_text(json.dumps(s, indent=1, default=str) + "\n")
    (OUT / "decision_scopes.json").write_text(json.dumps(decision_scopes(w, res), indent=1, default=str) + "\n")
    (OUT / "trace_sample.jsonl").write_text("".join(json.dumps(x, default=str) + "\n" for x in trace_sample(res)))
    if dump:
        b = ROOT / "build"
        b.mkdir(exist_ok=True)
        with (b / "g3_results.jsonl").open("w") as fh:
            for rs in res.values():
                for r in rs.values():
                    fh.write(json.dumps(r.to_json(), default=str) + "\n")
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    s = write(ap.parse_args().dump)
    for c, v in s["contracts"].items():
        print(c, v["lines"], v["amount_status"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
