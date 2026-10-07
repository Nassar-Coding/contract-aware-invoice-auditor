"""G5 population run: one outcome per template invoice, and the effect of every decision's alternatives.

Writes verification/g5/:
  outcomes.jsonl          every invoice: flag, category, expected and billed totals, confidence, the scenarios it was
                          evaluated under, the findings behind the flag (with their lines), check coverage 1-12
  summary.json            counts per contract (flags, categories, confidence bands, unpriceable), template coverage,
                          run context
  decision_effects.json   for every G5 decision (spec/g5_decisions.yaml): the lines and invoices it bears on and the flag
                          count per contract under the adopted reading and under each alternative
  audit_results.csv       the six template columns, in template order (the audit output)

Usage::  python -m audit.g5_run
"""
from __future__ import annotations

import csv
import io
import json
import sys
from collections import Counter, defaultdict

from . import g4_run
from .common import ROOT, SNAPSHOT
from .g4_core import base
from .g5_outcomes import DECIDED, Engine, Policy, cents, line_dims, options, template_ids

OUT = ROOT / "verification" / "g5"
COLUMNS = ["invoice_id", "flagged", "error_category", "expected_total_cents", "billed_total_cents", "confidence"]

# the twelve checks and where each is made (guidelines)
CHECK_SOURCES = {
    1: ("identity", "G3 identity check on every line; G5 header present"),
    2: ("term", "G3 term check on every line"),
    3: ("window/period", "G3 window and period checks on every line"),
    4: ("evidence", "G3 evidence check on every line (n/a where Schedule 5 names no record)"),
    5: ("quantity/unit", "G3 quantity and unit checks on every line"),
    6: ("identification", "G3 code family on every line (DS-900 recomputed at G5)"),
    7: ("rate", "G3 rate check on every line; G4 band repricing"),
    8: ("build-up/discount", "G3 build-up and discount by work date; G5 DS-900"),
    9: ("limits", "G4 daily limits, exclusions, bands, run/well events over every line"),
    10: ("duplicates", "G4 duplicate and once-only groups over every line of both contracts"),
    11: ("arithmetic", "G3 line arithmetic; G5 header arithmetic and judged total"),
    12: ("outcome", "G5 outcome with findings, expected total and confidence"),
}
G3_CHECK = {"identity": 1, "term": 2, "window": 3, "period": 3, "evidence": 4, "quantity": 5, "unit": 5,
            "identification": 6, "rate": 7, "status": 5, "arithmetic": 11}


def coverage(inv) -> dict:
    """Per check 1-12: the statuses recorded on this invoice (G3 per line, G4 per line where a state rule applies,
    G5 on the header). A check with no recorded status on any line is reported as not made."""
    seen = defaultdict(Counter)
    for _k, _v, g in inv.lines:
        for x in g.g3.checks:
            n = G3_CHECK.get(x.check)
            if n:
                seen[n][x.status] += 1
        for x in g.state:
            n = 10 if x.family in ("duplicate", "run_event", "well_event", "loss_event") else 9
            seen[n][x.status] += 1
        # check 6: G3 identifies every line against a priced item through its code family (spec/g3_code_families.yaml)
        seen[6]["identified (code family)" if g.g3.family else "not identified"] += 1
    seen[8]["g5 (discount, build-up in rate)"] += 1
    seen[9]["g4 state scan (every line)"] += 1
    seen[10]["g4 duplicate scan (every line)"] += 1
    seen[11]["g5 header"] += 1
    seen[12]["g5 outcome"] += 1
    return {str(n): dict(seen.get(n, {})) or "not made" for n in CHECK_SOURCES}


def serial(o: dict) -> dict:
    return json.loads(json.dumps(o, default=str))


def run(w=None, res=None, st=None, policy: Policy | None = None):
    if st is None:
        w, res, st = g4_run.run_g4(w, res)
    eng = Engine(w, st, policy)
    return w, res, st, eng, eng.run()


def result_rows(out: dict, ids: list[str]) -> list[dict]:
    rows = []
    for i in ids:
        o = out.get(i)
        if o is None:
            rows.append({"invoice_id": i, "flagged": "", "error_category": "", "expected_total_cents": "",
                         "billed_total_cents": "", "confidence": ""})
            continue
        rows.append({"invoice_id": i, "flagged": o["flagged"], "error_category": o["error_category"] if o["flagged"] else "",
                     "expected_total_cents": cents(o["expected_total"]), "billed_total_cents": cents(o["billed_total"]),
                     "confidence": f"{o['confidence']:.2f}"})
    return rows


