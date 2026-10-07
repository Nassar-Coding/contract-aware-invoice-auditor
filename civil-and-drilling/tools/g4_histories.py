"""G4 multi-invoice history packets: inputs only (G4 exit: small multi-invoice histories agreeing with
independent expected events and amounts).

Each history is a small set of applications or invoices with their lines and the site records or Daily Drilling
Reports they cite, built so that one state rule - or an interaction of several - decides the result: annual bands and
Contract Year resets, daily limits, exclusions, duplicate measurements and double charges, run and well events, loss
events, annual footage, the A3 retrospective adjustment and its recipient, retention and its release, P23. The packets
are written BEFORE any G4 code and given to independent readers, who
compute the expected lines, events and amounts from the contract scans alone. `materialize()` writes a history as a
snapshot directory the production G2 loader reads (claims CSVs, record and report files), so the engine sees exactly
what the readers see.

Usage::  python tools/g4_histories.py      (writes verification/g4/histories/packet_cw.jsonl, packet_dds.jsonl, meta.yaml)
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import sys
from decimal import Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "verification" / "g4" / "histories"

CW_HEAD = ["application_no", "contract_ref", "subcontractor", "site", "site_zone", "period_from", "period_to", "application_date",
           "application_total", "retention", "net_payable", "adjustment", "retention_released"]
CW_LINE = ["line_ref", "application_no", "line_no", "work_date", "site", "item_code", "description", "unit", "site_zone",
           "ground_class", "quantity", "rate_applied", "amount", "night_work", "record_ref"]
DDS_HEAD = ["invoice_no", "contract_ref", "contractor", "well_name", "rig", "field", "well_class", "period_start", "period_end",
            "invoice_date", "net_amount", "vat_amount", "invoice_total", "adjustment"]
DDS_LINE = ["line_ref", "invoice_no", "line_no", "service_date", "well_name", "service_code", "description", "unit", "hole_section",
            "day_status", "depth_from_m", "depth_to_m", "quantity", "unit_rate", "amount", "report_ref"]

AREAS = {"S-01": "S-01 Platform North", "S-02": "S-02 Platform South", "S-03": "S-03 Access Road", "S-04": "S-04 Drainage Corridor",
         "S-05": "S-05 Compound Extension"}
CW_ITEMS = {   # Schedule 1 description and unit (pp17-19) of the items the histories use
    "A.11.010": ("Site clearance, vegetation and topsoil strip", "m2"),
    "A.13.010": ("Disposal of excavated material off site", "m3"),
    "A.14.010": ("Imported granular fill, placed and compacted", "m3"),
    "A.14.020": ("Backfilling to trenches with selected arisings", "m3"),
    "A.16.010": ("Dewatering by wellpoint, per week in operation", "week"),
    "B.22.010": ("Formwork to vertical faces, basic finish", "m2"),
    "C.32.010": ("Precast manhole, 1200 mm diameter, depth to 3 m", "no."),
    "D.41.010": ("Sub-base, Type 1 granular, 250 mm compacted", "m2"),
    "D.41.050": ("Bitumen tack coat between courses", "m2"),
    "E.51.020": ("Traffic management, two-way signals, per day", "day"),
    "E.53.010": ("Attendance on statutory undertaker", "day"),
}
DDS_SERVICES = {   # Schedule 1 description and unit (pp15-16)
    "DD-101": ("Directional driller, 24-hour coverage", "person-day"),
    "DD-111": ("Motor redress and inspection", "run"),
    "DD-140": ("Well planning and anti-collision study", "well"),
    "HC-630": ("Casing drift and clean-out run", "run"),
    "LH-713": ("Lost in hole, MWD tool", "each"),
    "LW-420": ("Radioactive source handling", "run"),
    "LW-430": ("LWD data processing and final log", "well"),
    "MB-701": ("Mobilisation to rig site", "well"),
    "MB-702": ("Demobilisation from rig site", "well"),
    "PD-210": ("Performance drilling footage", "metre"),
}
FOREMAN, ENGINEER = "A. Signatory", "B. Signatory"
COMPANY, DRILLER = "C. Signatory", "D. Signatory"


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


# ------------------------------------------------------------------------------------------------------------ civil
def cw_app(no, adate, lines, site="S-05", zone="Z1 Compound"):
    """lines: (work_date, area, item, quantity, billed_rate, record_ref[, unit override])"""
    rows, total = [], Decimal("0")
    for i, ln in enumerate(lines, 1):
        wd, area, item, qty, rate, rec, *unit = ln
        desc, u = CW_ITEMS[item]
        amt = Decimal(qty) * Decimal(rate)
        total += amt
        rows.append({"line_ref": f"{no}-{i:02d}", "application_no": no, "line_no": str(i), "work_date": wd, "site": AREAS[area],
                     "item_code": item, "description": desc, "unit": unit[0] if unit else u, "site_zone": zone,
                     "ground_class": "G2 Firm Sabkha" if item in ("A.14.020", "C.32.010") else "", "quantity": qty,
                     "rate_applied": rate, "amount": f"{amt:.2f}", "night_work": "N", "record_ref": rec})
    dates = sorted(r["work_date"] for r in rows)
    ret = (total * Decimal("0.05")).quantize(Decimal("0.01"), rounding="ROUND_DOWN")
    head = {"application_no": no, "contract_ref": "CW-2025-0417-CIV", "subcontractor": "Ridgeway Civil Engineering LLC",
            "site": AREAS[site], "site_zone": zone, "period_from": dates[0], "period_to": dates[-1], "application_date": adate,
            "application_total": f"{total:.2f}", "retention": f"{ret:.2f}", "net_payable": f"{total - ret:.2f}",
            "adjustment": "0.00", "retention_released": "0.00"}
    return {"header": head, "lines": rows}


def ct(ticket, area, date, narrative, title="COMPACTION TEST CERTIFICATE"):
    d = _d(date)
    return (f"{title}\nTicket: {ticket}\nJob: Northern Access Road, Package 4\nArea: {AREAS[area]}\nDate: {d:%d/%m/%Y}\n\n"
            f"{narrative}\n\nSigned (foreman): {FOREMAN}\nCountersigned (Engineer's representative): {ENGINEER}\n")


def dw(ticket, area, monday, days, narrative):
    m = _d(monday)
    on = ", ".join(f"{(m + dt.timedelta(days=k)):%a %d/%m}" for k in range(days))
    return (f"DEWATERING LOG\nTicket: {ticket}\nJob: Northern Access Road, Package 4\nArea: {AREAS[area]}\n"
            f"Week beginning: {m:%d/%m/%Y}\nDays on: {on}\n\n{narrative}\n\nSigned (foreman): {FOREMAN}\n"
            f"Countersigned (Engineer's representative): {ENGINEER}\n")


CW_HISTORIES = [
    {"id": "CW-H01", "documents": [
        cw_app("PA-91002", "2025-03-20", [("2025-03-02", "S-05", "B.22.010", "600", "74.50", ""),
                                          ("2025-03-10", "S-05", "B.22.010", "400", "74.50", "")]),
        cw_app("PA-91001", "2025-03-25", [("2025-03-12", "S-05", "B.22.010", "500", "74.50", "")]),
        cw_app("PA-91003", "2025-04-20", [("2025-04-02", "S-05", "B.22.010", "3700", "74.50", "")])], "records": {}},
    {"id": "CW-H02", "documents": [
        cw_app("PA-92001", "2025-02-20", [("2025-02-01", "S-02", "A.13.010", "4900", "18.90", "")], site="S-02"),
        cw_app("PA-92003", "2025-02-18", [("2025-02-10", "S-02", "A.13.010", "80", "18.90", "")], site="S-02"),
        cw_app("PA-92002", "2025-02-25", [("2025-02-10", "S-03", "A.13.010", "60", "18.90", ""),
                                          ("2025-02-10", "S-04", "A.13.010", "30", "18.90", "")], site="S-03")], "records": {}},
    {"id": "CW-H03", "documents": [
        cw_app("PA-93001", "2025-12-20", [("2025-12-01", "S-03", "A.13.010", "19800", "18.90", "")], site="S-03"),
        cw_app("PA-93002", "2026-01-10", [("2026-01-04", "S-03", "A.13.010", "300", "18.90", ""),
                                          ("2026-01-05", "S-03", "A.13.010", "250", "18.90", "")], site="S-03")], "records": {}},
    {"id": "CW-H04", "documents": [
        cw_app("PA-94001", "2025-04-15", [("2025-04-01", "S-04", "D.41.010", "4000", "41.80", "CT-94001"),
                                          ("2025-04-02", "S-04", "D.41.010", "300", "41.80", "CT-94002", "m3"),
                                          ("2025-04-03", "S-04", "D.41.010", "400", "41.80", "")], site="S-04"),
        cw_app("PA-94002", "2025-04-25", [("2025-04-08", "S-04", "D.41.010", "600", "41.80", "CT-94003")], site="S-04")],
     "records": {"CT-94001": ct("CT-94001", "S-04", "2025-04-01", "4000 square metres of sub-base in and compacted"),
                 "CT-94002": ct("CT-94002", "S-04", "2025-04-02", "300 square metres of sub-base in and compacted"),
                 "CT-94003": ct("CT-94003", "S-04", "2025-04-08", "600 square metres of sub-base in and compacted")}},
    {"id": "CW-H05", "documents": [
        cw_app("PA-95001", "2025-06-10", [("2025-06-02", "S-01", "A.11.010", "3000", "3.85", ""),
                                          ("2025-06-02", "S-02", "A.11.010", "2000", "3.85", ""),
                                          ("2025-06-03", "S-01", "A.11.010", "1000", "3.85", "")], site="S-01"),
        cw_app("PA-95002", "2025-06-12", [("2025-06-02", "S-01", "A.11.010", "2000", "3.85", "")], site="S-01"),
        cw_app("PA-95003", "2025-06-15", [("2025-06-04", "S-01", "A.11.010", "4800", "3.85", "")], site="S-01")], "records": {}},
    {"id": "CW-H06", "documents": [
        cw_app("PA-96001", "2025-10-25", [("2025-10-06", "S-04", "A.14.010", "100", "53.20", "CT-96001"),
                                          ("2025-10-06", "S-04", "A.14.020", "50", "22.40", ""),
                                          ("2025-10-07", "S-04", "A.14.020", "40", "22.40", ""),
                                          ("2025-10-08", "S-04", "A.14.020", "30", "22.40", ""),
                                          ("2025-10-09", "S-04", "A.14.020", "20", "22.40", ""),
                                          ("2025-10-07", "S-03", "A.14.020", "60", "22.40", "")], site="S-04")],
     "records": {"CT-96001": ct("CT-96001", "S-04", "2025-10-06", "brought in 100 cube of fill, compacted in layers")}},
    {"id": "CW-H07", "documents": [
        cw_app("PA-97001", "2025-07-20", [("2025-07-07", "S-03", "D.41.050", "500", "6.20", ""),
                                          ("2025-07-07", "S-03", "E.51.020", "1", "684.00", ""),
                                          ("2025-07-07", "S-04", "E.51.020", "1", "684.00", ""),
                                          ("2025-07-08", "S-03", "E.51.020", "2", "684.00", "")], site="S-03")], "records": {}},
    {"id": "CW-H08", "documents": [
        cw_app("PA-98002", "2025-08-10", [("2025-08-04", "S-02", "E.53.010", "1", "389.00", ""),
                                          ("2025-08-05", "S-02", "E.53.010", "1", "389.00", "")], site="S-02"),
        cw_app("PA-98001", "2025-08-20", [("2025-08-04", "S-02", "E.53.010", "1", "389.00", ""),
                                          ("2025-08-04", "S-01", "E.53.010", "1", "389.00", "")], site="S-02"),
        cw_app("PA-98003", "2025-08-12", [("2025-08-05", "S-02", "E.53.010", "1", "389.00", ""),
                                          ("2025-08-05", "S-02", "E.53.010", "1", "389.00", "")], site="S-02"),
        cw_app("PA-98004", "2025-09-15", [("2025-09-02", "S-05", "A.16.010", "1", "1524.00", "DW-98001")]),
        cw_app("PA-98005", "2025-09-20", [("2025-09-04", "S-05", "A.16.010", "1", "1524.00", "DW-98001")])],
     "records": {"DW-98001": dw("DW-98001", "S-05", "2025-09-01", 5, "pumps kept going 1 week this period")}},
    {"id": "CW-H09", "documents": [
        cw_app("PA-99001", "2025-11-20", [("2025-10-31", "S-01", "C.32.010", "2", "1860.00", ""),
                                          ("2025-11-05", "S-01", "C.32.010", "3", "1860.00", "")], site="S-01"),
        cw_app("PA-99002", "2025-12-20", [("2025-12-10", "S-02", "C.32.010", "1", "1767.00", "")], site="S-02"),
        cw_app("PA-99003", "2026-05-12", [("2026-05-05", "S-03", "C.32.010", "2", "1825.28", "")], site="S-03"),
        cw_app("PA-99004", "2026-05-12", [("2026-05-06", "S-04", "C.32.010", "1", "1825.28", "")], site="S-04"),
        cw_app("PA-99005", "2026-05-14", [("2026-05-08", "S-05", "C.32.010", "1", "1825.28", "")])], "records": {}},
    {"id": "CW-H10", "documents": [
        cw_app("PA-9A001", "2026-08-10", [("2026-08-03", "S-01", "A.11.010", "1001", "3.85", "")], site="S-01"),
        cw_app("PA-9A002", "2026-09-30", [("2026-09-29", "S-02", "A.11.010", "999", "3.85", "")], site="S-02"),
        cw_app("PA-9A003", "2026-10-05", [("2026-09-30", "S-03", "A.11.010", "100", "3.85", "")], site="S-03"),
        cw_app("PA-9A004", "2026-10-10", [("2026-09-30", "S-04", "A.11.010", "100", "3.85", "")], site="S-04")], "records": {}},
    {"id": "CW-H11", "documents": [
        cw_app("PA-9B001", "2025-05-10", [("2025-05-02", "S-01", "D.41.010", "500", "41.80", "")], site="S-01"),
        cw_app("PA-9B002", "2025-06-10", [("2025-06-02", "S-01", "D.41.010", "600", "41.80", "CT-9B001")], site="S-01")],
     "records": {"CT-9B001": ct("CT-9B001", "S-01", "2025-06-02", "600 square metres of sub-base in and compacted")},
     "given": ["Question Q3 is decided (reading A): a line for an item that needs a Schedule 5 record is not payable in its own "
               "application until the record has been delivered (Cl.46); P23 governs the recovery of an amount that WAS "
               "included in a valuation without its record."]},
    {"id": "CW-H12", "documents": [
        cw_app("PA-9C001", "2025-11-15", [("2025-10-20", "S-01", "A.14.010", "1900", "53.20", "CT-9C001"),
                                          ("2025-11-03", "S-01", "A.14.010", "300", "53.20", "CT-9C002")], site="S-01"),
        cw_app("PA-9C002", "2026-05-20", [("2026-05-11", "S-02", "A.14.010", "100", "56.80", "CT-9C003")], site="S-02")],
     "records": {"CT-9C001": ct("CT-9C001", "S-01", "2025-10-20", "brought in 1900 cube of fill, compacted in layers"),
                 "CT-9C002": ct("CT-9C002", "S-01", "2025-11-03", "brought in 300 cube of fill, compacted in layers"),
                 "CT-9C003": ct("CT-9C003", "S-02", "2026-05-11", "brought in 100 cube of fill, compacted in layers")}},
]


# --------------------------------------------------------------------------------------------------------- drilling
RATES = {"DD-101": "1847.35", "DD-111": "3589.45", "DD-140": "12489.50", "HC-630": "4295.85", "LW-420": "2746.55",
         "LW-430": "8394.75", "MB-701": "18497.50", "MB-702": "14196.25"}


def _mon(s: str) -> str:
    return _d(s).strftime("%d-%b-%Y")


def dds_inv(no, well, rig, idate, lines, cls="Standard"):
    """lines: (service_date, code, quantity, unit_rate, section, status[, depth_from, depth_to])"""
    rows, net = [], Decimal("0")
    for i, ln in enumerate(lines, 1):
        sd, code, qty, rate, section, status, *depths = ln
        desc, unit = DDS_SERVICES[code]
        amt = (Decimal(qty) * Decimal(rate)).quantize(Decimal("0.01"), rounding="ROUND_HALF_EVEN")
        net += amt
        rows.append({"line_ref": f"{no}-{i:03d}", "invoice_no": no, "line_no": str(i), "service_date": _mon(sd), "well_name": well,
                     "service_code": code, "description": desc, "unit": unit, "hole_section": section, "day_status": status,
                     "depth_from_m": depths[0] if depths else "", "depth_to_m": depths[1] if depths else "", "quantity": qty,
                     "unit_rate": rate, "amount": f"{amt:.2f}",
                     "report_ref": f"DDR-{well.rsplit('-', 1)[-1]}-{_d(sd):%Y%m%d}"})
    dates = sorted(_d(ln[0]) for ln in lines)
    vat = (net * Decimal("0.15")).quantize(Decimal("0.01"), rounding="ROUND_HALF_EVEN")
    head = {"invoice_no": no, "contract_ref": "DDS-2025-118", "contractor": "Meridian Downhole Services Ltd", "well_name": well,
            "rig": rig, "field": "Harran", "well_class": cls, "period_start": f"{dates[0]:%d-%b-%Y}", "period_end": f"{dates[-1]:%d-%b-%Y}",
            "invoice_date": _mon(idate), "net_amount": f"{net:.2f}", "vat_amount": f"{vat:.2f}", "invoice_total": f"{net + vat:.2f}",
            "adjustment": "0.00"}
    return {"header": head, "lines": rows}


def ddr(well, rig, date, section, status, d0, d1, circ, run, tools, crew, *, cleanouts=0, gyro=0, part_b=None, part_d=None,
        part_e=None):
    """One Daily Drilling Report in the supplied layout (Sch 5 p24; the parts present as the day needs them)."""
    d = _d(date)
    lines = ["DAILY DRILLING REPORT", f"Report: DDR-{well.rsplit('-', 1)[-1]}-{d:%Y%m%d}", "Contract: DDS-2025-118", f"Well: {well}",
             f"Rig: {rig}", f"Date: {d:%d-%b-%Y}", "", "PART A — OPERATIONS SUMMARY", f"Hole section: {section}", f"Status: {status}",
             f"Depth start (m MD): {d0}", f"Depth end (m MD): {d1}", f"Circulating hours: {circ}", f"BHA run: {run}",
             f"In the hole: {', '.join(tools)}", f"Crew on tour: {', '.join(crew)}", f"Gyro surveys: {gyro}", "Pressure points: 0",
             "Wiper trips: 0", "Back-reaming hours: 0", f"Clean-out runs: {cleanouts}"]
    if part_b:
        first, last, run_tools, run_circ, source = part_b
        lines += ["", "PART B — BHA RUN RECORD", f"Run: {run}", f"Run first day: {_mon(first)}", f"Run last day: {_mon(last)}",
                  f"Tools in run: {', '.join(run_tools)}", f"Run circulating hours: {run_circ}", "Metres logged: 0", "Metres reamed: 0",
                  f"Radioactive source carried: {'Yes' if source else 'No'}"]
    if part_d:
        lines += ["", "PART D — RADIOACTIVE SOURCE HANDLING", f"Source run: {run}", f"Sources handled: {part_d}",
                  "Source handling certified: Yes"]
    if part_e:
        tool, hours = part_e
        lines += ["", "PART E — LOST IN HOLE", f"Lost in hole run: {run}", f"Lost in hole tool: {tool}",
                  f"Circulating hours accumulated on the well: {hours}"]
    lines += ["", f"Signed (Company Representative): {COMPANY}", f"Signed (lead directional driller): {DRILLER}", ""]
    return f"DDR_{well}_{d:%Y%m%d}", "\n".join(lines)


def reports(*items):
    return dict(items)


BASIC = ["MWD collar", "drilling jars", "mud motor", "real-time link"]
S12 = '12-1/4"'
S17 = '17-1/2"'

DDS_HISTORIES = [
    {"id": "DDS-H01", "documents": [
        dds_inv("MDS-90001", "NGP-ZZ-901", "NG-Rig 91", "2025-03-10", [("2025-03-03", "DD-101", "2", RATES["DD-101"], S12, "Operating"),
                                                                     ("2025-03-04", "DD-101", "2", RATES["DD-101"], S12, "Operating")]),
        dds_inv("MDS-90002", "NGP-ZZ-901", "NG-Rig 91", "2025-03-15", [("2025-03-04", "DD-101", "1", RATES["DD-101"], S12, "Operating"),
                                                                     ("2025-03-05", "DD-101", "3", RATES["DD-101"], S12, "Operating")])],
     "reports": reports(
         ddr("NGP-ZZ-901", "NG-Rig 91", "2025-03-03", S12, "Operating", 1000, 1150, 14, 1, BASIC, ["2 directional hands", "2 MWD engineers"],
             part_b=("2025-03-03", "2025-03-05", BASIC, 40, False)),
         ddr("NGP-ZZ-901", "NG-Rig 91", "2025-03-04", S12, "Operating", 1150, 1290, 13, 1, BASIC, ["2 directional hands", "2 MWD engineers"],
             part_b=("2025-03-03", "2025-03-05", BASIC, 40, False)),
         ddr("NGP-ZZ-901", "NG-Rig 91", "2025-03-05", S12, "Operating", 1290, 1420, 13, 1, BASIC, ["3 directional hands", "2 MWD engineers"],
             part_b=("2025-03-03", "2025-03-05", BASIC, 40, False)))},
    {"id": "DDS-H02", "documents": [
        dds_inv("MDS-90011", "NGP-ZZ-902", "NG-Rig 92", "2025-04-20", [("2025-04-10", "PD-210", "50", "42.35", S12, "Operating", "1450", "1500"),
                                                                     ("2025-04-10", "PD-210", "100", "58.15", S12, "Operating", "1500", "1600")]),
        dds_inv("MDS-90012", "NGP-ZZ-902", "NG-Rig 92", "2025-04-25", [("2025-04-10", "PD-210", "50", "58.15", S12, "Operating", "1550", "1600")])],
     "reports": reports(
         ddr("NGP-ZZ-902", "NG-Rig 92", "2025-04-10", S12, "Operating", 1450, 1600, 15, 2, BASIC, ["2 directional hands", "2 MWD engineers"],
             part_b=("2025-04-10", "2025-04-10", BASIC, 15, False))),
     "given": ["Item PD-210 is charged only on a performance-drilled section nominated in the call-off (Cl.23), and no call-off "
               "is supplied. For this history, value the PD-210 charges as if the 12-1/4 inch section were nominated; the "
               "nomination itself is decided elsewhere."]},
    {"id": "DDS-H03", "documents": [
        dds_inv("MDS-90021", "NGP-ZZ-903", "NG-Rig 93", "2025-05-10", [("2025-05-03", "DD-111", "1", RATES["DD-111"], S12, "Operating"),
                                                                     ("2025-05-02", "HC-630", "1", RATES["HC-630"], S12, "Operating"),
                                                                     ("2025-05-03", "HC-630", "1", RATES["HC-630"], S12, "Operating"),
                                                                     ("2025-05-04", "LW-420", "1", RATES["LW-420"], S12, "Operating")]),
        dds_inv("MDS-90022", "NGP-ZZ-903", "NG-Rig 93", "2025-05-20", [("2025-05-03", "DD-111", "1", RATES["DD-111"], S12, "Operating"),
                                                                     ("2025-05-04", "LW-420", "1", RATES["LW-420"], S12, "Operating")])],
     "reports": reports(*[
         ddr("NGP-ZZ-903", "NG-Rig 93", d, S12, "Operating", d0, d1, 12, run, tools, ["2 directional hands", "2 MWD engineers"] +
             (["1 logging engineers"] if run == 3 else []), cleanouts=co,
             part_b=(first, last, tools, 36, run == 3), part_d="density and neutron" if d == "2025-05-04" else None)
         for d, d0, d1, run, first, last, tools, co in [
             ("2025-05-01", 2000, 2120, 2, "2025-05-01", "2025-05-03", BASIC, 0),
             ("2025-05-02", 2120, 2240, 2, "2025-05-01", "2025-05-03", BASIC, 1),
             ("2025-05-03", 2240, 2350, 2, "2025-05-01", "2025-05-03", BASIC, 1),
             ("2025-05-04", 2350, 2470, 3, "2025-05-04", "2025-05-06", ["MWD collar", "drilling jars", "resistivity tool", "real-time link"], 0),
             ("2025-05-05", 2470, 2590, 3, "2025-05-04", "2025-05-06", ["MWD collar", "drilling jars", "resistivity tool", "real-time link"], 0),
             ("2025-05-06", 2590, 2700, 3, "2025-05-04", "2025-05-06", ["MWD collar", "drilling jars", "resistivity tool", "real-time link"], 0)]])},
    {"id": "DDS-H04", "documents": [
        dds_inv("MDS-90031", "NGP-ZZ-904", "NG-Rig 94", "2025-06-10", [("2025-06-01", "MB-701", "1", RATES["MB-701"], S17, "Operating"),
                                                                     ("2025-06-01", "DD-140", "1", RATES["DD-140"], S17, "Operating"),
                                                                     ("2025-06-03", "MB-701", "1", RATES["MB-701"], S17, "Operating")]),
        dds_inv("MDS-90032", "NGP-ZZ-904", "NG-Rig 94", "2025-06-15", [("2025-06-04", "MB-702", "1", RATES["MB-702"], S17, "Operating"),
                                                                     ("2025-06-05", "MB-702", "1", RATES["MB-702"], S17, "Operating"),
                                                                     ("2025-06-05", "LW-430", "1", RATES["LW-430"], S17, "Operating")])],
     "reports": reports(*[
         ddr("NGP-ZZ-904", "NG-Rig 94", d, S17, "Operating", d0, d1, 12, 1, BASIC, ["2 directional hands", "2 MWD engineers"],
             part_b=("2025-06-01", "2025-06-05", BASIC, 60, False))
         for d, d0, d1 in [("2025-06-01", 0, 300), ("2025-06-02", 300, 600), ("2025-06-03", 600, 900), ("2025-06-04", 900, 1200),
                           ("2025-06-05", 1200, 1450)]])},
    {"id": "DDS-H05", "documents": [
        dds_inv("MDS-90041", "NGP-ZZ-905", "NG-Rig 95", "2026-02-10", [("2026-01-31", "DD-101", "2", "1916.50", S12, "Operating"),
                                                                     ("2026-02-01", "DD-101", "2", "1916.50", S12, "Operating")]),
        dds_inv("MDS-90042", "NGP-ZZ-905", "NG-Rig 95", "2026-04-20", [("2026-04-06", "DD-101", "2", "1839.84", S12, "Operating")]),
        dds_inv("MDS-90043", "NGP-ZZ-906", "NG-Rig 96", "2026-08-17", [("2026-08-10", "DD-101", "1", "1817.22", S12, "Operating")]),
        dds_inv("MDS-90044", "NGP-ZZ-905", "NG-Rig 95", "2026-08-18", [("2026-08-15", "DD-101", "1", "1817.22", S12, "Operating")]),
        dds_inv("MDS-90045", "NGP-ZZ-906", "NG-Rig 96", "2026-08-18", [("2026-08-13", "DD-101", "1", "1817.22", S12, "Operating")])],
     "reports": reports(*[
         ddr(w, rig, d, S12, "Operating", d0, d1, 12, run, BASIC, [f"{n} directional hands", "2 MWD engineers"],
             part_b=(first, last, BASIC, 12 * days, False))
         for w, rig, d, d0, d1, n, run, first, last, days in [
             ("NGP-ZZ-905", "NG-Rig 95", "2026-01-31", 1000, 1120, 2, 1, "2026-01-31", "2026-02-01", 2),
             ("NGP-ZZ-905", "NG-Rig 95", "2026-02-01", 1120, 1240, 2, 1, "2026-01-31", "2026-02-01", 2),
             ("NGP-ZZ-905", "NG-Rig 95", "2026-04-06", 2000, 2110, 2, 2, "2026-04-06", "2026-04-06", 1),
             ("NGP-ZZ-905", "NG-Rig 95", "2026-08-15", 3000, 3100, 1, 3, "2026-08-15", "2026-08-15", 1),
             ("NGP-ZZ-906", "NG-Rig 96", "2026-08-10", 1000, 1100, 1, 1, "2026-08-10", "2026-08-10", 1),
             ("NGP-ZZ-906", "NG-Rig 96", "2026-08-13", 1300, 1400, 1, 2, "2026-08-13", "2026-08-13", 1)]])},
    {"id": "DDS-H06", "documents": [
        dds_inv("MDS-90051", "NGP-ZZ-907", "NG-Rig 97", "2025-07-10", [("2025-07-01", "PD-210", "39500", "98.70", S12, "Operating", "4500", "44000"),
                                                                     ("2025-07-02", "PD-210", "100", "98.70", S12, "Operating", "44000", "44100")]),
        dds_inv("MDS-90052", "NGP-ZZ-908", "NG-Rig 98", "2026-01-05", [("2025-12-30", "PD-210", "39990", "98.70", S12, "Operating", "4500", "44490")]),
        dds_inv("MDS-90053", "NGP-ZZ-908", "NG-Rig 98", "2026-01-10", [("2026-01-01", "PD-210", "20", "98.70", S12, "Operating", "44490", "44510")])],
     "reports": reports(
         ddr("NGP-ZZ-907", "NG-Rig 97", "2025-06-30", S17, "Operating", 4000, 4500, 14, 1, BASIC, ["2 directional hands", "2 MWD engineers"],
             part_b=("2025-06-30", "2025-06-30", BASIC, 14, False)),
         ddr("NGP-ZZ-907", "NG-Rig 97", "2025-07-01", S12, "Operating", 4500, 44000, 20, 2, BASIC, ["2 directional hands", "2 MWD engineers"],
             part_b=("2025-07-01", "2025-07-02", BASIC, 40, False)),
         ddr("NGP-ZZ-907", "NG-Rig 97", "2025-07-02", S12, "Operating", 44000, 44100, 20, 2, BASIC, ["2 directional hands", "2 MWD engineers"],
             part_b=("2025-07-01", "2025-07-02", BASIC, 40, False)),
         ddr("NGP-ZZ-908", "NG-Rig 98", "2025-12-30", S12, "Operating", 4500, 44490, 20, 1, BASIC, ["2 directional hands", "2 MWD engineers"],
             part_b=("2025-12-30", "2025-12-30", BASIC, 20, False)),
         ddr("NGP-ZZ-908", "NG-Rig 98", "2026-01-01", S12, "Operating", 44490, 44510, 6, 2, BASIC, ["2 directional hands", "2 MWD engineers"],
             part_b=("2026-01-01", "2026-01-01", BASIC, 6, False))),
     "given": ["Value the PD-210 charges as if the 12-1/4 inch sections were nominated (the nomination is decided elsewhere).",
               "The metres in these reports are what the reports state; no other day was drilled on these wells in 2025 or 2026 "
               "before the days shown."]},
    {"id": "DDS-H07", "documents": [
        dds_inv("MDS-90061", "NGP-ZZ-909", "NG-Rig 99", "2025-08-12", [("2025-08-05", "LH-713", "1", "377300.00", S12, "Operating")]),
        dds_inv("MDS-90062", "NGP-ZZ-909", "NG-Rig 99", "2025-08-20", [("2025-08-05", "LH-713", "1", "377300.00", S12, "Operating")])],
     "reports": reports(
         ddr("NGP-ZZ-909", "NG-Rig 99", "2025-08-03", S12, "Operating", 1650, 1800, 20, 2, BASIC, ["2 directional hands", "2 MWD engineers"],
             part_b=("2025-08-03", "2025-08-05", BASIC, 57, False)),
         ddr("NGP-ZZ-909", "NG-Rig 99", "2025-08-04", S12, "Operating", 1800, 1950, 20, 2, BASIC, ["2 directional hands", "2 MWD engineers"],
             part_b=("2025-08-03", "2025-08-05", BASIC, 57, False)),
         ddr("NGP-ZZ-909", "NG-Rig 99", "2025-08-05", S12, "Operating", 1950, 2010, 17, 2, BASIC, ["2 directional hands", "2 MWD engineers"],
             part_b=("2025-08-03", "2025-08-05", BASIC, 57, False), part_e=("MWD collar", 57)))},
]


# ------------------------------------------------------------------------------------ real slices of the population
def _snapshot_rows(rel: str) -> list[dict]:
    from audit.common import SNAPSHOT
    with (SNAPSHOT / rel).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _mask(text: str) -> str:
    """Personal names on signature lines become <signature> (tools/g3_cases.py convention; placeholders kept as printed)."""
    import re
    out = []
    for ln in text.splitlines():
        m = re.match(r"^((?:Signed|Countersigned)[^:]*:)\s*(.*)$", ln)
        if m and not re.fullmatch(r"[_\s]*", m.group(2)):
            ln = f"{m.group(1)} <signature>"
        out.append(ln)
    return "\n".join(out) + "\n"


def unmask(text: str) -> str:
    """The inverse the engine side applies (g3_case_compare.unmask): a masked name is a name, so the line is signed."""
    return text.replace("<signature>", "A. Signatory")


def real_slice(hid: str, line_refs: list[str], given: list[str] | None = None) -> dict:
    """Selected lines of the pinned population with their own headers (as billed) and the records/reports they cite."""
    from audit.common import SNAPSHOT
    cw = hid.startswith("CW")
    lines = [r for r in _snapshot_rows("civilwork/invoices/application_lines.csv" if cw else "drilling_services/invoices/invoice_lines.csv")
             if r["line_ref"] in line_refs]
    key = "application_no" if cw else "invoice_no"
    heads = {r[key]: r for r in _snapshot_rows("civilwork/invoices/applications.csv" if cw else "drilling_services/invoices/invoices.csv")}
    docs = []
    for no in dict.fromkeys(r[key] for r in lines):
        docs.append({"header": heads[no], "lines": [r for r in lines if r[key] == no]})
    texts = {}
    if cw:
        for r in lines:
            if r["record_ref"]:
                texts[r["record_ref"]] = _mask((SNAPSHOT / "civilwork" / "records" / f"{r['record_ref']}.txt").read_text())
    else:
        by_report = {}
        for p in sorted((SNAPSHOT / "drilling_services" / "records").iterdir()):
            first = p.read_text().split("\n", 2)[1] if p.suffix == ".txt" else ""
            if first.startswith("Report: "):
                by_report[first[len("Report: "):].strip()] = p
        for r in lines:
            p = by_report[r["report_ref"]]
            texts[p.stem] = _mask(p.read_text())
    return {"id": hid, "documents": docs, "records" if cw else "reports": texts, "given": given or [], "real": True}


REAL = [
    ("CW-R01", ["PA-00111-03", "PA-00111-12"], None),
    ("CW-R02", ["PA-00059-01", "PA-00309-05", "PA-00431-07"], None),
    ("CW-R03", ["PA-00801-02", "PA-00801-05"],
     ["Line PA-00801-02 is an annual-band item whose band depends on other lines not in this history: do not value it; "
      "state only whether it is payable and its allowed quantity."]),
    ("DDS-R01", ["MDS-01380-006", "MDS-01438-039"],
     ["The Daily Drilling Reports supplied for well NGP-QA-171 run on consecutive days from 09-May-2026 to 13-Jun-2026 "
      "(36 reports, none missing); only the reports the two lines cite are reproduced here."]),
    ("DDS-R02", ["MDS-00476-045", "MDS-00476-051"], None),
]


def real_histories() -> list[dict]:
    return [real_slice(hid, refs, given) for hid, refs, given in REAL]


def packet_view(h: dict, contract: str) -> dict:
    """What a reader receives: the claims, the evidence texts and any stated premise. Nothing about expected results."""
    out = {"id": h["id"], "contract": contract, "documents": h["documents"], "given": h.get("given", [])}
    out["records" if contract == "CW" else "reports"] = h.get("records" if contract == "CW" else "reports", {})
    return out


META = {   # what each history is built to exercise (NOT given to readers; used by the report and the coverage check)
    "CW-H01": "bands: execution-date order across applications (not application number or submission); one line crossing both edges",
    "CW-H02": "bands: same-date lines of one item in different work areas at a band edge (tie order)",
    "CW-H03": "bands: Contract Year boundary 2026-01-04/05 (Q12 reset vs no reset); a line crossing the top edge",
    "CW-H04": "bands: which quantities advance the count - a rejected wrong-unit line and an unpaid-for-want-of-record line (Q6)",
    "CW-H05": "duplicate measurement across applications (Cl.44) before the daily limit (Cl.31); other area; next day; single line over the limit",
    "CW-H06": "exclusion A.14.020 within two days of A.14.010 on the same work area: day 0/1/2/3 and another area (Cl.32, Sch 4 Part 5, P19)",
    "CW-H07": "exclusion E.51.020 on a surfacing day (P21), other area, and the one-day limit (Cl.19; Sch 4 Part 4)",
    "CW-H08": "duplicate measurement: 'later' by submission when the application numbers run the other way; two in one application; weekly record reused (Q7)",
    "CW-H09": "A3 retrospective difference on protected work; issue-day submissions; recipient under 'on or after' (31A) vs 'after' (A3) (Q1)",
    "CW-H10": "retention 5% rounded down per application; 45A release of half the earlier retention on the first application after 2026-09-30, once",
    "CW-H11": "P23 after a missing record under Q3 A: the amount is never both excluded and deducted",
    "CW-H12": "A3 difference on a band item that crosses a band edge; Contract Year for a later line (Q12); recipient",
    "DDS-H01": "Cl.29 service charged twice for a well-day across invoices; Cl.22 daily limit across invoices",
    "DDS-H02": "PD-210 split by depth interval (allowed) and an overlapping interval charged again (Cl.23, Cl.29)",
    "DDS-H03": "run events once per run across invoices (DD-111 last day, LW-420 first day); HC-630 per BHA run vs count (Q5 residual)",
    "DDS-H04": "well events: first day, last day, once per well, LW-430 only where LWD was run (Cl.27)",
    "DDS-H05": "A3 difference on protected invoices (incl. S2 discount); recipient: contract-wide on/after (36A), after (A3), per well (Q1 C)",
    "DDS-H06": "annual footage per well and Contract Year: 40,000 m edge, which metres count (Q11), reset on 1 January",
    "DDS-H07": "loss charged twice for one tool lost (Cl.31 once, Cl.29)",
    "CW-R01": "population: E.52.010 twice for S-01 on 2025-03-21 in PA-00111 (Cl.44 within one application)",
    "CW-R02": "population: one weekly dewatering record (DW-00004) cited by three A.16.010 lines in three applications (Q7)",
    "CW-R03": "population: A.14.020 measured the same day as A.14.010 on the same work area (P19 'within two days of')",
    "DDS-R01": "population: MB-701 charged on the well's first day and again on a later day (Cl.27 once, first day)",
    "DDS-R02": "population: MW-301 charged twice for one well-day in one invoice (Cl.29, Cl.22)",
}


def _csv(fields, rows) -> str:
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return out.getvalue()


def materialize(h: dict, root: Path) -> Path:
    """A snapshot directory holding only this history, laid out as the pinned snapshot is (claims CSVs, record and report
    files, submission template), for the production G2 loader (audit.build.build(snapshot=root))."""
    cw = h["id"].startswith("CW")
    docs = h["documents"]
    (root / "civilwork" / "invoices").mkdir(parents=True, exist_ok=True)
    (root / "civilwork" / "records").mkdir(parents=True, exist_ok=True)
    (root / "drilling_services" / "invoices").mkdir(parents=True, exist_ok=True)
    (root / "drilling_services" / "records").mkdir(parents=True, exist_ok=True)
    (root / "civilwork" / "invoices" / "applications.csv").write_text(_csv(CW_HEAD, [d["header"] for d in docs] if cw else []))
    (root / "civilwork" / "invoices" / "application_lines.csv").write_text(_csv(CW_LINE, [l for d in docs for l in d["lines"]] if cw else []))
    (root / "drilling_services" / "invoices" / "invoices.csv").write_text(_csv(DDS_HEAD, [] if cw else [d["header"] for d in docs]))
    (root / "drilling_services" / "invoices" / "invoice_lines.csv").write_text(_csv(DDS_LINE, [] if cw else [l for d in docs for l in d["lines"]]))
    for name, text in (h.get("records") or {}).items():
        (root / "civilwork" / "records" / f"{name}.txt").write_text(unmask(text))
    for name, text in (h.get("reports") or {}).items():
        (root / "drilling_services" / "records" / f"{name}.txt").write_text(unmask(text))
    ids = [d["header"]["application_no" if cw else "invoice_no"] for d in docs]
    (root / "submission_template.csv").write_text("invoice_id,flagged,error_category,expected_total_cents,billed_total_cents,confidence\n"
                                                  + "".join(f"{i},,,,,\n" for i in ids))
    return root


def load_packets() -> dict:
    """The committed packets (what the readers received), by history id - the engine side materializes these."""
    out = {}
    for name in ("packet_cw.jsonl", "packet_dds.jsonl"):
        for ln in (OUT / name).read_text().splitlines():
            h = json.loads(ln)
            out[h["id"]] = h
    return out


def histories() -> dict:
    return {h["id"]: h for h in CW_HISTORIES + DDS_HISTORIES + real_histories()}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    real = real_histories()
    for name, hs, c in (("packet_cw.jsonl", CW_HISTORIES + [h for h in real if h["id"].startswith("CW")], "CW"),
                        ("packet_dds.jsonl", DDS_HISTORIES + [h for h in real if h["id"].startswith("DDS")], "DDS")):
        (OUT / name).write_text("".join(json.dumps(packet_view(h, c), ensure_ascii=False) + "\n" for h in hs))
    (OUT / "meta.yaml").write_text(yaml.safe_dump({"purpose": META}, sort_keys=False, width=140))
    print(f"{len(CW_HISTORIES)} civil and {len(DDS_HISTORIES)} drilling synthetic histories, {len(real)} real slices, written to "
          f"{OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
