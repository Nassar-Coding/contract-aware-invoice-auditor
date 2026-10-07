"""G4 exit checks: chronology and shared state.

Exit condition: "Small multi-invoice histories agree with independent expected events and amounts; ordering, reset,
allocation and replay tests pass; no duplicate event or adjustment is counted twice." Checks:

  Y1 histories: every line, adjustment and retention figure of every history agrees with each isolated reader, or the
     disagreement carries a settlement (verification/g4/history_dispositions.yaml) that is still live; the committed
     comparison reproduces; the packets were committed before the readers' outputs and both before the G4 engines (git).
  Y2 ordering: the claim rows of both contracts in reverse order, and in a seeded shuffle, give identical G4 values,
     alternatives and state checks (no incidental row order decides a state outcome); the civil band ledgers count in
     execution-date order (never application or submission order), and every order of a same-date group that changes
     a division is carried.
  Y3 resets: every civil ledger starts at zero in its Contract Year (CY2 under Q12 A begins on 2026-01-05, Q12 B has no
     CY2); every entry's count before it is the sum of the counts before it in the ledger; every drilling footage
     accumulator starts at zero per well and Contract Year (Q14 A: 1 January 2026) and adds each charged interval once.
  Y4 allocation: every duplicate / once-only group leaves exactly one charge standing under every alternative, never a
     charge off its contractual day; every PD-210 well-day carries the complete joint allocation domain over its interval
     coverage (one scenario per assignment of each contested segment to one covering charge) and under every scenario
     the kept metres sum to the union of the day's intervals, each charge within its own interval (G4-B02); a daily limit binds the quantity that stands; the A3 difference of every protected
     line is posted on exactly one document per reading (a same-day tie stays open, never posted twice); the 45A
     release is posted once.
  Y5 replay: a second run gives identical results (determinism); a correction to an early measurement (the first line
     of a band ledger removed) replays every later entry of that ledger and nothing else, and the replayed ledger
     equals an independent recount; on the histories, removing a document and rerunning gives the same result as a
     packet built without it.
  Y6 exactly once: no measurement, charge, adjustment, deduction or release is counted twice: at most one standing
     measurement per civil item/area/date or week under every alternative; each band line once per ledger; each A3
     line once in its account and its own value left at the protected figure; no P23 deduction posted beside a line
     already excluded (Q3 A); retention released once.
  Y7 traces: every value G4 changed (and every alternative) replays with independent arithmetic
     (tools/verify_g3.replay), ends at the line's quantity and amount and cites a source on every step.
  Y8 boundary and reproduction: G3 results are unchanged by G4 (G4 works on copies); every G3 'g4_dependency' is
     answered by a G4 state check; the committed verification/g4 outputs reproduce; the G4 modules contain no G5+
     construct (classification, flag, invoice total, submission).
  Y9 registers: every entry of spec/g4_decisions.yaml has a basis with pages, histories that exist, and a computed
     scope; every question G4 owned (originally_blocks G4) is decided or carries a g4_disposition and a later owner;
     no question or carried item still blocks G4.

Usage::  python tools/verify_g4.py
"""
from __future__ import annotations

import copy
import json
import random
import re
import subprocess
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import g4_histories as H  # noqa: E402
import g4_history_compare as HC  # noqa: E402
import verify_g3 as vg3  # noqa: E402
from audit import g3_run, g4_cw, g4_dds, g4_run  # noqa: E402
from audit.g4_core import base, dims_of, local_dim  # noqa: E402

OUT = ROOT / "verification" / "g4"
DECISIONS = ROOT / "spec" / "g4_decisions.yaml"
QUESTIONS = ROOT / "spec" / "open_questions.yaml"
CARRIED = ROOT / "spec" / "carried_items.yaml"
G4_MODULES = ["audit/g4_core.py", "audit/g4_cw.py", "audit/g4_dds.py", "audit/g4_run.py"]
ZERO = Decimal(0)


def D(x) -> Decimal | None:
    return None if x is None else Decimal(str(x))


def git_first_commit(path: str) -> str | None:
    out = subprocess.run(["git", "log", "--diff-filter=A", "--format=%H", "--", path], cwd=ROOT, capture_output=True, text=True)
    hs = out.stdout.split()
    return hs[-1] if hs else None


def strictly_before(a: str | None, b: str | None) -> bool:
    if not a or not b or a == b:
        return False
    return subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=ROOT).returncode == 0


# ============================================================================== Y1 histories
def y1(result: dict, dispositions: dict, first_commit=git_first_commit, before=strictly_before) -> list[str]:
    errs = []
    live = set()
    for hid, e in result.items():
        if not e["readers"]:
            errs.append(f"{hid}: no independent expected result")
        for r, diffs in e["readers"].items():
            for d in diffs:
                key = f"{hid}|{r}|{d}"
                live.add(key)
                if key not in dispositions:
                    errs.append(f"{key}: disagrees with the independent expected value and has no settlement")
                elif dispositions[key].get("settled") not in ("engine", "both", "presentation"):
                    errs.append(f"{key}: settlement '{dispositions[key].get('settled')}' is not a closed settlement")
    errs += [f"{k}: settlement no longer matches a disagreement (stale)" for k in dispositions if k not in live]
    packets = [f"verification/g4/histories/{p}" for p in ("packet_cw.jsonl", "packet_dds.jsonl")]
    readers = [f"verification/g4/histories/expected_{r}_{p}.jsonl" for c in HC.READERS.values() for r, p in c]
    engines = ["audit/g4_cw.py", "audit/g4_dds.py"]
    for p in packets:
        for x in readers + engines:
            if not before(first_commit(p), first_commit(x)):
                errs.append(f"{p} was not committed before {x}")
    for x in readers:
        for e in engines:
            if not before(first_commit(x), first_commit(e)):
                errs.append(f"{x} was not committed before {e}")
    return errs


