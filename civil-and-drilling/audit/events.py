"""Identify events from the drilling reports alone (G2 "identify events"; Parts B, D, E).

A BHA run is one event per (full well, run number); its Part B metadata is repeated on every day of the run
and is kept once (repeated run totals are one fact). Differences between the copies are
conflicts. Source runs (Part D), losses (Part E) and each well's span of reports are listed as facts.
Nothing here reads invoices, allocates charges or builds cross-invoice state.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from .common import Queue

RUN_KEYS = ["Run first day", "Run last day", "Tools in run", "Run circulating hours", "Metres logged", "Metres reamed",
            "Radioactive source carried"]


@dataclass
class Run:
    well: str
    run: int
    reports: list[str] = field(default_factory=list)          # report numbers, date order
    dates: list[dt.date] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)             # one copy of Part B
    daily_circulating_hours: int = 0
    daily_metres_drilled: int = 0
    all_days_reported: bool = False
    source_days: list[dt.date] = field(default_factory=list)  # days with Part D
    losses: list[dict] = field(default_factory=list)

    ctx: str | None = None                      # run context id (audit.provenance)

@dataclass
class WellSpan:
    well: str
    first_report: dt.date
    last_report: dt.date
    reports: int
    missing_days: list[dt.date]

    ctx: str | None = None                      # run context id (audit.provenance)

def identify(ddrs: dict, q: Queue) -> tuple[dict[tuple[str, int], Run], dict[str, WellSpan]]:
    runs: dict[tuple[str, int], Run] = {}
    by_well = defaultdict(list)
    for rid, d in sorted(ddrs.items(), key=lambda kv: (kv[1].well or "", kv[1].date or dt.date.min)):
        by_well[d.well].append(d.date)
        b = d.parts.get("B")
        if not b or not isinstance(b.get("Run"), int):
            continue
        key = (d.well, b["Run"])
        r = runs.setdefault(key, Run(well=d.well, run=b["Run"]))
        meta = {k: b.get(k) for k in RUN_KEYS}
        if not r.metadata:
            r.metadata = meta
        else:
            for k in RUN_KEYS:
                if meta[k] != r.metadata[k]:
                    q.conflict("run", f"{d.well}#{b['Run']}", f"part_B_{k}_differs", f"{rid}: {meta[k]} vs {r.metadata[k]}")
        r.reports.append(rid)
        r.dates.append(d.date)
        a = d.parts.get("A", {})
        if isinstance(a.get("Circulating hours"), int):
            r.daily_circulating_hours += a["Circulating hours"]
        if isinstance(a.get("Depth end (m MD)"), int) and isinstance(a.get("Depth start (m MD)"), int):
            r.daily_metres_drilled += a["Depth end (m MD)"] - a["Depth start (m MD)"]
        if "D" in d.parts:
            r.source_days.append(d.date)
        if "E" in d.parts:
            e = d.parts["E"]
            r.losses.append({"report": rid, "date": d.date, "tool_term": d.lost_tool_term, "code": d.lost_tool_code,
                             "hours_on_well": e.get("Circulating hours accumulated on the well")})
    for key, r in runs.items():
        f, l = r.metadata.get("Run first day"), r.metadata.get("Run last day")
        if isinstance(f, dt.date) and isinstance(l, dt.date):
            span = {f + dt.timedelta(days=i) for i in range((l - f).days + 1)}
            r.all_days_reported = span == set(r.dates)
            if r.all_days_reported and r.metadata.get("Run circulating hours") != r.daily_circulating_hours:
                q.conflict("run", f"{r.well}#{r.run}", "run_hours_vs_daily_sum",
                           f"{r.metadata.get('Run circulating hours')} vs {r.daily_circulating_hours}")
            if not r.all_days_reported:
                q.conflict("run", f"{r.well}#{r.run}", "run_days_not_all_reported", f"{len(r.dates)} of {len(span)}")
        if r.source_days and r.metadata.get("Radioactive source carried") is False:
            q.conflict("run", f"{r.well}#{r.run}", "source_handled_on_run_without_source", "")
    # Loss corroboration (loss history: use Part E's hours AND corroborating history). Cl.31 (p7) takes the
    # hours "the tool accumulated on the well, as stated on the Lost in Hole Report" (Part E); P12 (p11): "those the
    # tool accumulated on the well on which it was lost, including the day of the loss". The corroborating history is
    # therefore the LOST TOOL's own days in the hole on that well (not the whole-well sum).
    # The whole-well and run-to-date sums are kept as facts.
    daily = defaultdict(list)
    for d in ddrs.values():
        a = d.parts.get("A", {})
        if isinstance(a.get("Circulating hours"), int):
            daily[d.well].append((d.date, (d.parts.get("B") or {}).get("Run"), a["Circulating hours"], set(d.tools_in_hole)))
    for r in runs.values():
        for loss in r.losses:
            well_sum = sum(h for day, _run, h, _t in daily[r.well] if day <= loss["date"])
            run_sum = sum(h for day, run, h, _t in daily[r.well] if day <= loss["date"] and run == r.run)
            tool_sum = sum(h for day, _run, h, terms in daily[r.well] if day <= loss["date"] and loss["tool_term"] in terms)
            loss["well_daily_hours_through_loss_day"] = well_sum
            loss["run_daily_hours_through_loss_day"] = run_sum
            loss["tool_daily_hours_through_loss_day"] = tool_sum
            loss["run_continues_after_loss"] = bool(isinstance(r.metadata.get("Run last day"), dt.date) and r.metadata["Run last day"] > loss["date"])
            if loss["hours_on_well"] != tool_sum:
                q.conflict("loss", loss["report"], "part_E_hours_vs_tool_daily_sum",
                           f"Part E {loss['hours_on_well']} vs the lost tool's daily sum {tool_sum} (well {well_sum}, run-to-date {run_sum})")
    spans = {}
    for well, dates in by_well.items():
        ds = sorted(x for x in dates if x)
        full = {ds[0] + dt.timedelta(days=i) for i in range((ds[-1] - ds[0]).days + 1)}
        spans[well] = WellSpan(well, ds[0], ds[-1], len(ds), sorted(full - set(ds)))
    return runs, spans
