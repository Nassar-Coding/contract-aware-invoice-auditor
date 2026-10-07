"""G5: one outcome per invoice (flag, category, expected judged total, confidence) from the G3 line results and the G4
state, under the decisions of spec/g5_decisions.yaml.

For every template invoice G5 assembles its lines (the G4-valued copies of the G3 results, never modified), fixes the
readings decided from source or adopted as policy, tests the invoice's own statements of facts assigned to documents not
supplied (Q9-3), and enumerates the readings still open (Q9-2) as scenarios. In each scenario the invoice is evaluated
check by check: every line's contract value against its billed value and its established findings, the header
arithmetic, the judged total (civil: sum of line amounts; drilling: services, DS-900, net, VAT, total - Cl.36-40), the
procedural checks, and the payment-only duties the contract gives this invoice (the A3 adjustment on its recipient,
the 45A release). The flag, category, expected total and confidence follow Q9-1..6. Nothing here reprices a line: G3
priced it under every admissible input and G4 applied the state; G5 selects and combines.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal

from .g3_core import headers_by_id, result_keys
from .g4_core import base, dims_of, local_dim

CENT = Decimal("0.01")
ZERO = Decimal("0")

# readings decided from source (spec/g5_decisions.yaml): the value every line is taken at
DECIDED = {"Q12": "A", "Q14": "A", "Q6": "A", "Q11": "A"}
# readings left open (Q9-2), with their values in the order the register lists them (the first is the working reading)
READING_ORDER = {
    "Q6-day0": ["A", "B"],
    "Q4": ["A", "B", "C", "counts"],
    "Q5-DD120": ["circulating (Cl.21, Sch 1)", "circulating or back-reaming (Sch 8 row)"],
    "Q5-RM530": ["back-reaming (Cl.30, Sch 1)", "circulating or back-reaming (Sch 8 row)"],
    "Q5-HC630": ["count (Cl.30)", "per BHA run (Sch 8)"],
    "order": ["Cl.30 wording (application number, then line)"],
    "earlier": [],
}
FACT_DIMS = {"class", "ground"}          # Q9-3: inputs assigned to documents not supplied
STANDS_DIMS = {"stands", "stands-run", "alloc"}   # kinds resolved by the Q7 C policy decision (local to their group)

# established findings -> root category (Q9-6), in the order categories are reported
CATEGORY = {
    "contract_ref_variant": "identity", "subcontractor_mismatch": "identity", "contractor_mismatch": "identity",
    "line_well_differs_from_invoice": "identity", "claim_field_missing": "identity",
    "out_of_term": "term",
    "submitted_late": "timing", "submitted_early": "timing", "outside_period": "timing",
    "record_missing": "evidence", "report_missing": "evidence", "required_part_missing": "evidence",
    "record_wrong_series": "evidence", "source_handling_not_certified": "evidence",
    "record_unsigned": "signature", "report_unsigned": "signature",
    "record_date_mismatch": "evidence_mismatch", "record_area_mismatch": "evidence_mismatch",
    "item_not_supported_by_record": "evidence_mismatch", "report_date_mismatch": "evidence_mismatch",
    "well_mismatch": "evidence_mismatch", "status_mismatch": "evidence_mismatch", "section_mismatch": "evidence_mismatch",
    "lost_tool_mismatch": "evidence_mismatch", "ground_differs_from_record": "evidence_mismatch",
    "spec_mismatch": "evidence_mismatch", "run_event_not_supported": "evidence_mismatch",
    "wrong_unit": "unit",
    "quantity_above_record": "quantity", "quantity_above_report": "quantity", "week_not_measurable": "quantity",
    "depths_differ_from_quantity": "quantity", "depths_missing": "quantity",
    "duplicate_measurement": "duplicate", "charged_twice": "duplicate", "run_event_repeated": "duplicate",
    "well_event_repeated": "duplicate", "loss_repeated": "duplicate",
    "not_chargeable_on_standby": "eligibility", "not_chargeable_on_operating": "eligibility",
    "tool_not_in_hole": "eligibility", "excluded_by_other_item": "eligibility", "well_event_not_on_its_day": "eligibility",
    "lwd_not_run": "eligibility", "not_performance_section": "eligibility", "section_not_nominated": "eligibility",
    "above_daily_limit": "limit",
    "rate_differs": "rate", "band_crossing_not_split": "rate",
    "discount": "discount", "discount_split": "discount", "discount_sign": "discount", "value_differs": "arithmetic",
    "amount_arithmetic": "arithmetic", "header_arithmetic": "arithmetic",
    "adjustment_omitted": "adjustment", "release_omitted": "adjustment", "retention_arithmetic": "arithmetic",
    "adjustment_differs": "adjustment", "adjustment_unsupported": "adjustment",
    "release_differs": "adjustment", "release_unsupported": "adjustment",
}
CATEGORY_ORDER = ["identity", "term", "timing", "evidence", "signature", "evidence_mismatch", "unit", "quantity", "duplicate",
                  "eligibility", "limit", "rate", "discount", "arithmetic", "adjustment"]
INFORMATIONAL = {"band_divided", "footage_band_divided"}      # the contract's own pricing state, not a defect
# G5-B06: breaches with no monetary consequence of their own. 'term' is not among them: out-of-term work is not payable
# (G3, closed) - a monetary consequence of the term, the cause of the value it changes
PROCEDURAL = {"identity", "timing"}
PAYMENT_ONLY = {"adjustment_omitted", "release_omitted", "retention_arithmetic", "adjustment_differs", "adjustment_unsupported",
                "release_differs", "release_unsupported"}
NOT_ESTABLISHED = {"engine_error", "input_unresolved", "no_admissible_result", "quantity_rule_missing",
                   "adjustment_not_established", "release_not_established"}


def payment_check(kind: str, billed: Decimal, expected: tuple, recipient: bool) -> list:
    """G5-B03: a payment field (the A3/31A/36A adjustment, the 45A release) reconciled to the amount G4's account gives
    this invoice in the scenario - (lo, hi) exact or a range across readings the account leaves open, (None, None) where
    the account is not established - not to zero versus nonzero. Not the recipient in the scenario: nothing is due."""
    lo, hi = expected
    if lo is None and hi is None:
        return [(f"{kind}_not_established", None)] if (recipient or billed != 0) else []
    if billed == lo == hi:
        return []
    if not recipient:
        return [(f"{kind}_unsupported", None)]
    # inside the range the account leaves open (an end of None: not bounded on that side - review findings B-1, C-1)
    if (lo is None or lo <= billed) and (hi is None or billed <= hi):
        return [(f"{kind}_not_established", None)]
    return [(f"{kind}_omitted" if billed == 0 else f"{kind}_differs", None)]


def half_even(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_EVEN)


def D(x) -> Decimal | None:
    return None if x is None or x == "" else Decimal(str(x))


@dataclass
class Invoice:
    contract: str
    id: str
    header: dict | None
    lines: list = field(default_factory=list)       # (key, claim values, G4Line)
    header_copies: tuple = ()


@dataclass
class Policy:
    """How G5 resolves each open item; the default is the adopted decision set. Alternatives change one field."""
    decided: dict = field(default_factory=lambda: dict(DECIDED))
    facts: str = "evidence"        # evidence (Q9-3 E, G5-B01) | default (C: the absent-document values only) | query (B: every
    #                                fact-dependent invoice flagged)
    q7c: str = "earlier"           # earlier (policy) | open (either charge stands)
    q9: str = "A"                  # A: any breach | B: only a changed judged total
    q2: str = "A"                  # A: adjustment outside the judged total | B: inside (DDS before DS-900/VAT)
    q1: str = "open"               # open (Q9-2 over A and B) | A | B
    force: dict = field(default_factory=dict)   # open reading -> the one value taken (alternative counts)


# ------------------------------------------------------------------------------------------------ assembly
def invoices(w, st: dict) -> dict:
    """{contract: {invoice id: Invoice}} for every header and every line of the claims (a line whose invoice number no
    single header carries is kept under its number, header None)."""
    out = {}
    for c, lk, hk, key in (("CW", "cw_lines", "cw_headers", "application_no"), ("DDS", "dds_lines", "dds_headers", "invoice_no")):
        heads, copies = headers_by_id(w.claims.rows[hk])
        inv = {}
        for no, h in heads.items():
            inv[no] = Invoice(c, no, h.values)
        for no, srcs in copies.items():
            inv[no] = Invoice(c, no, None, header_copies=srcs)
        for k, row in zip(result_keys(w.claims.rows[lk]), w.claims.rows[lk]):
            no = row.values.get(key)
            inv.setdefault(no, Invoice(c, no, None)).lines.append((k, row.values, st[c].lines[k]))
        out[c] = inv
    return out


def stated_facts(inv: Invoice, v: dict) -> dict:
    """The invoice's own statement of each fact input (Q9-3): the drilling header's well class, the civil line's ground."""
    out = {}
    if inv.contract == "DDS" and inv.header:
        out["class"] = inv.header.get("well_class") or None
    if inv.contract == "CW":
        g = (v.get("ground_class") or "").split(" ")[0]
        out["ground"] = g or None
    return out