def results_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    wr = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    wr.writeheader()
    for r in rows:
        wr.writerow(r)
    return buf.getvalue()


def flags(out: dict) -> dict:
    return {c: sum(o["flagged"] for o in out.values() if o["contract"] == c) for c in ("CW", "DDS")}


def summary(w, eng, out: dict, ids: list[str]) -> dict:
    s = {"run_context": w.run_context["id"], "template_rows": len(ids), "contracts": {}}
    extra = sorted(set(out) - set(ids))
    missing = sorted(set(ids) - set(out))
    s["template_coverage"] = {"outcomes": len(out), "missing_from_outcomes": missing, "outcomes_not_in_template": extra}
    for c in ("CW", "DDS"):
        o = [x for x in out.values() if x["contract"] == c]
        s["contracts"][c] = {
            "invoices": len(o), "flagged": sum(x["flagged"] for x in o),
            "flag_rate": f"{sum(x['flagged'] for x in o) / len(o):.4f}" if o else None,
            "categories": dict(Counter(x["error_category"] for x in o if x["flagged"]).most_common()),
            "confidence": dict(sorted(Counter(f"{x['flagged']}@{x['confidence']}" for x in o).items())),
            "flag_basis": dict(Counter(("wrong under every reading" if not x["right_under"] else "readings disagree (Q9-2)")
                                       for x in o if x["flagged"])),
            "judged_total_changed": sum(1 for x in o if x["flagged"] and x["expected_total"] != x["billed_total"]),
            "unpriceable": sum(1 for x in o if not x["formed"]),
            "fact_dependent": sum(1 for x in o if x["fact_dependent"] or x["nomination_dependent"]),
        }
    return s