def comparison_reproduces(result: dict) -> list[str]:
    p = OUT / "history_comparison.json"
    committed = json.loads(p.read_text()) if p.exists() else None
    return [] if committed == json.loads(json.dumps(result, sort_keys=True)) else ["verification/g4/history_comparison.json does not reproduce"]


# ============================================================================== helpers
def snapshot_state(st: dict) -> dict:
    """{contract: {key: comparable G4 value}} - values, alternatives, conditions' dimensions and state checks."""
    out = {}
    for c, s in st.items():
        out[c] = {}
        for k, g in s.lines.items():
            r = g.r
            out[c][k] = (r.payable, str(r.allowed_quantity), str(r.amount), r.amount_status,
                         tuple(sorted((a, str(v.get("allowed_quantity")), str(v.get("amount"))) for a, v in r.alternatives.items())),
                         tuple(sorted(x["dimension"] for x in r.conditions)),
                         tuple(sorted((x.family, x.status, x.finding or "", x.ledger or "") for x in g.state)))
    return out


def world_reordered(w, order: str, seed: int = 4):
    """A copy of the world whose claim rows (lines and headers of both contracts) are in another order; keys are kept
    (a result's key is its line_ref or its source position, never its row index)."""
    w2 = copy.copy(w)
    w2.claims = copy.copy(w.claims)
    w2.claims.rows = dict(w.claims.rows)
    for k in ("cw_lines", "cw_headers", "dds_lines", "dds_headers"):
        rows = list(w.claims.rows[k])
        if order == "reverse":
            rows.reverse()
        else:
            random.Random(seed).shuffle(rows)
        w2.claims.rows[k] = rows
    return w2


def run_state(w, res, order: str | None = None, a3_rerun=True) -> dict:
    w2 = world_reordered(w, order) if order else w
    return {"CW": g4_cw.run(w2, res["CW"], a3_rerun=a3_rerun), "DDS": g4_dds.run(w2, res["DDS"], a3_rerun=a3_rerun)}


def _diff_states(a: dict, b: dict, label: str, limit=20) -> list[str]:
    errs = []
    for c in a:
        for k, v in a[c].items():
            if b[c].get(k) != v:
                errs.append(f"{c} {k}: G4 result differs when the claim rows are in {label} order")
                if len(errs) >= limit:
                    return errs
    return errs


# ============================================================================== Y2 ordering
def y2(w, res, forward: dict, runner=run_state) -> list[str]:
    base = snapshot_state(forward)
    errs = []
    for order in ("reverse", "shuffle"):
        errs += _diff_states(base, snapshot_state(runner(w, res, order, a3_rerun=False)), order)
    errs += ledger_order_errors(forward["CW"])
    errs += chronology_errors(forward["CW"], forward["DDS"])
    return errs


def chronology_errors(cw, dds) -> list[str]:
    """G4-B03, independent of the engine's ordering: a measurement without a work date is listed as a possible contributor
    in every band ledger of its item; a document whose submission date is not established is a possible recipient of
    every posting (A3 under each reading, 45A release) - the recipient is then 'not established', never a single document
    chosen by omitting it; a charge whose date is not established is never an established 'not on its day' finding."""
    errs = []
    T = cw_terms()
    undated_band = defaultdict(set)
    for g in cw.lines.values():
        c = _claim(g)
        if c.get("item_code") in T.banded and not c.get("work_date") and g.g3.payable is not False:
            undated_band[c["item_code"]].add(g.g3.line_ref)
    for lk in cw.ledgers:
        item = lk.split("|")[0]
        listed = set((cw.undated.get(lk) or {}).get("lines", []))
        if listed != undated_band.get(item, set()):
            errs.append(f"band ledger {lk}: undated measurements {sorted(undated_band.get(item, set()))} listed as {sorted(listed)}")
    for item, refs in undated_band.items():
        if not any(lk.startswith(item + "|") for lk in cw.ledgers):
            errs.append(f"{item}: undated measurements {sorted(refs)} in no band ledger")
    heads = {"CW": {}, "DDS": {}}
    for st, c, no_f, d_f in ((cw, "CW", "application_no", "application_date"), (dds, "DDS", "invoice_no", "invoice_date")):
        for g in st.lines.values():
            cl = _claim(g)
            if cl.get(no_f):
                heads[c].setdefault(cl[no_f], set())
        for no, h in (_HEADS.get(c) or {}).items():
            heads[c][no] = bool(h.get(d_f))
        undated = sorted(no for no, dated in heads[c].items() if dated is False)
        if not undated:
            continue
        for a in st.adjustments:
            for reading, rec in a["recipient"].items():
                if reading.startswith("Q1:C"):
                    continue
                if not (isinstance(rec, dict) and set(undated) <= set(rec.get("not_established", []))):
                    errs.append(f"{c} {a['instrument']} {reading}: recipient {rec} although {undated} have no submission date")
        rel = (getattr(st, "retention", None) or {}).get("release") if c == "CW" else None
        if rel is not None and not (isinstance(rel["recipient"], dict) and set(undated) <= set(rel["recipient"].get("not_established", []))):
            errs.append(f"45A release recipient {rel['recipient']} although {undated} have no submission date")
        if c == "CW" and rel is None and undated and cw.retention.get("per_application"):
            errs.append(f"45A release omitted although {undated} may have been submitted after completion")
    for g in dds.lines.values():
        if not _claim(g).get("service_date") and any(x.finding == "well_event_not_on_its_day" and x.status == "finding"
                                                    for x in g.state):
            errs.append(f"{g.g3.line_ref}: 'not on its day' established although its date is not established")
    return errs