# the value the supplied evidence establishes where the document a fact belongs to is not supplied (EXPORT-D, G5-B01):
# CW S4 (p10) 'a classification not recorded on the day of excavation shall be taken to be G2'; DDS P2, P3 (p11) the HPHT /
# Extended Reach factors apply 'where the call-off so states' - no supplied call-off states it; Cl.23 (p6) PD-210 only on a
# section the call-off nominates - no supplied call-off nominates one. Never the invoice's own statement.
DEFAULT_FACTS = {"class": "Standard", "ground": "G2"}
FALLBACK_NOMINATED = False


def nomination_group(v: dict) -> tuple | None:
    """The call-off nomination a PD-210 charge depends on: its well's section (Cl.23)."""
    return (v.get("well_name"), v.get("hole_section"))


def nominated_conditional(g) -> bool:
    return any(x["dimension"] == "nomination" for x in g.r.conditions)


def standing_map(w, st: dict, inv_date: dict) -> dict:
    """Policy decision Q7 C: of the admissible charges of one group, the one on the earliest-submitted invoice stands;
    within one invoice (or one submission date) the earliest line. For overlapping PD-210 intervals the same decision
    gives each contested segment to the earliest-submitted charge covering it. Each group's choice is its own local
    dimension (G5-B04). {line key: {dimension: (value, open)}} - open where the order is not established."""
    out = {}
    for c in st:
        lines = {g.g3.line_ref: (k, g) for k, g in st[c].lines.items()}

        def order(ref):
            no = ref.rsplit("-", 1)[0]
            return (inv_date.get((c, no)), no, ref)

        def earliest(cands, possible=()):
            # G4-B03: a candidate on an invoice whose submission date is not established may be the earliest - never
            # ordered last by a sentinel date; with one, which charge stands is not established (open), and so it is
            # where the earliest candidate is only possibly of the same well-day (its service date not established)
            dated = sorted((m for m in cands if order(m)[0]), key=order)
            undated = [m for m in cands if not order(m)[0]]
            first = dated[0] if dated else sorted(cands)[0]
            # a charge on another invoice submitted the same day: which invoice is earlier is not established (open)
            tie = bool(undated) or first in possible or any(
                order(m)[0] == order(first)[0] and order(m)[1] != order(first)[1] for m in dated)
            return first, tie
        for grp in getattr(st[c], "groups", []):
            gid = grp["group"]
            if gid.startswith("DDS-PD210"):
                if not grp.get("overlaps") or not grp.get("allocations"):
                    continue
                assign, tie = [], False
                for seg, refs in grp["overlaps"]:
                    f, t = seg.split("-", 1)
                    # review finding A-1: a charge whose date or depths are not established is a possible contestant;
                    # where it would stand, which charge keeps the segment is not established (open)
                    first, tie_s = earliest([r for r in refs if r in lines], grp.get("possible", []))
                    assign.append(((Decimal(f), Decimal(t)), first))
                    tie = tie or tie_s
                well, date = gid.split(":", 1)[1].split("|")
                dim = f"alloc@{well}/{date}"
                val = ";".join(f"{a}-{b}>{ref}" for (a, b), ref in sorted(assign)) or "none"
                for m in grp["members"]:
                    if m in lines:
                        out.setdefault(lines[m][0], {})[dim] = (val, tie)
                continue
            cands = [m for m in grp.get("candidates", []) if m in lines]
            if len(cands) < 2:
                continue
            dim = local_dim("stands-run" if gid.startswith("DDS-HC630") else "stands", gid)
            first, tie = earliest(cands, grp.get("possible", []))
            for m in cands:
                out.setdefault(lines[m][0], {})[dim] = (first, tie)
    return out


