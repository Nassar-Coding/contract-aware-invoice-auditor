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
from .g4_core import dims_of

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
STANDS_DIMS = {"stands", "stands-run"}   # resolved by the Q7 C policy decision

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
    "lwd_not_run": "eligibility", "not_performance_section": "eligibility",
    "above_daily_limit": "limit",
    "rate_differs": "rate", "band_crossing_not_split": "rate",
    "discount": "discount",
    "amount_arithmetic": "arithmetic", "header_arithmetic": "arithmetic",
    "adjustment_omitted": "adjustment", "release_omitted": "adjustment", "retention_arithmetic": "arithmetic",
}
CATEGORY_ORDER = ["identity", "term", "timing", "evidence", "signature", "evidence_mismatch", "unit", "quantity", "duplicate",
                  "eligibility", "limit", "rate", "discount", "arithmetic", "adjustment"]
INFORMATIONAL = {"band_divided", "footage_band_divided"}      # the contract's own pricing state, not a defect
PROCEDURAL = {"identity", "term", "timing"}
PAYMENT_ONLY = {"adjustment_omitted", "release_omitted", "retention_arithmetic"}
NOT_ESTABLISHED = {"engine_error", "input_unresolved", "no_admissible_result", "quantity_rule_missing"}


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
    facts: str = "stated"          # stated (Q9-3) | default (C: Standard / G2) | query (B: every fact-dependent invoice flagged)
    q7c: str = "earlier"           # earlier (owner) | open (either charge stands)
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


DEFAULT_FACTS = {"class": "Standard", "ground": "G2"}


