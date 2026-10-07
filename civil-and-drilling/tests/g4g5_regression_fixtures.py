"""Fixtures for the G4/G5 regression tests: raw same-format histories built with the G4
history helpers and run through the production pipeline (materialize -> G2 build -> G3 -> G4)."""
from decimal import Decimal

import g4_histories as H
import g4_history_compare as HC
from audit import g3_cw, g3_dds, g4_cw, g4_dds

S12 = H.S12
CREW = ["2 directional hands", "2 MWD engineers"]


def run(h):
    w, _g3, _st = HC.run_history(h)
    res = {"CW": g3_cw.run(w), "DDS": g3_dds.run(w)}
    st = {"CW": g4_cw.run(w, res["CW"]), "DDS": g4_dds.run(w, res["DDS"])}
    return w, res, st


def by_ref(st, c):
    return {g.g3.line_ref: g for g in st[c].lines.values()}


def amounts(g):
    if g.r.alternatives:
        return sorted({v["amount"] for v in g.r.alternatives.values()})
    return [g.r.amount]


def pd210_history(hid, well, days, charges, invoice_dates=None):
    """days: [(date, d0, d1)] reports; charges: [(invoice_no, service_date, from, to)] PD-210 at 98.70/58.15 as billed."""
    invs = {}
    for no, sd, f, t in charges:
        q = str(Decimal(t) - Decimal(f))
        invs.setdefault(no, []).append((sd, "PD-210", q, "98.70", S12, "Operating", str(f), str(t)))
    docs = [H.dds_inv(no, well, "NG-Rig 97", (invoice_dates or {}).get(no, "2025-08-20"), ls) for no, ls in invs.items()]
    reps = H.reports(*[H.ddr(well, "NG-Rig 97", d, S12, "Operating", d0, d1, 20, 1, H.BASIC, CREW,
                             part_b=(days[0][0], days[-1][0], H.BASIC, 20 * len(days), False)) for d, d0, d1 in days])
    return {"id": hid, "documents": docs, "reports": reps, "records": {}, "given": []}