# ------------------------------------------------------------------------------------------------ line values
def options(g) -> list[tuple[dict, dict]]:
    r = g.r
    if r.alternatives:
        return [(dims_of(k), v) for k, v in r.alternatives.items()]
    return [({}, {"allowed_quantity": r.allowed_quantity, "amount": r.amount})]


def line_value(g, fixed: dict) -> tuple[Decimal | None, Decimal | None, bool]:
    """(quantity, amount, formed) of the line under a full assignment of its dimensions."""
    if g.r.payable is False:
        return ZERO, ZERO, True
    cands = [v for d, v in options(g) if all(fixed.get(k) == x for k, x in d.items())]
    vals = {(D(v.get("allowed_quantity")), D(v.get("amount"))) for v in cands}
    if len(vals) != 1:
        return None, None, False
    q, a = next(iter(vals))
    return q, a, a is not None


def scenario_breaches(g, fixed: dict, v: dict, contract: str) -> list[str]:
    """G4-B04: the checks that fail under this scenario only. (1) The breaches G4 carried on the scenario's alternative
    (a band line's displayed rate or arithmetic, wrong under some readings); (2) a displayed rate G3 deferred because
    the line has several admissible rates, completed against the rate of the scenario's alternative - a correct amount
    never makes a wrong displayed rate pass."""
    if g.r.payable is False:
        return []
    cands = [x for d, x in options(g) if all(fixed.get(k) == y for k, y in d.items())]
    if not cands:
        return []
    out = sorted(set.intersection(*[set(x.get("breaches") or ()) for x in cands]))
    deferred = any(c.check == "rate" and c.status == "unresolved" and c.finding == "rate_differs" for c in g.g3.checks)
    if deferred and not any(x.family == "band_rate" for x in g.state):
        rates = {D(x.get("unit_rate")) for x in cands}
        ra = D(v.get("rate_applied") if contract == "CW" else v.get("unit_rate"))
        if len(rates) == 1 and None not in rates and ra is not None and ra != next(iter(rates)) and \
                any(D(x.get("allowed_quantity")) for x in cands):
            out.append("rate_differs")
    return out


def line_dims(g) -> set:
    return {k for d, _v in options(g) for k in d}


# ------------------------------------------------------------------------------------------------ evaluation
@dataclass
class Scenario:
    label: dict
    wrong: bool
    total: Decimal | None
    reasons: list          # (finding, line_ref or None)
    formed: bool
    line_values: dict
    bounds: dict = field(default_factory=dict)      # ref -> (lo, hi or None) of a line with no single value (G5-B02)
    ev: dict = field(default_factory=dict)          # the evidence scenario (values of the unsupplied documents)
    every_value: list = field(default_factory=list)  # reasons, a ground line's being those common to all its grounds
    grounds: dict = field(default_factory=dict)      # ref -> the ground class a civil line is valued under


def established(g) -> list[str]:
    fs = [x.finding for x in g.g3.checks if x.status == "finding" and x.finding]
    fs += [x.finding for x in g.state if x.status == "finding" and x.finding]
    return [f for f in dict.fromkeys(fs) if f not in INFORMATIONAL]


def a3_recipients(st: dict, contract: str) -> dict:
    """{reading label: [candidate recipients]} of the A3 difference (Q1), and the difference total per reading."""
    out = {}
    for a in st[contract].adjustments:
        for label, rec in a["recipient"].items():
            q = label.split(" ")[0]          # 'Q1:A', 'Q1:B', 'Q1:C'
            if q == "Q1:C":
                continue                     # rejected from source (spec/g5_decisions.yaml Q1)
            if isinstance(rec, dict) and "tie" in rec:
                out[q] = list(rec["tie"])
            elif isinstance(rec, dict) and "not_established" in rec:
                out[q] = list(rec["not_established"])      # G4-B03: a submission date not established - open like a tie
            elif rec:
                out[q] = [rec]
        out["_total"] = a["total"]
    return out


ABSENT_DOCUMENT = {"section_not_nominated"}       # a breach that exists only through an unsupplied document's value


def favourable(scen: list) -> list:
    """The evidence scenarios most favourable to the invoice (EXPORT-E): those under which it is right; where none, those
    whose established breaches are not a strict superset of another's, then those with the fewest breaches that rest
    only on an unsupplied document's value (never the preferred explanation of a defect: missing evidence is not a
    breach, Q9-3 E)."""
    right = [x for x in scen if not x.wrong]
    if right:
        return right
    sets = [frozenset(r for r in x.reasons if r[0] not in NOT_ESTABLISHED) for x in scen]
    mins = [x for x, a in zip(scen, sets) if not any(b < a for b in sets)]
    k = min(sum(1 for r in x.reasons if r[0] in ABSENT_DOCUMENT) for x in mins)
    return [x for x in mins if sum(1 for r in x.reasons if r[0] in ABSENT_DOCUMENT) == k]


def _rank(line: tuple, billed) -> tuple:
    """Order of a line's admissible values from the most favourable to the line: fewest breaches, then formed, then the
    value nearest its bill (EXPORT-E)."""
    rs, _q, a, ok = line
    bad = [f for f in rs if f not in NOT_ESTABLISHED]
    return (len(bad), 0 if ok else 1, abs(a - billed) if ok and a is not None else ZERO)


