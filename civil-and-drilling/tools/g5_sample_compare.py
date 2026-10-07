"""G5 independent-outcome sample: compare the engine's outcome of every sampled invoice with each isolated reader's
(verification/g5/samples/expected_*.jsonl) - the flag and the expected judged total.

The readers never saw the implementation; the implementation never reads the readers' files except here. Every
disagreement carries a settlement in verification/g5/sample_dispositions.yaml, settled against the scan: 'engine' (the
reader's figure is not what the contract says, page cited), 'reader' (an engine defect, fixed), 'both' (a reading the
register leaves open; the engine applies the ambiguity rule Q9-2). --check fails on an unsettled or stale settlement and
when the committed comparison does not reproduce.

Usage::  python tools/g5_sample_compare.py [--check]      (reads verification/g5/outcomes.jsonl)
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "verification" / "g5" / "samples"
OUT = ROOT / "verification" / "g5" / "sample_comparison.json"
DISP = ROOT / "verification" / "g5" / "sample_dispositions.yaml"


def D(x):
    return None if x in (None, "") else Decimal(str(x))


def load_outcomes(path: Path | None = None) -> dict:
    out = {}
    for ln in (path or ROOT / "verification" / "g5" / "outcomes.jsonl").read_text().splitlines():
        o = json.loads(ln)
        out[o["invoice_id"]] = o
    return out


def readers() -> dict:
    res = {}
    for p in sorted(SAMPLES.glob("expected_*.jsonl")):
        r = p.stem.replace("expected_", "")
        for ln in p.read_text().splitlines():
            x = json.loads(ln)
            res.setdefault(x["id"], {})[r] = x
    return res


def compare(outcomes: dict) -> dict:
    result = {}
    for iid, rs in sorted(readers().items()):
        o = outcomes.get(iid)
        entry = {"engine": None if o is None else {"flagged": o["flagged"], "expected_total": o["expected_total"],
                                                  "confidence": o["confidence"], "error_category": o["error_category"]},
                 "readers": {}}
        for r, x in sorted(rs.items()):
            diffs = []
            if o is None:
                diffs.append("no engine outcome")
            else:
                if x.get("flagged") != o["flagged"]:
                    diffs.append(f"flag: engine {o['flagged']} reader {x.get('flagged')}")
                if D(x.get("expected_total")) != D(o["expected_total"]):
                    diffs.append(f"expected total: engine {o['expected_total']} reader {x.get('expected_total')}")
            entry["readers"][r] = diffs
        result[iid] = entry
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    res = compare(load_outcomes())
    disp = (yaml.safe_load(DISP.read_text()) or {}).get("dispositions", {}) if DISP.exists() else {}
    live = {f"{i}|{r}|{d}" for i, e in res.items() for r, ds in e["readers"].items() for d in ds}
    open_ = sorted(k for k in live if k not in disp)
    stale = sorted(k for k in disp if k not in live)
    n = sum(len(e["readers"]) for e in res.values())
    agree = sum(1 for e in res.values() for ds in e["readers"].values() if not ds)
    print(f"{len(res)} invoices, {n} reader results: {agree} agree on flag and expected total; {len(live)} disagreements, "
          f"{len(live) - len(open_)} settled, {len(open_)} open, {len(stale)} stale")
    for k in open_:
        print("OPEN ", k)
    for k in stale:
        print("STALE", k)
    if a.check:
        same = OUT.exists() and json.loads(OUT.read_text()) == json.loads(json.dumps(res, sort_keys=True))
        if not same:
            print("verification/g5/sample_comparison.json does not reproduce")
        return 0 if not open_ and not stale and same else 1
    OUT.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