def ledger_order_errors(cw) -> list[str]:
    errs = []
    for lk, entries in cw.ledgers.items():
        dates = [e["date"] for e in entries]
        if dates != sorted(dates):
            errs.append(f"band ledger {lk}: not counted in execution-date order")
    # a same-date group at an edge whose order changes a division carries every order
    for g in cw.lines.values():
        by_group = defaultdict(set)
        for k in g.r.alternatives:
            for d, x in dims_of(k).items():
                if base(d) == "order":
                    by_group[d].add(x)
        if any(len(v) < 2 for v in by_group.values()):
            errs.append(f"{g.g3.line_ref}: one order carried where the order changes the division")
    return errs


# ============================================================================== Y3 resets
def y3(cw, dds, T=None) -> list[str]:
    errs = []
    keys = defaultdict(set)
    for lk, entries in cw.ledgers.items():
        item, cy, q12, q6 = lk.split("|")
        keys[(item, q12, q6)].add(cy)
        # G4-B03: measurements without a work date are possible earlier contributors: the ledger starts at [0, their
        # count], never at [0, 0] with them omitted (chronology_errors checks that none is omitted)
        start_hi = D((cw.undated.get(lk) or {}).get("high", "0"))
        if entries and entries[0]["before"] != ["0", str(start_hi)]:
            errs.append(f"band ledger {lk}: does not start at zero (or at the undated measurements' range {start_hi})")
        run_lo, run_hi = ZERO, start_hi
        for e in entries:
            if [D(e["before"][0]), D(e["before"][1])] != [run_lo, run_hi]:
                errs.append(f"band ledger {lk}: {e['line']} starts at {e['before']}, the ledger before it sums to {run_lo}..{run_hi}")
                break
            run_lo += D(e["count"][0])
            run_hi += D(e["count"][1])
        if cy == "CY2" and entries and entries[0]["date"] < "2026-01-05":
            errs.append(f"band ledger {lk}: CY2 holds a measurement before 5 January 2026")
        if cy == "CY1" and q12 == "Q12:A" and entries and entries[-1]["date"] >= "2026-01-05":
            errs.append(f"band ledger {lk}: CY1 under Q12 A holds a measurement on or after 5 January 2026")
    for (item, q12, q6), cys in keys.items():
        if q12 == "Q12:B" and cys != {"CY1"}:
            errs.append(f"{item} {q12} {q6}: a new Contract Year under the no-reset reading")
    errs += footage_reset_errors(dds)
    return errs


def _pd210_raw(dds) -> dict:
    """{well: [(date, from, to, ref)]} of the admissible PD-210 charges with a date and depths, from the claims."""
    out = defaultdict(list)
    for g in dds.lines.values():
        c = _claim(g)
        if g.g3.code != "PD-210" or g.g3.payable is False:
            continue
        f, t, d = D(c.get("depth_from_m")), D(c.get("depth_to_m")), c.get("service_date")
        if d and f is not None and t is not None and t > f:
            out[c.get("well_name")].append((d, f, t, g.g3.line_ref))
    return out


def _union(intervals) -> list[tuple]:
    """Merge [(from, to)] into disjoint covering intervals (independent of the engine's segmentation)."""
    out = []
    for f, t in sorted(intervals):
        if out and f <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], t))
        else:
            out.append((f, t))
    return out


