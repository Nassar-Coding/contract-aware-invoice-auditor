"""G5 exit checks: full invoice outcomes.

Exit condition: "Every invoice has check coverage, findings, amount status and an evidence trail; monetary and procedural
outcomes remain distinct; independently reviewed complete invoices reconcile step by step." Checks:

  Z1 one outcome per template invoice: exactly the 2,806 template ids, each once; audit_results.csv has the six template
     columns in template order, binary flags, a category exactly when flagged, integer minor units, billed cents equal
     to 100 x the judged header field (application_total / invoice_total), confidence in [0, 1].
  Z2 check coverage: every invoice has a recorded status for each of the twelve checks, and its lines are exactly the
     claim lines that carry its number (every line through G3 and G4).
  Z3 findings and evidence trail: a flagged invoice names at least one established finding with its line or header, its
     category is exactly the root categories of those findings (every finding code mapped); an unflagged invoice has no
     finding under any scenario.
  Z4 monetary and procedural outcomes distinct: a flag resting only on procedural or payment-only findings keeps the
     contract value as its expected total (no blanket zero); billing is never authority - changing every billed amount
     and header total changes no contract total, and a flagged row exports the contract total; the invoice's statement of
     an unsupplied fact is never authority (G5-B01) - changing every stated class and unrecorded ground changes no flag,
     category, exported total, contract total or confidence.
  Z5 independent sample: every reader's flag and expected total agree with the engine or carry a live settlement
     (verification/g5/sample_dispositions.yaml); the comparison reproduces; packets committed before the readers'
     outputs and both before the G5 engine (git).
  Z6 reconciliation step by step (and G5-B04: the legal combinations, joined line by line without the engine's list of
     open dimensions, give exactly its formed totals): every expected total is recomputed from the outcome's line values (civil:
     their sum; drilling: services, DS-900 = -4% of the excess over 250,000, net, VAT 15%, total, rounding half-even with
     exact rational arithmetic) and every cents figure from its decimal.
  Z7 ambiguity rule: flag = 0 only when the invoice is correct under every scenario; a flag where scenarios disagree has
     confidence 0.50; the confidence follows the rubric (Q9-5) in every band; no row above 0.80 depends on an unsupplied
     fact.
  Z8 decisions and registers: every spec/g5_decisions.yaml entry has a computed effect; no question still blocks G5; every
     question G5 owned carries a g5_disposition; policy decisions are recorded as policy.
  Z9 reproduction and boundary: the committed verification/g5 outputs reproduce; G3 results and G4 state are unchanged by
     G5.

Usage::  python tools/verify_g5.py
"""
from __future__ import annotations

import copy
import csv
import io
import itertools
import json
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import g5_sample_compare as SC  # noqa: E402
from audit import g4_run, g5_run  # noqa: E402
from audit.common import SNAPSHOT  # noqa: E402
from audit.g3_core import result_keys  # noqa: E402
from audit.g4_core import base, dims_of  # noqa: E402
from audit.g5_outcomes import (CATEGORY, CATEGORY_ORDER, FACT_DIMS, PAYMENT_ONLY, Engine, options,  # noqa: E402
                               template_ids)

OUT = ROOT / "verification" / "g5"
DECISIONS = ROOT / "spec" / "g5_decisions.yaml"
QUESTIONS = ROOT / "spec" / "open_questions.yaml"


def D(x):
    return None if x in (None, "") else Decimal(str(x))


import commit_order  # noqa: E402


# The order of first commits in the original development history, recorded in verification/commit_order.json
# (tools/commit_order.py re-derives it from a clone of that repository).
recorded_first_commit = commit_order.first_commit
recorded_before = commit_order.committed_before


# ============================================================================== Z1
def z1(out: dict, ids: list[str], headers: dict, sub_text: str) -> list[str]:
    errs = []
    if len(ids) != 2806 or len(set(ids)) != len(ids):
        errs.append(f"template has {len(ids)} ids ({len(set(ids))} unique), expected 2806 unique")
    missing = [i for i in ids if i not in out]
    extra = [i for i in out if i not in set(ids)]
    errs += [f"{i}: no outcome" for i in missing[:10]] + [f"{i}: outcome not in the template" for i in extra[:10]]
    rows = list(csv.DictReader(io.StringIO(sub_text)))
    head = sub_text.splitlines()[0].split(",") if sub_text else []
    if head != g5_run.COLUMNS:
        errs.append(f"results columns {head} are not the template's {g5_run.COLUMNS}")
    if [r["invoice_id"] for r in rows] != ids:
        errs.append("result rows are not the template ids in template order, each once")
    for r in rows:
        i = r["invoice_id"]
        if r["flagged"] not in ("0", "1"):
            errs.append(f"{i}: flagged {r['flagged']!r} is not binary")
            continue
        if (r["flagged"] == "1") != bool(r["error_category"]):
            errs.append(f"{i}: category {'missing on a flagged' if r['flagged'] == '1' else 'present on an unflagged'} row")
        for col in ("expected_total_cents", "billed_total_cents"):
            if not r[col].lstrip("-").isdigit():
                errs.append(f"{i}: {col} {r[col]!r} is not an integer")
        h = headers.get(i)
        if h is not None:
            field = "application_total" if i.startswith("PA-") else "invoice_total"
            if r["billed_total_cents"].lstrip("-").isdigit() and int(r["billed_total_cents"]) != int(D(h[field]) * 100):
                errs.append(f"{i}: billed_total_cents {r['billed_total_cents']} is not 100 x {field} {h[field]}")
        try:
            c = Decimal(r["confidence"])
            if not Decimal(0) <= c <= Decimal(1):
                errs.append(f"{i}: confidence {c} outside [0, 1]")
        except Exception:
            errs.append(f"{i}: confidence {r['confidence']!r} is not a number")
    return errs


# ============================================================================== Z2
def z2(out: dict, eng: Engine, w) -> list[str]:
    errs = []
    for lk, key in (("cw_lines", "application_no"), ("dds_lines", "invoice_no")):
        claim = {}
        for k, row in zip(result_keys(w.claims.rows[lk]), w.claims.rows[lk]):
            claim.setdefault(row.values.get(key), set()).add(k)
        c = "CW" if lk == "cw_lines" else "DDS"
        for no, inv in eng.inv[c].items():
            if {k for k, _v, _g in inv.lines} != claim.get(no, set()):
                errs.append(f"{no}: its lines are not the claim lines that carry its number")
    for i, o in out.items():
        cov = o.get("coverage") or {}
        for n in range(1, 13):
            if cov.get(str(n)) in (None, "not made", {}):
                errs.append(f"{i}: check {n} has no recorded status")
    return errs


