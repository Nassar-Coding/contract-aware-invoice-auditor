"""G4 population run: the G3 line results of both contracts through the G4 state engines (diagnostic, not a
classification).

Writes verification/g4/:
  summary.json          per contract: G3 -> G4 amount status, state checks by family/status/finding, lines G4 changed
                        (by code), coverage of every G3 'g4_dependency' by a G4 state check, run context id
  decision_scopes.json  for every G4 decision and every question G4 keeps open: the lines, groups and amounts it decides
  ledgers/*.json        the shared state itself: civil band ledgers per item, Contract Year and reading; duplicate,
                        exclusion and once-only groups; daily-limit cuts; footage accumulators; the A3 difference account
                        with its recipient per reading; P23 links; retention per application and the 45A release
  changed_lines.jsonl   every line whose value G4 changed: G3 value, G4 value or alternatives, state checks, trace
Nothing here flags an invoice, totals an invoice or writes the audit results (G5).

Usage::  python -m audit.g4_run
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal

from . import g4_cw, g4_dds
from .g4_core import base
from .common import ROOT
from .g3_run import run_all

OUT = ROOT / "verification" / "g4"
DEP_FAMILY = {"daily_limit": "daily_limit", "band_state": "band", "exclusion": "exclusion", "p23_link": "p23",
              "once_per_run": "run_event", "once_per_well": "well_event"}


def run_g4(w=None, res=None):
    if res is None:
        w, res = run_all(w)
    return w, res, {"CW": g4_cw.run(w, res["CW"]), "DDS": g4_dds.run(w, res["DDS"])}


def _s(x):
    return None if x is None else str(x)


def _dep(d: str) -> str:
    return d.split(" ")[0].split(":")[0]


def coverage(st, contract: str) -> dict:
    """Every G3 line dependency on G4 state is answered by a G4 check of that family (or, for A3, by the account)."""
    a3 = {ref for a in st.adjustments for ref in a["by_line"]}
    out = defaultdict(lambda: {"lines": 0, "answered": 0, "unanswered": []})
    for g in st.lines.values():
        fams = {s.family for s in g.state}
        for d in {_dep(x) for x in g.g3.g4_dependencies}:
            e = out[d]
            e["lines"] += 1
            ok = (g.g3.line_ref in a3) if d == "a3_adjustment" else (DEP_FAMILY.get(d) in fams)
            if ok:
                e["answered"] += 1
            elif len(e["unanswered"]) < 20:
                e["unanswered"].append(g.g3.line_ref)
    return dict(sorted(out.items()))


def summary(w, st: dict) -> dict:
    out = {"run_context": w.run_context["id"], "contracts": {}}
    for c, s in st.items():
        status = Counter()
        checks = Counter()
        changed = Counter()
        for g in s.lines.values():
            status[f"{g.g3.amount_status} -> {g.r.amount_status}"] += 1
            for x in g.state:
                checks[f"{x.family} | {x.status} | {x.finding or '-'}"] += 1
            if g.changed:
                changed[g.g3.code] += 1
        out["contracts"][c] = {"lines": len(s.lines), "amount_status_g3_to_g4": dict(sorted(status.items())),
                               "state_checks": dict(sorted(checks.items())), "changed_lines": sum(changed.values()),
                               "changed_by_code": dict(sorted(changed.items())), "g4_dependency_coverage": coverage(s, c)}
    return out


def ledgers(st: dict) -> dict:
    cw, dds = st["CW"], st["DDS"]
    limits = {c: [{"line": g.g3.line_ref, "ledger": x.ledger, "detail": x.detail} for g in s.lines.values() for x in g.state
                  if x.family == "daily_limit" and x.status == "finding"] for c, s in st.items()}
    return {
        "cw_bands.json": cw.ledgers,
        "cw_duplicates.json": cw.duplicates,
        "cw_exclusions.json": cw.exclusions,
        "daily_limits.json": limits,
        "dds_once_groups.json": dds.groups,
        "dds_footage.json": dds.footage,
        "adjustments.json": {"CW": cw.adjustments, "DDS": dds.adjustments},
        "p23.json": cw.p23,
        "retention.json": cw.retention,
    }


def _alts(r):
    return {k: {"allowed_quantity": _s(v.get("allowed_quantity")), "amount": _s(v.get("amount")),
                **({"breaches": v["breaches"]} if v.get("breaches") else {})} for k, v in r.alternatives.items()}


def changed_lines(st: dict):
    for c, s in st.items():
        for k in sorted(s.lines):
            g = s.lines[k]
            if not g.changed:
                continue
            yield {"contract": c, "key": k, "line_ref": g.g3.line_ref, "code": g.g3.code,
                   "g3": {"payable": g.g3.payable, "allowed_quantity": _s(g.g3.allowed_quantity), "amount": _s(g.g3.amount),
                          "amount_status": g.g3.amount_status, "alternatives": _alts(g.g3)},
                   "g4": {"payable": g.r.payable, "allowed_quantity": _s(g.r.allowed_quantity), "amount": _s(g.r.amount),
                          "amount_status": g.r.amount_status, "alternatives": _alts(g.r), "conditions": g.r.conditions},
                   "state": [x.__dict__ for x in g.state], "trace": g.r.trace,
                   "alternative_traces": {a: v.get("trace") for a, v in g.r.alternatives.items()}}


def _lines_with(st, pred) -> list[str]:
    return sorted(g.g3.line_ref for g in st.lines.values() if pred(g))


def _dim_lines(st, dim) -> list[str]:
    return _lines_with(st, lambda g: any(dim == base(p.split(":", 1)[0]) for k in g.r.alternatives for p in k.split("|")))


def _amount_under(st, dim) -> dict:
    """Per value of an open dimension: the sum over the lines carrying it of the amount under that value (the other
    dimensions of each line at their min..max). A diagnostic of what the choice moves, not a total of any document."""
    out = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for g in st.lines.values():
        vals = defaultdict(list)
        for k, v in g.r.alternatives.items():
            dd = dict(p.split(":", 1) for p in k.split("|"))
            if dim in dd and v.get("amount") is not None:
                vals[dd[dim]].append(Decimal(str(v["amount"])))
        if len(vals) > 1:
            for val, xs in vals.items():
                out[val][0] += min(xs)
                out[val][1] += max(xs)
    return {k: {"min": str(a), "max": str(b)} for k, (a, b) in sorted(out.items())}


def decision_scopes(st: dict) -> dict:
    cw, dds = st["CW"], st["DDS"]
    finding = lambda s, fam, f: _lines_with(s, lambda g: any(x.family == fam and x.finding == f and x.status == "finding"  # noqa: E731
                                                              for x in g.state))
    unresolved = lambda s, fam, f: _lines_with(s, lambda g: any(x.family == fam and x.finding == f and x.status == "unresolved"  # noqa: E731
                                                                 for x in g.state))
    a3 = {c: {"total": a["total"], "recipient": a["recipient"], "eligible_lines": a["eligible_lines"]}
          for c, s in st.items() for a in s.adjustments}
    return {
        "G4-D1_order": {"lines_with_order_alternatives": _dim_lines(cw, "order"), "amount_by_order": _amount_under(cw, "order")},
        "G4-D2_contract_year": {"cw_lines_with_Q12": len(_dim_lines(cw, "Q12")), "cw_amount_by_Q12": _amount_under(cw, "Q12"),
                                "dds_lines_with_Q14": len(_dim_lines(dds, "Q14"))},
        "G4-D3_weekly_duplicates": {"groups": [d for d in cw.duplicates if "," in d["group"]],
                                    "lines_disallowed": finding(cw, "duplicate", "duplicate_measurement")},
        "G4-D4_exclusion_window": {"excluded": finding(cw, "exclusion", "excluded_by_other_item"),
                                   "open_Q6_day0": _dim_lines(cw, "Q6-day0")},
        "G4-D5_later_measurement": {"groups": len(cw.duplicates), "same_day_ties": [d for d in cw.duplicates if len(d["earlier"]) > 1]},
        "G4-D6_daily_limits": {"cw_lines_cut": finding(cw, "daily_limit", "above_daily_limit"),
                               "dds_lines_cut": finding(dds, "daily_limit", "above_daily_limit")},
        "G4-D7_band_quantity_basis_Q6": {"lines_with_Q6": _dim_lines(cw, "Q6"), "amount_by_Q6": _amount_under(cw, "Q6")},
        "G4-D8_A3": a3,
        "G4-D9_P23": {"links": len(cw.p23), "posted": sum(1 for x in cw.p23 if x.get("status") != "not posted")},
        "G4-D10_retention": {"applications": len(cw.retention["per_application"]), "release": {
            k: v for k, v in (cw.retention.get("release") or {}).items() if k != "not_established"}},
        "G4-D11_once_only": {"groups": len([g for g in dds.groups if g["group"].startswith("DDS-ONCE")]),
                             "stands_open": [g for g in dds.groups if len(g.get("candidates", [])) > 1],
                             "not_on_its_day": finding(dds, "well_event", "well_event_not_on_its_day"),
                             "lwd_not_run": finding(dds, "well_event", "lwd_not_run")},
        "G4-D12_HC630_per_run": {"runs_with_more_than_one_charge": [g for g in dds.groups if g["group"].startswith("DDS-HC630")]},
        "G4-D13_footage": {"lines_repriced": _lines_with(dds, lambda g: any(x.family == "footage" and x.status != "pass"
                                                                               for x in g.state)),
                           "max_metres_before_any_line": str(max((Decimal(v[0]) for f in dds.footage.values() for v in f.values()),
                                                                 default=Decimal(0))),
                           "segments_with_open_upper_bound": sum(1 for f in dds.footage.values() for v in f.values()
                                                                 if v[1] is None)},
        "G4-D14_pd210_overlaps": {"groups_with_overlap": [g for g in dds.groups if g.get("overlaps")]},
        "G4-D15_loss_once": {"loss_lines": len(_lines_with(dds, lambda g: any(x.family == "loss_event" for x in g.state))),
                             "repeated": unresolved(dds, "loss_event", "loss_repeated") + finding(dds, "loss_event", "loss_repeated")},
        "Q7C_stands": {"lines": _dim_lines(dds, "stands"), "pd210_allocation_lines": _dim_lines(dds, "alloc")},
    }


def write() -> dict:
    w, _res, st = run_g4()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ledgers").mkdir(exist_ok=True)
    s = summary(w, st)
    (OUT / "summary.json").write_text(json.dumps(s, indent=1, default=str) + "\n")
    (OUT / "decision_scopes.json").write_text(json.dumps(decision_scopes(st), indent=1, default=str) + "\n")
    for name, obj in ledgers(st).items():
        (OUT / "ledgers" / name).write_text(json.dumps(obj, indent=1, default=str) + "\n")
    with (OUT / "changed_lines.jsonl").open("w") as fh:
        for x in changed_lines(st):
            fh.write(json.dumps(x, default=str) + "\n")
    return s


def main() -> int:
    s = write()
    for c, v in s["contracts"].items():
        print(c, v["lines"], "changed", v["changed_lines"], {k: f"{e['answered']}/{e['lines']}" for k, e in v["g4_dependency_coverage"].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