def footage_reset_errors(dds) -> list[str]:
    """Independent recount of the Q11 A accumulator over UNIQUE metres (G4-B01): per well and Contract Year, the metres
    already drilled before each unique segment = the length of the union of the admissible PD-210 intervals of earlier
    days plus the union below the segment on its own day. The engine's segments of a day must partition that day's union
    exactly (a metre counted once, however many charges state it), and every position must equal the recount."""
    errs = []
    T = dds_terms()
    raw = _pd210_raw(dds)
    for well, acc in dds.footage.items():
        by_day = defaultdict(list)
        for d, f, t, _r in raw.get(well, []):
            by_day[d].append((f, t))
        segs = defaultdict(list)
        for k, v in acc.items():
            date, ab, q14, q11 = k.split("|")
            a, b = (D(x) for x in ab.split("-"))
            segs[(date, q14, q11)].append((a, b, D(v[0]), None if v[1] is None else D(v[1])))
        for d, ivs in by_day.items():
            union_len = sum((t - f for f, t in _union(ivs)), ZERO)
            for q14 in ("Q14:A", "Q14:B"):
                got = segs.get((str(d), q14, "Q11:A"), [])
                if g4_dds._cy(d, q14[-1], T) is None:
                    continue
                if sum((b - a for a, b, _lo, _hi in got), ZERO) != union_len:
                    errs.append(f"footage {well} {d} {q14}: segments sum to {sum((b - a for a, b, _l, _h in got), ZERO)} m, "
                                f"the day's unique metres are {union_len} m")
                for a, b, lo, _hi in got:
                    cy = g4_dds._cy(d, q14[-1], T)
                    expect = sum((sum((t - f for f, t in _union(v2)), ZERO) for d2, v2 in by_day.items()
                                  if d2 < d and g4_dds._cy(d2, q14[-1], T) == cy), ZERO)
                    expect += sum((min(t, a) - f for f, t in _union(ivs) if f < a), ZERO)
                    if lo != expect:
                        errs.append(f"footage {well} {d} {a}-{b} {q14}: starts at {lo}, the unique-metre recount gives {expect}")
                        break
    return errs


def pd210_coverage_errors(dds) -> list[str]:
    """G4-B02 oracle: for every PD-210 well-day whose intervals overlap, every allocation the engine carries charges the
    day's unique metres exactly once (sum of kept metres = union length, each charge at most its interval), and the
    number of allocations equals the independent count of ways to give every contested elementary segment to one of the
    charges covering it."""
    errs = []
    lines = {g.g3.line_ref: g for g in dds.lines.values()}
    raw = _pd210_raw(dds)
    for well, items in raw.items():
        days = defaultdict(list)
        for d, f, t, ref in items:
            days[d].append((f, t, ref))
        for d, ivs in days.items():
            pts = sorted({x for f, t, _r in ivs for x in (f, t)})
            expect_n, contested = 1, False
            for a, b in zip(pts, pts[1:]):
                k = sum(1 for f, t, _r in ivs if f <= a and b <= t)
                if k > 1:
                    contested = True
                    expect_n *= k
            if not contested:
                continue
            union_len = sum((t - f for f, t in _union([(f, t) for f, t, _r in ivs])), ZERO)
            dim = g4_dds.alloc_dim(well, d)
            kept = defaultdict(lambda: defaultdict(set))
            unresolved = False
            for f, t, ref in ivs:
                g = lines[ref]
                if g.r.payable is None:
                    unresolved = True
                    continue
                for k, v in g.r.alternatives.items():
                    dd = dims_of(k)
                    if dim in dd:
                        kept[dd[dim]][ref].add(D(v.get("allowed_quantity")))
            if unresolved and not kept:
                continue                      # honest bounds (unresolved), not a finite domain presented as complete
            if len(kept) != expect_n:
                errs.append(f"PD-210 {well} {d}: {len(kept)} allocations carried, the joint domain has {expect_n}")
            for val, per in kept.items():
                tot = ZERO
                for f, t, ref in ivs:
                    qs = per.get(ref)
                    if not qs or len(qs) != 1:
                        errs.append(f"PD-210 {well} {d} [{val}]: {ref} has no single kept quantity")
                        continue
                    q = next(iter(qs))
                    if q > t - f:
                        errs.append(f"PD-210 {well} {d} [{val}]: {ref} keeps {q} m of a {t - f} m interval")
                    tot += q
                if tot != union_len:
                    errs.append(f"PD-210 {well} {d} [{val}]: {tot} m charged for {union_len} unique metres")
    return errs


# ============================================================================== Y4 allocation
def y4(cw, dds) -> list[str]:
    errs = []
    lines_cw = {g.g3.line_ref: g for g in cw.lines.values()}
    for d in cw.duplicates:
        errs += _one_stands([lines_cw[m] for m in d["members"] if m in lines_cw], d["group"], local_dim("earlier", d["group"]))
    lines_dds = {g.g3.line_ref: g for g in dds.lines.values()}
    for grp in dds.groups:
        ms = [lines_dds[m] for m in grp["members"] if m in lines_dds]
        if grp["group"].startswith("DDS-PD210"):
            continue                          # metre coverage, not one charge per group (pd210_coverage_errors)
        for ref in (grp.get("not_on_day") or {}):
            if lines_dds[ref].r.payable is not False:
                errs.append(f"{grp['group']}: {ref} is charged off its contractual day and still stands")
        dim = local_dim("stands-run" if grp["group"].startswith("DDS-HC630") else "stands", grp["group"])
        errs += _one_stands([g for g in ms if g.g3.line_ref in grp.get("candidates", grp["members"])], grp["group"], dim,
                            reading=("Q5-HC630", "per BHA run (Sch 8)") if grp["group"].startswith("DDS-HC630") else None)
    errs += pd210_coverage_errors(dds)
    errs += limit_errors(cw, dds)
    errs += a3_posting_errors(cw) + a3_posting_errors(dds)
    rel = cw.retention.get("release")
    if rel and isinstance(rel.get("recipient"), list):
        errs.append("45A release posted on more than one application")
    return errs