def decision_effects(w, st, base_out: dict) -> dict:
    """Flags per contract under the adopted decisions and under each alternative, with the invoices that change."""
    def alt(policy: Policy) -> dict:
        _w, _r, _s, _e, out = run(w, None, st, policy)
        changed = sorted(i for i in out if out[i]["flagged"] != base_out[i]["flagged"]
                         or out[i]["expected_total"] != base_out[i]["expected_total"])
        return {"flags": flags(out), "invoices_changed": len(changed), "changed": changed[:40]}

    eng = Engine(w, st)

    def invoices_with(pred) -> dict:
        ids = defaultdict(set)
        n = 0
        for c, invs in eng.inv.items():
            for no, inv in invs.items():
                for k, v, g in inv.lines:
                    if pred(c, k, v, g):
                        ids[c].add(no)
                        n += 1
        return {"lines": n, "invoices": {c: len(v) for c, v in ids.items()}}

    def dim_pred(dim):
        return lambda c, k, v, g: dim in {base(x) for x in line_dims(g)}

    procedural = {"submitted_late", "submitted_early", "outside_period", "contract_ref_variant", "subcontractor_mismatch",
                  "contractor_mismatch", "line_well_differs_from_invoice"}
    out = {"adopted": {"flags": flags(base_out)}}
    out["Q9"] = {"B_only_changed_total": alt(Policy(q9="B"))}
    out["Q2"] = {"scope": "the A3 recipients", "B_inside_judged_total": alt(Policy(q2="B"))}
    out["Q1"] = {"recipients": {c: {k: v for k, v in eng.a3[c].items() if not k.startswith("_")} for c in ("CW", "DDS")},
                 "A_only": alt(Policy(q1="A")), "B_only": alt(Policy(q1="B"))}
    out["Q7C"] = {**invoices_with(lambda c, k, v, g: k in eng.stands), "open_either_stands": alt(Policy(q7c="open"))}
    for q, vals in (("Q12", ["B"]), ("Q14", ["B"]), ("Q6", ["B"]), ("Q11", ["B"])):
        out[q] = {**invoices_with(dim_pred(q))}
        for v in vals:
            out[q][f"{v}"] = alt(Policy(decided={**DECIDED, q: v}))
    for dim in ("Q6-day0", "Q4", "Q5-DD120", "Q5-RM530", "Q5-HC630", "order", "earlier"):
        entry = invoices_with(dim_pred(dim))
        values = sorted({x for c in eng.inv for inv in eng.inv[c].values() for _k, _v, g in inv.lines
                         for d, _x in options(g) for k, x in d.items() if base(k) == dim})
        for v in values:
            entry[f"only {v}"] = alt(Policy(force={dim: v}))
        out[dim] = entry
    out["Q8-class"] = {**invoices_with(dim_pred("class")), "B_query": alt(Policy(facts="query")),
                       "C_default": alt(Policy(facts="default"))}
    out["Q8-ground"] = {**invoices_with(dim_pred("ground")), "note": "B and C are computed together with Q8-class (facts policy)"}
    out["PD210-nomination"] = {**invoices_with(lambda c, k, v, g: any(x["dimension"] == "nomination" for x in g.r.conditions)),
                               "B_query": "counted in Q8-class B_query (facts policy covers nomination)",
                               "C_not_chargeable": "every PD-210 line not chargeable: every invoice listed above is wrong"}
    out["G3-D1"] = {**invoices_with(lambda c, k, v, g: bool(procedural & set(g.g3.findings))),
                    "note": "alternative 'not payable now' values these lines 0.00: every such invoice is already flagged under "
                            "Q9-1 A (procedural breach); under Q9 B it would be flagged for the changed total"}
    out["G3-D2"] = {**invoices_with(lambda c, k, v, g: "report_unsigned" in g.g3.findings),
                    "note": "alternative 'every line on an unsigned report not payable': these invoices are already flagged "
                            "(signature); only their expected totals change"}
    out["G3-D3"] = {**invoices_with(lambda c, k, v, g: c == "CW"),
                    "note": "zone and night work accepted as stated on every civil line (verification/g3/decision_scopes.json "
                            "G3-D3: 6,978 zone lines, 167 night lines); the alternatives (every zone; no night uplift) would "
                            "make the value of nearly every civil line conditional - not computed per invoice here"}
    out["D8-I1"] = {"note": "resolved through Q5 (entries Q5-DD120, Q5-RM530, Q5-HC630)"}
    out["Q5"] = {"entries": ["Q5-DD120", "Q5-RM530", "Q5-HC630"],
                 "lines": sum(out[k].get("lines", 0) for k in ("Q5-DD120", "Q5-RM530", "Q5-HC630"))}
    out["Q10"] = {"lines": 0, "note": "A adopted: no unbilled event is added; B (add unbilled chargeable events) is not computed - "
                                      "the records are read only for the lines billed"}
    fnd = lambda code: sorted(i for i, o in base_out.items() if any(f.split("@")[0] == code for f in o["findings"]))  # noqa: E731
    out["totals"] = {"discount_findings": fnd("discount"), "header_arithmetic_findings": fnd("header_arithmetic")}
    out["retention"] = {"release": {k: v for k, v in (eng.release or {}).items() if k != "not_established"},
                        "release_omitted": fnd("release_omitted"), "retention_arithmetic": fnd("retention_arithmetic")}
    return out


def write() -> dict:
    w, res, st, eng, out = run()
    ids = template_ids(SNAPSHOT)
    OUT.mkdir(parents=True, exist_ok=True)
    for o in out.values():
        o["coverage"] = coverage(eng.inv[o["contract"]][o["invoice_id"]])
    with (OUT / "outcomes.jsonl").open("w") as fh:
        for i in ids + sorted(set(out) - set(ids)):
            if i in out:
                fh.write(json.dumps(serial(out[i]), sort_keys=True) + "\n")
    s = summary(w, eng, out, ids)
    (OUT / "summary.json").write_text(json.dumps(s, indent=1, default=str) + "\n")
    (OUT / "decision_effects.json").write_text(json.dumps(decision_effects(w, st, out), indent=1, default=str) + "\n")
    (OUT / "audit_results.csv").write_text(results_csv(result_rows(out, ids)))
    return s


def main() -> int:
    s = write()
    for c, v in s["contracts"].items():
        print(c, v["invoices"], "flagged", v["flagged"], v["flag_rate"], "unpriceable", v["unpriceable"])
    print("template", s["template_coverage"]["outcomes"], "missing", len(s["template_coverage"]["missing_from_outcomes"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
