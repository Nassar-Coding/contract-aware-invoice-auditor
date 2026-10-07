"""G4 shared core: state checks, the G4 line result, orderings, and alternatives over open readings.

G4 consumes the G2 world and the G3 line results. G3 is closed: its results are never modified - every G4 result is
a copy carrying the state consequences as StateChecks, each naming the rule, the clause and the ledger entry it came
from. A value G4 changes carries a replayable trace (tools/verify_g3.replay). Where the contract, or a question the
register keeps open, leaves a state outcome open (the Contract Year reset Q12, which quantities count Q6, the order of
measurements of one day, which of two equal charges stands), every admissible outcome is carried as an alternative
with its condition and owner - never chosen by billing, by incidental row order or by the smallest document number.
"""
from __future__ import annotations

import copy
import itertools
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal

from .g3_core import LineResult

CENT = Decimal("0.01")
READING_DIMS = {"Q4", "Q5-DD120", "Q5-RM530", "Q5-HC630", "Q11", "Q12", "Q14", "Q6", "Q6-day0", "order", "stands", "earlier",
                "stands-run", "alloc"}


def local_dim(kind: str, group: str) -> str:
    """A choice local to one evidence/state group (G5-B04): its dimension is named after the group it governs, so two
    independent groups never share one assignment. Group ids lose the label separators (':' '|' ',')."""
    return f"{kind}@" + group.replace(":", "-").replace("|", "/").replace(",", "+")


def base(dim: str) -> str:
    """The kind of a dimension: a local choice is namespaced by the evidence group it governs ('order@<ledger>/<date>',
    'stands@<group>', 'alloc@<well>/<date>'); its kind is the part before '@'."""
    return dim.split("@", 1)[0]
OWNER = {   # who decides a dimension G4 leaves open (the value is carried under each alternative)
    "Q12": ("G5", "3A (p32): whether the civil count restarts on 5 January 2026 (anniversary) or runs on through the "
                  "extension (no new Contract Year); spec/open_questions.yaml Q12, kept open at G4"),
    "Q14": ("G5", "3A (p35): whether the drilling Contract Year restarts on 1 January 2026 or runs on through the extension; "
                  "spec/open_questions.yaml Q14, kept open at G4"),
    "Q6": ("G5", "Sch 4 Part 3 (p24) 'the quantity measured': whether a measurement not payable for want of its record (Cl.46) "
                 "counts (A) or only payable quantity (B); spec/open_questions.yaml Q6, kept open at G4"),
    "Q6-day0": ("G5", "Cl.32 (p6) and Sch 4 Part 5 (p26) exclude A.14.020 'within the stated period following' the "
                      "A.14.010 measurement (the days after it), P19 (p13) 'within two days of' it (the same day too); "
                      "Sch 4 prevails over the Conditions (Agreement p3) and Part P over Parts I-III (p12), but neither ranks "
                      "Part P against a Schedule; spec/open_questions.yaml Q6 D, kept open at G4"),
    "order": ("G5", "Sch 4 Part 3 substitutes Cl.30 (p24): no order is stated for measurements of one date; Cl.30's former "
                    "'application number and then line number' is one alternative (a convention), every other order another"),
    "stands": ("G5", "DDS Cl.26, 27, 29, 31 (p7): the service is charged once, but no clause says which of two admissible "
                     "charges is the one that stands (CW Cl.44's later-copy rule is not imported: Q7 C)"),
    "alloc": ("G5", "DDS Cl.23 (p6), Cl.29 (p7): PD-210 metres are charged once; which charge keeps each contested segment of "
                    "a well-day is not stated (Q7 C) - every joint allocation carried"),
    "earlier": ("G5", "CW Cl.44 (p8): 'the later measurement shall be disallowed', but these applications were submitted on the "
                      "same day: which measurement is the later is not established (no contractual tie-breaker)"),
}


def half_even(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_EVEN)