def _nonzero(v) -> bool:
    return v is not None and D(v) != 0


def _one_stands(ms: list, gid: str, dim: str, reading: tuple | None = None) -> list[str]:
    """Under every alternative of the group exactly one member keeps a value (a member G3 did not value is not counted:
    under the alternative in which it stands, no valued member does). `dim` is the group's own dimension; a kind without
    '@' checks every group of that kind the members carry, each on its own (G5-B04: local dimensions)."""
    if len(ms) < 2:
        return []
    if "@" not in dim:
        own = sorted({d for g in ms for k in g.r.alternatives for d in dims_of(k) if base(d) == dim})
        if own:
            return [e for d in own for e in _one_stands([g for g in ms if any(d in dims_of(k) for k in g.r.alternatives)
                                                        or not g.r.alternatives], gid, d, reading)]
    labels = {dims_of(k).get(dim) for g in ms for k in g.r.alternatives if dim in dims_of(k)}
    valued = [g for g in ms if g.g3.payable is not False]
    if not labels:
        standing = [g for g in valued if g.r.payable is not False and (_nonzero(g.r.amount) or g.r.alternatives)]
        if len(standing) > 1:
            return [f"{gid}: {len(standing)} charges stand ({', '.join(g.g3.line_ref for g in standing)})"]
        return []
    errs = []
    for lab in labels:
        standing = []
        for g in valued:
            if not g.r.alternatives:
                if g.r.payable is not False and _nonzero(g.r.amount):
                    standing.append(g.g3.line_ref)
                continue
            vals = [v for k, v in g.r.alternatives.items() if dims_of(k).get(dim) == lab
                    and (reading is None or dims_of(k).get(reading[0]) == reading[1])]
            if any(_nonzero(v.get("amount")) or _nonzero(v.get("allowed_quantity")) for v in vals):
                standing.append(g.g3.line_ref)
        unvalued = {g.g3.line_ref for g in ms if g.g3.payable is None}
        if len(standing) != 1 and not (not standing and lab in unvalued):
            errs.append(f"{gid}: under {dim}:{lab} {len(standing)} charges stand ({', '.join(standing)})")
    return errs


def limit_errors(cw, dds) -> list[str]:
    errs = []
    T = cw_terms()
    for g in list(cw.lines.values()):
        lim = T.daily_limits.get(g.g3.code)
        if lim is None:
            continue
        for q in _quantities(g):
            if q is not None and q > lim:
                errs.append(f"{g.g3.line_ref}: {q} above the daily limitation {lim}")
    Td = dds_terms()
    for g in dds.lines.values():
        lim = Td.daily_limits.get(g.g3.code)
        if lim is None:
            continue
        for q in _quantities(g):
            if q is not None and q > lim:
                errs.append(f"{g.g3.line_ref}: {q} above the daily limit {lim}")
    return errs


def _quantities(g) -> list:
    if g.r.payable is False:
        return []
    if g.r.alternatives:
        return [D(v.get("allowed_quantity")) for v in g.r.alternatives.values()]
    return [D(g.r.allowed_quantity)]


_T = {}


def cw_terms():
    if "CW" not in _T:
        from audit import terms
        T = terms.cw()
        g4_cw._terms_extras(T)
        _T["CW"] = T
    return _T["CW"]


def dds_terms():
    if "DDS" not in _T:
        from audit import terms
        T = terms.dds()
        g4_dds._limits_table(T)
        _T["DDS"] = T
    return _T["DDS"]


def a3_posting_errors(st) -> list[str]:
    errs = []
    for a in st.adjustments:
        for reading, rec in a["recipient"].items():
            if isinstance(rec, list):
                errs.append(f"{a['instrument']} {reading}: posted on several documents {rec}")
            if isinstance(rec, dict) and "tie" in rec and len(rec["tie"]) < 2:
                errs.append(f"{a['instrument']} {reading}: a 'tie' of one document")
            if isinstance(rec, dict) and "not_established" in rec:
                if not rec.get("undated"):
                    errs.append(f"{a['instrument']} {reading}: recipient 'not established' with no undated document")
                continue
            if isinstance(rec, dict) and "tie" not in rec:
                for well, r in rec.items():
                    if isinstance(r, dict) and "not_established" in r:
                        continue
                    if isinstance(r, list):
                        errs.append(f"{a['instrument']} {reading} {well}: posted on several documents")
    return errs


# ============================================================================== Y5 replay
def y5(w, res, forward: dict, runner=run_state) -> list[str]:
    errs = _diff_states(snapshot_state(forward), snapshot_state(runner(w, res, None, a3_rerun=False)), "a repeated run's")
    errs += correction_replay_errors(w, res)
    errs += history_removal_errors()
    return errs