# ============================================================================== Z3
def z3(out: dict) -> list[str]:
    errs = []
    for i, o in out.items():
        codes = [f.split("@")[0] for f in o["findings"]]
        unmapped = [c for c in codes if c not in CATEGORY]
        if unmapped:
            errs.append(f"{i}: finding(s) {unmapped} have no category")
        if o["flagged"]:
            if not o["findings"]:
                errs.append(f"{i}: flagged without a finding")
            cats = sorted({CATEGORY[c] for c in codes if c in CATEGORY}, key=CATEGORY_ORDER.index)
            if o["error_category"] != "; ".join(cats):
                errs.append(f"{i}: category {o['error_category']!r} is not the root categories of its findings {cats}")
        else:
            if o["wrong_under"]:
                errs.append(f"{i}: unflagged although wrong under {o['wrong_under'][:2]}")
            if o["findings"] or o["error_category"]:
                errs.append(f"{i}: unflagged with findings or a category")
    return errs


# ============================================================================== Z4
def _export_fixed(o: dict, eng: Engine, key, g) -> dict:
    """The assignment a line is exported under: the decided readings, the working label's readings (never its Q1/R45
    payment scenario), the Q7 C policy choices, and the export values of the unsupplied documents (export_facts)."""
    xf = o.get("export_facts") or {}
    fixed = {**eng.policy.decided, **{k: v for k, v in (o["expected_under"] or {}).items() if k not in ("Q1", "R45")}}
    if eng.policy.q7c == "earlier":
        fixed.update({d: st[0] for d, st in eng.stands.get(key, {}).items() if not st[1]})
    if xf.get("class") is not None:
        fixed["class"] = xf["class"]
    return fixed


def export_errors(out: dict, eng: Engine) -> list[str]:
    """EXPORT-E (Q9-7; review findings B-3, B-4, C-2), from G4's line alternatives and the claims, not the engine's
    scenario evaluation: (1) an unflagged row exports its own billed total, a row that depends on no unsupplied document
    its contract value; (2) every exported line value is one of the line's admissible contract values under the export
    values (the well class, each section's nomination, the line's ground) and the working readings - never a billed
    amount the contract does not give; (3) on a flagged row every line exported at a value other than its bill is named
    by a finding on that line; (4) every row of a well is exported under one well class (Cl.4) and every section under
    one nomination (Cl.23)."""
    errs = []
    well_class, sec_nom = {}, {}
    for i, o in out.items():
        if not o["formed"]:
            continue
        inv = eng.inv[o["contract"]][i]
        dep = o["fact_dependent"] or o["nomination_dependent"]
        xf = o.get("export_facts") or {}
        if not o["flagged"] and D(o["expected_total"]) != D(o["billed_total"]):
            errs.append(f"{i}: unflagged, but exports {o['expected_total']}, not its own total {o['billed_total']}")
        if not dep and D(o["expected_total"]) != D(o["contract_total"]):
            errs.append(f"{i}: depends on no unsupplied document, but exports {o['expected_total']}, not its contract value "
                        f"{o['contract_total']}")
        if dep and not xf:
            errs.append(f"{i}: depends on an unsupplied document and states no export values")
        for key, v, g in inv.lines:
            if inv.contract == "DDS" and g.g3.code == "DS-900":
                continue
            ref = v.get("line_ref") or key
            val = o["line_values"].get(ref)
            if val is None:
                continue
            val = D(val)
            nom = any(x["dimension"] == "nomination" for x in g.r.conditions)
            if g.r.payable is False or (nom and not (xf.get("nominated") or {}).get(
                    f"{v.get('well_name')}|{v.get('hole_section')}", True)):
                allowed = {Decimal(0)}
            else:
                fixed = _export_fixed(o, eng, key, g)
                gr = (xf.get("ground") or {}).get(ref)
                if gr is not None:
                    fixed["ground"] = gr
                allowed = {D(x.get("amount")) for d, x in options(g) if all(fixed.get(k, y) == y for k, y in d.items())}
            if val not in allowed:
                errs.append(f"{i}: line {ref} exported at {val}, not an admissible value of the line under its export "
                            f"values {sorted(map(str, allowed - {None}))[:4]}")
            billed = D(v.get("amount")) or Decimal(0)
            if o["flagged"] and val != billed and not any(f.endswith("@" + ref) for f in o["findings"]):
                errs.append(f"{i}: line {ref} exported at {val} against its bill {billed}, and no finding names the line")
        if xf.get("class") is not None and inv.header:
            well_class.setdefault(inv.header.get("well_name"), set()).add(xf["class"])
        for sec, x in (xf.get("nominated") or {}).items():
            sec_nom.setdefault(sec, set()).add(x)
    for well, cs in sorted(well_class.items(), key=str):
        if len(cs) > 1:
            errs.append(f"well {well}: its rows are exported under different well classes {sorted(cs)} (Cl.4)")
    for sec, xs in sorted(sec_nom.items()):
        if len(xs) > 1:
            errs.append(f"section {sec}: its rows are exported both nominated and not (Cl.23)")
    return errs


# G5-B06, stated here and not taken from the engine: breaches with no monetary consequence of their own are identity and
# timing (submission window, period, reference). Out-of-term work is not payable (G3, closed): a monetary term
# consequence, whose zero valuation is source-correct and must not fail this check.
PROCEDURAL_BREACH = {"identity", "timing"}


