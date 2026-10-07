"""G5 independent-outcome sample: inputs only (G5 exit condition: independently reviewed complete invoices reconcile
step by step).

A sample of complete invoices of both contracts, chosen from the RAW pinned data only (a seeded random draw, plus
invoices picked for raw features a reader can see without any engine: header arithmetic that does not reconcile,
submission dates outside the window, the same item/area/date or well/day/service billed twice, the documents submitted
around the A3 dates of issue and after the civil completion). Each packet gives the reader the invoice as billed, every
record or report its lines cite, and the raw context the cross-invoice checks need (other invoices' lines for the same
measurement or well-day, the well's report dates and runs, the submission dates around an A3 issue), plus the adopted
G5 decisions as stated premises (they are the register's decisions, including the Q7 C policy, not results). Written BEFORE any
G5 code; independent readers compute the expected outcome of each invoice from the
contract scans alone.

Usage::  python tools/g5_samples.py      (writes verification/g5/samples/packet_cw_*.jsonl, packet_dds_*.jsonl, meta.yaml)
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import random
import re
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from g4_histories import _mask  # noqa: E402

OUT = ROOT / "verification" / "g5" / "samples"
SEED = 5505


def snapshot() -> Path:
    from audit.common import SNAPSHOT
    return Path(SNAPSHOT)


def rows(rel: str) -> list[dict]:
    with (snapshot() / rel).open(newline="") as fh:
        return list(csv.DictReader(fh))


def ddmmmyyyy(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%d-%b-%Y").date()


PREMISES = [
    "Flag semantics (Q9-1): an invoice is wrong when any of the twelve checks fails on it - a line whose contract value "
    "differs from its billed value, a header whose arithmetic does not reconcile, an established breach with no monetary "
    "effect (identity/contract reference, term, period, submission window, missing/unsigned/mismatched evidence), or a "
    "payment-only defect the contract makes this invoice carry (the A3 adjustment omitted from its recipient, the 45A "
    "retention release omitted). flagged = 1 when wrong, 0 when every check passes.",
    "Judged total: civil application_total = the measured total (sum of line amounts; the A3 adjustment and retention are "
    "payment fields outside it, 45A p32); drilling invoice_total = net + VAT, net = charges incl. DS-900 (Cl.36-40 p8); "
    "the A3 adjustment is outside the judged total (Q2 A).",
    "Readings adopted: civil Contract Year resets on 5 January 2026 (Q12 A); drilling on its anniversary (Q14 A); a "
    "measurement unpaid for want of its record still counts as measured for bands (Q6 A); for civil duplicates (Cl.44) "
    "'the later measurement' is the one on the later-submitted application, within one application the later line; a "
    "weekly item is measured by the week (lines citing one week's log for the same item and area are one measurement); "
    "for drilling repeats (Cl.29, Cl.26, Cl.27, Cl.31) the charge on the earlier-submitted invoice stands and the later one "
    "is the repeat (owner decision), within one invoice one of the identical lines is the repeat; only what the contract "
    "requires is added (no unbilled events added, Q10 A).",
    "Readings left open (report each alternative that changes the outcome in `alternatives`): the A3 adjustment "
    "recipient ('on or after' 31A/36A vs 'after' A3, same-day ties); civil exclusion on the same day as A.14.010 (P19 vs "
    "Cl.32); the hour readings of 21A and Schedule 8 (DD-120/RM-530/HC-630); the order of civil measurements of one date "
    "at a band edge.",
    "Facts assigned to documents not supplied (Q9-3): the drilling well class (call-off, Cl.4), the civil ground class of "
    "a line no supplied record classifies (S4, Cl.5), the PD-210 section nomination (Cl.23). Compute the value under the "
    "input the invoice itself states (header well_class; the line's ground_class; the PD-210 charge states the section "
    "was nominated) and say in `note` what would change under another input. The invoice's statement is tested, not "
    "assumed: an invoice priced inconsistently with its own statement is wrong.",
    "Accepted as stated (G3-D3): the civil zone of execution and night working stated on the line. Procedural breaches "
    "(submission window, period, contract reference) are findings that do not remove a line's value (G3-D1). An unsigned "
    "Daily Drilling Report makes the Schedule 5 services it evidences not payable; other lines keep their value (G3-D2). "
    "A charge whose required record or Schedule 5 part is not delivered is not payable in the invoice that carries it (Q3 A).",
]


def build() -> dict:
    snap = snapshot()
    rnd = random.Random(SEED)
    # --------------------------------------------------------------------------------------------------- civil
    terms = yaml.safe_load((ROOT / "spec" / "terms_cw.yaml").read_text())["tables"]
    banded = {r[0] for r in terms["CW.T12_BANDS"]["rows"]}
    apps = {r["application_no"]: r for r in rows("civilwork/invoices/applications.csv")}
    cl = rows("civilwork/invoices/application_lines.csv")
    by = defaultdict(list)
    for r in cl:
        by[r["application_no"]].append(r)
    bandfree = sorted(a for a, ls in by.items() if not any(x["item_code"] in banded for x in ls))
    pick = {}
    for a in rnd.sample(bandfree, 7):
        pick[a] = "random (band-free applications, seed 5505)"
    D = Decimal
    feat = []
    for a in bandfree:
        h = apps[a]
        s = sum(D(x["amount"]) for x in by[a])
        pto, adate = dt.date.fromisoformat(h["period_to"]), dt.date.fromisoformat(h["application_date"])
        if s != D(h["application_total"]):
            feat.append((a, "raw: application_total differs from the sum of its lines"))
        if (adate - pto).days > 21 or adate < pto:
            feat.append((a, "raw: submitted outside 0..21 days after period_to"))
        if h["contract_ref"] != "CW-2025-0417-CIV":
            feat.append((a, "raw: contract reference differs"))
        if any(x["quantity"] and D(x["quantity"]) * D(x["rate_applied"]) != D(x["amount"]) for x in by[a]):
            feat.append((a, "raw: a line amount is not quantity x rate"))
    key = defaultdict(list)
    for r in cl:
        key[(r["item_code"], r["site"], r["work_date"])].append(r)
    for k, v in key.items():
        if len(v) > 1 and all(x["application_no"] in bandfree for x in v):
            feat.append((sorted({x["application_no"] for x in v})[-1], "raw: the same item, site and work date billed twice"))
    for a in ("PA-00443", "PA-00006", "PA-00678"):
        feat.append((a, "raw: submitted on/after an A3 issue date or after the extended completion (Q1 / 45A)"))
    for a, why in feat:
        if a not in pick and len([p for p in pick.values() if not p.startswith("random")]) < 7:
            pick[a] = why
    # ------------------------------------------------------------------------------------------------- drilling
    invs = {r["invoice_no"]: r for r in rows("drilling_services/invoices/invoices.csv")}
    dl = rows("drilling_services/invoices/invoice_lines.csv")
    dby = defaultdict(list)
    for r in dl:
        dby[r["invoice_no"]].append(r)
    small = sorted(i for i, ls in dby.items() if len(ls) <= 22)
    dpick = {}
    for i in rnd.sample(small, 6):
        dpick[i] = "random (invoices of at most 22 lines, seed 5505)"
    dfeat = []
    for i, ls in dby.items():
        h = invs[i]
        svc = sum(D(x["amount"]) for x in ls if x["service_code"] != "DS-900")
        ds = sum(D(x["amount"]) for x in ls if x["service_code"] == "DS-900")
        exp = -((svc - 250000) * D("0.04")).quantize(D("0.01")) if svc > 250000 else D(0)
        if ds != exp and len(ls) <= 60:
            dfeat.append((i, "raw: the DS-900 line does not equal 4% of the services above 250,000"))
        pe, idate = ddmmmyyyy(h["period_end"]), ddmmmyyyy(h["invoice_date"])
        if ((idate - pe).days > 30 or idate < pe) and len(ls) <= 40:
            dfeat.append((i, "raw: submitted outside 0..30 days after period_end"))
    wd = defaultdict(list)
    for r in dl:
        wd[(r["well_name"], r["service_date"], r["service_code"])].append(r)
    for k, v in wd.items():
        if len(v) > 1 and k[2] != "PD-210" and len(dby[v[-1]["invoice_no"]]) <= 60:
            dfeat.append((v[-1]["invoice_no"], "raw: the same service billed twice for one well-day"))
    dfeat.append(("MDS-01625", "raw: first invoice submitted on the A3 date of issue (Q1 A)"))
    for i, why in dfeat:
        if i not in dpick and len([p for p in dpick.values() if not p.startswith("random")]) < 5:
            dpick[i] = why
    return {"cw": pick, "dds": dpick, "apps": apps, "by": by, "cl": cl, "invs": invs, "dby": dby, "dl": dl}


def report_index() -> dict:
    idx = {}
    for p in sorted((snapshot() / "drilling_services" / "records").iterdir()):
        if p.suffix != ".txt":
            continue
        head = p.read_text().split("\n", 3)
        m = re.search(r"Report:\s*(\S+)", "\n".join(head[:3]))
        if m:
            idx[m.group(1)] = p
    return idx


def cw_packet(a: str, why: str, s: dict) -> dict:
    snap = snapshot()
    lines = s["by"][a]
    recs = {}
    for x in lines:
        if x["record_ref"] and (snap / "civilwork" / "records" / f"{x['record_ref']}.txt").exists():
            recs[x["record_ref"]] = _mask((snap / "civilwork" / "records" / f"{x['record_ref']}.txt").read_text())
    ctx = []
    for x in lines:
        same = [o for o in s["cl"] if o is not x and o["item_code"] == x["item_code"] and o["site"] == x["site"]
                and (o["work_date"] == x["work_date"] or (x["record_ref"] and o["record_ref"] == x["record_ref"]))]
        for o in same:
            ctx.append({"line": o, "submitted": s["apps"][o["application_no"]]["application_date"],
                        "why": "another line for the same item and site on the same work date or citing the same record"})
        if x["item_code"] == "A.14.020":
            for o in s["cl"]:
                if o["item_code"] == "A.14.010" and o["site"] == x["site"] and \
                        0 <= (dt.date.fromisoformat(x["work_date"]) - dt.date.fromisoformat(o["work_date"])).days <= 2:
                    ctx.append({"line": o, "submitted": s["apps"][o["application_no"]]["application_date"],
                                "why": "A.14.010 on the same site within two days before"})
        if x["item_code"] == "E.51.020":
            for o in s["cl"]:
                if o["item_code"] in ("D.41.020", "D.41.030", "D.41.050", "D.43.010", "D.43.020") and o["site"] == x["site"] \
                        and o["work_date"] == x["work_date"]:
                    ctx.append({"line": o, "submitted": s["apps"][o["application_no"]]["application_date"],
                                "why": "a surfacing item on the same site and day"})
    terms = yaml.safe_load((ROOT / "spec" / "terms_cw.yaml").read_text())["tables"]
    banded = {r[0] for r in terms["CW.T12_BANDS"]["rows"]}
    band_facts = []
    for x in lines:
        if x["item_code"] not in banded:
            continue
        d = x["work_date"]
        cy0 = "2026-01-05" if d >= "2026-01-05" else "2025-01-05"
        prior = [o for o in s["cl"] if o["item_code"] == x["item_code"] and cy0 <= o["work_date"] < d]
        same = [o for o in s["cl"] if o["item_code"] == x["item_code"] and o["work_date"] == d and o is not x]
        band_facts.append(
            f"Band item {x['item_code']} (line {x['line_ref']}): every measurement of this item in any application (this one included) in the "
            f"same Contract Year (from {cy0}) executed before {d}, as billed (not checked): {len(prior)} lines, "
            f"{sum(Decimal(o['quantity']) for o in prior)} {x['unit']}; on {d} itself: "
            f"{', '.join(o['line_ref'] + ' ' + o['quantity'] for o in same) or 'none'}. Take the count before this line as that "
            f"billed quantity (say so in the note).")
    subs = sorted((h["application_date"], k) for k, h in s["apps"].items())
    near = [f"{k} submitted {d}" for d, k in subs if "2026-05-10" <= d <= "2026-05-16" or d > "2026-09-30"]
    return {"id": a, "contract": "CW", "header": s["apps"][a], "lines": lines, "records": recs, "context_lines": ctx,
            "facts": [f"Submission dates around the A3 issue (12 May 2026) and after the extended completion (30 Sep 2026): "
                      f"{'; '.join(near)}", "No application in the data shows an adjustment or a retention release "
                      "(every adjustment and retention_released is 0.00)."] + band_facts,
            "given": PREMISES}


def dds_packet(i: str, why: str, s: dict, idx: dict) -> dict:
    lines = s["dby"][i]
    reps = {}
    for x in lines:
        p = idx.get(x["report_ref"])
        if p is not None:
            reps[p.stem] = _mask(p.read_text())
    well = s["invs"][i]["well_name"]
    wreps = sorted(r for r, p in idx.items() if f"_{well}_" in p.name or p.name.startswith(f"DDR_{well}_"))
    dates = sorted(p.stem.rsplit("_", 1)[-1] for r, p in idx.items() if p.stem.startswith(f"DDR_{well}_"))
    ctx = []
    for x in lines:
        for o in s["wd"].get((x["well_name"], x["service_date"], x["service_code"]), []):
            if o is not x:
                ctx.append({"line": o, "invoice_date": s["invs"][o["invoice_no"]]["invoice_date"],
                            "why": "the same service for the same well and day"})
    # run context: for DD-111 / LW-420 the reports of the whole run are given
    for x in lines:
        if x["service_code"] in ("DD-111", "LW-420", "MB-701", "MB-702", "DD-140", "LW-430"):
            for r in wreps:
                p = idx[r]
                reps.setdefault(p.stem, _mask(p.read_text()))
    subs = sorted((ddmmmyyyy(h["invoice_date"]), k) for k, h in s["invs"].items())
    near = [f"{k} submitted {d}" for d, k in subs if dt.date(2026, 8, 15) <= d <= dt.date(2026, 8, 19)]
    return {"id": i, "contract": "DDS", "header": s["invs"][i], "lines": lines, "reports": reps, "context_lines": ctx,
            "facts": [f"Daily Drilling Reports supplied for well {well}: {len(dates)} reports, first {dates[0] if dates else None}, "
                      f"last {dates[-1] if dates else None} (dates as in the file names, YYYYMMDD).",
                      f"Invoices submitted around the A3 issue (17 Aug 2026): {'; '.join(near)}",
                      "No invoice in the data shows an adjustment (every adjustment is 0.00)."],
            "given": PREMISES}


def main() -> int:
    s = build()
    s["wd"] = defaultdict(list)
    for r in s["dl"]:
        s["wd"][(r["well_name"], r["service_date"], r["service_code"])].append(r)
    idx = report_index()
    OUT.mkdir(parents=True, exist_ok=True)
    cw = [cw_packet(a, why, s) for a, why in s["cw"].items()]
    dds = [dds_packet(i, why, s, idx) for i, why in s["dds"].items()]
    halves = {"packet_cw_a.jsonl": cw[::2], "packet_cw_b.jsonl": cw[1::2],
              "packet_dds_a.jsonl": dds[::2], "packet_dds_b.jsonl": dds[1::2]}
    for name, ps in halves.items():
        (OUT / name).write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in ps))
    (OUT / "meta.yaml").write_text(yaml.safe_dump({"seed": SEED, "why": {**s["cw"], **s["dds"]},
                                                   "packets": {k: [p["id"] for p in v] for k, v in halves.items()}},
                                                  sort_keys=False, width=140))
    print({k: [(p["id"], len(p["lines"]), len(p.get("reports", p.get("records", {})))) for p in v] for k, v in halves.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