def correction_replay_errors(w, res) -> list[str]:
    """Remove the first measurement of the longest civil band ledger (Q12 A, Q6 A) and rerun: every later entry of that
    ledger moves down by exactly its count; entries of every other ledger are unchanged."""
    base = g4_cw.run(w, res["CW"], a3_rerun=False)
    lk = max((k for k in base.ledgers if k.endswith("Q12:A|Q6:A")), key=lambda k: len(base.ledgers[k]))
    first = base.ledgers[lk][0]
    w2 = copy.copy(w)
    w2.claims = copy.copy(w.claims)
    w2.claims.rows = dict(w.claims.rows)
    keep = [r for r in w.claims.rows["cw_lines"] if r.values.get("line_ref") != first["line"]]
    w2.claims.rows["cw_lines"] = keep
    res2 = {k: v for k, v in res["CW"].items() if k != first["line"]}
    after = g4_cw.run(w2, res2, a3_rerun=False)
    errs = []
    cnt = D(first["count"][0])
    old = {e["line"]: e for e in base.ledgers[lk][1:]}
    new = {e["line"]: e for e in after.ledgers[lk]}
    if set(old) != set(new):
        errs.append(f"replay {lk}: the ledger lost or gained lines other than the one removed")
    for ref, e in old.items():
        if ref in new and D(new[ref]["before"][0]) != D(e["before"][0]) - cnt:
            errs.append(f"replay {lk}: {ref} not replayed (before {new[ref]['before'][0]}, expected {D(e['before'][0]) - cnt})")
            break
    for k, entries in base.ledgers.items():
        if k.split("|")[0] != lk.split("|")[0] and after.ledgers.get(k) != entries:
            errs.append(f"replay: ledger {k} changed although it does not depend on the removed measurement")
            break
    return errs


def history_removal_errors() -> list[str]:
    """For each multi-document history: dropping its last-submitted document and rerunning equals running a packet
    built without it (the state is a pure function of the history, never of an earlier run)."""
    errs = []
    packets = H.load_packets()
    for hid in ("CW-H01", "CW-H08", "DDS-H01", "DDS-H03"):
        h = packets[hid]
        key = "application_date" if hid.startswith("CW") else "invoice_date"
        docs = sorted(h["documents"], key=lambda d: str(d["header"].get(key)))
        h2 = {**h, "documents": docs[:-1]}
        _w, _g3, st_a = HC.run_history(h2)
        _w, _g3, st_b = HC.run_history(json.loads(json.dumps(h2)))
        a, b = snapshot_state({"X": st_a}), snapshot_state({"X": st_b})
        if a != b:
            errs.append(f"{hid}: rerun without its last document is not reproducible")
    return errs


# ============================================================================== Y6 exactly once
def y6(cw, dds, without_a3: dict | None = None) -> list[str]:
    errs = []
    # one standing measurement per civil item/area/date under every alternative (Cl.44 applied before anything counts)
    by_key = defaultdict(list)
    for g in cw.lines.values():
        if g.r.payable is False:
            continue
        v = g.g3
        by_key[(v.code, _area(g), _date(g))].append(g)
    for k, gs in by_key.items():
        if None in k or len(gs) < 2:
            continue
        errs += _one_stands(gs, f"CW {k}", "earlier")
    # each band line once per ledger; counts sum
    for lk, entries in cw.ledgers.items():
        refs = [e["line"] for e in entries]
        if len(refs) != len(set(refs)):
            errs.append(f"band ledger {lk}: a line is counted twice")
    # each instrument has one account, each A3 line is in it once, and no line's own value carries the difference (the
    # state with the A3 account must equal the state without it, line by line)
    for st in (cw, dds):
        ids = [a["instrument"] for a in st.adjustments]
        if len(ids) != len(set(ids)):
            errs.append(f"an A3 account is posted more than once ({ids})")
    if without_a3 is not None:
        errs += [e.replace("in with the A3 account order", "differs when the A3 account is computed: the difference is "
                                                          "embedded in the line")
                 for e in _diff_states(without_a3, snapshot_state({"CW": cw, "DDS": dds}), "with the A3 account")]
    # P23 never beside an exclusion (Q3 A)
    posted = [x for x in cw.p23 if x.get("status") != "not posted"]
    for x in posted:
        g = next((g for g in cw.lines.values() if g.g3.line_ref == x["line"]), None)
        if g is not None and g.r.payable is False:
            errs.append(f"P23: {x['line']} deducted from {x.get('next_valuation')} and also excluded from its own valuation")
    # DDS: every metre once in the footage accumulator - no two segments of a well-day overlap (G4-B01)
    for well, acc in dds.footage.items():
        seen = defaultdict(list)
        for k in acc:
            date, ab, q14, q11 = k.split("|")
            a, b = (D(x) for x in ab.split("-"))
            seen[(date, q14, q11)].append((a, b))
        for key, segs in seen.items():
            segs.sort()
            if any(b1 > a2 for (_a1, b1), (a2, _b2) in zip(segs, segs[1:])):
                errs.append(f"footage {well} {key}: a metre counted twice")
    return errs


def _area(g):
    """The work area of a civil line: its site code (as audit/g4_cw.Line.area)."""
    return (_claim(g).get("site") or "").split(" ")[0] or None


def _date(g):
    return _claim(g).get("work_date")


_CLAIMS = {}
_HEADS = {}           # contract -> {document number: header values} (claims), for the chronology oracle