def z4(out: dict, perturbed: dict | None = None, claims_changed: dict | None = None) -> list[str]:
    errs = []
    for i, o in out.items():
        codes = {f.split("@")[0] for f in o["findings"]}
        if o["flagged"] and codes and all(CATEGORY.get(c) in PROCEDURAL_BREACH or c in PAYMENT_ONLY for c in codes):
            if D(o["expected_total"]) != D(o["billed_total"]) and not o["right_under"]:
                errs.append(f"{i}: a procedural/payment-only flag changed the expected total "
                            f"({o['billed_total']} -> {o['expected_total']})")
            if D(o["expected_total"]) == 0 and D(o["billed_total"]) != 0:
                errs.append(f"{i}: procedural breach valued at zero")
    for i, o in out.items():
        # G5-B02: a figure presented as a formed total is one; an unformed total is explicit - its lower bound, named
        if o["formed"] != (o.get("expected_status", "formed") == "formed"):
            errs.append(f"{i}: formed={o['formed']} but expected_status {o.get('expected_status')}")
        if not o["formed"]:
            b = o.get("expected_bounds") or [None, None]
            if b[0] is None or D(o["expected_total"]) != D(b[0]) or not str(o.get("expected_basis", "")).startswith("EXPORT-U"):
                errs.append(f"{i}: unformed total exported as {o['expected_total']} without its EXPORT-U lower bound {b}")
            continue
        # the exported figure (EXPORT-E): an admissible contract total - checked line by line by export_errors
        if o["formed"] and o.get("admissible_totals") is not None and \
                D(o["expected_total"]) not in {D(t) for t in o["admissible_totals"]}:
            errs.append(f"{i}: exports {o['expected_total']}, which is none of its admissible totals {o['admissible_totals'][:4]}")
    if perturbed is not None:
        # every billed figure changed: the contract total (absent-document values) never moves; a row that depends on no
        # unsupplied document still exports its contract value; any other row exports an admissible total, never a bill
        for i, o in out.items():
            p = perturbed.get(i)
            if p is None or D(p["contract_total"]) != D(o["contract_total"]):
                errs.append(f"{i}: the contract total moves with the billed figures ({o['contract_total']} -> "
                            f"{None if p is None else p['contract_total']})")
            elif p["formed"] and not (o["fact_dependent"] or o["nomination_dependent"]) and \
                    D(p["expected_total"]) != D(o["contract_total"]):
                errs.append(f"{i}: with the billed figures changed it exports {p['expected_total']} - it moves with the "
                            f"billed figures instead of the contract value {o['contract_total']}")
            elif p["formed"] and (D(p["expected_total"]) == D(p["billed_total"]) and not D(p["billed_total"]) in
                                  {D(t) for t in p.get("admissible_totals", [])}):
                errs.append(f"{i}: with the billed figures changed it exports the bill {p['expected_total']}, which is no "
                            "admissible total")
    if claims_changed is not None:
        # G5-B01: the invoice's own statement of a fact whose document is not supplied (header well class; a civil line's
        # ground where no record classifies it) changes nothing - flag, category, exported total, contract total, confidence
        for i, o in out.items():
            q = claims_changed.get(i)
            key = ("flagged", "error_category", "expected_total", "contract_total", "confidence")
            if q is None or any(str(q[k]) != str(o[k]) for k in key):
                errs.append(f"{i}: changing only its stated class/ground moves the outcome "
                            f"{[o[k] for k in key]} -> {None if q is None else [q[k] for k in key]}")
    return errs


CLASSES = ["Standard", "Extended Reach", "HPHT"]
GROUNDS = ["G1", "G2", "G3", "G4", "G5"]


def perturb_facts(w, st):
    """A copy of the world in which every drilling header states another well class and every civil line whose ground no
    supplied record classifies (its G4 value carries ground alternatives) states another ground class - evidence and
    billed figures unchanged."""
    unrecorded = {k for k, g in st["CW"].lines.items() if any("ground:" in a for a in g.r.alternatives)}
    keys = dict(zip(map(id, w.claims.rows["cw_lines"]), result_keys(w.claims.rows["cw_lines"])))
    w2 = copy.copy(w)
    w2.claims = copy.copy(w.claims)
    w2.claims.rows = dict(w.claims.rows)
    for k in ("dds_headers", "cw_lines"):
        new = []
        for r in w.claims.rows[k]:
            r2 = copy.copy(r)
            r2.values = dict(r.values)
            if k == "dds_headers" and r2.values.get("well_class") in CLASSES:
                r2.values["well_class"] = CLASSES[(CLASSES.index(r2.values["well_class"]) + 1) % 3]
            if k == "cw_lines" and keys[id(r)] in unrecorded:
                g0 = (r2.values.get("ground_class") or "").split(" ")[0]
                r2.values["ground_class"] = GROUNDS[(GROUNDS.index(g0) + 2) % 5] if g0 in GROUNDS else "G4"
            new.append(r2)
        w2.claims.rows[k] = new
    return w2


