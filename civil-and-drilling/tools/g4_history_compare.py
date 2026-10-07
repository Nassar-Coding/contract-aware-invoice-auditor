"""G4 multi-invoice histories: run each committed packet through the production pipeline (G2 build of a snapshot holding
only that history, G3 line engine, G4 state engine) and compare, history by history and line by line, with the expected
results the isolated readers computed from the contract scans (verification/g4/histories/expected_r*.jsonl).

The readers never saw the implementation; the implementation never reads the readers' files except here, to compare.
Every disagreement must carry a disposition in verification/g4/history_dispositions.yaml, settled against the scan:
'engine' (the engine is right; the reader's value is wrong, with the page), 'reader' (a defect: fixed in the engine),
'both' (the contract leaves it open; the engine carries it as an alternative) or 'presentation' (same money, different
form). --check fails on any disagreement without a disposition, and on a disposition that no longer matches a
disagreement (stale), so a changed engine value cannot hide behind an old settlement.

Usage::  python tools/g4_history_compare.py [--check] [--only H]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import yaml  # noqa: E402

import g4_histories as H  # noqa: E402
from audit import build, g3_cw, g3_dds, g4_cw, g4_dds  # noqa: E402

HIST = ROOT / "verification" / "g4" / "histories"
OUT = ROOT / "verification" / "g4" / "history_comparison.json"
DISP = ROOT / "verification" / "g4" / "history_dispositions.yaml"
READERS = {"CW": [("r1", "cw_a"), ("r2", "cw_a"), ("r3", "cw_b"), ("r4", "cw_b")], "DDS": [("r5", "dds"), ("r6", "dds")]}
STATE_CODES = {"band_divided", "above_daily_limit", "excluded_by_other_item", "duplicate_measurement", "charged_twice",
               "run_event_repeated", "well_event_not_on_its_day", "well_event_repeated", "lwd_not_run", "loss_repeated",
               "footage_band_divided"}


def run_history(h: dict):
    """(world, G3 results, G4 state) for one history, from its packet only."""
    with tempfile.TemporaryDirectory() as d:
        w = build.build(snapshot=H.materialize(h, Path(d)))
    if h["id"].startswith("CW"):
        g3 = g3_cw.run(w)
        return w, g3, g4_cw.run(w, g3)
    g3 = g3_dds.run(w)
    return w, g3, g4_dds.run(w, g3)


def _d(x):
    if x is None or x == "":
        return None
    try:
        return Decimal(str(x).replace(",", ""))
    except Exception:
        return None


def engine_view(st) -> dict:
    lines = {}
    for g in st.lines.values():
        r = g.r
        alts = {k: {"allowed_quantity": _d(v.get("allowed_quantity")), "amount": _d(v.get("amount"))} for k, v in r.alternatives.items()}
        lines[r.line_ref] = {"payable": r.payable, "allowed_quantity": _d(r.allowed_quantity), "amount": _d(r.amount),
                             "alternatives": alts, "state": sorted(g.state_findings), "open": sorted(g.state_unresolved),
                             "local": sorted(set(g.g3.findings)), "status": r.amount_status}
    return lines


def amounts(v: dict) -> set:
    """The set of admissible amounts of a line (its single amount, or every alternative's)."""
    if v.get("alternatives"):
        return {_d(a.get("amount")) for a in v["alternatives"].values()}
    return {_d(v.get("amount"))}


def compare_line(e: dict, x: dict) -> list[str]:
    """Amounts: the set of admissible amounts must be equal (a reader who was told not to value a line - null amount, no
    alternatives - is compared on payability and quantity only). State findings: every finding the engine establishes must
    be the reader's, and every reader finding must be established or left open by the engine."""
    out = []
    ea, xa = amounts(e), amounts(x)
    not_valued = x.get("amount") is None and not x.get("alternatives")
    if not_valued:
        if x.get("payable") is not None and e["payable"] is not None and bool(x["payable"]) != bool(e["payable"]):
            out.append(f"payable: engine {e['payable']} reader {x['payable']}")
        if x.get("allowed_quantity") is not None and _d(x["allowed_quantity"]) != e["allowed_quantity"]:
            out.append(f"allowed quantity: engine {e['allowed_quantity']} reader {x['allowed_quantity']}")
    elif ea != xa:
        out.append(f"amount: engine {sorted(map(str, ea))} reader {sorted(map(str, xa))}")
    if not not_valued and e["payable"] is not None and x.get("payable") is not None and bool(e["payable"]) != bool(x["payable"]) and ea != xa:
        out.append(f"payable: engine {e['payable']} reader {x['payable']}")
    xs = set(x.get("findings") or []) & STATE_CODES
    est = {f for f in e["state"] if f in STATE_CODES}
    if not est <= xs or not xs <= est | set(e["open"]):
        out.append(f"state findings: engine {sorted(est)} (open {sorted(set(e['open']) & STATE_CODES)}) reader {sorted(xs)}")
    return out


def compare_adjustments(st, exp: dict) -> list[str]:
    out = []
    xs = exp.get("adjustments") or []
    eng = [a for a in st.adjustments if a.get("eligible_lines")]
    for a in eng:
        x = next((y for y in xs if (y.get("instrument") or "").upper().endswith(a["instrument"].split(".")[-1])), None)
        if x is None:
            out.append(f"adjustment {a['instrument']}: engine total {a['total']['by_reading']} reader none")
            continue
        et = {Decimal(v["min"]) for v in a["total"]["by_reading"].values()} | {Decimal(v["max"]) for v in a["total"]["by_reading"].values()}
        xt = {_d(x.get("difference"))} if x.get("difference") is not None else set()
        if xt and not xt <= et:
            out.append(f"adjustment total: engine {sorted(map(str, et))} reader {sorted(map(str, xt))}")
        er = {str(v) for v in a["recipient"].values() if not isinstance(v, dict)}
        er |= {z for v in a["recipient"].values() if isinstance(v, dict) and "tie" in v for z in v["tie"]}
        xr = {x["recipient"]} if x.get("recipient") else set()
        xr |= {v for v in (x.get("recipient_alternatives") or {}).values() if v and v != "none"}
        if er != xr:
            out.append(f"adjustment recipient: engine {sorted(er)} reader {sorted(xr)}")
    for x in xs:
        if not eng and (x.get("instrument") or "").upper().endswith("A3") and _d(x.get("difference")):
            out.append(f"adjustment {x['instrument']}: engine none reader {x.get('difference')}")
    return out


def _vals(v) -> set:
    """Every value a retention/release field admits: a single value, or the ends of each reading's range."""
    if v is None:
        return set()
    if isinstance(v, dict):
        return {_d(y) for r in v["by_reading"].values() for y in (r["min"], r["max"]) if y is not None}
    return {_d(v)}


def compare_retention(st, exp: dict) -> list[str]:
    ret = getattr(st, "retention", None)
    xs = exp.get("retention") or {}
    if not ret or not xs:
        return []
    out = []
    per = ret["per_application"]
    rel = ret.get("release") or {}
    for app, x in xs.items():
        e = per.get(app)
        if e is None:
            out.append(f"retention {app}: engine none")
            continue
        er = _vals(e.get("retention"))
        if x.get("retention") is not None and _d(x["retention"]) not in er:
            out.append(f"retention {app}: engine {sorted(map(str, er))} reader {x['retention']}")
        released = _vals(rel.get("released")) if rel and rel.get("recipient") == app else {Decimal(0)}
        xr = _d(x.get("released")) if x.get("released") is not None else None
        if xr is not None and xr not in released:
            out.append(f"release {app}: engine {sorted(map(str, released))} reader {x.get('released')}")
    return out


def compare() -> dict:
    packets = H.load_packets()
    expected = {}
    for c, rs in READERS.items():
        for r, part in rs:
            for ln in (HIST / f"expected_{r}_{part}.jsonl").read_text().splitlines():
                x = json.loads(ln)
                expected.setdefault(x["id"], {})[r] = x
    result = {}
    for hid, h in packets.items():
        _w, _g3, st = run_history(h)
        ev = engine_view(st)
        entry = {"engine": {k: {"amount": str(v["amount"]) if v["amount"] is not None else None,
                                "alternatives": {a: str(b["amount"]) for a, b in v["alternatives"].items()},
                                "state": v["state"], "open": v["open"], "status": v["status"]} for k, v in ev.items()},
                 "readers": {}}
        for r, x in sorted(expected.get(hid, {}).items()):
            diffs = []
            for ref, xl in x["lines"].items():
                if ref not in ev:
                    diffs.append(f"{ref}: not an engine line")
                    continue
                diffs += [f"{ref}: {d}" for d in compare_line(ev[ref], xl)]
            diffs += compare_adjustments(st, x)
            diffs += compare_retention(st, x)
            entry["readers"][r] = diffs
        result[hid] = entry
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--only")
    a = ap.parse_args()
    res = compare()
    if a.only:
        print(json.dumps(res[a.only], indent=1))
        return 0
    disp = yaml.safe_load(DISP.read_text())["dispositions"] if DISP.exists() else {}
    open_, stale, n = [], [], 0
    for hid, e in res.items():
        for r, diffs in e["readers"].items():
            for d in diffs:
                n += 1
                key = f"{hid}|{r}|{d}"
                if key not in disp:
                    open_.append(key)
    live = {f"{hid}|{r}|{d}" for hid, e in res.items() for r, diffs in e["readers"].items() for d in diffs}
    stale = sorted(k for k in disp if k not in live)
    agree = sum(1 for e in res.values() for diffs in e["readers"].values() if not diffs)
    total = sum(len(e["readers"]) for e in res.values())
    if not a.check:
        OUT.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    print(f"{len(res)} histories, {total} reader results: {agree} agree in full; {n} disagreements, "
          f"{n - len(open_)} dispositioned, {len(open_)} open, {len(stale)} stale dispositions")
    for k in open_:
        print("OPEN ", k)
    for k in stale:
        print("STALE", k)
    if a.check:
        committed = json.loads(OUT.read_text()) if OUT.exists() else None
        same = committed == json.loads(json.dumps(res, sort_keys=True))
        if not same:
            print("verification/g4/history_comparison.json does not reproduce")
        return 0 if not open_ and not stale and same else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