def load_heads(w) -> None:
    for c, hk, no_f in (("CW", "cw_headers", "application_no"), ("DDS", "dds_headers", "invoice_no")):
        _HEADS[c] = {r.values.get(no_f): r.values for r in w.claims.rows[hk] if r.values.get(no_f)}


def _claim(g) -> dict:
    return _CLAIMS.get(g.key, {})


# ============================================================================== Y7 traces
def y7(cw, dds) -> list[str]:
    errs = []
    for c, st in (("CW", cw), ("DDS", dds)):
        for g in st.lines.values():
            if not g.changed or g.r.payable is False:
                continue
            opts = ({k: v for k, v in g.r.alternatives.items()} if g.r.alternatives else
                    {None: {"trace": g.r.trace, "amount": g.r.amount, "allowed_quantity": g.r.allowed_quantity}})
            for k, v in opts.items():
                if v.get("amount") is None or D(v["amount"]) == 0 and D(v.get("allowed_quantity") or 0) == 0:
                    continue
                e, out = vg3.replay(v.get("trace") or [])
                if e:
                    errs.append(f"{c} {g.g3.line_ref} [{k}]: {e[0]}")
                elif out["amount"] is None or out["amount"] != D(v["amount"]):
                    errs.append(f"{c} {g.g3.line_ref} [{k}]: trace ends at {out['amount']}, the value is {v['amount']}")
                elif out["quantity"] is not None and v.get("allowed_quantity") is not None and out["quantity"] != D(v["allowed_quantity"]):
                    errs.append(f"{c} {g.g3.line_ref} [{k}]: trace quantity {out['quantity']}, the value {v['allowed_quantity']}")
    errs += deferred_rate_errors(cw)
    return errs


def deferred_rate_errors(cw) -> list[str]:
    """G4-B04, from the traces and G3's band rates (not the engine's verdicts): a band line whose displayed-rate check G3
    deferred and whose value G4 established must carry the completed check - a pass exactly where, under every scenario,
    the displayed rate is the one band's rate (one part in the trace) or one of the item's band rates (a divided
    measurement), a finding where under none, never left unresolved or missing. A correct amount does not decide it."""
    errs = []
    for g in cw.lines.values():
        rate3 = next((x for x in g.g3.checks if x.check == "rate"), None)
        if rate3 is None or rate3.status != "unresolved" or g.r.payable is not True:
            continue
        band_alts = [(dims_of(k), D(a["unit_rate"])) for k, a in g.g3.alternatives.items()
                     if "band" in dims_of(k) and a.get("unit_rate")]
        if not band_alts:
            continue
        ra = D(_claim(g).get("rate_applied"))
        opts = g.r.alternatives or {None: {"trace": g.r.trace, "amount": g.r.amount, "allowed_quantity": g.r.allowed_quantity}}
        verdicts = []
        for k, v in opts.items():
            parts = [D(s["rate"]) for s in (v.get("trace") or []) if s.get("op") == "part"]
            if not parts or (v.get("amount") is not None and D(v["amount"]) == 0 and D(v.get("allowed_quantity") or 0) == 0):
                continue
            kd = dims_of(k)
            # the item's band rates under this scenario's other inputs (its ground, ...), from G3
            band_rates = {rt for d, rt in band_alts if all(kd.get(x, y) == y for x, y in d.items() if x != "band")}
            verdicts.append(ra == parts[0] if len(parts) == 1 else ra in band_rates)
        if not verdicts or ra is None:
            continue
        want = "pass" if all(verdicts) else "finding" if not any(verdicts) else "unresolved"
        got = [x.status for x in g.state if x.family == "band_rate"]
        if got != [want]:
            errs.append(f"CW {g.g3.line_ref}: displayed rate {ra} under the band state: expected {want}, the state has {got}")
    return errs


# ============================================================================== Y8 boundary and reproduction
FORBIDDEN = vg3.FORBIDDEN


def y8(w, res, st: dict, g3_before: dict, committed: dict | None = None) -> list[str]:
    errs = []
    for c, rs in res.items():
        for k, r in rs.items():
            if json.dumps(r.to_json(), sort_keys=True, default=str) != g3_before[c][k]:
                errs.append(f"{c} {k}: the G3 result was modified by G4")
                break
    for c, s in st.items():
        for dep, e in g4_run.coverage(s, c).items():
            if e["answered"] != e["lines"]:
                errs.append(f"{c} {dep}: {e['lines'] - e['answered']} lines without a G4 state check (e.g. {e['unanswered'][:3]})")
    fresh = {"summary.json": json.dumps(g4_run.summary(w, st), indent=1, default=str) + "\n",
             "decision_scopes.json": json.dumps(g4_run.decision_scopes(st), indent=1, default=str) + "\n"}
    fresh.update({f"ledgers/{k}": json.dumps(v, indent=1, default=str) + "\n" for k, v in g4_run.ledgers(st).items()})
    fresh["changed_lines.jsonl"] = "".join(json.dumps(x, default=str) + "\n" for x in g4_run.changed_lines(st))
    committed = committed if committed is not None else {k: (OUT / k).read_text() if (OUT / k).exists() else None for k in fresh}
    for k, v in fresh.items():
        if committed.get(k) != v:
            errs.append(f"verification/g4/{k} does not reproduce from the current code and inputs")
    errs += text_errors()
    return errs