def rebill_admissible(w, eng: Engine, out: dict):
    """EXPORT-E independence (G5-B01): a copy of the world in which every unflagged fact-dependent invoice with no other
    open reading is re-billed exactly at a DIFFERENT admissible value of its unsupplied facts - drilling: another well
    class for every class-rated line (DS-900 and VAT recomputed); civil: another ground class for every unrecorded-ground
    line (retention and net recomputed). Returns (world, {invoice: new billed total})."""
    from decimal import ROUND_DOWN, ROUND_HALF_EVEN
    q = Decimal("0.01")
    fixed0 = dict(eng.policy.decided)
    new_line, new_head, rebilled = {}, {}, {}
    # drilling invoices that can move: unflagged, formed, no other open reading, fact-dependent; with the classes other
    # than the one each line is billed at
    eligible = {}
    for i, o in out.items():
        if o["contract"] == "DDS" and not o["flagged"] and o["formed"] and not o["open_readings"] and o["fact_dependent"] \
                and not o["nomination_dependent"] and eng.inv["DDS"][i].header is not None:
            cls = set()
            for _k, v, g in eng.inv["DDS"][i].lines:
                vals = {d["class"]: x for d, x in options(g) if "class" in d and all(fixed0.get(a, b) == b for a, b in d.items())}
                if vals:
                    cls = cls | {c for c, x in vals.items() if D(x.get("amount")) != D(v.get("amount"))} if not cls else \
                        cls & {c for c, x in vals.items() if D(x.get("amount")) != D(v.get("amount"))}
            eligible[i] = cls
    for i, o in out.items():
        if o["flagged"] or not o["formed"] or o["open_readings"] or not o["fact_dependent"] or o["nomination_dependent"]:
            continue
        inv = eng.inv[o["contract"]][i]
        if inv.header is None:
            continue
        dim = "class" if o["contract"] == "DDS" else "ground"
        amounts, ok = {}, True
        for k, v, g in inv.lines:
            vals = {d.get(dim): x for d, x in options(g) if dim in d and all(fixed0.get(a, b) == b for a, b in d.items())}
            if not vals:
                continue
            billed = D(v.get("amount"))
            other = sorted(c for c, x in vals.items() if D(x.get("amount")) != billed and x.get("amount") is not None
                           and x.get("unit_rate") is not None)
            if not other:
                ok = False
                break
            x = vals[other[0]] if dim == "ground" else None
            amounts[k] = (other, vals)
        if not ok or not amounts:
            continue
        if dim == "class":
            # the well class is one fact for all the well's services (Cl.4): every invoice of the well moves together
            well = inv.header.get("well_name")
            sisters = [j for j in eng.inv["DDS"].values() if j.header and j.header.get("well_name") == well
                       and any("class" in d for _k, _v, g in j.lines for d, _x in options(g))]
            if any(j.id not in eligible for j in sisters):
                continue
            common = set.intersection(*[set(o_) for o_, _v in amounts.values()], *[eligible[j.id] for j in sisters])
            if not common:
                continue
            c = sorted(common)[0]
            chosen = {k: vals[c] for k, (_o, vals) in amounts.items()}
        else:
            chosen = {k: vals[o_[0]] for k, (o_, vals) in amounts.items()}
        rate_f = "unit_rate" if o["contract"] == "DDS" else "rate_applied"
        lines = {k: dict(v) for k, v, _g in inv.lines}
        for k, x in chosen.items():
            lines[k]["amount"] = D(x["amount"])
            lines[k][rate_f] = D(x["unit_rate"])
        h = dict(inv.header)
        if o["contract"] == "DDS":
            svc = sum((D(v.get("amount")) or Decimal(0) for k, v, g in inv.lines if g.g3.code != "DS-900" and k not in chosen),
                      Decimal(0)) + sum((D(x["amount"]) for x in chosen.values()), Decimal(0))
            due = -((svc - 250000) * Decimal("0.04")).quantize(q, rounding=ROUND_HALF_EVEN) if svc > 250000 else Decimal(0)
            ds = [k for k, v, g in inv.lines if g.g3.code == "DS-900"]
            if (due != 0 and len(ds) != 1) or (due == 0 and any(D(lines[k].get("amount")) for k in ds)):
                continue
            for k in ds:
                lines[k]["amount"] = due
            net = svc + due
            vat = (net * Decimal("0.15")).quantize(q, rounding=ROUND_HALF_EVEN)
            h.update({"net_amount": net, "vat_amount": vat, "invoice_total": net + vat})
            rebilled[i] = net + vat
        else:
            tot = sum((D(v.get("amount")) or Decimal(0) for v in lines.values()), Decimal(0))
            ret = (tot * Decimal("0.05")).quantize(q, rounding=ROUND_DOWN)
            h.update({"application_total": tot, "retention": ret,
                      "net_payable": tot + (D(h.get("adjustment")) or 0) - ret + (D(h.get("retention_released")) or 0)})
            rebilled[i] = tot
        new_line.update(lines)
        new_head[i] = h
    w2 = copy.copy(w)
    w2.claims = copy.copy(w.claims)
    w2.claims.rows = dict(w.claims.rows)
    for lk, hk, no_f in (("cw_lines", "cw_headers", "application_no"), ("dds_lines", "dds_headers", "invoice_no")):
        rows = []
        for k, r in zip(result_keys(w.claims.rows[lk]), w.claims.rows[lk]):
            if k in new_line:
                r2 = copy.copy(r)
                r2.values = new_line[k]
                rows.append(r2)
            else:
                rows.append(r)
        w2.claims.rows[lk] = rows
        hrows = []
        for r in w.claims.rows[hk]:
            if r.values.get(no_f) in new_head:
                r2 = copy.copy(r)
                r2.values = new_head[r.values[no_f]]
                hrows.append(r2)
            else:
                hrows.append(r)
        w2.claims.rows[hk] = hrows
    return w2, rebilled


def export_e_errors(out: dict, rebilled_out: dict, rebilled: dict) -> list[str]:
    """The flag and confidence of an invoice right under some admissible value of an unsupplied document never depend on
    WHICH admissible value its bill matches; its contract total never moves; its export follows its own admissible
    total (EXPORT-E)."""
    errs = []
    for i, new_total in rebilled.items():
        o, r = out[i], rebilled_out.get(i)
        if r is None:
            errs.append(f"{i}: no outcome after re-billing")
            continue
        if (r["flagged"], str(r["confidence"])) != (o["flagged"], str(o["confidence"])):
            errs.append(f"{i}: re-billed at another admissible value, flag/confidence {o['flagged']}/{o['confidence']} -> "
                        f"{r['flagged']}/{r['confidence']}")
        elif D(r["contract_total"]) != D(o["contract_total"]) or D(r["expected_total"]) != new_total:
            errs.append(f"{i}: re-billed at another admissible value, contract {o['contract_total']} -> {r['contract_total']}, "
                        f"export {r['expected_total']} (own total {new_total})")
    if not rebilled:
        errs.append("EXPORT-E independence: no invoice could be re-billed - the check exercised nothing")
    return errs


def unformed_branch_errors() -> list[str]:
    """G5-B02: the population has no unformed invoice, so the branch is executed here on the review's raw input through the
    production pipeline (materialized claims and report -> G2 -> G3 -> G4 -> G5): one PD-210 charge with no start depth,
    end 1,500 m, quantity 50, billed 2,117.50 - and the same with the billed amount changed to 9,999.00. The exported
    total must be the explicit lower bound, the same for both, never either billed amount."""
    import g4_histories as H
    import g4_history_compare as HC
    from audit import g3_cw, g3_dds, g4_cw, g4_dds
    outs = []
    for billed in ("2117.50", "9999.00"):
        doc = H.dds_inv("MDS-95901", "NGP-ZZ-951", "NG-Rig 97", "2025-04-20",
                        [("2025-04-10", "PD-210", "50", "42.35", H.S12, "Operating", "", "1500")])
        doc["lines"][0]["amount"] = billed
        reps = H.reports(H.ddr("NGP-ZZ-951", "NG-Rig 97", "2025-04-10", H.S12, "Operating", 1450, 1500, 15, 1, H.BASIC,
                               ["2 directional hands", "2 MWD engineers"], part_b=("2025-04-10", "2025-04-10", H.BASIC, 15, False)))
        w, _g3, _st = HC.run_history({"id": "Z4-UNFORMED", "documents": [doc], "reports": reps, "records": {}, "given": []})
        res = {"CW": g3_cw.run(w), "DDS": g3_dds.run(w)}
        st = {"CW": g4_cw.run(w, res["CW"]), "DDS": g4_dds.run(w, res["DDS"])}
        e = Engine(w, st)
        outs.append(e.outcome(e.inv["DDS"]["MDS-95901"]))
    errs = []
    for o, billed in zip(outs, ("2117.50", "9999.00")):
        if o["formed"] or not str(o.get("expected_basis", "")).startswith("EXPORT-U"):
            errs.append(f"unformed branch (billed {billed}): formed={o['formed']}, basis {o.get('expected_basis')!r}")
        if D(o["expected_total"]) in (D("2117.50"), D("9999.00")) or D(o["expected_total"]) != D(o["expected_bounds"][0]):
            errs.append(f"unformed branch (billed {billed}): exports {o['expected_total']}")
    if D(outs[0]["expected_total"]) != D(outs[1]["expected_total"]):
        errs.append(f"unformed branch: the export moves with the billed amount ({outs[0]['expected_total']} -> "
                    f"{outs[1]['expected_total']})")
    return errs


