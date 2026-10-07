"""Build the G3 reference-case packets (inputs only) for the independent expected-value readers.

Synthetic cases come from verification/g3/cases/synthetic_{cw,dds}.yaml (hand-written boundaries and exceptions) and
identity_{cw,dds}.yaml (identity and report-evidence cases added in G3 when the first set was found to lack them; their
packets are separate so the packets already read stay unchanged).
Real cases are billed lines chosen by the fixed criteria below (first line_ref in sort order that matches), so the
choice is reproducible and does not look at any expected value. Each packet item carries what a reader needs:
the claim (line and header as CSV-like fields) and the evidence (record/report text, personal names masked), plus
any fixed state input or question reading. No packet carries an expected rate, quantity or amount; synthetic billed
rates are the Schedule 1 figures as printed.

Usage::  python tools/g3_cases.py        (writes verification/g3/cases/packet_*.jsonl and cases_index.json)
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import spec_lib as sl  # noqa: E402
from audit.common import SNAPSHOT  # noqa: E402

DIR = ROOT / "verification" / "g3" / "cases"
CW_SCH1 = {r[0]: r for r in sl.load_terms("CW")["tables"]["CW.T01_SCH1"]["rows"]}
DDS_SCH1 = {r[0]: r for r in sl.load_terms("DDS")["tables"]["DDS.T01_SCH1"]["rows"]}
DDS_SCH6 = {r[0]: r[1] for r in sl.load_terms("DDS")["tables"]["DDS.T19_SCH6_USD"]["rows"]}


def _d(s: str) -> Decimal:
    return Decimal(str(s).replace(",", ""))


def _fmt_dmy(iso: str) -> str:
    return dt.date.fromisoformat(iso).strftime("%d-%b-%Y")


# ----------------------------------------------------------------------------- civil
def cw_synthetic(name: str = "synthetic_cw.yaml") -> list[dict]:
    doc = yaml.safe_load((DIR / name).read_text())
    dfl = doc["defaults"]
    out = []
    for c in doc["cases"]:
        line = {**dfl["line"], **c["line"]}
        code = line["item_code"]
        line.setdefault("rate_applied", CW_SCH1[code][4].replace(",", ""))
        line.setdefault("amount", str(_d(line["quantity"]) * _d(line["rate_applied"])))
        wd = dt.date.fromisoformat(line["work_date"])
        app = {"application_no": f"SYN-{c['id']}", "contract_ref": dfl["application"]["contract_ref"],
               "subcontractor": dfl["application"]["subcontractor"], "site": line["site"], "site_zone": line["site_zone"],
               "period_from": line["work_date"], "period_to": line["work_date"],
               "application_date": (wd + dt.timedelta(days=dfl["application"]["submit_after_days"])).isoformat()}
        app.update(c.get("application", {}))
        line.update({"line_ref": f"{app['application_no']}-01", "application_no": app["application_no"],
                     "description": CW_SCH1[code][1] if code in CW_SCH1 else ""})
        out.append({"id": c["id"], "kind": "synthetic", "contract": "CW", "tests": c["tests"], "application": app,
                    "line": line, "record": _cw_record_text(c.get("record"), line), "state": {**dfl["state"], **c.get("state", {})}})
    return out


CW_AREAS = {r[0]: r[1] for r in sl.load_terms("CW")["tables"]["CW.T03_WORK_AREAS"]["rows"]}
CW_GROUND = {r[0]: r[1] for r in sl.load_terms("CW")["tables"]["CW.T08_GROUND_FACTORS"]["rows"]}


def _cw_record_text(rec, line) -> str | None:
    """Record text in the real layout (Area and Ground carry their names, as in every supplied record)."""
    if not rec:
        return None
    lines = [rec["title"], f"Ticket: {line['record_ref']}", "Job: Northern Access Road, Package 4",
             f"Area: {rec['area']} {CW_AREAS[rec['area']]}"]
    if rec.get("week_beginning"):
        wb = dt.date.fromisoformat(rec["week_beginning"])
        lines.append(f"Week beginning: {wb:%d/%m/%Y}")
        lines.append("Days on: " + ", ".join(f"{dt.date.fromisoformat(d):%a %d/%m}" for d in rec["days_on"]))
    else:
        lines.append(f"Date: {dt.date.fromisoformat(rec['date']):%d/%m/%Y}")
    if rec.get("ground"):
        lines.append(f"Ground: {rec['ground']} {CW_GROUND[rec['ground']]}")
    lines += ["", rec["narrative"], ""]
    lines.append("Signed (foreman): <signature>")
    lines.append("Countersigned (Engineer's representative): " + ("<signature>" if rec["signed"] == "both" else "____________________"))
    return "\n".join(lines)


# ----------------------------------------------------------------------------- drilling
def dds_synthetic(name: str = "synthetic_dds.yaml") -> list[dict]:
    doc = yaml.safe_load((DIR / name).read_text())
    dfl = doc["defaults"]
    out = []
    for c in doc["cases"]:
        ln = dict(c["line"])
        code, sd = ln["service_code"], ln["service_date"]
        sch = DDS_SCH1[code]
        rate = ln.get("unit_rate") or (DDS_SCH6[code].replace(",", "") if code.startswith("LH-") else
                                      "42.35" if code == "PD-210" else sch[3].replace(",", ""))
        inv = {"invoice_no": f"SYN-{c['id']}", **{k: v for k, v in dfl["invoice"].items() if k != "submit_after_days"},
               "period_start": sd, "period_end": sd,
               "invoice_date": (dt.date.fromisoformat(sd) + dt.timedelta(days=dfl["invoice"]["submit_after_days"])).isoformat()}
        inv.update(c.get("invoice", {}))
        rep = {"A": dict(dfl["report"]["A"]), "B": dict(dfl["report"]["B"]), "signed": dfl["report"]["signed"]}
        for k, v in (c.get("report") or {}).items():
            if isinstance(v, dict):
                rep.setdefault(k, {}).update(v)
            else:
                rep[k] = v
        report_no = f"DDR-SY1-{rep.get('date', sd).replace('-', '')}"
        line = {"line_ref": f"{inv['invoice_no']}-001", "invoice_no": inv["invoice_no"], "service_date": sd,
                "well_name": ln.get("well_name", inv["well_name"]), "service_code": code, "description": sch[1], "unit": ln.get("unit", sch[2]),
                "hole_section": ln.get("hole_section", rep["A"]["Hole section"]), "day_status": ln.get("day_status", "Operating"),
                "depth_from_m": ln.get("depth_from_m", ""), "depth_to_m": ln.get("depth_to_m", ""), "quantity": ln["quantity"],
                "unit_rate": rate, "amount": ln.get("amount") or str(_d(ln["quantity"]) * _d(rate)), "report_ref": report_no}
        out.append({"id": c["id"], "kind": "synthetic", "contract": "DDS", "tests": c["tests"], "invoice": inv, "line": line,
                    "report": None if rep.get("absent") else _dds_report_text(rep, inv, report_no, sd, dfl["invoice"]["contract_ref"]),
                    "state": dfl["state"],
                    "question_readings": c.get("question_readings", {})})
    return out


def _dds_report_text(rep, inv, report_no, sd, contract_ref=None) -> str:
    """The report's own header: its contract is the contract's reference (an invoice's variant is not copied onto the
    rig's record), its well and date are the invoice's unless the case gives the report its own."""
    sd = rep.get("date", sd)
    d = dt.date.fromisoformat(sd)
    sub = {"<service date>": _fmt_dmy(sd), "<service date + 3>": _fmt_dmy((d + dt.timedelta(days=3)).isoformat())}
    lines = ["DAILY DRILLING REPORT", f"Report: {report_no}", f"Contract: {contract_ref or inv['contract_ref']}",
             f"Well: {rep.get('well', inv['well_name'])}", f"Rig: {inv['rig']}", f"Date: {_fmt_dmy(sd)}", ""]
    titles = {"A": "PART A — OPERATIONS SUMMARY", "B": "PART B — BHA RUN RECORD", "C": "PART C — GYRO SURVEY RECORD",
              "D": "PART D — RADIOACTIVE SOURCE HANDLING", "E": "PART E — LOST IN HOLE"}
    for p in "ABCDE":
        if p in rep and rep[p]:
            lines.append(titles[p])
            for k, v in rep[p].items():
                v = sub.get(v, v)
                if v == "<as In the hole>":
                    v = rep["A"]["In the hole"]
                lines.append(f"{k}: {v}")
            lines.append("")
    lines.append("Signed (Company Representative): " + ("<signature>" if rep["signed"] == "both" else "____________________"))
    lines.append("Signed (lead directional driller): <signature>")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- real lines
def _rows(rel):
    with (SNAPSHOT / rel).open(newline="") as fh:
        return list(csv.DictReader(fh))


def _mask(text: str) -> str:
    """Personal names in signature lines are replaced by <signature> (placeholders kept as printed)."""
    out = []
    for ln in text.splitlines():
        m = re.match(r"^((?:Signed|Countersigned)[^:]*:)\s*(.*)$", ln)
        if m and not re.fullmatch(r"[_\s]*", m.group(2)):
            ln = f"{m.group(1)} <signature>"
        elif re.match(r"^(Company Representative|Lead directional driller)\s*:", ln):
            ln = re.sub(r":.*$", ": <name>", ln)
        out.append(ln)
    return "\n".join(out)


CW_REAL = [   # (label, predicate on (line, header))
    ("USD item", lambda l, h: l["item_code"] == "B.23.020"),
    ("indexed C.31.010", lambda l, h: l["item_code"] == "C.31.010"),
    ("indexed D.41.030 at night", lambda l, h: l["item_code"] == "D.41.030" and l["night_work"] == "Y"),
    ("D.41.020 in 2026", lambda l, h: l["item_code"] == "D.41.020" and l["work_date"] >= "2026-01-01"),
    ("C.32.030 on/after 2026-04-01", lambda l, h: l["item_code"] == "C.32.030" and l["work_date"] >= "2026-04-01"),
    ("A3 C.32.010 submitted before issue", lambda l, h: l["item_code"] == "C.32.010" and l["work_date"] >= "2025-11-01" and h["application_date"] < "2026-05-12"),
    ("A3 C.32.010 submitted on/after issue", lambda l, h: l["item_code"] == "C.32.010" and l["work_date"] >= "2025-11-01" and h["application_date"] >= "2026-05-12"),
    ("A3 A.14.010 with record", lambda l, h: l["item_code"] == "A.14.010" and l["work_date"] >= "2025-11-01" and l["record_ref"]),
    ("ground item after 2025-09-27 not G2", lambda l, h: l["item_code"] == "A.12.030" and l["work_date"] > "2025-09-27" and l["ground_class"] and not l["ground_class"].startswith("G2") and l["record_ref"]),
    ("ground item G5 before 2025-09-28", lambda l, h: l["item_code"] in ("A.12.010", "A.12.050") and l["ground_class"].startswith("G5") and l["work_date"] < "2025-09-28"),
    ("night item in Z3/Z4", lambda l, h: l["night_work"] == "Y" and l["site_zone"][:2] in ("Z3", "Z4")),
    ("rest item on a rest day", lambda l, h: l["item_code"] in ("B.21.020", "B.21.030", "B.21.040") and dt.date.fromisoformat(l["work_date"]).weekday() in (4, 5) and l["record_ref"]),
    ("E.51.030 plant standing", lambda l, h: l["item_code"] == "E.51.030" and l["record_ref"]),
    ("A.16.010 dewatering week", lambda l, h: l["item_code"] == "A.16.010" and l["record_ref"]),
    ("B.23.010 surveyed", lambda l, h: l["item_code"] == "B.23.010" and l["record_ref"]),
    ("wrong unit", lambda l, h: l["item_code"] == "A.14.020" and l["unit"] == "no."),
    ("blank ref on a Schedule 5 item", lambda l, h: l["line_ref"] == "PA-00111-12"),
    ("placeholder countersignature", lambda l, h: l["line_ref"] == "PA-00613-01"),
    ("E.54.010 A2 monthly", lambda l, h: l["item_code"] == "E.54.010" and l["work_date"] >= "2026-05-01"),
    ("wrong-series reference", lambda l, h: l["line_ref"] == "PA-00170-04"),
]

DDS_REAL = [
    ("DD-101 on Standby", lambda l, h: l["service_code"] == "DD-101" and l["day_status"] == "Standby"),
    ("DD-120 HPHT", lambda l, h: l["service_code"] == "DD-120" and h["well_class"] == "HPHT"),
    ("DD-120 Apr-Jun 2026 before A3 issue", lambda l, h: l["service_code"] == "DD-120" and "Apr-2026" in l["service_date"] and _iso(h["invoice_date"]) < "2026-08-17"),
    ("DD-120 Extended Reach", lambda l, h: l["service_code"] == "DD-120" and h["well_class"] == "Extended Reach"),
    ("PD-210", lambda l, h: l["service_code"] == "PD-210" and h["well_class"] == "Standard"),
    ("MW-310 in 26 inch", lambda l, h: l["service_code"] == "MW-310" and l["hole_section"] == '26"'),
    ("HC-601 in 2026", lambda l, h: l["service_code"] == "HC-601" and l["service_date"].endswith("2026")),
    ("HC-620 indexed", lambda l, h: l["service_code"] == "HC-620" and "2025" in l["service_date"] and l["day_status"] == "Standby"),
    ("LH, Part E matching neither well nor run sum", lambda l, h: l["line_ref"] == "MDS-00853-005"),
    ("LH-712", lambda l, h: l["service_code"] == "LH-712"),
    ("DD-102", lambda l, h: l["service_code"] == "DD-102"),
    ("DD-121", lambda l, h: l["line_ref"] == "MDS-00022-030"),
    ("RM-511 tool absent", lambda l, h: l["line_ref"] == "MDS-00287-082"),
    ("DD-130 with Part C", lambda l, h: l["service_code"] == "DD-130" and l["line_ref"] != "MDS-00954-021"),
    ("LW-411 metres", lambda l, h: l["service_code"] == "LW-411"),
    ("LW-420", lambda l, h: l["service_code"] == "LW-420" and l["line_ref"] not in ("MDS-00876-062", "MDS-01393-036")),
    ("MB-701 Extended Reach", lambda l, h: l["service_code"] == "MB-701" and h["well_class"] == "Extended Reach"),
    ("HC-630", lambda l, h: l["service_code"] == "HC-630"),
    ("RM-530", lambda l, h: l["service_code"] == "RM-530"),
    ("DD-111", lambda l, h: l["service_code"] == "DD-111"),
    ("LW-413", lambda l, h: l["service_code"] == "LW-413"),
    ("gyro without Part C (CI-02)", lambda l, h: l["line_ref"] == "MDS-00954-021"),
]


def _iso(dmy: str) -> str:
    return dt.datetime.strptime(dmy, "%d-%b-%Y").date().isoformat()


def cw_real() -> list[dict]:
    hdr = {h["application_no"]: h for h in _rows("civilwork/invoices/applications.csv")}
    lines = sorted(_rows("civilwork/invoices/application_lines.csv"), key=lambda r: r["line_ref"])
    out = []
    for i, (label, pred) in enumerate(CW_REAL, 1):
        l = next(x for x in lines if pred(x, hdr[x["application_no"]]))
        h = hdr[l["application_no"]]
        rec_path = SNAPSHOT / f"civilwork/records/{l['record_ref']}.txt" if l["record_ref"] else None
        rec = _mask(rec_path.read_text()) if rec_path and rec_path.exists() else None
        out.append({"id": f"CW-R{i:02d}", "kind": "real", "contract": "CW", "tests": label, "application": h, "line": l,
                    "record": rec, "record_note": None if rec or not l["record_ref"] else f"no record file exists for {l['record_ref']}",
                    "state": {"band_pct": "100", "note": "annual quantity-band state is supplied as an input (G4 owns it); other lines are not considered"}})
    return out


def dds_real() -> list[dict]:
    hdr = {h["invoice_no"]: h for h in _rows("drilling_services/invoices/invoices.csv")}
    lines = sorted(_rows("drilling_services/invoices/invoice_lines.csv"), key=lambda r: r["line_ref"])
    reports = {}
    for f in (SNAPSHOT / "drilling_services/records").iterdir():
        t = f.read_text()
        reports[t.splitlines()[1].split(": ", 1)[1]] = t
    out = []
    for i, (label, pred) in enumerate(DDS_REAL, 1):
        l = next(x for x in lines if x["report_ref"] and pred(x, hdr[x["invoice_no"]]))
        out.append({"id": f"DDS-R{i:02d}", "kind": "real", "contract": "DDS", "tests": label, "invoice": hdr[l["invoice_no"]],
                    "line": l, "report": _mask(reports[l["report_ref"]]) if l["report_ref"] in reports else None,
                    "state": {"annual_footage_pct": "100", "note": "annual footage-band state is supplied as an input (G4 owns it); other lines and invoices are not considered"},
                    "question_readings": {}})
    return out


# Review findings F1-F3: real lines named by independent review and a real inconsistent counterpart
CW_CORR_REAL = [("CW-R21", "PA-00076-08", "band-rated item, band state not known: amount = a division at a band edge (audit F3)", None),
                ("CW-R22", "PA-00610-02", "item without bands whose amount is not quantity x rate (F3 counterpart)", "100"),
                ("CW-R23", "PA-00031-06", "ground item, application states G4, no record supplied (audit F2)", "100")]
DDS_CORR_REAL = [("DDS-R23", "MDS-00001-008", "MW-310; the invoice header states Standard; no call-off supplied (audit F1)")]
# earlier cases whose v1 expected value cannot express the corrected treatment (alternatives): re-read from the SAME
# inputs with the v3 reader instructions; the v1 results stay in their files and the comparison uses the v3 re-reading for these ids
REREAD = {"CW": ["CW-R10", "CW-S20", "CW-S21", "CW-S28", "CW-S29"],
          "DDS": ["DDS-R02", "DDS-R03", "DDS-R04", "DDS-R06", "DDS-R15", "DDS-R21", "DDS-S01", "DDS-S02", "DDS-S09", "DDS-S10",
                  "DDS-S12", "DDS-S13", "DDS-S24", "DDS-S26", "DDS-S28", "DDS-S29", "DDS-S30", "DDS-S32", "DDS-S33"]}


def cw_corr_real() -> list[dict]:
    hdr = {h["application_no"]: h for h in _rows("civilwork/invoices/applications.csv")}
    lines = {r["line_ref"]: r for r in _rows("civilwork/invoices/application_lines.csv")}
    out = []
    for cid, ref, label, band in CW_CORR_REAL:
        l = lines[ref]
        rec_path = SNAPSHOT / f"civilwork/records/{l['record_ref']}.txt" if l["record_ref"] else None
        rec = _mask(rec_path.read_text()) if rec_path and rec_path.exists() else None
        out.append({"id": cid, "kind": "real", "contract": "CW", "tests": label, "application": hdr[l["application_no"]], "line": l,
                    "record": rec, "record_note": None if rec or not l["record_ref"] else f"no record file exists for {l['record_ref']}",
                    "state": {"band_pct": band, "note": ("annual quantity-band state is NOT known (it depends on other lines)" if band is None
                                                         else "annual quantity-band state is supplied as an input (G4 owns it); other lines are not considered")}})
    return out


def dds_corr_real() -> list[dict]:
    hdr = {h["invoice_no"]: h for h in _rows("drilling_services/invoices/invoices.csv")}
    lines = {r["line_ref"]: r for r in _rows("drilling_services/invoices/invoice_lines.csv")}
    reports = {}
    for f in (SNAPSHOT / "drilling_services/records").iterdir():
        t = f.read_text()
        reports[t.splitlines()[1].split(": ", 1)[1]] = t
    out = []
    for cid, ref, label in DDS_CORR_REAL:
        l = lines[ref]
        out.append({"id": cid, "kind": "real", "contract": "DDS", "tests": label, "invoice": hdr[l["invoice_no"]], "line": l,
                    "report": _mask(reports[l["report_ref"]]) if l["report_ref"] in reports else None,
                    "state": {"annual_footage_pct": "100", "note": "annual footage-band state is supplied as an input (G4 owns it); other lines and invoices are not considered"},
                    "question_readings": {}})
    return out


# Review finding B1: the review's probe - a real line given a
# record of another day and area as its reference - and a real line whose own record applies (first A.12.040 before
# 28 Sep 2025 with a record reference, in line order)
CW_R2_REAL = [("CW-R24", "PA-00031-06", "DX-00007", "audit probe B1: the real line with its blank record reference replaced by "
               "DX-00007, a record of another day and work area (the claim cites it; it is not a record of this work)"),
              ("CW-R25", "PA-00017-07", None, "Schedule 5 ground item before 28 Sep 2025 with its own excavation record")]


def cw_r2_real() -> list[dict]:
    hdr = {h["application_no"]: h for h in _rows("civilwork/invoices/applications.csv")}
    lines = {r["line_ref"]: r for r in _rows("civilwork/invoices/application_lines.csv")}
    out = []
    for cid, ref, cited, label in CW_R2_REAL:
        l = dict(lines[ref])
        if cited:
            l["record_ref"] = cited
        rec_path = SNAPSHOT / f"civilwork/records/{l['record_ref']}.txt" if l["record_ref"] else None
        rec = _mask(rec_path.read_text()) if rec_path and rec_path.exists() else None
        out.append({"id": cid, "kind": "real" if not cited else "probe", "contract": "CW", "tests": label,
                    "application": hdr[l["application_no"]], "line": l, "record": rec,
                    "record_note": None if rec or not l["record_ref"] else f"no record file exists for {l['record_ref']}",
                    "state": {"band_pct": "100", "note": "annual quantity-band state is supplied as an input (G4 owns it); other lines are not considered"}})
    return out


def reread(contract: str, earlier: list[dict]) -> list[dict]:
    by = {c["id"]: c for c in earlier}
    return [by[i] for i in REREAD[contract]]


def main() -> int:
    groups = {"cw_synthetic": cw_synthetic(), "dds_synthetic": dds_synthetic(), "cw_real": cw_real(), "dds_real": dds_real(),
              "cw_identity": cw_synthetic("identity_cw.yaml"), "dds_identity": dds_synthetic("identity_dds.yaml")}
    groups["cw_correction"] = cw_synthetic("correction_cw.yaml") + cw_corr_real()
    groups["dds_correction"] = dds_synthetic("correction_dds.yaml") + dds_corr_real()
    groups["cw_correction_r2"] = cw_synthetic("correction_r2_cw.yaml") + cw_r2_real()
    groups["dds_correction_r2"] = dds_synthetic("correction_r2_dds.yaml")
    groups["cw_reread"] = reread("CW", groups["cw_synthetic"] + groups["cw_real"])
    groups["dds_reread"] = reread("DDS", groups["dds_synthetic"] + groups["dds_real"])
    split = {"cw_synthetic": 2, "dds_synthetic": 2, "cw_real": 1, "dds_real": 1, "cw_identity": 1, "dds_identity": 1,
             "cw_correction": 1, "dds_correction": 1, "cw_reread": 1, "dds_reread": 1, "cw_correction_r2": 1, "dds_correction_r2": 1}
    index = {}
    for g, cases in groups.items():
        n = split[g]
        size = (len(cases) + n - 1) // n
        for k in range(n):
            part = cases[k * size:(k + 1) * size]
            name = f"packet_{g}_{k + 1}" if n > 1 else f"packet_{g}"
            (DIR / f"{name}.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in part))
            index[name] = [c["id"] for c in part]
    (DIR / "cases_index.json").write_text(json.dumps(index, indent=1) + "\n")
    print({k: len(v) for k, v in index.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