def standing_map(w, st: dict, inv_date: dict) -> dict:
    """Policy decision Q7 C: of the admissible charges of one group, the one on the earliest-submitted invoice stands;
    within one invoice (or one submission date) the earliest line. {line key: {dim: standing ref}}."""
    out = {}
    for c in st:
        lines = {g.g3.line_ref: (k, g) for k, g in st[c].lines.items()}
        groups = getattr(st[c], "groups", [])
        for grp in groups:
            cands = [m for m in grp.get("candidates", []) if m in lines]
            if len(cands) < 2:
                continue

            def order(ref):
                no = ref.rsplit("-", 1)[0]
                return (inv_date.get((c, no)) or "9999", no, ref)
            dim = "stands-run" if grp["group"].startswith("DDS-HC630") and any(
                "stands-run" in dims_of(a) for m in cands for a in lines[m][1].r.alternatives) else "stands"
            first = sorted(cands, key=order)[0]
            # a charge on another invoice submitted the same day: which invoice is earlier is not established (open)
            tie = any(order(m)[0] == order(first)[0] and order(m)[1] != order(first)[1] for m in cands)
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
            elif rec:
                out[q] = [rec]
        out["_total"] = a["total"]
    return out


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
    def fixed_for(self, inv: Invoice, key, v, g, readings: dict, facts_override: dict | None = None) -> dict | None:
        p = self.policy
        fixed = dict(p.decided)
        fixed.update(readings)
        stated = stated_facts(inv, v)
        dims = line_dims(g)
        for d in dims & FACT_DIMS:
            if facts_override and d in facts_override:
                fixed[d] = facts_override[d]
            elif p.facts == "default":
                fixed[d] = DEFAULT_FACTS[d]
            else:
                fixed[d] = stated.get(d)
        for d in dims & STANDS_DIMS:
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
                for k, x in d.items():
                    if k in p.decided or k in FACT_DIMS:
                        continue
                    if k in STANDS_DIMS:
                        s = self.stands.get(key, {}).get(k)
                        if p.q7c == "earlier" and s is not None and not s[1]:
                            continue
                    out.setdefault(k, [])
                    if x not in out[k]:
                        out[k].append(x)
        for k, vals in out.items():
            pref = READING_ORDER.get(k, [])
            out[k] = [x for x in pref if x in vals] + sorted(x for x in vals if x not in pref)
            if k in p.force:
                out[k] = [p.force[k]]
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
    def evaluate(self, inv: Invoice, readings: dict, recipient: str | None, facts_override: dict | None = None) -> Scenario:
        p = self.policy
        reasons = []
        vals = {}
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
                continue
            fixed = self.fixed_for(inv, key, v, g, readings, facts_override)
            q, a, ok = line_value(g, fixed)
            vals[ref] = a
            for f in established(g):
                reasons.append((f, ref))
            if not ok:
                formed = False
                reasons.append(("input_unresolved", ref))
                continue
            if a != billed:
                if not any(r[1] == ref and CATEGORY.get(r[0]) not in PROCEDURAL for r in reasons):
                    # the line is wrong in this scenario: name it by the rule that makes it so (a state or G3 finding left
                    # open across scenarios), else by what differs (quantity below the billed one, or the rate)
                    open_f = [x.finding for x in g.state if x.status == "unresolved" and x.finding
                              and x.finding not in INFORMATIONAL and x.finding in CATEGORY and x.finding != "rate_differs"]
                    bq = D(v.get("quantity"))
                    reasons.append((open_f[0] if open_f else
                                    "quantity_above_record" if (q is not None and bq is not None and q < bq and q != 0)
                                    else "rate_differs", ref))
            # a fact statement the scenario contradicts is itself a breach (Q9-3)
            st_f = stated_facts(inv, v)
            for d in line_dims(g) & FACT_DIMS:
                if st_f.get(d) is not None and fixed.get(d) != st_f.get(d):
                    reasons.append(("class_statement" if d == "class" else "ground_differs_from_record", ref))
            if inv.contract == "DDS":
                svc_exp += a
                svc_bill += billed
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
            if self.release and self.release.get("recipient") == inv.id and (D(h.get("retention_released")) or ZERO) == 0:
                reasons.append(("release_omitted", None))
            total = exp_total
        else:
            billed_total = D(h.get("invoice_total"))
            ds_on_billed = -half_even((svc_bill - 250000) * Decimal("0.04")) if svc_bill > 250000 else ZERO
            if ds_bill != ds_on_billed:
                reasons.append(("discount", None))
            if D(h.get("net_amount")) != billed_lines or D(h.get("vat_amount")) != half_even((D(h.get("net_amount")) or ZERO) * Decimal("0.15")) \
                    or billed_total != (D(h.get("net_amount")) or ZERO) + (D(h.get("vat_amount")) or ZERO):
                reasons.append(("header_arithmetic", None))
            ds = -half_even((svc_exp - 250000) * Decimal("0.04")) if svc_exp > 250000 else ZERO
            adj = ZERO
            if recipient and p.q2 == "B":
                adj = self._a3_difference(inv.contract)
            net = svc_exp + adj + ds
            total = net + half_even(net * Decimal("0.15"))
        if recipient:
            if (D(h.get("adjustment")) or ZERO) == 0:
                reasons.append(("adjustment_omitted", None))
            if p.q2 == "B" and inv.contract == "CW":
                total = total + self._a3_difference(inv.contract)
        if billed_total is not None and formed and total != billed_total:
            if not any(CATEGORY.get(r[0]) not in PROCEDURAL | {"adjustment"} for r in reasons):
                reasons.append(("header_arithmetic", None))
        wrong = self._wrong(reasons, total, billed_total, formed)
        return Scenario({**readings, **({"Q1": recipient} if recipient else {})}, wrong, total if formed else None,
                        reasons, formed, vals)

    def _a3_difference(self, contract: str) -> Decimal:
        t = self.a3[contract].get("_total") or {}
        by = t.get("by_reading", {})
        v = by.get("Q12:A") or by.get("") or next(iter(by.values()), {"min": "0"})
        return Decimal(v["min"])

    def _wrong(self, reasons, total, billed_total, formed) -> bool:
        if self.policy.q9 == "B":
            return formed and billed_total is not None and total != billed_total
        # an input that cannot be formed is not itself a failed check (Q9-4: flagged only when a check fails)
        return bool([r for r in reasons if r[0] not in NOT_ESTABLISHED])

    # -------------------------------------------------------------------------------------------- outcome
    def outcome(self, inv: Invoice) -> dict:
        p = self.policy
        od = self.open_dims(inv)
        names = sorted(od)
        combos = [dict(zip(names, c)) for c in itertools.product(*[od[n] for n in names])] or [{}]
        q1 = self.q1_scenarios(inv)
        recips = [None] + q1 if p.q1 == "open" else (q1 or [None])
        scen = [self.evaluate(inv, c, r) for c in combos for r in recips]
        working = scen[0] if p.q1 == "open" else scen[0]
        # fact dependence (Q9-3)
        fact_dep = any(line_dims(g) & FACT_DIMS for _k, _v, g in inv.lines)
        nomination = any(any(x["dimension"] == "nomination" for x in g.r.conditions) for _k, _v, g in inv.lines)
        wrong_all = all(s.wrong for s in scen)
        right_all = not any(s.wrong for s in scen)
        formed = all(s.formed for s in scen)
        totals = {s.total for s in scen if s.formed}
        billed = D((inv.header or {}).get("application_total" if inv.contract == "CW" else "invoice_total"))
        flag = 0 if right_all else 1
        if p.facts == "query" and (fact_dep or nomination):
            flag = 1
        # expected total: the total under the working reading, always - a reading chosen from source, never the scenario
        # the billed figures happen to fall in (Z4 found the earlier 'first wrong scenario' rule moving with the bill)
        pick = working
        expected = pick.total
        # the evidence behind the flag: the working scenario's findings, or those of the scenarios under which it is wrong
        evid = pick.reasons if pick.wrong or not flag else [x for sc in scen if sc.wrong for x in sc.reasons]
        if expected is None:
            expected = self._best_supported(inv, pick)
        # confidence (Q9-5)
        if not formed:
            conf = Decimal("0.30")
        elif not wrong_all and not right_all:
            conf = Decimal("0.50")
        elif wrong_all and len(totals) > 1:
            conf = Decimal("0.60")
        else:
            conf = Decimal("0.95")
            cats = {CATEGORY.get(r[0], "other") for r in pick.reasons}
            if fact_dep or nomination or self._q7c_depends(inv) or (flag and cats and cats <= PROCEDURAL | {"adjustment"}):
                conf = Decimal("0.80")
        if p.facts == "query" and (fact_dep or nomination) and not wrong_all:
            conf = min(conf, Decimal("0.30"))
        cats = []
        if flag:
            for r in sorted((r for r in evid if r[0] not in NOT_ESTABLISHED),
                            key=lambda r: CATEGORY_ORDER.index(CATEGORY.get(r[0], "arithmetic"))):
                c = CATEGORY.get(r[0], "arithmetic")
                if c not in cats:
                    cats.append(c)
        return {"invoice_id": inv.id, "contract": inv.contract, "flagged": flag, "error_category": "; ".join(cats),
                "expected_total": expected, "billed_total": billed, "confidence": conf,
                "wrong_under": [s.label for s in scen if s.wrong], "right_under": [s.label for s in scen if not s.wrong],
                "scenarios": len(scen), "open_readings": {k: v for k, v in od.items()}, "q1": q1,
                "fact_dependent": fact_dep, "nomination_dependent": nomination,
                "findings": sorted({f"{r[0]}@{r[1]}" if r[1] else r[0] for r in evid if r[0] not in NOT_ESTABLISHED}),
                "not_established": sorted({f"{r[0]}@{r[1]}" if r[1] else r[0] for r in evid if r[0] in NOT_ESTABLISHED}),
                "totals": sorted(str(t) for t in totals), "formed": formed,
                "expected_under": pick.label, "line_values": {k: (None if a is None else str(a)) for k, a in pick.line_values.items()}}

    def _q7c_depends(self, inv: Invoice) -> bool:
        return any(k in self.stands for k, _v, _g in inv.lines)

    def _best_supported(self, inv: Invoice, s: Scenario) -> Decimal:
        """Q9-4: every valued line at its working value; an unvalued line at its billed value (disclosed)."""
        tot = ZERO
        for key, v, g in inv.lines:
            ref = v.get("line_ref") or key
            a = s.line_values.get(ref)
            tot += a if a is not None else (D(v.get("amount")) or ZERO)
        return tot

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