def perturb_billing(w):
    """A copy of the world whose billed line amounts, rates and header totals are all changed."""
    w2 = copy.copy(w)
    w2.claims = copy.copy(w.claims)
    w2.claims.rows = {}
    for k, rows in w.claims.rows.items():
        new = []
        for r in rows:
            r2 = copy.copy(r)
            r2.values = dict(r.values)
            for f in ("amount", "rate_applied", "unit_rate", "application_total", "invoice_total", "net_amount", "vat_amount",
                      "retention", "net_payable"):
                if isinstance(r2.values.get(f), Decimal):
                    r2.values[f] = r2.values[f] * Decimal("1.37") + Decimal("11.11")
            new.append(r2)
        w2.claims.rows[k] = new
    return w2


def attribution_errors(out: dict, eng: Engine) -> list[str]:
    """G5-B06, from the line's own facts, not the engine's finding list: a rate finding on a line needs a displayed rate
    that is not an admissible contract rate of the line (or a G3/G4 rate check that is not a pass); a quantity finding
    needs an admissible allowed quantity below the billed one (or a G3/G4 quantity finding)."""
    errs = []
    for i, o in out.items():
        inv = eng.inv[o["contract"]].get(i)
        if inv is None:
            continue
        by_ref = {(v.get("line_ref") or k): (v, g) for k, v, g in inv.lines}
        for f in o["findings"]:
            code, _, ref = f.partition("@")
            if not ref or ref not in by_ref:
                continue
            v, g = by_ref[ref]
            checks = [(x.check, x.status, x.finding) for x in g.g3.checks] + [(x.family, x.status, x.finding) for x in g.state]
            if code == "rate_differs":
                ra = D(v.get("rate_applied") if o["contract"] == "CW" else v.get("unit_rate"))
                named = any(f_ == "rate_differs" and st == "finding" for _c, st, f_ in checks) or \
                    any("rate_differs" in (x.get("breaches") or []) for x in g.r.alternatives.values())
                # the scenarios the outcome is judged under: in one of them the displayed rate must not be the line's rate
                scen = [dict(x) for x in (o["wrong_under"] or [])] + [dict(o["expected_under"] or {})]
                xf = o.get("export_facts") or {}
                for lab in scen:            # the unsupplied documents at their export values (EXPORT-E)
                    if xf.get("class") is not None and "class" not in lab:
                        lab["class"] = xf["class"]
                    if (xf.get("ground") or {}).get(ref) is not None:
                        lab["ground"] = xf["ground"][ref]
                differs = False
                for lab in scen:
                    rates = {D(x.get("unit_rate")) for d, x in options(g)
                             if all(lab.get(k, y) == y for k, y in d.items())}
                    if g.r.payable is False and not g.r.alternatives:
                        rates = {D(a.get("unit_rate")) for a in g.g3.alternatives.values()} | {g.g3.unit_rate}
                    if ra is not None and ra not in rates - {None} and rates - {None}:
                        differs = True
                if not named and not differs:
                    errs.append(f"{i}: rate_differs on {ref}, whose displayed rate {ra} is an admissible contract rate")
            if code == "quantity_above_record":
                bq = D(v.get("quantity"))
                qs = {D(x.get("allowed_quantity")) for _d, x in options(g)} - {None}
                named = any(f_ == "quantity_above_record" and st == "finding" for _c, st, f_ in checks)
                if not named and not any(q < bq for q in qs if bq is not None):
                    errs.append(f"{i}: quantity_above_record on {ref}, but no admissible quantity is below the billed {bq}")
    return errs


def ds900_errors(out: dict, eng: Engine) -> list[str]:
    """G5-B05, recomputed here in exact rationals from the claim lines: Cl.38 requires one negative DS-900 charge of 4%
    of the invoice's services above 250,000 (half-even), none at or below; every departure - two or more charges, a
    positive one, a wrong sum - must be a discount finding of the outcome."""
    errs = []
    for i, o in out.items():
        if o["contract"] != "DDS":
            continue
        inv = eng.inv["DDS"].get(i)
        if inv is None:
            continue
        ds = [D(v.get("amount")) or Decimal(0) for _k, v, g in inv.lines if g.g3.code == "DS-900"]
        svc = sum((D(v.get("amount")) or Decimal(0) for _k, v, g in inv.lines if g.g3.code != "DS-900"), Decimal(0))
        due = -_half_even_frac((Fraction(svc) - 250000) * Fraction(4, 100)) if svc > 250000 else Fraction(0)
        fs = {f.split("@")[0] for f in o["findings"]}
        charged = [x for x in ds if x != 0]
        if len(charged) > 1 and "discount_split" not in fs:
            errs.append(f"{i}: {len(charged)} DS-900 charges (Cl.38: one) and no discount_split finding")
        if any(x > 0 for x in charged) and "discount_sign" not in fs:
            errs.append(f"{i}: a positive DS-900 charge and no discount_sign finding")
        if Fraction(sum(ds, Decimal(0))) != due and "discount" not in fs:
            errs.append(f"{i}: DS-900 {sum(ds, Decimal(0))} against {float(due):.2f} due and no discount finding")
    return errs


def a3_account(eng: Engine, contract: str) -> dict:
    accts = eng.st[contract].adjustments if hasattr(eng, "st") else []
    return accts[0] if accts else {}


def _unvalued_sides(acct: dict, contract: str) -> tuple:
    """(below open, above open) of an account from its unvalued lines, each line's sign recomputed from the terms: the
    instrument's base rate for the line's item and date against the rate it replaces."""
    from audit import terms
    T = terms.cw() if contract == "CW" else terms.dds()
    ins = next((i for i in T.instruments if i.id == acct.get("instrument")), None)
    lo_open = hi_open = False
    for ref, v in (acct.get("by_line") or {}).items():
        d = v["difference"]
        if not (d is None or (isinstance(d, dict) and None in d.values())):
            continue
        date = v.get("work_date") or v.get("service_date")
        code = _LINE_CODE.get((contract, ref))
        if ins is None or code is None or date in (None, "None"):
            lo_open = hi_open = True
            continue
        import datetime as _dt
        wd = _dt.date.fromisoformat(date)
        pick = lambda c: max(c, key=lambda x: (x[0], x[1]))[2] if c else T.sch1[code]["rate"]  # noqa: E731
        new, old = pick(T.rate_candidates(code, wd, None)), pick(T.rate_candidates(code, wd, ins.issued - _dt.timedelta(days=1)))
        lo_open = lo_open or new < old
        hi_open = hi_open or new > old
    return lo_open, hi_open