@dataclass
class StateCheck:
    family: str                 # duplicate | exclusion | daily_limit | band | run_event | well_event | loss_event | footage
    status: str                 # pass | finding | unresolved | n/a
    rule: str
    clause: str
    finding: str | None = None
    detail: str = ""
    ledger: str | None = None   # the ledger entry / group the consequence comes from


@dataclass
class G4Line:
    key: str
    g3: LineResult              # the closed G3 result (never modified)
    r: LineResult = None        # the G4-valued copy
    state: list = field(default_factory=list)

    def __post_init__(self):
        if self.r is None:
            self.r = copy.deepcopy(self.g3)

    def add(self, family, status, rule, clause, finding=None, detail="", ledger=None):
        self.state.append(StateCheck(family, status, rule, clause, finding, str(detail), ledger))

    @property
    def state_findings(self) -> list[str]:
        return sorted({s.finding for s in self.state if s.status == "finding" and s.finding})

    @property
    def state_unresolved(self) -> list[str]:
        return sorted({s.finding for s in self.state if s.status == "unresolved" and s.finding})

    @property
    def changed(self) -> bool:
        a, b = self.g3, self.r
        return (a.payable, a.allowed_quantity, a.amount, a.amount_status, _alt_values(a)) != \
               (b.payable, b.allowed_quantity, b.amount, b.amount_status, _alt_values(b))

    def to_json(self) -> dict:
        j = self.r.to_json()
        j["key"] = self.key
        j["state"] = [s.__dict__ for s in self.state]
        j["state_findings"] = self.state_findings
        j["state_unresolved"] = self.state_unresolved
        j["g3"] = {"payable": self.g3.payable, "allowed_quantity": _s(self.g3.allowed_quantity), "amount": _s(self.g3.amount),
                   "amount_status": self.g3.amount_status}
        j["changed"] = self.changed
        return j


def _s(v):
    return None if v is None else str(v)


def _alt_values(r: LineResult):
    return tuple(sorted((k or "", str(v.get("allowed_quantity")), str(v.get("amount"))) for k, v in r.alternatives.items()))


# ------------------------------------------------------------------------------------------------ alternatives
def dims_of(label: str | None) -> dict:
    return dict(x.split(":", 1) for x in label.split("|")) if label else {}


def label_of(d: dict) -> str | None:
    return "|".join(f"{k}:{v}" for k, v in sorted(d.items())) or None


def collapse(options: dict) -> dict:
    """Drop every dimension whose value changes no result (allowed quantity, amount and the breaches carried under it -
    G4-B04), then merge identical labels
    (the G3 rule, spec/g3_decisions.yaml): an alternative is carried only where it changes something."""
    names = sorted({d for k in options for d in dims_of(k)})
    for name in names:
        allv = {dims_of(k).get(name) for k in options}
        groups = {}
        for k, v in options.items():
            dd = dims_of(k)
            rest = label_of({d: x for d, x in dd.items() if d != name})
            groups.setdefault(rest, {})[dd.get(name)] = (v["allowed_quantity"], v["amount"], tuple(sorted(v.get("breaches") or ())))
        # droppable only where every other combination carries every value of the dimension with one result (a label
        # set that is not a full product - a dimension that applies under one reading only - keeps it)
        if all(set(g) == allv and len(set(g.values())) == 1 for g in groups.values()):
            new = {}
            for k, v in options.items():
                new.setdefault(label_of({d: x for d, x in dims_of(k).items() if d != name}), v)
            options = new
    return options