class Engine:
    def __init__(self, w, st: dict, policy: Policy | None = None):
        self.w, self.st = w, st
        self.policy = policy or Policy()
        self.inv = invoices(w, st)
        self.inv_date = {}
        for c, invs in self.inv.items():
            for no, i in invs.items():
                if i.header:
                    d = i.header.get("application_date" if c == "CW" else "invoice_date")
                    self.inv_date[(c, no)] = str(d) if d else None
        self.stands = standing_map(w, st, self.inv_date)
        self.a3 = {c: a3_recipients(st, c) for c in ("CW", "DDS")}
        rel = (st["CW"].retention or {}).get("release") or {}
        self.release = rel

    # -------------------------------------------------------------------------------------------- dims
    def fixed_for(self, inv: Invoice, key, v, g, readings: dict) -> dict:
        """The decided readings, the scenario's open readings and the Q7 C policy choices for this line. Facts assigned
        to documents not supplied are NOT fixed here - never from the invoice's statement (G5-B01): evaluate() takes
        them from the evidence scenario."""
        p = self.policy
        fixed = dict(p.decided)
        fixed.update(readings)
        dims = line_dims(g)
        for d in {x for x in dims if base(x) in STANDS_DIMS}:
            s = self.stands.get(key, {}).get(d)
            if p.q7c == "earlier" and s is not None and not s[1]:
                fixed[d] = s[0]
            # else: left to the readings (open)
        return fixed

    def open_dims(self, inv: Invoice) -> dict:
        """The open readings the invoice's lines depend on, with their values in register order."""
        p = self.policy
        out = {}
        for key, v, g in inv.lines:
            for d, _val in options(g):
                if any(d.get(k, x) != x for k, x in p.decided.items()):
                    continue            # an option under a reading not adopted: its local choices are not open
                for k, x in d.items():
                    if k in p.decided or base(k) in FACT_DIMS:
                        continue
                    if base(k) in STANDS_DIMS:
                        s = self.stands.get(key, {}).get(k)
                        if p.q7c == "earlier" and s is not None and not s[1]:
                            continue
                    out.setdefault(k, [])
                    if x not in out[k]:
                        out[k].append(x)
        for k, vals in out.items():
            pref = READING_ORDER.get(base(k), [])
            out[k] = [x for x in pref if x in vals] + sorted(x for x in vals if x not in pref)
            f = p.force.get(k, p.force.get(base(k)))
            if f is not None and (f in vals or k in p.force):
                out[k] = [f]
        return out

    def q1_scenarios(self, inv: Invoice) -> list:
        """Q1 recipient scenarios this invoice takes part in: None (not the recipient) or the reading under which it is."""
        p = self.policy
        cands = []
        for q, recs in self.a3[inv.contract].items():
            if q.startswith("_"):
                continue
            if p.q1 != "open" and q != f"Q1:{p.q1}":
                continue
            if inv.id in recs:
                cands.append(q + (f" (tie of {len(recs)})" if len(recs) > 1 else ""))
        return cands

    # -------------------------------------------------------------------------------------------- one scenario
    def evaluate(self, inv: Invoice, readings: dict, recipient: str | None, ev: dict | None = None,
                 release_to: bool = False) -> Scenario:
        """One scenario: the readings, the Q1 recipient and an evidence scenario ev = {'class': a well class or None,
        'nominated': {section group: bool}, 'ground': 'fallback' | 'consistent'} for the facts whose documents are not
        supplied. 'consistent' gives each civil line (its ground is its own fact) a ground class under which it is right,
        where one exists - the question it answers is whether ANY admissible evidence makes the invoice right; the
        invoice's statement of a fact plays no part (G5-B01)."""
        ev = ev or {"class": DEFAULT_FACTS["class"], "nominated": {}, "ground": "fallback"}
        p = self.policy
        reasons = []
        every_value = []           # (ground line, the breaches common to all its grounds)
        chosen = {}
        vals = {}
        bounds = {}
        ds_lines = []
        formed = True
        billed_lines = ZERO
        svc_exp, svc_bill, ds_bill = ZERO, ZERO, ZERO
        exp_total = ZERO
        for key, v, g in inv.lines:
            ref = v.get("line_ref") or key
            billed = D(v.get("amount")) or ZERO
            billed_lines += billed
            code = g.g3.code
            if inv.contract == "DDS" and code == "DS-900":
                ds_bill += billed
                ds_lines.append((billed, ref, v))
                continue
            if inv.contract == "DDS":
                svc_bill += billed          # the invoice's own services, valued or not (its DS-900 check, Cl.38)
            fixed = self.fixed_for(inv, key, v, g, readings)
            dims = line_dims(g)
            if "class" in dims:
                fixed["class"] = ev["class"]
            every = None
            if "ground" in dims:
                fixed["ground"] = DEFAULT_FACTS["ground"]
                if ev["ground"] == "consistent":
                    # a civil line's ground is its own fact (G5-B01): the admissible ground most favourable to the line -
                    # one under which it is right where one exists, else the one leaving it the fewest breaches, then the
                    # value nearest its bill, then G2 (EXPORT-E; review findings B-3, C-2) - so that the line's reported
                    # breaches and its value are those of ONE admissible ground; the breaches common to every ground are
                    # kept for disclosure (every_value)
                    grounds = sorted({d["ground"] for d, _x in options(g) if "ground" in d},
                                     key=lambda x: (x != DEFAULT_FACTS["ground"], x))
                    per = {gv: self._line_reasons(g, {**fixed, "ground": gv}, v, billed, inv.contract) for gv in grounds}
                    fixed["ground"] = min(grounds, key=lambda gv: _rank(per[gv], billed))
                    every = set.intersection(*[set(x[0]) for x in per.values()]) if per else None
                chosen[ref] = fixed["ground"]
            if nominated_conditional(g) and not ev["nominated"].get(nomination_group(v), True):
                # evidence scenario: the section is not nominated - the PD-210 charge is not chargeable (Cl.23)
                vals[ref] = ZERO
                for f in established(g):
                    reasons.append((f, ref))
                if billed != 0:
                    reasons.append(("section_not_nominated", ref))
                continue
            rs, q, a, ok = self._line_reasons(g, fixed, v, billed, inv.contract)
            vals[ref] = a
            reasons += [(f, ref) for f in rs]
            if every is not None:
                every_value.append((ref, sorted(every)))
            if not ok:
                formed = False
                reasons.append(("input_unresolved", ref))
                # G5-B02: never the billed amount, never zero: the line's admissible values consistent with the scenario
                # bound it; where no admissible value is established it may be not payable at all (0) and has no upper
                # bound
                amts = [D(x.get("amount")) for d, x in options(g) if all(fixed.get(k, y) == y for k, y in d.items())]
                bounds[ref] = (min(amts), max(amts)) if amts and None not in amts else (ZERO, None)
                continue
            if inv.contract == "DDS":
                svc_exp += a
            else:
                exp_total += a
        h = inv.header or {}
        if inv.header is None:
            reasons.append(("claim_field_missing", None))
        if inv.contract == "CW":
            billed_total = D(h.get("application_total"))
            if billed_total is not None and billed_total != billed_lines:
                reasons.append(("header_arithmetic", None))
            if billed_total is not None:
                ret = (billed_total * Decimal("0.05")).quantize(CENT, rounding=ROUND_DOWN)
                if D(h.get("retention")) != ret:
                    reasons.append(("retention_arithmetic", None))
                net = billed_total + (D(h.get("adjustment")) or ZERO) - (D(h.get("retention")) or ZERO) + \
                    (D(h.get("retention_released")) or ZERO)
                if D(h.get("net_payable")) != net:
                    reasons.append(("retention_arithmetic", None))
            reasons += payment_check("release", D(h.get("retention_released")) or ZERO,
                                     self._release_amount(readings) if release_to else (ZERO, ZERO), release_to)
            total = exp_total
        else:
            billed_total = D(h.get("invoice_total"))
            # G5-B05 - DS-900 (Cl.38 p8; P11): a SINGLE NEGATIVE charge of 4% of the services above 250,000, rounded half
            # to even; none at or below it. Checked on the invoice's own services: cardinality, sign, amount, threshold,
            # rounding; its contribution to net and VAT by the header check below. A zero DS-900 line is no charge. The
            # ordinary-service obligations (date, report, unit, section, quantity x rate) are not imposed on it: Cl.38 is
            # a special amount rule, not a Schedule 1 service (spec/g5_decisions.yaml totals).
            due = -half_even((svc_bill - 250000) * Decimal("0.04")) if svc_bill > 250000 else ZERO
            charged = [(a, ref) for a, ref, _v in ds_lines if a != 0]
            if len(charged) > 1:
                reasons.append(("discount_split", None))
            reasons += [("discount_sign", ref) for a, ref in charged if a > 0]
            if ds_bill != due:
                reasons.append(("discount", None))
            well = h.get("well_name")
            reasons += [("line_well_differs_from_invoice", ref) for _a, ref, v in ds_lines
                        if v.get("well_name") and well and v.get("well_name") != well]
            if D(h.get("net_amount")) != billed_lines or D(h.get("vat_amount")) != half_even((D(h.get("net_amount")) or ZERO) * Decimal("0.15")) \
                    or billed_total != (D(h.get("net_amount")) or ZERO) + (D(h.get("vat_amount")) or ZERO):
                reasons.append(("header_arithmetic", None))
            ds = -half_even((svc_exp - 250000) * Decimal("0.04")) if svc_exp > 250000 else ZERO
            adj = ZERO
            if recipient and p.q2 == "B":
                lo_, hi_ = self._a3_amount(inv.contract, readings)
                if lo_ is None or lo_ != hi_:
                    formed = False           # Q2 B: the adjustment inside the total is not established - never an endpoint
                    reasons.append(("adjustment_not_established", None))
                else:
                    adj = lo_
            net = svc_exp + adj + ds
            total = net + half_even(net * Decimal("0.15"))
        reasons += payment_check("adjustment", D(h.get("adjustment")) or ZERO,
                                 self._a3_amount(inv.contract, readings) if recipient else (ZERO, ZERO), bool(recipient))
        if recipient and p.q2 == "B" and inv.contract == "CW":
            lo_, hi_ = self._a3_amount(inv.contract, readings)
            if lo_ is None or lo_ != hi_:
                formed = False
                reasons.append(("adjustment_not_established", None))
            else:
                total = total + lo_
        if billed_total is not None and formed and total != billed_total:
            if not any(CATEGORY.get(r[0]) not in PROCEDURAL | {"adjustment"} for r in reasons):
                reasons.append(("header_arithmetic", None))
        wrong = self._wrong(reasons, total, billed_total, formed)
        ground_lines = {ref for ref, _x in every_value}
        every = [r for r in reasons if r[1] not in ground_lines] + [(f, ref) for ref, fs in every_value for f in fs]
        return Scenario({**readings, **({"Q1": recipient} if recipient else {})}, wrong, total if formed else None,
                        reasons, formed, vals, bounds, ev, every, chosen)

    def _line_reasons(self, g, fixed: dict, v: dict, billed, contract: str) -> tuple:
        """(breaches, quantity, amount, formed) of one line under a full assignment: its established findings, the
        checks that fail under this scenario only (scenario_breaches), and - where its value differs from its bill - the
        obligation each differing dimension violates, unless a breach already present explains that dimension (a value
        removed by an established finding needs no other cause)."""
        q, a, ok = line_value(g, fixed)
        rs = list(established(g))
        rs += [f for f in scenario_breaches(g, fixed, v, contract) if f not in rs]
        if ok and a != billed:
            cats = {CATEGORY.get(f) for f in rs} - {None}
            removed = a == 0 and bool(cats - PROCEDURAL)
            if not removed:
                rs += [f for f in self._causes(g, fixed, v, q, a, billed, contract, cats) if f not in rs]
        return rs, q, a, ok

    def _causes(self, g, fixed: dict, v: dict, q, a, billed, contract: str, explained: set) -> list:
        """G5-B06 (with review finding C-2): the obligations a line's differing value violates in this scenario, one per
        dimension that differs and that no breach already on the line explains (explained: their categories) - the
        quantity (the contract allows less than billed: quantity_above_record), the displayed rate (not the scenario's
        rate: rate_differs), the displayed arithmetic (quantity x displayed rate is not the amount: amount_arithmetic);
        a state rule left open across scenarios is the cause of the value it changes; where nothing else differs, the
        division at a band edge (band_crossing_not_split) or value_differs. Never 'rate' by default: a correctly priced
        line whose value another rule changes keeps that rule's cause."""
        open_f = [x.finding for x in g.state if x.status == "unresolved" and x.finding and x.finding not in INFORMATIONAL
                  and x.finding in CATEGORY and x.finding not in ("rate_differs", "amount_arithmetic")]
        if open_f:
            return [open_f[0]]
        out = []
        bq = D(v.get("quantity"))
        if q is not None and bq is not None and q < bq and not explained & {"quantity", "limit", "unit", "duplicate"}:
            out.append("quantity_above_record")
        ra = D(v.get("rate_applied") if contract == "CW" else v.get("unit_rate"))
        cands = [x for d, x in options(g) if all(fixed.get(k, y) == y for k, y in d.items())]
        rates = {D(x.get("unit_rate")) for x in cands} - {None}
        if ra is not None and len(rates) == 1 and ra not in rates and not explained & {"rate", "evidence_mismatch"}:
            out.append("rate_differs")
        amt = D(v.get("amount"))
        if ra is not None and bq is not None and amt is not None and bq * ra != amt and "arithmetic" not in explained:
            out.append("amount_arithmetic")
        if not out and not explained - PROCEDURAL:
            # rate, quantity and arithmetic as displayed are right: the value differs by a division at a band edge (no
            # single rate) or for a reason no single check names - reported as such, never given a rate category
            out.append("band_crossing_not_split" if not rates else "value_differs")
        return out

    def _account(self, by: dict, readings: dict) -> tuple:
        """(lo, hi) of an account {reading label: {min, max}} under the scenario's decided and open readings; hi None
        where the account is bounded only from below (review finding B-1)."""
        fixed = {**self.policy.decided, **readings}
        hit = [v for k, v in by.items() if all(fixed.get(d, x) == x for d, x in dims_of(k or None).items())]
        if not hit:
            return None, None
        his = [v["max"] for v in hit]
        return min(Decimal(v["min"]) for v in hit), (None if None in his else max(Decimal(x) for x in his))

    def _a3_amount(self, contract: str, readings: dict) -> tuple:
        t = self.a3[contract].get("_total")
        if not t:
            return ZERO, ZERO
        lo, hi = self._account(t.get("by_reading", {}), readings)
        if t.get("lines_not_established"):
            # a line's difference is not established: the account is bounded only on the side no such line can move
            # (G4 open_sides, from each line's rate change; review findings B-2, C-1) - never taken as exact, never erased
            return (None if t.get("unknown_lo_open", True) else lo), (None if t.get("unknown_hi_open", True) else hi)
        return lo, hi

    def _release_amount(self, readings: dict) -> tuple:
        r = (self.release or {}).get("released")
        if r is None:
            return None, None
        if isinstance(r, str):
            return Decimal(r), Decimal(r)
        return self._account(r.get("by_reading", {}), readings)

    def release_scenarios(self, inv: Invoice) -> list:
        """G5-B03: whether this application receives the 45A release, per G4's recipient - one application, a same-day
        tie or a set whose submission dates are not established (each candidate the recipient in one scenario)."""
        if inv.contract != "CW" or not self.release:
            return [False]
        rec = self.release.get("recipient")
        if rec == inv.id:
            return [True]
        cands = rec.get("tie") or rec.get("not_established") if isinstance(rec, dict) else None
        return [True, False] if cands and inv.id in cands else [False]

    def _wrong(self, reasons, total, billed_total, formed) -> bool:
        if self.policy.q9 == "B":
            return formed and billed_total is not None and total != billed_total
        # an input that cannot be formed is not itself a failed check (Q9-4: flagged only when a check fails)
        return bool([r for r in reasons if r[0] not in NOT_ESTABLISHED])

    # -------------------------------------------------------------------------------------------- evidence
    def classes_of(self, inv: Invoice) -> list:
        memo = self.__dict__.setdefault("_cl", {})
        if inv.id not in memo:
            memo[inv.id] = sorted({d["class"] for _k, _v, g in inv.lines for d, _x in options(g) if "class" in d}) or [None]
        return memo[inv.id]

    def nomination_groups(self, inv: Invoice) -> list:
        memo = self.__dict__.setdefault("_ng", {})
        if inv.id not in memo:
            memo[inv.id] = sorted({nomination_group(v) for _k, v, g in inv.lines if nominated_conditional(g)}, key=str)
        return memo[inv.id]

    def rows_of_group(self, contract: str, gk) -> list:
        """The invoices with a PD-210 charge on a section (one nomination), from an index built once."""
        idx = self.__dict__.setdefault("_rg", {})
        if contract not in idx:
            idx[contract] = {}
            for i in self.inv[contract].values():
                for g_ in self.nomination_groups(i):
                    idx[contract].setdefault(g_, []).append(i)
        return idx[contract].get(gk, [])

    def rows_of_well(self, contract: str, well) -> list:
        """The class-bearing invoices of a well, from an index built once."""
        idx = self.__dict__.setdefault("_rw", {})
        if contract not in idx:
            idx[contract] = {}
            for i in self.inv[contract].values():
                if i.header and self.classes_of(i) != [None]:
                    idx[contract].setdefault(i.header.get("well_name"), []).append(i)
        return idx[contract].get(well, [])

    def evidence_scenarios(self, inv: Invoice, classes: list) -> list[dict]:
        """Every admissible value of the facts whose documents are not supplied (Q9-3 E): each well class the lines were
        priced under, each nominated / not-nominated combination of the PD-210 sections; ground per civil line through
        'consistent' (whether some ground class makes each line right)."""
        groups = self.nomination_groups(inv)
        noms = [dict(zip(groups, c)) for c in itertools.product(*[[True, False] for _ in groups])] or [{}]
        return [{"class": c, "nominated": n, "ground": "consistent"} for c in classes for n in noms]

    def fallback(self, inv: Invoice, cls=None) -> dict:
        """EXPORT-D: the values the supplied evidence establishes for absent documents (DEFAULT_FACTS)."""
        return {"class": cls if cls is not None else (DEFAULT_FACTS["class"] if self.classes_of(inv) != [None] else None),
                "nominated": {gk: FALLBACK_NOMINATED for gk in self.nomination_groups(inv)}, "ground": "fallback"}

    def work_cons(self, inv: Invoice) -> list:
        """The invoice's evidence scenarios under the working reading, not the A3 recipient (memo)."""
        memo = self.__dict__.setdefault("_wcons", {})
        if inv.id not in memo:
            od = self.open_dims(inv)
            w0 = {k: v[0] for k, v in od.items()}
            memo[inv.id] = [self.evaluate(inv, w0, None, e) for e in self.evidence_scenarios(inv, self.classes_of(inv))]
        return memo[inv.id]

    def right_classes(self, inv: Invoice) -> set:
        """Well classes under which some admissible evidence makes this invoice right under the working reading (memo)."""
        return {s.ev["class"] for s in self.work_cons(inv) if not s.wrong}

    def fit(self, inv: Invoice, fact) -> set:
        """The values of one unsupplied fact ('class', or a section's nomination group) under which the invoice is at its
        most favourable (favourable(): right, else the fewest breaches) under the working reading."""
        return {s.ev["class"] if fact == "class" else s.ev["nominated"].get(fact) for s in favourable(self.work_cons(inv))}

    def _shared_value(self, rows: list, fact, values: list):
        """EXPORT-E, one value of a shared unsupplied fact for every invoice sharing it (Cl.4 'the well class stated in the
        call-off governs the whole well'; Cl.23 a section's nomination): among the values that keep every invoice that
        can be right right (so an unflagged invoice exports its own total), the value under which the fewest of those
        invoices are at less than their most favourable; ties to the first value in the order given (class: Standard,
        the absent-document value, first; nomination: nominated first - the value under which a PD-210 charge exists at
        all, since missing evidence is not a breach). It chooses only the values behind the exported total and the
        reported findings: the flag never depends on it."""
        right = [{s.ev["class"] if fact == "class" else s.ev["nominated"].get(fact) for s in self.work_cons(i) if not s.wrong}
                 for i in rows]
        right = [x for x in right if x]
        keep = set.intersection(*right) if right else set(values)
        cands = [x for x in values if x in keep] or list(values)
        return min(cands, key=lambda x: sum(1 for i in rows if x not in self.fit(i, fact)))

    def export_facts(self, inv: Invoice, cls=None) -> dict:
        """The values of the unsupplied documents behind the exported total and the reported findings (EXPORT-E): the
        well's class (or the open reading's value where the class is one open shared fact) and each section's
        nomination, shared by every invoice of the well (memo per well and per section)."""
        classes = self.classes_of(inv)
        if cls is None and classes != [None]:
            well = (inv.header or {}).get("well_name")
            memo = self.__dict__.setdefault("_xc", {})
            key = well if well else ("invoice", inv.id)
            if key not in memo:
                rows = (self.rows_of_well(inv.contract, well) if well else []) or [inv]
                values = sorted({c for i in rows for c in self.classes_of(i)}, key=lambda c: (c != DEFAULT_FACTS["class"], c))
                memo[key] = DEFAULT_FACTS["class"] if self.class_conflict(inv) else self._shared_value(rows, "class", values)
            cls = memo[key]
        noms = {}
        memo = self.__dict__.setdefault("_xn", {})
        for gk in self.nomination_groups(inv):
            if gk not in memo:
                rows = self.rows_of_group(inv.contract, gk)
                memo[gk] = self._shared_value(rows, gk, [True, False])
            noms[gk] = memo[gk]
        return {"class": cls, "nominated": noms}

    def class_conflict(self, inv: Invoice) -> bool:
        """Cl.4 (p3): 'the well class stated in the call-off governs the whole well'. Where the invoices of one well can
        be right only under different classes, no single call-off makes them all right: the class is then one open fact
        shared by them (Q9-2), not a value each invoice may take for itself."""
        if inv.contract != "DDS" or self.classes_of(inv) == [None] or not inv.header:
            return False
        well = inv.header.get("well_name")
        memo = self.__dict__.setdefault("_wc", {})
        if well not in memo:
            sets = [self.right_classes(i) for i in self.rows_of_well("DDS", well)]
            sets = [x for x in sets if x]
            memo[well] = len(sets) > 1 and not set.intersection(*sets)
        return memo[well]

    # -------------------------------------------------------------------------------------------- outcome
    def outcome(self, inv: Invoice) -> dict:
        p = self.policy
        od = self.open_dims(inv)
        classes = self.classes_of(inv)
        conflict = self.class_conflict(inv)
        if conflict:
            # one open fact shared by the well's invoices (Q9-2); its working value the absent-document one (Standard)
            od = {**od, "class": sorted(classes, key=lambda c: (c != DEFAULT_FACTS["class"], c))}
        names = sorted(od)
        combos = [dict(zip(names, c)) for c in itertools.product(*[od[n] for n in names])] or [{}]
        q1 = self.q1_scenarios(inv)
        # 'not the recipient' is a scenario only where some Q1 reading (or a tie) leaves the adjustment to another document
        readings_q1 = [q for q in self.a3[inv.contract] if not q.startswith("_") and (p.q1 == "open" or q == f"Q1:{p.q1}")]
        sole = bool(readings_q1) and all(self.a3[inv.contract][q] == [inv.id] for q in readings_q1)
        recips = (q1 if sole else [None] + q1) if p.q1 == "open" else (q1 or [None])
        fact_dep = classes != [None] or any("ground" in line_dims(g) for _k, _v, g in inv.lines)
        nomination = bool(self.nomination_groups(inv))
        labels = []
        for c in combos:
            rd = {k: v for k, v in c.items() if k != "class"}
            for r, rel_to in [(r, x) for r in recips for x in self.release_scenarios(inv)]:
                cls = c.get("class")
                evs = self.evidence_scenarios(inv, [cls] if cls is not None else classes)
                if p.facts == "default":
                    evs = [self.fallback(inv, cls)]
                cons = [self.evaluate(inv, rd, r, e, rel_to) for e in evs]
                fb = self.evaluate(inv, rd, r, self.fallback(inv, cls), rel_to) if (fact_dep or nomination) else cons[0]
                right = [s for s in cons if not s.wrong]
                # the scenario reported and exported (EXPORT-E; review findings B-3, B-4, C-2): the one at the export
                # values of the unsupplied documents - the flag (right under SOME admissible value, Q9-3 E) never depends
                # on it; its findings and its total are those of one admissible value, the same for the whole well
                xf = self.export_facts(inv, cls)
                star = next((s for s in cons if s.ev.get("class") == xf["class"] and s.ev.get("nominated") == xf["nominated"]),
                            cons[0])
                if right and star.wrong:
                    star = next((s for s in right if s.ev.get("class") == xf["class"]), right[0])
                common = set.intersection(*[{x for x in s.every_value if x[0] not in NOT_ESTABLISHED} for s in cons])
                multi = len(self.release_scenarios(inv)) > 1
                labels.append({"label": {**c, **({"Q1": r} if r else {}), **({"R45": "recipient" if rel_to else "not"} if multi else {})},
                               "wrong": not right, "reasons": star.reasons, "star": star, "every_value": common,
                               "fb": fb, "cons": cons,
                               "formed": fb.formed and all(s.formed for s in cons)})
        wrong_all = all(x["wrong"] for x in labels)
        right_all = not any(x["wrong"] for x in labels)
        formed = all(x["formed"] for x in labels)
        totals = {x["star"].total for x in labels if x["star"].formed}
        ev_totals = {s.total for x in labels for s in x["cons"] + [x["fb"]] if s.formed}
        billed = D((inv.header or {}).get("application_total" if inv.contract == "CW" else "invoice_total"))
        flag = 0 if right_all else 1
        if p.facts == "query" and (fact_dep or nomination):
            flag = 1
        # expected total under the working reading (its readings chosen from source, never by the bill): the total of the
        # reported scenario (EXPORT-E) - for an unflagged invoice its own total, an admissible total; for a flagged one the
        # total whose every difference from the bill its findings name - never the invoice's statement of a fact
        work = labels[0]
        # the totals the contract supports under the working reading, one per admissible value of the unsupplied documents
        admissible = {x.total for x in work["cons"] + [work["fb"]] if x.formed}
        expected = work["star"].total
        # the line values behind the exported total, so that it reconciles line by line
        pick = work["star"]
        evid = work["reasons"] if work["wrong"] or not flag else [x for lb in labels if lb["wrong"] for x in lb["reasons"]]
        status, bnds = "formed", [expected, expected]
        if not formed:
            # G5-B02 (EXPORT-U): some admissible scenario of the working reading cannot be valued - the total is not
            # established; export the lower bound over its evidence scenarios, disclose the upper (None: not bounded)
            bs = [self._bounds(inv, x) for x in [work["fb"]] + work["cons"]]
            lo = min(b[0] for b in bs)
            hi = None if any(b[1] is None for b in bs) else max(b[1] for b in bs)
            status, bnds, expected = "bounded", [lo, hi], lo
            pick = min([work["fb"]] + work["cons"], key=lambda x: self._bounds(inv, x)[0])
        # confidence (Q9-5)
        if not formed:
            conf = Decimal("0.30")
        elif not wrong_all and not right_all:
            conf = Decimal("0.50")
        elif wrong_all and (len(totals) > 1 or len(ev_totals) > 1):
            conf = Decimal("0.60")
        else:
            conf = Decimal("0.95")
            cats = {CATEGORY.get(r[0], "other") for r in evid}
            if fact_dep or nomination or self._q7c_depends(inv) or (flag and cats and cats <= PROCEDURAL | {"adjustment"}):
                conf = Decimal("0.80")
        if p.facts == "query" and (fact_dep or nomination) and not wrong_all:
            conf = min(conf, Decimal("0.30"))
        # review finding B-1 (Q9-5): 0.95 and 0.80 require every applicable check to have been made - where a check's
        # account or input is not established (a payment account, an unvalued input), whether an unflagged row is right is
        # not established (0.50), and a flagged row's findings may be incomplete (at most 0.80)
        if formed and any(r[0] in NOT_ESTABLISHED for lb in labels for s in [lb["fb"]] + lb["cons"] for r in s.reasons):
            conf = min(conf, Decimal("0.50") if not flag else Decimal("0.80"))
        cats = []
        if flag:
            for r in sorted((r for r in evid if r[0] not in NOT_ESTABLISHED),
                            key=lambda r: CATEGORY_ORDER.index(CATEGORY.get(r[0], "arithmetic"))):
                c = CATEGORY.get(r[0], "arithmetic")
                if c not in cats:
                    cats.append(c)
        return {"invoice_id": inv.id, "contract": inv.contract, "flagged": flag, "error_category": "; ".join(cats),
                "expected_total": expected, "billed_total": billed, "confidence": conf,
                # the contract value on the supplied evidence under the working reading: never the bill, never a stated fact
                "contract_total": work["fb"].total,
                "wrong_under": [x["label"] for x in labels if x["wrong"]],
                "right_under": [x["label"] for x in labels if not x["wrong"]],
                "scenarios": len(labels), "open_readings": {k: v for k, v in od.items()}, "q1": q1,
                "fact_dependent": fact_dep, "nomination_dependent": nomination, "class_conflict": conflict,
                "evidence_totals": [str(min(ev_totals)), str(max(ev_totals))] if ev_totals else None,
                "admissible_totals": sorted(str(t) for t in admissible),
                "expected_status": status, "expected_bounds": [None if x is None else str(x) for x in bnds],
                "expected_basis": ("EXPORT-U: the lower bound of the admissible total - the valued lines and each unvalued line at "
                                   "the least its admissible values allow (0 where none is established); the total itself "
                                   f"is not established (upper bound {'none' if bnds[1] is None else bnds[1]})")
                if status != "formed" else ("EXPORT-E: an admissible total - the contract total under the values of the "
                                            "unsupplied documents shared by the well (export_facts), under which the invoice "
                                            "is right" if not work["star"].wrong else
                                            "EXPORT-E: an admissible total - the contract total under the values of the "
                                            "unsupplied documents shared by the well (export_facts); every difference from "
                                            "the bill is named by the findings")
                if (fact_dep or nomination) else "contract value under the working reading",
                "export_facts": ({"class": work["star"].ev.get("class"),
                                  "nominated": {f"{k[0]}|{k[1]}": x for k, x in (work["star"].ev.get("nominated") or {}).items()},
                                  "ground": dict(sorted(work["star"].grounds.items()))}
                                 if (fact_dep or nomination) else None),
                "findings": sorted({f"{r[0]}@{r[1]}" if r[1] else r[0] for r in evid if r[0] not in NOT_ESTABLISHED}),
                # the findings that hold under EVERY admissible value of the unsupplied documents (the rest hold under the
                # export values; whether the invoice is wrong never depends on those values)
                "findings_every_value": sorted({f"{r[0]}@{r[1]}" if r[1] else r[0] for r in work["every_value"]})
                if flag else [],
                "not_established": sorted({f"{r[0]}@{r[1]}" if r[1] else r[0] for r in evid + work["star"].reasons
                                           if r[0] in NOT_ESTABLISHED}),
                "totals": sorted(str(t) for t in totals), "formed": formed,
                "expected_under": work["label"], "line_values": {k: (None if a is None else str(a)) for k, a in pick.line_values.items()}}

    def _q7c_depends(self, inv: Invoice) -> bool:
        return any(k in self.stands for k, _v, _g in inv.lines)

    def _bounds(self, inv: Invoice, sc: Scenario) -> tuple:
        """G5-B02: the admissible judged total of a scenario that cannot be formed, as bounds - each valued line at its
        value, each unvalued line between its bounds; drilling through DS-900 (Cl.38) and VAT (Cl.40), which rise with
        the services total. No billed amount and no zero is substituted for a value; hi is None where not bounded."""
        lo = hi = ZERO
        for ref, a in sc.line_values.items():
            b = (a, a) if a is not None else sc.bounds.get(ref, (ZERO, None))
            lo += b[0]
            hi = None if hi is None or b[1] is None else hi + b[1]

        def total(svc):
            if svc is None or inv.contract == "CW":
                return svc
            ds = -half_even((svc - 250000) * Decimal("0.04")) if svc > 250000 else ZERO
            return svc + ds + half_even((svc + ds) * Decimal("0.15"))
        return total(lo), total(hi)

    def run(self, template_ids: list[str] | None = None) -> dict:
        out = {}
        for c, invs in self.inv.items():
            for no, inv in invs.items():
                out[no] = self.outcome(inv)
        return out


def template_ids(snapshot) -> list[str]:
    import csv
    with (snapshot / "submission_template.csv").open(newline="") as fh:
        return [r["invoice_id"] for r in csv.DictReader(fh)]


def cents(x: Decimal | None) -> int | None:
    if x is None:
        return None
    return int((x * 100).to_integral_value(rounding=ROUND_HALF_EVEN))