_LINE_CODE = {}


def payment_errors(out: dict, eng: Engine) -> list[str]:
    """G5-B03, from G4's accounts and recipients directly: a claimed adjustment or release that differs from an exact
    account on its sole recipient, or appears on a document that is no candidate recipient under any reading, is a
    finding of the outcome (never passed because it is nonzero)."""
    errs = []
    for c in ("CW", "DDS"):
        for _i, inv in eng.inv[c].items():
            for k, v, g in inv.lines:
                _LINE_CODE[(c, v.get("line_ref") or k)] = g.g3.code
    rel = eng.release or {}
    rec = rel.get("recipient")
    rel_cands = ({rec} if isinstance(rec, str) else set(rec.get("tie") or rec.get("not_established") or [])
                 if isinstance(rec, dict) else set())
    for i, o in out.items():
        inv = eng.inv[o["contract"]].get(i)
        h = (inv.header if inv else None) or {}
        fs = {f.split("@")[0] for f in o["findings"]}
        adj = D(h.get("adjustment")) or Decimal(0)
        a3 = eng.a3[o["contract"]]
        cands = {x for q, xs in a3.items() if not q.startswith("_") for x in xs}
        if adj != 0 and i not in cands and "adjustment_unsupported" not in fs:
            errs.append(f"{i}: adjustment {adj} on a document no reading makes the recipient, and no finding")
        reads = [q for q in a3 if not q.startswith("_")]
        t = a3.get("_total") or {}
        vals = {(v["min"], v["max"]) for v in (t.get("by_reading") or {}).values()}
        if reads and all(a3[q] == [i] for q in reads) and len(vals) == 1:
            lo, hi = (D(x) for x in next(iter(vals)))
            if t.get("lines_not_established"):
                # a line's difference not established: the account is open on each side such a line can move it (the
                # sign of its rate change, recomputed here from the terms for each unvalued line)
                lo_open, hi_open = _unvalued_sides(a3_account(eng, o["contract"]), o["contract"])
                lo, hi = (None if lo_open else lo), (None if hi_open else hi)
            outside = (lo is not None and adj < lo) or (hi is not None and adj > hi)
            if outside and not fs & {"adjustment_omitted", "adjustment_differs"}:
                errs.append(f"{i}: adjustment {adj} outside the account [{lo}, {hi}], and no finding")
            elif not outside and lo != hi and "adjustment_not_established" not in {x.split("@")[0] for x in o["not_established"]} \
                    and not fs & {"adjustment_omitted", "adjustment_differs"}:
                errs.append(f"{i}: adjustment {adj} inside an account not established [{lo}, {hi}], and not disclosed")
        if o["contract"] == "CW":
            r = D(h.get("retention_released")) or Decimal(0)
            if r != 0 and i not in rel_cands and "release_unsupported" not in fs:
                errs.append(f"{i}: release {r} on an application that is not the 45A recipient, and no finding")
            if rec == i and isinstance(rel.get("released"), str) and r != D(rel["released"]) and \
                    not fs & {"release_omitted", "release_differs"}:
                errs.append(f"{i}: release {r}, the account {rel['released']}, and no finding")
            if rec == i and isinstance(rel.get("released"), dict):
                # a range (readings) or a lower bound only (an earlier application's value not established): a release
                # below every reading's minimum, or above every bounded maximum, is established
                rs = list(rel["released"].get("by_reading", {}).values())
                lo = min(D(x["min"]) for x in rs)
                hi = None if any(x["max"] is None for x in rs) else max(D(x["max"]) for x in rs)
                if (r < lo or (hi is not None and r > hi)) and not fs & {"release_omitted", "release_differs"}:
                    errs.append(f"{i}: release {r} outside the account [{lo}, {hi}], and no finding")
    return errs


# ============================================================================== Z5
def z5(result: dict, dispositions: dict, first_commit=recorded_first_commit, before=recorded_before) -> list[str]:
    errs = []
    live = set()
    for i, e in result.items():
        if not e["readers"]:
            errs.append(f"{i}: no independent result")
        for r, ds in e["readers"].items():
            for d in ds:
                k = f"{i}|{r}|{d}"
                live.add(k)
                if k not in dispositions:
                    errs.append(f"{k}: disagrees with the independent expected outcome and has no settlement")
                elif dispositions[k].get("settled") not in ("engine", "both", "presentation"):
                    errs.append(f"{k}: settlement {dispositions[k].get('settled')!r} is not a closed settlement")
    errs += [f"{k}: settlement no longer matches a disagreement (stale)" for k in dispositions if k not in live]
    packets = sorted(str(p.relative_to(ROOT)) for p in (OUT / "samples").glob("packet_*.jsonl"))
    readers = sorted(str(p.relative_to(ROOT)) for p in (OUT / "samples").glob("expected_*.jsonl"))
    engine = "audit/g5_outcomes.py"
    for p in packets:
        for x in readers + [engine]:
            if not before(first_commit(p), first_commit(x)):
                errs.append(f"{p} was not committed before {x}")
    for x in readers:
        if not before(first_commit(x), first_commit(engine)):
            errs.append(f"{x} was not committed before {engine}")
    return errs


# ============================================================================== Z6
def _half_even_frac(x: Fraction) -> Fraction:
    """Round to cents, half to even, in exact rational arithmetic (independent of decimal quantize)."""
    c = x * 100
    n, r = divmod(c.numerator, c.denominator)
    rem = Fraction(r, c.denominator)
    if rem > Fraction(1, 2) or (rem == Fraction(1, 2) and n % 2 == 1):
        n += 1
    return Fraction(n, 100)


def z6(out: dict) -> list[str]:
    errs = []
    for i, o in out.items():
        vals = o["line_values"]
        if any(v is None for v in vals.values()):
            continue
        s = sum((Fraction(Decimal(v)) for v in vals.values()), Fraction(0))
        if o["contract"] == "CW":
            tot = s
        else:
            ds = -_half_even_frac((s - 250000) * Fraction(4, 100)) if s > 250000 else Fraction(0)
            net = s + ds
            tot = net + _half_even_frac(net * Fraction(15, 100))
        exp = D(o["expected_total"])
        if o["formed"] and Fraction(exp) != tot:
            errs.append(f"{i}: expected total {exp} does not reconcile with its lines ({float(tot):.2f})")
        c = g5_run.cents(exp)
        if c != int(Fraction(exp) * 100):
            errs.append(f"{i}: cents {c} is not 100 x {exp}")
    return errs


