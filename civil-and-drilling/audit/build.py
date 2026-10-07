"""G2 build: load claims and records, identify record events, link claims to evidence, summarise coverage.

Writes (deterministic, committed): verification/g2/coverage.json, unresolved.json, conflicts.json, run_context.json.
Every derived fact carries the run context id (audit.provenance) in its ctx field.
With --dump also writes build/evidence.jsonl (every parsed record, event and link; gitignored).

Usage::  python -m audit.build [--dump] [--quiet]
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import re
import sys
from collections import Counter
from decimal import Decimal

from . import claims, events, links, provenance, records_cw, records_dds
from .common import ROOT, Queue, SNAPSHOT

OUT = ROOT / "verification" / "g2"


@dataclasses.dataclass
class World:
    claims: claims.Claims
    cw: dict
    ddr: dict
    ddr_by_file: dict
    runs: dict
    wells: dict
    cw_links: dict
    dds_links: dict
    queue: Queue
    run_context: dict | None = None


def build(snapshot=SNAPSHOT) -> World:
    q = Queue()
    c = claims.load(snapshot, q)
    cw, _ = records_cw.load(snapshot, q)
    ddr, by_file, _ = records_dds.load(snapshot, q)
    runs, wells = events.identify(ddr, q)
    w = World(c, cw, ddr, by_file, runs, wells, links.link_cw(c.rows["cw_lines"], cw),
              links.link_dds(c.rows["dds_lines"], c.rows["dds_headers"], ddr), q, provenance.run_context())
    provenance.stamp(w, w.run_context["id"])          # every derived fact references the run context
    return w


def _j(o):
    if isinstance(o, (dt.date, Decimal)):
        return str(o)
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)
    if isinstance(o, (set, tuple)):
        return sorted(o)
    raise TypeError(type(o))


def special_word_scan(w: World) -> dict:
    words = records_cw.SPEC["special_condition_words"]
    counts = {x: 0 for x in words}
    for r in w.cw.values():
        text = (r.narrative or "").lower()
        for x in words:
            counts[x] += x in text
    return counts


def coverage(w: World) -> dict:
    q = w.queue
    ddrs = w.ddr.values()
    return {
        "run_context": w.run_context,
        "claims_rows": {k: len(v) for k, v in w.claims.rows.items()},
        "claims_input_assertions": w.claims.assertions,
        "cw_records_parsed": len(w.cw),
        "cw_families": dict(sorted(Counter(r.family for r in w.cw.values()).items())),
        "cw_rules": dict(sorted(Counter(str(r.rule) for r in w.cw.values()).items())),
        "cw_ground_stated_by_family": dict(sorted(Counter(r.family for r in w.cw.values() if r.ground).items())),
        "cw_jobs": dict(Counter(r.job for r in w.cw.values())),
        "cw_placeholder_signatures": sorted(r.ticket for r in w.cw.values() if not (r.foreman_signed and r.engineer_signed)),
        "cw_weekly_days_on_counts": dict(sorted(Counter(len(r.days_on) for r in w.cw.values() if r.family == "DW").items())),
        "cw_weekly_crossing_month": sorted(r.ticket for r in w.cw.values() if r.days_on and len({(d.year, d.month) for d in r.days_on + [r.week_beginning]}) > 1),
        "cw_weekly_crossing_year": sorted(r.ticket for r in w.cw.values() if r.days_on and len({d.year for d in r.days_on + [r.week_beginning]}) > 1),
        "cw_special_condition_words": special_word_scan(w),
        "ddr_files": len(w.ddr_by_file), "ddr_indexed_by_report_number": len(w.ddr),
        "ddr_part_combinations": dict(sorted(Counter("".join(sorted(d.parts)) for d in ddrs).items())),
        "ddr_statuses": dict(sorted(Counter(d.parts["A"].get("Status") for d in ddrs).items())),
        "ddr_sections": dict(sorted(Counter(d.parts["A"].get("Hole section") for d in ddrs).items())),
        "ddr_tool_terms": dict(sorted(Counter(t for d in ddrs for t in d.tools_in_hole).items())),
        "ddr_crew_terms": dict(sorted(Counter(t for d in ddrs for t in d.crew_terms).items())),
        "ddr_lost_tool_terms": dict(sorted(Counter(d.lost_tool_term for d in ddrs if d.lost_tool_term).items())),
        "ddr_placeholder_signatures": sorted(k for k, d in w.ddr.items() if not (d.company_signed and d.driller_signed)),
        "ddr_signatures_after_last_part": sum(d.signatures_after_last_part for d in ddrs),
        "runs": len(w.runs), "runs_all_days_reported": sum(r.all_days_reported for r in w.runs.values()),
        "runs_with_source_days": sum(bool(r.source_days) for r in w.runs.values()),
        "losses": sum(len(r.losses) for r in w.runs.values()),
        "loss_hours_readings": dict(sorted(Counter(
            "+".join(k for k, f in (("tool", "tool_daily_hours_through_loss_day"), ("well", "well_daily_hours_through_loss_day"),
                                    ("run", "run_daily_hours_through_loss_day")) if l["hours_on_well"] == l[f]) or "none"
            for r in w.runs.values() for l in r.losses).items())),
        "observations": {
            "source_carried_reports": sum(d.parts["B"].get("Radioactive source carried") is True for d in ddrs),
            # Keyed by Appendix G service code, never by the rig word: 'resistivity tool' is LW-411 (LWD density and
            # neutron, the radioactive-source service) and 'density-neutron' is LW-412 (LWD sonic) (App G p36).
            "source_carried_with_LW-411_in_hole": sum(d.parts["B"].get("Radioactive source carried") is True and "LW-411" in d.tools_in_hole.values() for d in ddrs),
            "source_carried_without_LW-411_in_hole": sum(d.parts["B"].get("Radioactive source carried") is True and "LW-411" not in d.tools_in_hole.values() for d in ddrs),
            "LW-411_in_hole_without_source_carried": sum(d.parts["B"].get("Radioactive source carried") is not True and "LW-411" in d.tools_in_hole.values() for d in ddrs),
            "part_D_days_without_LW-411_in_hole": sum("D" in d.parts and "LW-411" not in d.tools_in_hole.values() for d in ddrs),
            "part_D_on_run_first_day": sum("D" in d.parts and d.date == d.parts["B"].get("Run first day") for d in ddrs),
            "pressure_points_reported_days": sum((d.parts["A"].get("Pressure points") or 0) > 0 for d in ddrs),
            "run_metres_logged_equals_drilled_or_zero": sum(r.metadata.get("Metres logged") in (0, r.daily_metres_drilled) for r in w.runs.values()),
            "run_metres_reamed_equals_drilled_or_zero": sum(r.metadata.get("Metres reamed") in (0, r.daily_metres_drilled) for r in w.runs.values()),
        },
        "wells": len(w.wells), "wells_with_report_gaps": sum(bool(s.missing_days) for s in w.wells.values()),
        "cw_reference_status": dict(sorted(Counter(l.reference for l in w.cw_links.values()).items())),
        "dds_reference_status": dict(sorted(Counter(l.reference for l in w.dds_links.values()).items())),
        "cw_record_accounting": links.accounting(w.cw_links, w.cw),
        "ddr_accounting": links.accounting(w.dds_links, w.ddr),
        "cw_semantic_false_counts": {k: sum(1 for l in w.cw_links.values() if l.semantic.get(k) is False)
                                     for k in ("area_match", "date_match", "work_date_in_week", "work_date_is_day_on",
                                               "activity_includes_item", "unit_equal", "ground_match", "signed_foreman", "signed_engineer")},
        "cw_quantity_relation": dict(sorted(Counter(str(l.semantic.get("quantity_relation")) for l in w.cw_links.values() if l.semantic).items())),
        "dds_semantic_false_counts": {k: sum(1 for l in w.dds_links.values() if l.semantic.get(k) is False)
                                      for k in ("date_match", "well_match", "line_well_matches_header", "rig_matches_header", "status_match",
                                                "section_match", "signed_company", "signed_driller", "required_part_present", "tool_in_hole")},
        "dds_tool_presence_by_service": links.tool_presence_population(w.dds_links, w.claims.rows["dds_lines"]),
        "dds_tool_presence_basis": {"substitutes": {k: v["tool_code"] for k, v in links.TOOL_PRESENCE["substitutes"].items()},
                                    "no_tool": {k: v["question"] for k, v in links.TOOL_PRESENCE["no_tool"].items()},
                                    "undeclared": links.TOOL_BASIS_UNDECLARED},
        "dds_personnel_lines_with_no_crew_recorded": sum(1 for l in w.dds_links.values() if l.semantic.get("crew_recorded") == 0),
        "unresolved_total": len(q.items),
        "unresolved_by_kind_field": dict(sorted(Counter(f"{u.kind}|{u.field}" for u in q.items).items())),
        "conflicts_total": len(q.conflicts),
        "conflicts_by_check": dict(sorted(Counter(f"{c.kind}|{c.check}" for c in q.conflicts).items())),
    }


def write(w: World, dump=False) -> dict:
    cov = coverage(w)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "coverage.json").write_text(json.dumps(cov, indent=1, default=_j) + "\n")
    (OUT / "unresolved.json").write_text(json.dumps(w.queue.items, indent=1, default=_j) + "\n")
    (OUT / "conflicts.json").write_text(json.dumps(w.queue.conflicts, indent=1, default=_j) + "\n")
    (OUT / "run_context.json").write_text(json.dumps(w.run_context, indent=1) + "\n")
    if dump:
        b = ROOT / "build"
        b.mkdir(exist_ok=True)
        with (b / "evidence.jsonl").open("w") as fh:
            for kind, coll in (("cw_record", w.cw), ("ddr", w.ddr), ("run", {f"{k[0]}#{k[1]}": v for k, v in w.runs.items()}),
                               ("cw_link", w.cw_links), ("dds_link", w.dds_links)):
                for k, v in coll.items():
                    fh.write(json.dumps({"kind": kind, "id": k, "ctx": v.ctx, "data": v}, default=_j) + "\n")
    return cov


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    cov = write(build(), args.dump)
    if not args.quiet:
        print(json.dumps(cov, indent=1, default=_j))
    return 0


if __name__ == "__main__":
    sys.exit(main())
