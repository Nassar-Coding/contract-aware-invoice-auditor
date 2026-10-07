"""G6 population review and containment.

Reads the committed G3/G4/G5 results of the whole population (recomputed in memory from the pinned inputs) and writes
the review tables the G6 exit evidence needs - no rule is changed here and no count is a target:

  rule_exposure        every G3 check / G4 state family / G5 finding that decides lines: lines, invoices, flags
  categories           flag count per error category (each root category and each exact combination) per contract
  residuals            per contract and service/item code: lines whose contract value differs from the billed value
                       under the exported scenario, grouped by the finding that explains the difference
  outliers             per contract: the largest differences between exported and billed totals, the invoices with the
                       most findings, and the confidence bands
  unsupported_pass     every UNFLAGGED invoice that carries a check left unresolved, a line with no single value, a
                       coverage gap, or a fallback export - the candidates for an unsupported pass or silent fallback
  uncertainty          every flagged or unflagged row below 0.95, grouped by what the confidence rests on

Usage::  python tools/g6_review.py          (writes verification/g6/review.json)
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from audit import g4_run, g5_run  # noqa: E402
from audit.g5_outcomes import Engine, options  # noqa: E402

OUT = ROOT / "verification" / "g6"
FACT_CHECKS = {"ground_differs_from_record"}      # a check left open only because a fact's document is not supplied


def D(x):
    return None if x in (None, "") else Decimal(str(x))


def unsupported_reasons(o: dict, inv) -> set:
    """Why an UNFLAGGED outcome could be an unsupported pass or a silent fallback: a total not formed, a check not made,
    a G3 check left unresolved that no later layer completed (a band line's rate/arithmetic is completed by G4 per band
    state; a multi-rate line's displayed rate by G5 against each scenario's rate when every scenario carries one), a G4
    state check unresolved with no scenario carrying it, a line with no established payability. Empty: supported."""
    why = set()
    if not o["formed"]:
        why.add("total not formed (EXPORT-U)")
    cov = g5_run.coverage(inv)
    for n, v in cov.items():
        if v == "not made":
            why.add(f"check {n} not made")
    for _k, v, g in inv.lines:
        if g.g3.code == "DS-900":
            continue                  # valued by G5 from the invoice's services (Cl.38), never a line of its own
        fam = {s.family: s.status for s in g.state}
        rated = bool(g.r.alternatives) and all(a.get("unit_rate") is not None for a in g.r.alternatives.values()) \
            or (not g.r.alternatives and g.r.unit_rate is not None)
        for x in g.g3.checks:
            if x.status != "unresolved" or (x.finding or "") in FACT_CHECKS:
                continue
            # completed later: a band line's rate/arithmetic by G4 per band state (band_rate / band_arithmetic); a
            # multi-rate line's displayed rate by G5 against each scenario's rate (every scenario carries one)
            if x.check == "rate" and ("band_rate" in fam or rated):
                continue
            if x.check == "arithmetic" and ("band_arithmetic" in fam or g.r.payable is False):
                continue
            if g.r.payable is False:
                continue              # not payable at G3/G4: the unresolved check changes nothing
            why.add(f"G3 {x.check}:{x.finding or '-'} left unresolved and not completed later")
        for x in g.state:
            if x.status == "unresolved" and x.finding and not any(x.finding in (a.get("breaches") or [])
                                                                   for a in g.r.alternatives.values()):
                dims = sorted({d.split("@")[0] for k in g.r.alternatives for d in (k or "").split("|") if d})
                if not dims:
                    why.add(f"G4 {x.family}:{x.finding} unresolved with no scenario carrying it")
        if g.r.payable is None:
            why.add("a line with no established payability")
    return why


def review(w, st, eng: Engine, out: dict) -> dict:
    flagged = {i for i, o in out.items() if o["flagged"]}
    # ------------------------------------------------------------------------------------------ rule exposure
    expo = defaultdict(lambda: {"lines": 0, "invoices": set()})
    for c, invs in eng.inv.items():
        for no, inv in invs.items():
            for _k, _v, g in inv.lines:
                for x in g.g3.checks:
                    if x.status in ("finding", "unresolved"):
                        e = expo[(c, "G3", x.check, x.status, x.finding or "")]
                        e["lines"] += 1
                        e["invoices"].add(no)
                for x in g.state:
                    if x.status in ("finding", "unresolved"):
                        e = expo[(c, "G4", x.family, x.status, x.finding or "")]
                        e["lines"] += 1
                        e["invoices"].add(no)
    rule_exposure = sorted(({"contract": c, "layer": layer, "check": chk, "status": s, "finding": f, "lines": v["lines"],
                             "invoices": len(v["invoices"]), "of_which_flagged": len(v["invoices"] & flagged)}
                            for (c, layer, chk, s, f), v in expo.items()), key=lambda r: (r["contract"], -r["lines"]))
    # ------------------------------------------------------------------------------------------ categories
    cats = {c: {"root": Counter(), "combination": Counter(), "findings": Counter()} for c in ("CW", "DDS")}
    for i, o in out.items():
        if not o["flagged"]:
            continue
        c = o["contract"]
        cats[c]["combination"][o["error_category"]] += 1
        for r in o["error_category"].split("; "):
            cats[c]["root"][r] += 1
        for f in {f.split("@")[0] for f in o["findings"]}:
            cats[c]["findings"][f] += 1
    categories = {c: {k: dict(sorted(v.items(), key=lambda kv: (-kv[1], kv[0]))) for k, v in d.items()}  # ties by name
                  for c, d in cats.items()}
    # ------------------------------------------------------------------------------------------ residuals
    res = defaultdict(lambda: {"lines": 0, "invoices": set(), "billed_minus_contract": Decimal(0)})
    for i, o in out.items():
        inv = eng.inv[o["contract"]][i]
        vals = o["line_values"] or {}
        fs = defaultdict(list)
        for f in o["findings"]:
            code, _, ref = f.partition("@")
            if ref:
                fs[ref].append(code)
        for k, v, g in inv.lines:
            ref = v.get("line_ref") or k
            a, b = vals.get(ref), D(v.get("amount"))
            if a is None or b is None or D(a) == b:
                continue
            key = (o["contract"], g.g3.code or "", "+".join(sorted(fs.get(ref, []))) or "(no line finding)",
                   "flagged" if o["flagged"] else "unflagged")
            r = res[key]
            r["lines"] += 1
            r["invoices"].add(i)
            r["billed_minus_contract"] += b - D(a)
    residuals = sorted(({"contract": c, "code": code, "explained_by": why, "invoice_flag": fl, "lines": v["lines"],
                         "invoices": len(v["invoices"]), "billed_minus_contract": str(v["billed_minus_contract"]),
                         "sample": sorted(v["invoices"])[:5]}
                        for (c, code, why, fl), v in res.items()), key=lambda r: (r["contract"], -r["lines"]))
    # ------------------------------------------------------------------------------------------ outliers
    outliers = {}
    for c in ("CW", "DDS"):
        rows = [o for o in out.values() if o["contract"] == c]
        diff = sorted(rows, key=lambda o: -abs(D(o["expected_total"]) - D(o["billed_total"])))[:15]
        many = sorted(rows, key=lambda o: -len(o["findings"]))[:10]
        outliers[c] = {
            "largest_difference": [{"id": o["invoice_id"], "flagged": o["flagged"], "billed": str(o["billed_total"]),
                                    "expected": str(o["expected_total"]), "category": o["error_category"],
                                    "basis": o.get("expected_basis")} for o in diff],
            "most_findings": [{"id": o["invoice_id"], "findings": len(o["findings"]), "category": o["error_category"]}
                              for o in many],
            "confidence": dict(sorted(Counter(f"{o['flagged']}@{o['confidence']}" for o in rows).items()))}
    # ------------------------------------------------------------------------------------------ unsupported passes
    unsupported = defaultdict(list)
    for i, o in out.items():
        if o["flagged"]:
            continue
        why = unsupported_reasons(o, eng.inv[o["contract"]][i])
        for w_ in why:
            unsupported[w_].append((i, str(o["confidence"])))
    unsupported_pass = {k: {"invoices": len(v), "by_confidence": dict(Counter(c for _i, c in v)), "sample": [i for i, _c in v[:8]]}
                        for k, v in sorted(unsupported.items(), key=lambda kv: -len(kv[1]))}
    # ------------------------------------------------------------------------------------------ uncertainty
    unc = defaultdict(list)
    for i, o in out.items():
        if str(o["confidence"]) == "0.95":
            continue
        basis = []
        if o["open_readings"]:
            basis.append("open readings: " + ", ".join(sorted({k.split("@")[0] for k in o["open_readings"]})))
        if o["fact_dependent"] or o["nomination_dependent"]:
            basis.append("unsupplied document (class/ground/nomination)")
        if o["q1"]:
            basis.append("A3 recipient (Q1)")
        if not o["formed"]:
            basis.append("total not formed")
        unc[(o["contract"], o["flagged"], str(o["confidence"]), "; ".join(basis) or "procedural / Q7 C policy allocation")].append(i)
    uncertainty = [{"contract": c, "flagged": f, "confidence": conf, "rests_on": b, "invoices": len(v), "sample": v[:6]}
                   for (c, f, conf, b), v in sorted(unc.items(), key=lambda kv: -len(kv[1]))]
    return {"run_context": w.run_context["id"],
            "flags": {c: sum(1 for o in out.values() if o["contract"] == c and o["flagged"]) for c in ("CW", "DDS")},
            "rule_exposure": rule_exposure, "categories": categories, "residuals": residuals, "outliers": outliers,
            "unsupported_pass": unsupported_pass, "uncertainty": uncertainty}


def main() -> int:
    w, res, st = g4_run.run_g4()
    eng = Engine(w, st)
    out = eng.run()
    r = review(w, st, eng, out)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "review.json").write_text(json.dumps(r, indent=1, default=str) + "\n")
    print("flags", r["flags"])
    print("unsupported-pass candidates:", {k: v["invoices"] for k, v in r["unsupported_pass"].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