def joint_errors(out: dict, eng: Engine) -> list[str]:
    """G5-B04, independent of the engine's collection of open dimensions: the legal combinations of an invoice are built
    by joining its lines' alternatives directly - two alternatives combine only where they agree on every dimension they
    share, so independent groups combine freely and one group's choice is never imposed on another. The readings the
    register decides, the Q7 C policy choices and the invoice's facts under the adopted fact rule fix their
    dimensions. The joined totals (civil: the sum; drilling: services, DS-900, VAT) must be exactly the engine's
    formed totals, and the invoice formed exactly when every legal combination values every line."""
    errs = []
    p = eng.policy
    for i, o in out.items():
        inv = eng.inv[o["contract"]][i]
        states = {((), ())}
        for key, v, g in inv.lines:
            if inv.contract == "DDS" and g.g3.code == "DS-900":
                continue
            if g.r.payable is False:
                states = {(a, vals + (Decimal(0),)) for a, vals in states}
                continue
            fixed = dict(p.decided)
            if p.q7c == "earlier":
                fixed.update({d: s[0] for d, s in eng.stands.get(key, {}).items() if not s[1]})
            fixed.update(fact_values(o, g, v.get("line_ref") or key))
            xf = o.get("export_facts") or {}
            if any(x["dimension"] == "nomination" for x in g.r.conditions) and \
                    not (xf.get("nominated") or {}).get(f"{v.get('well_name')}|{v.get('hole_section')}", False):
                states = {(a, vals + (Decimal(0),)) for a, vals in states}      # not nominated: not chargeable
                continue
            # a civil line's ground class is its own fact (its own work area and day): local to the line in the join;
            # a well's class is one fact for all its services (joint)
            opts = [({(f"{k}@{key}" if k == "ground" else k): y for k, y in d.items()}, x) for d, x in options(g)
                    if all(fixed.get(k, y) == y for k, y in d.items())]
            new = set()
            for a, vals in states:
                ad = dict(a)
                for d, x in opts:
                    if all(ad.get(k, y) == y for k, y in d.items()):
                        amt = D(x.get("amount"))
                        new.add((tuple(sorted({**ad, **d}.items())), vals + (amt,)))
            states = new
        formed = bool(states) and all(None not in vals for _a, vals in states)
        totals = set()
        for _a, vals in states:
            if None in vals:
                continue
            s = sum(vals, Decimal(0))
            if inv.contract == "DDS":
                ds = -(((s - 250000) * Decimal("0.04")).quantize(Decimal("0.01"), rounding="ROUND_HALF_EVEN")) if s > 250000 else 0
                net = s + ds
                s = net + (net * Decimal("0.15")).quantize(Decimal("0.01"), rounding="ROUND_HALF_EVEN")
            totals.add(s)
        if formed != o["formed"]:
            errs.append(f"{i}: {'every' if formed else 'not every'} legal combination values every line, the engine says "
                        f"formed={o['formed']}")
        elif formed and totals != {D(t) for t in o["totals"]}:
            errs.append(f"{i}: legal combinations give totals {sorted(map(str, totals))[:6]}, the engine "
                        f"{o['totals'][:6]}")
    return errs


def fact_values(o: dict, g, ref) -> dict:
    """The totals an outcome reports are those of its export values (EXPORT-E, Q9-7): the well class, the line's ground
    as the outcome states them - except a well class the engine reports open across the well's invoices (Cl.4
    conflict), which stays free in the join."""
    xf = o.get("export_facts") or {}
    dims = {d for k in g.r.alternatives for d in dims_of(k)}
    out = {}
    if "class" in dims and xf.get("class") is not None and "class" not in o.get("open_readings", {}):
        out["class"] = xf["class"]
    if "ground" in dims and (xf.get("ground") or {}).get(ref) is not None:
        out["ground"] = xf["ground"][ref]
    return out


# ============================================================================== Z7
def z7(out: dict) -> list[str]:
    errs = []
    for i, o in out.items():
        wrong, right = o["wrong_under"], o["right_under"]
        c = D(o["confidence"])
        if o["flagged"] == 0 and wrong:
            errs.append(f"{i}: not flagged although wrong under {wrong[:2]}")
        if o["flagged"] == 1 and not wrong and not (o.get("fact_dependent") and c <= Decimal("0.30")):
            errs.append(f"{i}: flagged although correct under every scenario")
        if wrong and right and c != Decimal("0.50"):
            errs.append(f"{i}: scenarios disagree but confidence is {c}, not 0.50")
        if wrong and not right and len(o["totals"]) > 1 and c > Decimal("0.60"):
            errs.append(f"{i}: totals disagree across scenarios but confidence is {c}")
        if (o["fact_dependent"] or o["nomination_dependent"]) and c > Decimal("0.80"):
            errs.append(f"{i}: rests on an unsupplied fact but confidence is {c}")
        if not o["formed"] and c > Decimal("0.30"):
            errs.append(f"{i}: total not formed but confidence is {c}")
        ne = [x for x in o.get("not_established", []) if not x.startswith("input_unresolved")]
        if ne and o["formed"] and c > (Decimal("0.50") if not o["flagged"] else Decimal("0.80")):
            errs.append(f"{i}: a check is not established ({ne[:2]}) but confidence is {c}")
        if c not in {Decimal(x) for x in ("0.95", "0.80", "0.60", "0.50", "0.30")}:
            errs.append(f"{i}: confidence {c} is not a rubric value")
    return errs