def text_errors(paths=None) -> list[str]:
    errs = []
    for p in paths or [ROOT / m for m in G4_MODULES]:
        t = p.read_text().lower()
        errs += [f"{p.name}: '{x}' (G5+ construct)" for x in FORBIDDEN if x in t]
    return errs


# ============================================================================== Y9 registers
def y9(decisions: dict, scopes: dict, questions: dict, carried: dict, packets: dict) -> list[str]:
    errs = []
    for d in decisions["decisions"]:
        if not d.get("basis") or not any(re.search(r"\(pp?\d", b) for b in d["basis"]):
            errs.append(f"{d['id']}: basis without pages")
        if not d.get("histories") or any(h not in packets for h in d["histories"]):
            errs.append(f"{d['id']}: no history, or a history that does not exist")
        if d.get("scope_key") not in scopes:
            errs.append(f"{d['id']}: scope {d.get('scope_key')} not computed")
        if d.get("status") not in ("decided", "decided in part", "open"):
            errs.append(f"{d['id']}: status {d.get('status')}")
    qs = {q["id"]: q for q in questions["questions"]}
    for q in qs.values():
        if q.get("blocks") == "G4":
            errs.append(f"{q['id']}: still blocks G4")
        if q.get("originally_blocks") == "G4":
            if q["status"] != "decided" and not q.get("g4_disposition"):
                errs.append(f"{q['id']}: G4 question without a G4 disposition")
            if q["status"] != "decided" and not str(q.get("blocks", "")).startswith(("G5", "G6", "G7")):
                errs.append(f"{q['id']}: kept open without a later owner")
    for d in decisions["decisions"]:
        for q in d.get("questions", []):
            if q not in qs:
                errs.append(f"{d['id']}: question {q} not in the register")
    for it in carried.get("items", []):
        if it.get("owner_gate") == "G4" and not it.get("g4_treatment"):
            errs.append(f"{it['id']}: carried item owned by G4 without a G4 treatment")
    return errs


# ============================================================================== main
def main() -> int:
    w, res = g3_run.run_all()
    for c, lk in (("CW", "cw_lines"), ("DDS", "dds_lines")):
        from audit.g3_core import result_keys
        for k, row in zip(result_keys(w.claims.rows[lk]), w.claims.rows[lk]):
            _CLAIMS[k] = row.values
    load_heads(w)
    g3_before = {c: {k: json.dumps(r.to_json(), sort_keys=True, default=str) for k, r in rs.items()} for c, rs in res.items()}
    st = {"CW": g4_cw.run(w, res["CW"]), "DDS": g4_dds.run(w, res["DDS"])}
    cmp_ = HC.compare()
    disp = (yaml.safe_load((OUT / "history_dispositions.yaml").read_text()) or {}).get("dispositions", {})
    load = lambda p: yaml.safe_load(p.read_text())  # noqa: E731
    checks = [
        ("Y1 small multi-invoice histories agree with the independent expected events and amounts (or carry a live settlement); "
         "packets before readers before engines (git)", lambda: y1(cmp_, disp) + comparison_reproduces(cmp_)),
        ("Y2 ordering: reverse and shuffled claim rows give identical state; execution-date ledgers; every relevant same-date order carried",
         lambda: y2(w, res, st)),
        ("Y3 resets: every band ledger and footage accumulator starts at zero in its Contract Year and sums without gaps",
         lambda: y3(st["CW"], st["DDS"])),
        ("Y4 allocation: one charge stands per group under every alternative; PD-210 metres allocated once over the complete joint domain; limits bind; A3 and 45A posted on one document",
         lambda: y4(st["CW"], st["DDS"])),
        ("Y5 replay: deterministic rerun; a corrected early measurement replays every later ledger entry and nothing else",
         lambda: y5(w, res, st)),
        ("Y6 exactly once: no measurement, charge, adjustment, deduction or release counted twice",
         lambda: y6(st["CW"], st["DDS"], snapshot_state(run_state(w, res, None, a3_rerun=False)))),
        ("Y7 every value G4 changed replays from its trace with independent arithmetic", lambda: y7(st["CW"], st["DDS"])),
        ("Y8 G3 results untouched; every G3 dependency answered; committed G4 outputs reproduce; no G5+ construct",
         lambda: y8(w, res, st, g3_before)),
        ("Y9 decisions and registers: basis, histories, scopes; no question or carried item still blocks G4",
         lambda: y9(load(DECISIONS), json.loads((OUT / "decision_scopes.json").read_text()), load(QUESTIONS), load(CARRIED),
                    H.load_packets())),
    ]
    ok = True
    print(f"G4 population: CW {len(st['CW'].lines)} lines, DDS {len(st['DDS'].lines)} lines; {len(cmp_)} histories; "
          f"run context {w.run_context['id']}")
    for name, fn in checks:
        e = fn()
        print(("PASS " if not e else "FAIL ") + name)
        for x in e[:20]:
            print("     -", x)
        if len(e) > 20:
            print(f"     ... {len(e) - 20} more")
        ok = ok and not e
    print("G4 VERIFY OK" if ok else "G4 VERIFY FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
