"""Load the four invoice CSVs as claims with provenance (G2). Billed values are claims, not evidence.

Each row keeps its file, CSV line number, the original strings of every field, and typed values parsed
exactly. A blank field is None; a field that fails to parse is None *and* an Unresolved entry. Input
assertions (uniqueness, header/line joins, template coverage) are recomputed and returned as facts.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field

from .common import SNAPSHOT, Queue, Source, dec, dmy_mon, iso_date

FILES = {
    "cw_headers": "civilwork/invoices/applications.csv",
    "cw_lines": "civilwork/invoices/application_lines.csv",
    "dds_headers": "drilling_services/invoices/invoices.csv",
    "dds_lines": "drilling_services/invoices/invoice_lines.csv",
}
ID_FIELD = {"cw_headers": "application_no", "cw_lines": "line_ref", "dds_headers": "invoice_no", "dds_lines": "line_ref"}
PARENT = {"cw_lines": ("application_no", "cw_headers"), "dds_lines": ("invoice_no", "dds_headers")}

# field -> parser (observed formats: civil ISO dates, drilling DD-Mon-YYYY); other fields stay strings.
TYPES = {
    "cw_headers": {"period_from": iso_date, "period_to": iso_date, "application_date": iso_date,
                   "application_total": dec, "retention": dec, "net_payable": dec, "adjustment": dec,
                   "retention_released": dec},
    "cw_lines": {"line_no": int, "work_date": iso_date, "quantity": dec, "rate_applied": dec, "amount": dec},
    "dds_headers": {"period_start": dmy_mon, "period_end": dmy_mon, "invoice_date": dmy_mon, "net_amount": dec,
                    "vat_amount": dec, "invoice_total": dec, "adjustment": dec},
    "dds_lines": {"line_no": int, "service_date": dmy_mon, "depth_from_m": dec, "depth_to_m": dec, "quantity": dec,
                  "unit_rate": dec, "amount": dec},
}
# Fields a row needs to be usable. A blank one is queued, never defaulted.
REQUIRED = {
    "cw_headers": ["application_no", "contract_ref", "subcontractor", "site", "site_zone", "period_from", "period_to",
                   "application_date", "application_total", "retention", "net_payable", "adjustment", "retention_released"],
    "cw_lines": ["line_ref", "application_no", "line_no", "work_date", "site", "item_code", "unit", "site_zone",
                 "quantity", "rate_applied", "amount", "night_work"],
    "dds_headers": ["invoice_no", "contract_ref", "contractor", "well_name", "rig", "field", "well_class", "period_start",
                    "period_end", "invoice_date", "net_amount", "vat_amount", "invoice_total", "adjustment"],
    "dds_lines": ["line_ref", "invoice_no", "line_no", "service_code", "unit", "quantity", "unit_rate", "amount"],
}
# DS-900 is an invoice-level charge with no day or report (DDS Cl.38). Every other drilling line
# must carry its day context; PD-210 must carry its depth interval (Cl.23, Cl.34).
DDS_DAY_FIELDS = ["service_date", "well_name", "hole_section", "day_status", "report_ref"]
DDS_DEPTH_FIELDS = ["depth_from_m", "depth_to_m"]


@dataclass
class Row:
    kind: str
    ident: str
    source: Source
    raw: dict[str, str]
    values: dict = field(default_factory=dict)

    ctx: str | None = None                      # run context id (audit.provenance)

@dataclass
class Claims:
    rows: dict[str, list[Row]]
    queue: Queue
    assertions: dict = field(default_factory=dict)


def required_fields(kind: str, raw: dict[str, str]) -> list[str]:
    req = list(REQUIRED[kind])
    if kind == "dds_lines" and raw.get("service_code") != "DS-900":
        req += DDS_DAY_FIELDS
        if raw.get("service_code") == "PD-210":
            req += DDS_DEPTH_FIELDS
    return req


def load(snapshot=SNAPSHOT, q: Queue | None = None) -> Claims:
    q = q or Queue()
    out: dict[str, list[Row]] = {}
    for kind, rel in FILES.items():
        rows = []
        with (snapshot / rel).open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for lineno, r in enumerate(reader, start=2):          # line 1 is the header
                ident = r.get(ID_FIELD[kind]) or f"{rel}:{lineno}"
                src = Source(rel, lineno, ",".join(r.values()))
                row = Row(kind, ident, src, dict(r))
                for k, v in r.items():
                    p = TYPES[kind].get(k)
                    if p is None:
                        row.values[k] = v
                    elif v.strip() == "":
                        row.values[k] = None
                    else:
                        try:
                            row.values[k] = p(v)
                        except Exception as e:  # noqa: BLE001 - recorded, never defaulted
                            row.values[k] = q.add(kind, ident, k, f"unparseable {v!r} ({type(e).__name__})", src)
                for k in required_fields(kind, r):
                    if (r.get(k) or "").strip() == "" and not q.has(ident, k):
                        q.add(kind, ident, k, "required field blank", src)
                rows.append(row)
        out[kind] = rows
    c = Claims(out, q)
    c.assertions = assertions(c, snapshot)
    return c


def assertions(c: Claims, snapshot=SNAPSHOT) -> dict:
    """Input assertions: not invoice classifications, just structural facts."""
    a = {}
    for kind, rows in c.rows.items():
        ids = [r.ident for r in rows]
        a[f"{kind}_unique_ids"] = len(ids) == len(set(ids))
    for kind, (fk, parent) in PARENT.items():
        parents = {r.ident for r in c.rows[parent]}
        children = {r.values[fk] for r in c.rows[kind]}
        a[f"{kind}_parents_exist"] = children <= parents
        a[f"{parent}_have_lines"] = parents <= children
    with (snapshot / "submission_template.csv").open(newline="") as fh:
        tmpl = [r["invoice_id"] for r in csv.DictReader(fh)]
    a["template_covers_all_headers"] = set(tmpl) == {r.ident for r in c.rows["cw_headers"]} | {r.ident for r in c.rows["dds_headers"]}
    return a