# ============================================================================== Z8
def z8(decisions: dict, effects: dict, questions: dict) -> list[str]:
    errs = []
    keys = set(effects)
    for d in decisions["decisions"]:
        k = d.get("effect_key")
        if k not in keys:
            errs.append(f"{d['id']}: effect {k} not computed")
        if d["id"] in ("Q7C",) and d.get("decided_by") != "policy":
            errs.append(f"{d['id']}: the policy decision is not recorded as policy")
    for k in ("Q6", "Q6-day0", "Q8-class", "PD210-nomination", "Q1", "Q2", "Q12", "Q9"):
        if k not in keys:
            errs.append(f"decision effect {k} not computed")
    if "q9_rule" not in decisions:
        errs.append("no Q9 rule")
    for q in questions["questions"]:
        if q.get("blocks") == "G5":
            errs.append(f"{q['id']}: still blocks G5")
        if q.get("originally_blocks") in ("G5", "G7") or q["id"] in ("Q1", "Q4", "Q5", "Q6", "Q7", "Q8", "Q11", "Q12", "Q14"):
            if not q.get("g5_disposition"):
                errs.append(f"{q['id']}: G5 item without a g5_disposition")
    q7 = next(q for q in questions["questions"] if q["id"] == "Q7")
    if "policy" not in str(q7.get("decided_by", "")):
        errs.append("Q7: the policy decision is not recorded as policy")
    return errs


# ============================================================================== Z9
def z9(committed: dict, fresh: dict, g34_before: str | None = None, g34_after: str | None = None) -> list[str]:
    errs = [f"verification/g5/{k} does not reproduce from the current code and inputs" for k, v in fresh.items()
            if committed.get(k) != v]
    if g34_before is not None and g34_before != g34_after:
        errs.append("G3 results or G4 state changed while G5 ran")
    return errs


def g34_digest(res, st) -> str:
    import hashlib
    h = hashlib.sha256()
    for c in ("CW", "DDS"):
        for k in sorted(res[c]):
            h.update(json.dumps(res[c][k].to_json(), sort_keys=True, default=str).encode())
        for k in sorted(st[c].lines):
            g = st[c].lines[k]
            h.update(json.dumps(g.to_json(), sort_keys=True, default=str).encode())
    return h.hexdigest()


def fresh_outputs(w, st, eng, out, ids) -> dict:
    for o in out.values():
        o["coverage"] = g5_run.coverage(eng.inv[o["contract"]][o["invoice_id"]])
    return {"outcomes.jsonl": "".join(json.dumps(g5_run.serial(out[i]), sort_keys=True) + "\n"
                                      for i in ids + sorted(set(out) - set(ids)) if i in out),
            "summary.json": json.dumps(g5_run.summary(w, eng, out, ids), indent=1, default=str) + "\n",
            "audit_results.csv": g5_run.results_csv(g5_run.result_rows(out, ids)),
            "decision_effects.json": json.dumps(g5_run.decision_effects(w, st, out), indent=1, default=str) + "\n"}


def main() -> int:
    w, res, st = g4_run.run_g4()
    before = g34_digest(res, st)
    eng = Engine(w, st)
    out = eng.run()
    ids = template_ids(SNAPSHOT)
    fresh = fresh_outputs(w, st, eng, out, ids)
    after = g34_digest(res, st)
    serial = {i: g5_run.serial(o) for i, o in out.items()}
    headers = {h.values.get("application_no") or h.values.get("invoice_no"): h.values
               for k in ("cw_headers", "dds_headers") for h in w.claims.rows[k]}
    perturbed = {i: g5_run.serial(o) for i, o in Engine(perturb_billing(w), st).run().items()}
    facts_changed = {i: g5_run.serial(o) for i, o in Engine(perturb_facts(w, st), st).run().items()}
    w_rb, rebilled = rebill_admissible(w, eng, out)
    # the re-billed claims pass through G3 and G4 again: their rate checks read the displayed rate (G4-B04)
    from audit import g3_run as _g3, g4_cw as _g4c, g4_dds as _g4d
    _w, res_rb = _g3.run_all(w_rb)
    e_rb = Engine(w_rb, {"CW": _g4c.run(w_rb, res_rb["CW"]), "DDS": _g4d.run(w_rb, res_rb["DDS"])})
    del res_rb
    rebilled_out = {i: e_rb.outcome(e_rb.inv[out[i]["contract"]][i]) for i in rebilled}
    cmp_ = SC.compare(serial)
    disp = (yaml.safe_load((OUT / "sample_dispositions.yaml").read_text()) or {}).get("dispositions", {})
    load = lambda p: yaml.safe_load(p.read_text())  # noqa: E731
    committed = {k: (OUT / k).read_text() if (OUT / k).exists() else None for k in fresh}
    checks = [
        ("Z1 one outcome per template invoice; audit_results.csv in the template format", lambda: z1(serial, ids, headers, fresh["audit_results.csv"])),
        ("Z2 every invoice has a status for each of the twelve checks over exactly its claim lines", lambda: z2(serial, eng, w)),
        ("Z3 findings and evidence trail: flag <-> findings, category = root categories; payment fields reconciled to "
         "G4's accounts and recipients", lambda: z3(serial) + payment_errors(out, eng) + ds900_errors(out, eng) + attribution_errors(out, eng)),
        ("Z4 monetary and procedural outcomes distinct; billing never authority for an expected total", lambda: z4(serial, perturbed, facts_changed) + export_errors(out, eng) + unformed_branch_errors()
         + export_e_errors(out, rebilled_out, rebilled)),
        ("Z5 independently reviewed invoices agree (or carry a live settlement); inputs before outputs (git)",
         lambda: z5(cmp_, disp) + ([] if json.loads((OUT / "sample_comparison.json").read_text()) ==
                                   json.loads(json.dumps(cmp_, sort_keys=True)) else ["verification/g5/sample_comparison.json does not reproduce"])),
        ("Z6 every expected total reconciles step by step with its lines (independent rational arithmetic); the legal "
         "combinations joined line by line give exactly the engine's formed totals", lambda: z6(serial) + joint_errors(out, eng)),
        ("Z7 ambiguity rule and confidence rubric", lambda: z7(serial)),
        ("Z8 decisions and registers: effects computed, no question blocks G5, policy decisions recorded",
         lambda: z8(load(DECISIONS), json.loads(fresh["decision_effects.json"]), load(QUESTIONS))),
        ("Z9 committed G5 outputs reproduce; G3/G4 untouched by G5", lambda: z9(committed, fresh, before, after)),
    ]
    ok = True
    s = json.loads(fresh["summary.json"])
    print(f"G5 population: {len(out)} outcomes for {len(ids)} template ids; flagged CW {s['contracts']['CW']['flagged']} / "
          f"DDS {s['contracts']['DDS']['flagged']}; run context {w.run_context['id']}")
    for name, fn in checks:
        e = fn()
        print(("PASS " if not e else "FAIL ") + name)
        for x in e[:20]:
            print("     -", x)
        if len(e) > 20:
            print(f"     ... {len(e) - 20} more")
        ok = ok and not e
    print("G5 VERIFY OK" if ok else "G5 VERIFY FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