def apply_options(g: G4Line, options: dict, basis: dict, reason: str) -> None:
    """Set the G4 value of a line from its options {label: {unit_rate, allowed_quantity, amount, trace}}: one option is
    a single value; several are carried as alternatives with a condition per remaining dimension (owner and basis)."""
    r = g.r
    options = collapse(options)
    kept = {d for k in options for d in dims_of(k)}
    old_conditions = {c["dimension"]: c for c in r.conditions}
    r.alternatives = {}
    if len(options) == 1:
        (k, v), = options.items()
        r.allowed_quantity, r.amount, r.trace = v["allowed_quantity"], v["amount"], v["trace"]
        r.unit_rate = v.get("unit_rate")
        payable = v["amount"] is None or v["amount"] != 0 or v["allowed_quantity"] != 0
        r.payable = True if payable else r.payable
        r.conditions = [c for c in r.conditions if c["dimension"] in {"nomination"}]
        if r.amount_status in ("alternatives", "conditional") and not r.conditions:
            r.amount_status = "determined"
    else:
        vals = list(options.values())
        r.allowed_quantity = vals[0]["allowed_quantity"] if len({v["allowed_quantity"] for v in vals}) == 1 else None
        rates = {v.get("unit_rate") for v in vals}
        r.unit_rate = next(iter(rates)) if len(rates) == 1 else None
        r.amount = None
        for k, v in options.items():
            r.alternatives[k] = {"unit_rate": v.get("unit_rate"), "allowed_quantity": v["allowed_quantity"], "amount": v["amount"],
                                 "trace": v["trace"]}
            if v.get("breaches"):
                r.alternatives[k]["breaches"] = sorted(set(v["breaches"]))       # G4-B04: a breach under this scenario only
        r.conditions = [c for c in r.conditions if c["dimension"] in kept | {"nomination"}]
        for d in sorted(kept):
            if d not in {c["dimension"] for c in r.conditions}:
                owner, why = basis.get(d) or OWNER.get(base(d)) or old_conditions.get(d, {}).get("owner", "G5"), None
                if isinstance(owner, tuple):
                    owner, why = owner
                r.conditions.append({"dimension": d, "owner": owner, "basis": why or basis.get(d + ":basis") or reason})
        r.amount_status = "alternatives" if {base(k) for k in kept} & READING_DIMS else "conditional"
    if any(c["dimension"] == "nomination" for c in r.conditions) and r.amount_status == "determined":
        r.amount_status = "conditional"


def not_payable(g: G4Line, reason: str, clause: str) -> None:
    """A G4 consequence that takes the line out of the valuation entirely (C-REJECT), with its trace note."""
    r = g.r
    r.payable, r.allowed_quantity, r.amount, r.unit_rate = False, Decimal("0"), Decimal("0.00"), None
    r.alternatives, r.conditions = {}, []
    r.amount_status = "not_payable"
    r.reasons = list(r.reasons) + [reason]
    r.trace = list(g.g3.trace) + [{"op": "note", "label": f"G4: not payable - {reason}", "source": clause}]


def base_options(r: LineResult) -> dict:
    """The G3 value of a line as options {label: {...}} (the single value under label None)."""
    if r.alternatives:
        return {k: {"unit_rate": v.get("unit_rate"), "allowed_quantity": v.get("allowed_quantity"), "amount": v.get("amount"),
                    "trace": v.get("trace") or []} for k, v in r.alternatives.items()}
    return {None: {"unit_rate": r.unit_rate, "allowed_quantity": r.allowed_quantity, "amount": r.amount, "trace": r.trace}}


def product_labels(*dimsets) -> list[dict]:
    """Every combination of dimension values: dimsets are lists of (dim, [values])."""
    names = [d for d, _ in dimsets]
    return [dict(zip(names, combo)) for combo in itertools.product(*[v for _, v in dimsets])]


def recipient_of(first: list, undated: list):
    """The one document a posting goes to (A3 difference, 45A release) from the dated documents tied first and the
    documents whose submission date is not established (G4-B03): any undated one may have been submitted first, so
    with any of them the recipient is not established - never chosen by omitting them or by a sentinel date."""
    if undated:
        return {"not_established": sorted(set(first) | set(undated)), "dated_first": list(first), "undated": list(undated)}
    if not first:
        return None
    return first[0] if len(first) == 1 else {"tie": list(first)}
